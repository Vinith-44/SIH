"""PR-0 contract: event schema v2, MQTT bus (paho 2.x + Last Will), config v2."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from storemind.core.bus import (
    QOS1_TYPES,
    RETAINED_TYPES,
    MqttBus,
    command_topic,
    qos_for,
    retain_for,
    status_topic,
    topic_for,
)
from storemind.core.clock import IST
from storemind.core.config import StoreMindConfig, load_config, load_secrets
from storemind.core.events import (
    PAYLOADS,
    SCHEMA_VERSION,
    Event,
    EventType,
    HealthData,
    make_event,
)

ROOT = Path(__file__).resolve().parents[2]
TS = datetime(2026, 9, 24, 18, 0, 0, tzinfo=IST)

V1_TYPES = {"ENTRY", "EXIT", "ZONE_VISIT", "QUEUE_STATE", "SERVICE_DONE", "SLOT_STATE",
            "SENSOR", "PICKUP", "SHRINK_FLAG", "LOST_SALE_RISK", "FORECAST", "ALERT", "HEALTH"}

# One valid payload per v2 type, exactly as documented in docs/INTERFACES.md.
V2_EXAMPLES = {
    EventType.SHELF_MOTION: {"node": "m1", "shelf": "shelf-a", "slot": "A1", "kind": "TOUCH",
                             "peak_mg": 412, "rms_mg": 138, "dur_ms": 640},
    EventType.CAMERA_MOUNT: {"node": "m2", "cam": "entrance", "kind": "KNOCK", "peak_mg": 1850},
    EventType.BEAM_CROSS: {"node": "stm32-01", "door": "door1", "direction": "in",
                           "t_ms_mcu": 610140},
    EventType.PRESENCE: {"node": "stm32-01", "zone": "aisle1", "active": True},
    EventType.ENVIRONMENT: {"node": "stm32-01", "lux": 420, "temp_c": 28.4, "rh_pct": 61.5,
                            "pressure_hpa": 1009.3},
    EventType.WEIGHT: {"node": "stm32-01", "slot": "A1", "grams": 1622, "stable": True},
    EventType.NODE_HEALTH: {"node": "stm32-01", "uptime_s": 720, "free_heap": 6144,
                            "min_stack_words": 38, "i2c_err": 0, "uart_err": 0, "crc_err": 0,
                            "reset_cause": "POR", "link": "up"},
    EventType.CAMERA_HEALTH: {"cam": "entrance", "state": "ok", "fps": 9.8, "lag_ms": 120,
                              "reconnects": 0},
}


def ev(event_type: EventType, data: dict, node: str = "stm32-01", cam: str | None = None) -> Event:
    return make_event(ts=TS, store="bvrit-demo", node=node, cam=cam, type=event_type, data=data)


# --------------------------------------------------------------------------- #
# Event schema v2
# --------------------------------------------------------------------------- #

def test_schema_is_v2_and_every_v1_type_is_still_there():
    assert SCHEMA_VERSION == 2
    assert V1_TYPES <= {t.value for t in EventType}
    assert set(V2_EXAMPLES) | {EventType(t) for t in V1_TYPES} == set(EventType)
    assert set(PAYLOADS) == set(EventType)


@pytest.mark.parametrize("event_type", list(V2_EXAMPLES))
def test_every_v2_type_validates_and_round_trips(event_type):
    event = ev(event_type, V2_EXAMPLES[event_type])
    assert event.v == 2
    restored = Event(**json.loads(event.to_json()))
    assert restored.type is event_type and restored.data == event.data


def test_a_stored_v1_event_still_loads():
    old = {"v": 1, "id": "abc", "ts": TS.isoformat(), "store": "s", "node": "pi5-01",
           "cam": "entrance", "type": "ENTRY",
           "data": {"line": "door", "track": 3, "direction": "in"}}
    assert Event(**old).v == 1


@pytest.mark.parametrize("event_type, bad", [
    (EventType.SHELF_MOTION, {"kind": "SHAKE"}),
    (EventType.CAMERA_MOUNT, {"kind": "TOUCH"}),         # only KNOCK | TILT on a camera
    (EventType.BEAM_CROSS, {"direction": "IN"}),         # lower-case on the bus
    (EventType.NODE_HEALTH, {"link": "flaky"}),
    (EventType.CAMERA_HEALTH, {"state": "broken"}),
    (EventType.WEIGHT, {"typo": 1}),
])
def test_v2_payloads_reject_bad_values(event_type, bad):
    with pytest.raises(ValidationError):
        ev(event_type, {**V2_EXAMPLES[event_type], **bad})


def test_optional_v2_fields_may_be_omitted():
    ev(EventType.ENVIRONMENT, {"node": "stm32-01", "lux": 3})       # no BME280 fitted
    ev(EventType.SHELF_MOTION, {k: v for k, v in V2_EXAMPLES[EventType.SHELF_MOTION].items()
                                if k != "slot"})


def test_health_gains_power_fields_and_old_health_still_validates():
    assert HealthData().power_w is None
    event = ev(EventType.HEALTH, {"fps": {"entrance": 9.5}, "power_w": 6.8,
                                  "mj_per_frame": 715.8, "throttled": False}, node="pi5-01")
    assert event.data["mj_per_frame"] == pytest.approx(715.8)
    ev(EventType.HEALTH, {"fps": {"entrance": 9.5}}, node="pi5-01")


# --------------------------------------------------------------------------- #
# MQTT bus
# --------------------------------------------------------------------------- #

class FakeClient:
    """Records what MqttBus asks paho to do; no broker needed."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscribed: list[str] = []
        self.will: tuple | None = None
        self.on_connect = self.on_message = None

    def username_pw_set(self, *_):
        pass

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def connect(self, *_a, **_k):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


