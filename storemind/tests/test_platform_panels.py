"""M7a: dashboard platform panels, tower LED / buzzer policy, Pi power parsing."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from storemind.alerts.manager import AlertManager, TowerLightSink
from storemind.alerts.tower import TowerPolicy, patterns_for
from storemind.api.server import create_app
from storemind.core.clock import VideoClock
from storemind.core.config import StoreMindConfig
from storemind.core.events import (
    CameraHealthData,
    EventType,
    NodeHealthData,
    Severity,
    WeightData,
    make_event,
)
from storemind.health.monitor import parse_pmic_watts, parse_throttled_now
from storemind.pipeline import Pipeline


class FakeNode:
    def __init__(self) -> None:
        self.led: list[str] = []
        self.buzzer: list[str] = []
        self.submitted: list[tuple] = []

    def set_led(self, pattern: str) -> bool:
        self.led.append(pattern)
        return True

    def set_buzzer(self, pattern: str) -> bool:
        self.buzzer.append(pattern)
        return True

    def submit(self, msg_type, fields) -> bool:
        self.submitted.append((msg_type, fields))
        return True

    def snapshot(self) -> dict:
        return {"node": "stm32-01", "link": "up"}


@pytest.mark.parametrize("key, led, buzzer", [
    ("QUEUE_OVERFLOW:counter-1", "FAST", "FAST"),
    ("SHELF_TILT:shelf-a", "ALERT", "ALERT"),
    ("FALLEN_STOCK:shelf-a/A1", "FAST", "FAST"),
    ("CAMERA_MOVED:entrance", "ALERT", "ALERT"),
    ("CAMERA_BUMP:entrance", "SLOW", None),
    ("CAMERA_TILT:entrance", "FAST", None),
    ("AFTER_HOURS:aisle1", "ALERT", "ALERT"),
    ("SENSOR_LINK:stm32-01", "SLOW", None),
])
def test_every_new_alert_has_a_tower_pattern(key, led, buzzer):
    assert patterns_for(key, "WARN") == (led, buzzer)


def test_unknown_keys_fall_back_to_severity():
    assert patterns_for("SOMETHING_NEW:x", "CRITICAL") == ("ALERT", "ALERT")
    assert patterns_for("SOMETHING_NEW:x", "INFO") == ("ON", None)


def test_led_shows_the_worst_open_alert_and_goes_off_when_all_are_acked():
    node = FakeNode()
    policy = TowerPolicy(node)
    policy.raised("a", "SLOT_LOW:shelf-a:A1", "WARN")
    policy.raised("b", "AFTER_HOURS:aisle1", "CRITICAL")
    assert policy.led == "ALERT" and node.buzzer == ["ALERT"]
    policy.acknowledged("b")
    assert policy.led == "SLOW"
    policy.acknowledged("a")
    assert policy.led == "OFF" and node.led == ["SLOW", "ALERT", "SLOW", "OFF"]


def test_alert_manager_ack_reaches_the_tower_light():
    node = FakeNode()
    manager = AlertManager(store="s", node="pi")
    manager.add_sink(TowerLightSink(node))
    clock = VideoClock()
    [event] = manager.raise_alert("QUEUE_OVERFLOW:counter-1", "queue full", Severity.WARN, clock)
    assert node.led[-1] == "FAST" and node.buzzer == ["FAST"]
    manager.acknowledge(event.data["alert_id"])
    assert node.led[-1] == "OFF"


def test_bridge_service_follows_alert_events_including_acks():
    node = FakeNode()
    policy = TowerPolicy(node)
    ts = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    alert = {"severity": "CRITICAL", "message_key": "SHELF_TILT:shelf-a", "message": "tilt",
             "alert_id": "SHELF_TILT:shelf-a@1"}
    policy.on_alert_event(make_event(ts=ts, store="s", node="pi", type=EventType.ALERT, data=alert))
    assert policy.led == "ALERT"
    policy.on_alert_event(make_event(ts=ts, store="s", node="pi", type=EventType.ALERT,
                                     data={**alert, "ack": True}))
    assert policy.led == "OFF"


def test_pmic_and_throttle_parsing():
    text = """   3V7_WL_SW_A current(0)=0.10000000A
   3V7_WL_SW_V volt(8)=3.70000000V
   VDD_CORE_A current(7)=1.50000000A
   VDD_CORE_V volt(15)=0.80000000V"""
    # (0.37 + 1.2) W from the PMIC, then real = 1.1451 x PMIC + 0.5879
    assert parse_pmic_watts(text) == pytest.approx(2.39, abs=0.01)
    assert parse_pmic_watts("") is None
    assert parse_throttled_now("throttled=0x50005") is True
    assert parse_throttled_now("throttled=0x50000") is False      # happened earlier, not now
    assert parse_throttled_now("") is None


@pytest.fixture()
def app_and_pipeline(tmp_path):
    config = StoreMindConfig(store="bvrit-demo")
    config.storage.db_path = str(tmp_path / "events.db")
    config.detector.backend = "stub"
    config.sensors.enabled = True
    pipeline = Pipeline(config, replay=True)
    app = create_app(pipeline)
    yield app, pipeline
    pipeline.close()


def test_platform_panels_show_node_camera_and_sensor_state(app_and_pipeline):
    app, pipeline = app_and_pipeline
    ts = datetime.now(timezone.utc)
    bus = pipeline.bus
    bus.publish(make_event(ts=ts, store="bvrit-demo", node="stm32-01", type=EventType.NODE_HEALTH,
                           data=NodeHealthData(node="stm32-01", uptime_s=720, free_heap=6144,
                                               min_stack_words=38, crc_err=2, reset_cause="POR")))
    bus.publish(make_event(ts=ts, store="bvrit-demo", node="stm32-01", type=EventType.WEIGHT,
                           data=WeightData(node="stm32-01", slot="1", grams=1622, stable=True)))
    bus.publish(make_event(ts=ts, store="bvrit-demo", node="pi5-01", cam="entrance",
                           type=EventType.CAMERA_HEALTH,
                           data=CameraHealthData(cam="entrance", state="reconnecting", fps=0.0, reconnects=3)))
    client = TestClient(app)
    platform = client.get("/api/platform").json()
    node = platform["sensors"]["nodes"][0]
    assert node["node"] == "stm32-01" and node["colour"] == "green" and node["crc_err"] == 2
    assert platform["sensors"]["latest"]["WEIGHT"][0]["grams"] == 1622
    cam = platform["camera_health"][0]
    assert cam["cam"] == "entrance" and cam["colour"] == "amber" and cam["reconnects"] == 3
    assert platform["privacy"]["video_bytes_stored"] == 0 and platform["privacy"]["face_recognition"] is False
    assert "power_w" in platform["hardware"] and "reorder" in platform
    state = client.get("/api/state").json()
    assert state["platform"]["sensors"]["enabled"] is True


def test_external_alerts_and_reorder_list(app_and_pipeline):
    app, pipeline = app_and_pipeline
    ts = datetime.now(timezone.utc)
    pipeline.bus.publish(make_event(ts=ts, store="bvrit-demo", node="stm32-01", type=EventType.ALERT, data={
        "severity": "WARN", "message_key": "SENSOR_LINK:stm32-01", "message": "node silent",
        "alert_id": "SENSOR_LINK:stm32-01@1"}))
    pipeline.bus.publish(make_event(ts=ts, store="bvrit-demo", node="pi5-01", type=EventType.SLOT_STATE, data={
        "shelf": "shelf-a", "slot": "A1", "sku": "Parle-G", "state": "EMPTY"}))
    client = TestClient(app)
    platform = client.get("/api/platform").json()
    assert platform["external_alerts"][0]["message_key"] == "SENSOR_LINK:stm32-01"
    reorder = client.get("/api/reorder").json()
    assert reorder["open"][0]["sku"] == "Parle-G" and "Parle-G: OUT" in reorder["whatsapp"]


def test_node_test_buttons_need_a_bridge(app_and_pipeline):
    app, pipeline = app_and_pipeline
    client = TestClient(app)
    assert client.post("/api/node/LED?pattern=ALERT").status_code == 409
    pipeline.sensor_bridge = FakeNode()
    response = client.post("/api/node/LED?pattern=ALERT")
    assert response.status_code == 200 and pipeline.sensor_bridge.submitted == [("L", ["ALERT"])]
    assert client.post("/api/node/TOASTER").status_code == 400
