"""STM32 simulator (M5): every line it sends is valid protocol; scenarios behave."""

from __future__ import annotations

import time

import pytest

from storemind.core.bus import EventBus
from storemind.core.config import MemsNodeConfig, StoreMindConfig
from storemind.core.events import EventType
from storemind.fusion.interaction import ShelfInteractionEngine, SlotInfo
from storemind.core.clock import VideoClock
from storemind.sensors.bridge import SensorBridge, TcpTransport
from storemind.sensors.protocol import LineAssembler, encode, parse_line
from storemind.sensors.simulator import SCENARIOS, SimulatorServer, VirtualNode


def _lines(data: bytes) -> list:
    return [parse_line(line) for line in LineAssembler().feed(data)]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_only_sends_valid_lines(name):
    node = VirtualNode(seed=1)
    duration = node.scenario(name)
    messages = _lines(node.advance(duration + 11_000))
    assert messages, name
    assert all(m.direction == "up" for m in messages)
    seqs = [m.seq for m in messages]
    if name != "reboot":
        assert all((b - a) % 256 == 1 for a, b in zip(seqs, seqs[1:]))


def test_periodic_lines_follow_protocol_md_rates():
    node = VirtualNode()
    messages = _lines(node.advance(60_000))
    kinds = [m.type for m in messages]
    assert kinds.count("H") == 6          # every 10 s
    assert kinds.count("E") == 12         # every 5 s
    assert kinds.count("W") == 14         # 2 slots: at boot, then every 10 s


def test_pick_produces_touch_unstable_weights_then_settled():
    node = VirtualNode(seed=2)
    node.advance(1)
    node.scenario("pick", slot="1", units=2)
    messages = [m for m in _lines(node.advance(4000)) if m.type in ("M", "W")]
    events = [(m.type, m.values.get("event"), m.values.get("stable")) for m in messages]
    assert ("M", "TOUCH", None) in events and ("M", "SETTLED", None) in events
    assert ("W", None, False) in events
    stable = [m.values["grams"] for m in messages if m.type == "W" and m.values["stable"]]
    assert stable[0] - stable[-1] == 2 * 218


def test_simulated_pick_is_counted_by_vinith_fusion_engine():
    """The simulator feeds A's M6 engine through the bridge: one pick of 2 packs."""
    config = StoreMindConfig(store="s")
    config.sensors.enabled = True
    config.sensors.mems_nodes = [MemsNodeConfig(id="m1", role="shelf", shelf="shelf-a", slot="A1")]
    bus = EventBus()
    bridge = SensorBridge(config, bus, transport=_Null())
    engine = ShelfInteractionEngine([SlotInfo("shelf-a", "A1", "1", 218.0)], store="s", node="pi",
                                    mems_enabled=True, person_at_shelf=lambda shelf: True)
    clock = VideoClock()
    produced = []
    bus.subscribe_types([EventType.SHELF_MOTION, EventType.WEIGHT],
                        lambda e: produced.extend(engine.on_event(e, clock)))
    node = VirtualNode(seed=4)
    bridge.feed(node.advance(12_000))                      # stable baseline
    node.scenario("pick", slot="1", units=2)
    bridge.feed(node.advance(6_000))
    picks = [e for e in produced if e.type is EventType.PICKUP]
    assert len(picks) == 1
    assert picks[0].data["action"] == "pick" and picks[0].data["units"] == 2


def test_commands_and_disabled_sensors():
    node = VirtualNode(enabled=["hx711", "led"])
    node.receive(encode("L", 1, 0, ["SLOW"]).encode())
    node.receive(encode("Z", 2, 0, ["SLOW"]).encode())       # buzzer not fitted
    node.receive(encode("C", 3, 0, ["ENABLE", "buzzer", 1]).encode())
    node.receive(encode("Z", 4, 0, ["SLOW"]).encode())
    node.receive(encode("C", 5, 0, ["MODE", "BIN"]).encode())
    node.receive(b"$L,6,0,ON*00\r\n")                         # bad checksum
    node.receive(encode("C", 7, 0, ["ENABLE", "toaster", 1]).encode())
    acks = [(m.values["cmd_seq"], m.values["status"], m.values["code"])
            for m in _lines(node.advance(1)) if m.type == "K"]
    assert acks == [(1, "OK", 0), (2, "ERR", 4), (3, "OK", 0), (4, "OK", 0),
                    (5, "ERR", 3), (6, "ERR", 1), (7, "ERR", 3)]
    assert node.led == "SLOW" and node.buzzer == "SLOW"
    # No BME280/BH1750 fitted -> no $E at all; TARE zeroes the reading.
    node.receive(encode("C", 8, 0, ["TARE", "1"]).encode())
    later = _lines(node.advance(20_000))
    assert not any(m.type == "E" for m in later)
    assert [m.values["grams"] for m in later if m.type == "W" and m.values["slot"] == "1"][-1] == 0


def test_i2c_fault_blanks_the_environment_and_counts_errors():
    node = VirtualNode()
    node.scenario("i2c_fault", seconds=12)
    messages = _lines(node.advance(20_000))
    env = [m.values for m in messages if m.type == "E"]
    assert env[0]["lux"] is None and env[0]["temp_c"] is None
    health = [m.values for m in messages if m.type == "H"]
    assert health[-1]["i2c_err"] >= 2


def test_tcp_simulator_to_bridge_end_to_end():
    node = VirtualNode(seed=7)
    server = SimulatorServer(node, port=0, speed=50.0, scenario="demo").start()
    config = StoreMindConfig(store="s")
    config.sensors.enabled = True
    bus = EventBus()
    seen = []
    bus.subscribe_all(seen.append)
    bridge = SensorBridge(config, bus, TcpTransport(f"127.0.0.1:{server.port}")).start()
    try:
        deadline = time.monotonic() + 10
        wanted = {EventType.NODE_HEALTH, EventType.WEIGHT, EventType.SHELF_MOTION, EventType.BEAM_CROSS}
        while time.monotonic() < deadline and not wanted <= {e.type for e in seen}:
            time.sleep(0.05)
        assert wanted <= {e.type for e in seen}
        result = bridge.send_command("L", ["ON"])
        assert result.status == "OK" and node.led == "ON"
        assert bridge.stats.crc_err == 0 and bridge.stats.rx_err == 0
    finally:
        bridge.stop()
        server.stop()


class _Null:
    name = "null"

    def open(self): ...
    def read(self, size=256): return b""
    def write(self, data): ...
    def close(self): ...
