"""Serial bridge (M5): lines -> validated events, counters, commands, reconnects."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from storemind.core.bus import EventBus
from storemind.core.config import MemsNodeConfig, StoreMindConfig
from storemind.core.events import EventType
from storemind.health.systemd import Notifier
from storemind.sensors.bridge import (
    ClockMapper,
    MqttCommandListener,
    SensorBridge,
    command_line_fields,
)
from storemind.sensors.protocol import encode, parse_line
from storemind.sensors.simulator import LoopbackTransport, Noise, VirtualNode

DOC = Path(__file__).resolve().parents[2] / "docs" / "PROTOCOL.md"


def _config(**sensors) -> StoreMindConfig:
    config = StoreMindConfig(store="bvrit-demo")
    config.sensors.enabled = True
    config.sensors.mems_nodes = [MemsNodeConfig(id="m1", role="shelf", shelf="shelf-a", slot="A1"),
                                 MemsNodeConfig(id="m2", role="camera_mount", cam="entrance")]
    for key, value in sensors.items():
        setattr(config.sensors, key, value)
    return config


class _NullTransport:
    name = "null"

    def __init__(self) -> None:
        self.written: list[bytes] = []

    def open(self) -> None: ...
    def read(self, size: int = 256) -> bytes:
        time.sleep(0.01)
        return b""

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None: ...


def _bridge(config=None, transport=None):
    bus = EventBus()
    seen = []
    bus.subscribe_all(seen.append)
    bridge = SensorBridge(config or _config(), bus, transport or _NullTransport())
    return bridge, seen


def _uplink_examples() -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    lines = []
    for block in re.findall(r"```text\n(.*?)```", text, flags=re.DOTALL):
        lines += [ln.strip() for ln in block.splitlines() if ln.strip()]
    return [ln for ln in lines if parse_line(ln).direction == "up"]


def test_every_uplink_example_in_protocol_md_becomes_a_valid_event_or_nothing():
    bridge, seen = _bridge()
    for line in _uplink_examples():
        bridge.feed((line + "\r\n").encode())
    types = [e.type for e in seen]
    # $B is debug-only, $K resolves commands, $Q only with ld2450 enabled.
    assert EventType.WEIGHT in types and EventType.SHELF_MOTION in types
    assert EventType.CAMERA_MOUNT in types and EventType.BEAM_CROSS in types
    assert EventType.PRESENCE in types and EventType.ENVIRONMENT in types
    assert EventType.NODE_HEALTH in types and EventType.SENSOR in types
    assert bridge.stats.crc_err == 0 and bridge.stats.rx_err == 0


def test_mapping_matches_interfaces_md_examples():
    bridge, seen = _bridge()
    bridge.feed(b"$M,18,523610,m1,S,TOUCH,412,138,640,*1E\r\n")
    bridge.feed(b"$M,22,601400,m2,C,KNOCK,1850,420,40,*02\r\n")
    bridge.feed(b"$D,24,610140,door1,IN*60\r\n")
    bridge.feed(b"$E,26,612000,420,284,615,10093*45\r\n")
    bridge.feed(b"$R,28,700000,shelf-a*4B\r\n")
    motion, mount, beam, env, restock = seen
    assert motion.type is EventType.SHELF_MOTION and motion.node == "stm32-01"
    assert motion.data == {"node": "m1", "shelf": "shelf-a", "slot": "A1", "kind": "TOUCH",
                           "peak_mg": 412.0, "rms_mg": 138.0, "dur_ms": 640}
    assert mount.cam == "entrance" and mount.data["cam"] == "entrance" and mount.data["tilt_deg"] is None
    assert beam.data == {"node": "stm32-01", "door": "door1", "direction": "in", "t_ms_mcu": 610140}
    assert env.data["temp_c"] == pytest.approx(28.4) and env.data["pressure_hpa"] == pytest.approx(1009.3)
    # INTERFACES.md M3: the restock button is SENSOR sensor=restock channel=<shelf>
    assert restock.type is EventType.SENSOR
    assert restock.data["sensor"] == "restock" and restock.data["channel"] == "shelf-a"


def test_camera_mount_touch_and_settled_are_dropped_and_unknown_mems_nodes_still_publish():
    bridge, seen = _bridge()
    bridge.feed(encode("M", 1, 10, ["m2", "C", "TOUCH", 300, 90, 100, None]).encode())
    assert seen == []
    bridge.feed(encode("M", 2, 20, ["m9", "S", "KNOCK", 1500, 300, 50, None]).encode())
    assert seen[-1].data["shelf"] == "m9" and seen[-1].data["slot"] is None


def test_counters_for_checksum_framing_and_seq_gaps():
    bridge, seen = _bridge()
    bridge.feed(b"$W,40,800000,A1,1840,1*3C\r\n")         # checksum
    bridge.feed(b"$W,40,800000,A1,1840,1*3b\r\n")         # framing
    bridge.feed(b"$X,45,800500,1*49\r\n")                 # unknown type
    bridge.feed(b"$L,6,3600250,ALERT*2A\r\n")             # downlink line on the uplink
    bridge.feed(encode("P", 10, 1, ["z", 1]).encode())
    bridge.feed(encode("P", 14, 2, ["z", 0]).encode())     # 11, 12, 13 lost
    assert bridge.stats.crc_err == 1
    assert bridge.stats.rx_err == 3
    assert bridge.stats.lost == 3
    bridge.feed(encode("H", 15, 3, [5, 6144, 38, 2, 1, "POR"]).encode())
    health = seen[-1]
    assert health.type is EventType.NODE_HEALTH
    # uart_err = MCU's 1 + Pi-side 3 bad lines + 3 lost; crc_err is the Pi's count.
    assert health.data["uart_err"] == 7 and health.data["crc_err"] == 1
    assert health.data["i2c_err"] == 2 and health.data["link"] == "up"


def test_link_down_after_silence_publishes_health_and_one_alert():
    now = [100.0]
    bus = EventBus()
    seen = []
    bus.subscribe_all(seen.append)
    bridge = SensorBridge(_config(), bus, _NullTransport(), link_timeout_s=30, monotonic=lambda: now[0])
    bridge.feed(encode("H", 1, 1000, [1, 6144, 38, 0, 0, "POR"]).encode())
    now[0] += 29
    assert bridge.check_link() == []
    now[0] += 2
    events = bridge.check_link()
    assert [e.type for e in events] == [EventType.NODE_HEALTH, EventType.ALERT]
    assert events[0].data["link"] == "down"
    assert events[1].data["message_key"] == "SENSOR_LINK:stm32-01"
    now[0] += 60
    assert bridge.check_link() == []                       # alerted once
    bridge.feed(encode("H", 2, 2000, [2, 6144, 38, 0, 0, "POR"]).encode())
    assert bridge.link == "up" and seen[-1].data["link"] == "up"


def test_clock_mapper_uses_the_smallest_latency_and_resets_on_reboot():
    mapper = ClockMapper(drift_ppm=0)
    assert mapper.to_wall_ms(1000, 50_000) == 50_000          # first sample
    assert mapper.to_wall_ms(2000, 51_080) == 51_000          # 80 ms late: not believed
    assert mapper.to_wall_ms(3000, 51_990) == 51_990          # 10 ms faster: better offset
    assert mapper.to_wall_ms(10, 60_000) == 60_000            # reboot: ms went backwards


def test_commands_get_acked_by_the_simulated_mcu():
    node = VirtualNode()
    transport = LoopbackTransport(node, step_ms=20)
    bridge, _ = _bridge(transport=transport)
    bridge.start()
    try:
        assert _wait(lambda: bridge.connected)
        led = bridge.send_command("L", ["ALERT"])
        assert (led.status, led.code) == ("OK", 0) and node.led == "ALERT"
        assert bridge.patterns["LED"] == "ALERT"
        servo = bridge.send_command("V", [90])
        assert (servo.status, servo.code) == ("ERR", 4)       # servo not fitted by default
        tare = bridge.send_command("C", ["TARE", "nope"])
        assert (tare.status, tare.code) == ("ERR", 3)
        bad = bridge.send_command("V", [270])                 # refused before sending
        assert (bad.status, bad.code, bad.attempts) == ("ERR", 3, 0)
        assert _wait(lambda: node.epoch_ms is not None)       # $S sent at connect
    finally:
        bridge.stop()


def test_command_retries_then_times_out_when_nobody_answers():
    config = _config()
    config.sensors.node.cmd_timeout_ms = 30
    transport = _NullTransport()
    bridge, _ = _bridge(config, transport)
    result = bridge.send_command("L", ["ON"])
    assert result.status == "TIMEOUT" and result.attempts == 4
    assert len(transport.written) == 4 and len(set(transport.written)) == 1   # same seq each time
    assert bridge.stats.cmd_timeouts == 1 and bridge.stats.cmd_retries == 3


def test_reconnects_after_the_port_disappears():
    node = VirtualNode()
    transport = LoopbackTransport(node, step_ms=500)
    transport.fail_opens = 1
    bridge, seen = _bridge(transport=transport)
    bridge.start()
    try:
        assert _wait(lambda: bridge.connected, 5)
        assert _wait(lambda: any(e.type is EventType.NODE_HEALTH for e in seen), 5)
        transport.fail_next_read = True
        assert _wait(lambda: bridge.stats.reconnects >= 1, 5)
        count = len(seen)
        assert _wait(lambda: len(seen) > count, 5)             # events flow again
    finally:
        bridge.stop()


def test_tower_light_api_is_non_blocking_and_maps_colours():
    bridge, _ = _bridge()
    assert bridge.set_light("R") and bridge.buzz(2)
    queued = [bridge._commands.get_nowait() for _ in range(2)]
    assert queued == [("L", ["ALERT"]), ("Z", ["ALERT"])]


@pytest.mark.parametrize("command, payload, expected", [
    ("LED", {"pattern": "slow"}, ("L", ["SLOW"])),
    ("BUZZER", {"pattern": "OFF"}, ("Z", ["OFF"])),
    ("SERVO", {"angle": 45}, ("V", [45])),
    ("TARE", {"slot": "A1"}, ("C", ["TARE", "A1"])),
    ("CAL", {"slot": "A1", "grams": 500}, ("C", ["CAL", "A1", 500])),
    ("CONFIG", {"key": "MEMS_THR", "args": ["m1", 120]}, ("C", ["MEMS_THR", "m1", 120])),
])
def test_mqtt_command_payloads_follow_interfaces_md(command, payload, expected):
    assert command_line_fields(command, payload) == expected


def test_mqtt_listener_acks_every_command():
    class FakeClient:
        def __init__(self):
            self.published = []

        def username_pw_set(self, *a): ...
        def connect(self, *a, **k): ...
        def loop_start(self): ...
        def loop_stop(self): ...
        def disconnect(self): ...
        def subscribe(self, *a, **k): ...

        def publish(self, topic, payload, qos=0, retain=False):
            self.published.append((topic, payload))

    node = VirtualNode()
    bridge, _ = _bridge(transport=LoopbackTransport(node, step_ms=20))
    bridge.start()
    client = FakeClient()
    try:
        assert _wait(lambda: bridge.connected)
        listener = MqttCommandListener(bridge, "localhost", 1883, client=client)
        listener.handle("LED", {"pattern": "FAST"})
        listener.handle("SERVO", {})                           # missing angle
        topic, payload = client.published[0]
        assert topic == "storemind/bvrit-demo/stm32-01/ack" and '"status": "OK"' in payload
        assert '"status": "ERR"' in client.published[1][1] and '"code": 3' in client.published[1][1]
        assert node.led == "FAST"
    finally:
        bridge.stop()


def test_systemd_notifier_is_a_no_op_off_systemd():
    notifier = Notifier(env={})
    assert not notifier.enabled
    assert notifier.ready() is False and notifier.watchdog() is False
    timed = Notifier(env={"WATCHDOG_USEC": "20000000"})
    assert timed.interval_s == 10.0


def test_one_simulated_hour_over_a_clean_link_has_zero_framing_errors():
    """M5 acceptance (bucket C): 1 h simulated run with 0 framing errors."""
    node = VirtualNode(seed=3)
    bridge, seen = _bridge()
    t = 0
    while t < 3600_000:
        if t % 60_000 == 0:
            node.scenario(["pick", "rush", "lean", "camera_knock", "put_back", "restock"][(t // 60_000) % 6])
        bridge.feed(node.advance(250))
        t += 250
    assert bridge.stats.crc_err == 0 and bridge.stats.rx_err == 0 and bridge.stats.lost == 0
    assert bridge.stats.lines == node.counters.lines
    assert sum(e.type is EventType.NODE_HEALTH for e in seen) == 360


def test_noisy_link_is_counted_not_crashed():
    node = VirtualNode(seed=5, noise=Noise(bad_checksum=0.02, drop=0.02, garbage=0.02))
    bridge, seen = _bridge()
    for _ in range(4 * 600):                                  # 10 simulated minutes
        bridge.feed(node.advance(250))
    assert bridge.stats.crc_err == node.counters.corrupted
    # A dropped line is only seen as a gap once the next line arrives.
    assert node.counters.dropped - 1 <= bridge.stats.lost <= node.counters.dropped
    assert seen


def _wait(predicate, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