def make_bus(listen=False):
    client = FakeClient()
    bus = MqttBus(store="bvrit-demo", node="pi5-01", listen=listen, client=client)
    return bus, client


def test_last_will_is_retained_offline_on_the_status_topic():
    _, client = make_bus()
    assert client.will == ("storemind/bvrit-demo/pi5-01/status", "offline", 1, True)


def test_online_is_published_on_connect_and_offline_on_clean_close():
    bus, client = make_bus(listen=True)
    client.on_connect(client, None, None, SimpleNamespace(is_failure=False), None)
    assert client.published[-1] == ("storemind/bvrit-demo/pi5-01/status", "online", 1, True)
    assert client.subscribed == ["storemind/bvrit-demo/#"]
    bus.close()
    assert client.published[-1][1] == "offline"


def test_qos_and_retain_follow_the_contract():
    assert qos_for(EventType.BEAM_CROSS) == 1 and qos_for(EventType.SHELF_MOTION) == 1
    assert qos_for(EventType.ENVIRONMENT) == 0
    assert retain_for(EventType.NODE_HEALTH) and retain_for(EventType.CAMERA_HEALTH)
    assert not retain_for(EventType.BEAM_CROSS)
    assert EventType.ALERT in QOS1_TYPES and EventType.HEALTH in RETAINED_TYPES
    bus, client = make_bus()
    event = ev(EventType.ENVIRONMENT, V2_EXAMPLES[EventType.ENVIRONMENT])
    bus.publish(event)
    topic, payload, qos, retain = client.published[-1]
    assert topic == "storemind/bvrit-demo/stm32-01/_/ENVIRONMENT"
    assert (qos, retain) == (0, True)
    assert json.loads(payload)["id"] == event.id


def test_events_from_other_processes_reach_local_subscribers_but_own_echo_does_not():
    bus, client = make_bus(listen=True)
    got: list[Event] = []
    bus.subscribe_types(EventType.BEAM_CROSS, got.append)

    mine = ev(EventType.BEAM_CROSS, V2_EXAMPLES[EventType.BEAM_CROSS])
    bus.publish(mine)                                            # delivered locally once
    client.on_message(client, None, SimpleNamespace(topic=topic_for(mine),
                                                    payload=mine.to_json().encode()))
    theirs = ev(EventType.BEAM_CROSS, V2_EXAMPLES[EventType.BEAM_CROSS])
    client.on_message(client, None, SimpleNamespace(topic=topic_for(theirs),
                                                    payload=theirs.to_json().encode()))
    client.on_message(client, None, SimpleNamespace(topic="storemind/x/y/_/BEAM_CROSS",
                                                    payload=b"not json"))
    assert [e.id for e in got] == [mine.id, theirs.id]


def test_command_and_status_topics():
    assert status_topic("s", "stm32-01") == "storemind/s/stm32-01/status"
    assert command_topic("s", "stm32-01", "TARE") == "storemind/s/stm32-01/cmd/TARE"
    with pytest.raises(ValueError):
        command_topic("s", "stm32-01", "REBOOT")


def test_real_paho_client_is_built_with_callback_api_v2():
    paho = pytest.importorskip("paho.mqtt")
    import paho.mqtt.client as mqtt

    assert int(paho.__version__.split(".")[0]) >= 2
    assert mqtt.Client(mqtt.CallbackAPIVersion.VERSION2) is not None  # the form bus.py uses
    source = (ROOT / "storemind" / "storemind" / "core" / "bus.py").read_text(encoding="utf-8")
    assert "CallbackAPIVersion.VERSION2" in source


def test_requirements_pin_paho_2():
    req = (ROOT / "storemind" / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^paho-mqtt>=2,<3", req, flags=re.MULTILINE)


# --------------------------------------------------------------------------- #
# Config v2 + secrets
# --------------------------------------------------------------------------- #

def test_default_enabled_sensors_leave_optional_hardware_off():
    config = StoreMindConfig()
    assert "ld2450" not in config.sensors.node.enabled_sensors
    assert "servo" not in config.sensors.node.enabled_sensors
    assert not config.sensors.has("mems")          # sensors.enabled is false by default


def test_unknown_sensor_name_is_a_clear_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("sensors:\n  enabled: true\n  node:\n    enabled_sensors: [hx711, tof]\n",
                    encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        load_config(path)
    assert "sensors.node.enabled_sensors.1" in str(caught.value)


def test_mems_node_must_say_what_it_is_mounted_on():
    with pytest.raises(ValidationError):
        StoreMindConfig(sensors={"mems_nodes": [{"id": "m1", "role": "shelf"}]})
    config = StoreMindConfig(sensors={"enabled": True, "mems_nodes": [
        {"id": "m1", "role": "shelf", "shelf": "shelf-a", "slot": "A1"},
        {"id": "m2", "role": "camera_mount", "cam": "entrance"}]})
    assert config.sensors.has("mems")


def test_every_shipped_config_still_validates():
    for path in (ROOT / "storemind" / "configs").glob("*.yaml"):
        if path.name.startswith("secrets"):
            continue
        load_config(path, secrets=path.parent / "does-not-exist.yaml")


def test_secrets_example_has_placeholders_only_and_env_overrides(tmp_path, monkeypatch):
    example = ROOT / "storemind" / "configs" / "secrets.example.yaml"
    data = load_secrets(example)
    assert "entrance" in data["cameras"]
    monkeypatch.setenv("STOREMIND_MQTT_PASSWORD", "from-env")
    monkeypatch.setenv("STOREMIND_CAM_ENTRANCE_PASSWORD", "cam-env")
    data = load_secrets(example)
    assert data["mqtt"]["password"] == "from-env"
    assert data["cameras"]["entrance"]["password"] == "cam-env"


def test_mqtt_credentials_are_filled_from_secrets(tmp_path):
    (tmp_path / "store.yaml").write_text("mqtt:\n  enabled: false\n", encoding="utf-8")
    (tmp_path / "secrets.yaml").write_text("mqtt:\n  username: sm\n  password: pw\n",
                                           encoding="utf-8")
    config = load_config(tmp_path / "store.yaml")
    assert (config.mqtt.username, config.mqtt.password) == ("sm", "pw")


# --------------------------------------------------------------------------- #
# run_all platform hook (Person B's results)
# --------------------------------------------------------------------------- #

def test_platform_results_are_included_with_their_bucket(tmp_path):
    from storemind.eval.run_all import platform_results

    (tmp_path / "stream_density.json").write_text(json.dumps({
        "title": "Stream density - laptop", "bucket": "S", "device": "ASUS Vivobook 15",
        "rows": [{"metric": "cameras at 8 FPS", "value": "4", "target": ">= 3"}],
        "commands": ["python tools/stream_density.py"]}), encoding="utf-8")
    (tmp_path / "soak.md").write_text("<!-- bucket: B -->\n# 24 h soak\n\nRSS flat.\n",
                                      encoding="utf-8")
    (tmp_path / "no_bucket.md").write_text("# oops\n", encoding="utf-8")
    (tmp_path / "_README.md").write_text("skipped", encoding="utf-8")

    sections = {s.title: s for s in platform_results(tmp_path)}
    assert set(sections) == {"Stream density - laptop", "24 h soak", "Platform - no_bucket"}
    density = sections["Stream density - laptop"]
    assert density.bucket == "S" and density.rows == [("cameras at 8 FPS", "4", ">= 3")]
    assert "ASUS Vivobook 15" in density.markdown()
    assert sections["24 h soak"].bucket == "B" and "RSS flat." in sections["24 h soak"].note
    assert sections["Platform - no_bucket"].failed
