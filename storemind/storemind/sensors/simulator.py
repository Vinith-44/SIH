"""STM32 sensor-node simulator (M5, Person B).

A virtual node that speaks exactly the serial protocol of docs/PROTOCOL.md, so
the bridge, the fusion engine and the dashboard can be built and tested before
the real board is wired (research/26 section 4.1: "simulators both ways").

    python -m storemind.sensors.simulator --port 7777 --scenario demo
    python -m storemind.sensors.bridge --config configs/sensors_demo.yaml --tcp 127.0.0.1:7777 --print

What it models (the same assumptions as docs/MEMS.md section 5, so Vinith's fusion
evaluation and this simulator agree):

*   periodic lines: `$H` every 10 s, `$E` every 5 s, `$W` every 10 s per slot;
*   scripted scenarios (`SCENARIOS`): pick, put-back, lean on the shelf, trolley
    knock (sometimes a pack falls), shelf tilt, camera knock / tilt, entrance
    rush (IR beams + PIR), empty shelf, restock button, lights off, after-hours
    intrusion, I2C fault, watchdog reboot;
*   every command gets a `$K` with the right code (PROTOCOL.md section 3):
    disabled sensors answer 4, bad arguments 3, unknown commands 2, bad
    checksums 1 (with the seq, so the Pi can retry);
*   optional line noise (`Noise`): corrupted checksums, dropped lines (seq gaps),
    garbage bytes and torn writes, to exercise the bridge's error counters.

Nothing here is a measurement of hardware: results from it are bucket C.
"""

from __future__ import annotations

import argparse
import logging
import random
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core.config import DEFAULT_SENSORS
from .protocol import CONFIG_KEYS, LineAssembler, ProtocolError, encode, parse_frame, decode

log = logging.getLogger(__name__)

H_PERIOD_MS = 10_000
E_PERIOD_MS = 5_000
W_PERIOD_MS = 10_000
W_CHANGE_G = 5                       # PROTOCOL.md: $W on change > 5 g

ALL_SENSORS = ("hx711", "mems", "ir_beam", "pir", "bh1750", "bme280", "buzzer", "led",
               "servo", "restock_button", "ld2450")


@dataclass
class Noise:
    """Line-level faults, as probabilities per line (0 = a perfect cable)."""

    bad_checksum: float = 0.0
    drop: float = 0.0
    garbage: float = 0.0

    @property
    def any(self) -> bool:
        return self.bad_checksum > 0 or self.drop > 0 or self.garbage > 0


@dataclass
class Slot:
    grams: float
    full_grams: float
    unit_grams: float
    tare: float = 0.0
    stable: bool = True
    last_sent: float | None = None
    last_sent_ms: int = -W_PERIOD_MS


@dataclass
class _Action:
    at_ms: int
    run: Callable[[], None]
    order: int = 0


@dataclass
class NodeCounters:
    lines: int = 0
    corrupted: int = 0
    dropped: int = 0
    garbage: int = 0
    commands: int = 0


class VirtualNode:
    """The MCU model.  Transport-agnostic: `advance()` returns bytes to send,
    `receive()` takes bytes from the Pi."""

    def __init__(self, *, node_id: str = "stm32-01", enabled: list[str] | None = None,
                 slots: dict[str, tuple[float, float]] | None = None,
                 shelf_mems: str = "m1", camera_mems: str = "m2", shelf: str = "shelf-a",
                 door: str = "door1", zone: str = "aisle1", seed: int = 0,
                 noise: Noise | None = None) -> None:
        self.node_id = node_id
        self.enabled = set(DEFAULT_SENSORS if enabled is None else enabled)
        # slot id -> (full grams, unit grams)
        slots = slots or {"1": (1840.0, 218.0), "2": (2400.0, 400.0)}
        self.slots = {k: Slot(full, full, unit) for k, (full, unit) in slots.items()}
        self.shelf_mems, self.camera_mems = shelf_mems, camera_mems
        self.shelf, self.door, self.zone = shelf, door, zone
        self.rng = random.Random(seed)
        self.noise = noise or Noise()

        self.ms = 0
        self.seq = 0
        self.reset_cause = "POR"
        self.boot_ms = 0
        self.i2c_err = 0
        self.uart_err = 0
        self.lux = 420
        self.i2c_fault_until = -1
        self.epoch_ms: int | None = None
        self.led = "OFF"
        self.buzzer = "OFF"
        self.servo = 90
        self.mems_thr = {shelf_mems: 120, camera_mems: 600}
        self.mode = "TXT"
        self.counters = NodeCounters()

        self._next_h = H_PERIOD_MS
        self._next_e = 1_000
        self._actions: list[_Action] = []
        self._order = 0
        self._out: list[bytes] = []
        self._asm = LineAssembler()

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def emit(self, msg_type: str, fields: list) -> None:
        line = encode(msg_type, self.seq, self.ms, fields)
        self.seq = (self.seq + 1) % 256
        self.counters.lines += 1
        if self.noise.any:
            roll = self.rng.random()
            if roll < self.noise.drop:
                self.counters.dropped += 1
                return
            if roll < self.noise.drop + self.noise.bad_checksum:
                self.counters.corrupted += 1
                star = line.index("*")
                good = int(line[star + 1:star + 3], 16)
                line = f"{line[:star + 1]}{good ^ 0x5A:02X}\r\n"
            if self.rng.random() < self.noise.garbage:
                self.counters.garbage += 1
                self._out.append(bytes(self.rng.randrange(1, 255) for _ in range(5)).replace(b"$", b"#"))
        self._out.append(line.encode("ascii"))

    def has(self, sensor: str) -> bool:
        return sensor in self.enabled

    def _i2c_ok(self) -> bool:
        return self.ms >= self.i2c_fault_until

    # ------------------------------------------------------------------ #
    # Time
    # ------------------------------------------------------------------ #
    def at(self, delay_ms: int, run: Callable[[], None]) -> None:
        self._order += 1
        self._actions.append(_Action(self.ms + int(delay_ms), run, self._order))

    def advance(self, dt_ms: int) -> bytes:
        """Run `dt_ms` of MCU time; return everything sent meanwhile."""
        end = self.ms + int(dt_ms)
        while True:
            due = [a for a in self._actions if a.at_ms <= end]
            next_tick = min(self._next_h, self._next_e, self._next_w_due())
            next_action = min((a.at_ms for a in due), default=end + 1)
            step_to = min(next_tick, next_action)
            if step_to > end:
                break
            self.ms = max(self.ms, step_to)
            for action in sorted((a for a in due if a.at_ms <= self.ms), key=lambda a: (a.at_ms, a.order)):
                self._actions.remove(action)
                action.run()
            self._periodic()
        self.ms = end
        out = b"".join(self._out)
        self._out.clear()
        return out

    def _next_w_due(self) -> int:
        if not self.has("hx711") or not self.slots:
            return 1 << 62
        return min(s.last_sent_ms + W_PERIOD_MS for s in self.slots.values())

    def _periodic(self) -> None:
        if self.ms >= self._next_h:
            self._next_h += H_PERIOD_MS
            self.send_health()
        if self.ms >= self._next_e:
            self._next_e += E_PERIOD_MS
            self.send_env()
        if self.has("hx711"):
            for slot_id, slot in self.slots.items():
                if self.ms >= slot.last_sent_ms + W_PERIOD_MS:
                    self.send_weight(slot_id)

    def send_health(self) -> None:
        uptime_s = (self.ms - self.boot_ms) // 1000
        self.emit("H", [uptime_s, 6144 - self.rng.randrange(0, 64), 38, self.i2c_err,
                        self.uart_err, self.reset_cause])

    def send_env(self) -> None:
        light, env = self.has("bh1750"), self.has("bme280")
        if not (light or env):
            return
        ok = self._i2c_ok()
        if not ok:
            self.i2c_err += 1
        lux = int(max(0, self.lux + self.rng.randint(-8, 8))) if light and ok else None
        if env and ok:
            temp, rh, hpa = 284 + self.rng.randint(-3, 3), 615 + self.rng.randint(-10, 10), 10093
        else:
            temp = rh = hpa = None
        self.emit("E", [lux, temp, rh, hpa])

    def send_weight(self, slot_id: str) -> None:
        if not self.has("hx711"):
            return
        slot = self.slots[slot_id]
        reported = round(slot.grams - slot.tare)
        slot.last_sent, slot.last_sent_ms = reported, self.ms
        self.emit("W", [slot_id, reported, 1 if slot.stable else 0])

    def set_weight(self, slot_id: str, grams: float, stable: bool) -> None:
        """Change a load cell reading; sends `$W` if it moved > 5 g or the flag flipped."""
        slot = self.slots[slot_id]
        changed = slot.stable != stable or slot.last_sent is None or \
            abs((grams - slot.tare) - slot.last_sent) > W_CHANGE_G
        slot.grams, slot.stable = max(0.0, grams), stable
        if changed:
            self.send_weight(slot_id)

    def mems(self, node: str, role: str, event: str, peak: int, rms: int, dur: int,
             tilt_ddeg: int | None = None) -> None:
        if self.has("mems"):
            self.emit("M", [node, role, event, peak, rms, dur, tilt_ddeg])

    # ------------------------------------------------------------------ #
    # Receiving commands
    # ------------------------------------------------------------------ #
    def receive(self, data: bytes) -> None:
        for line in self._asm.feed(data):
            self.handle_command(line)

    def _ack(self, cmd_seq: int, code: int) -> None:
        self.emit("K", [cmd_seq, "OK" if code == 0 else "ERR", code])

    def handle_command(self, line: str) -> None:
        self.counters.commands += 1
        try:
            frame = parse_frame(line)
        except ProtocolError as error:
            self.uart_err += 1
            if error.reason == "checksum":
                parts = line.split(",")
                if len(parts) > 1 and parts[1].isdigit():
                    self._ack(int(parts[1]) % 256, 1)
            return
        try:
            message = decode(frame)
        except ProtocolError as error:
            self._ack(frame.seq, 2 if error.reason == "unknown_type" else 3)
            return
        if message.direction != "down":
            self._ack(frame.seq, 2)
            return
        self._ack(frame.seq, self._apply(message.type, message.values))

    def _apply(self, msg_type: str, v: dict) -> int:
        if msg_type == "S":
            self.epoch_ms = v["epoch_ms"]
            return 0
        if msg_type == "L":
            if not self.has("led"):
                return 4
            self.led = v["pattern"]
            return 0
        if msg_type == "Z":
            if not self.has("buzzer"):
                return 4
            self.buzzer = v["pattern"]
            return 0
        if msg_type == "V":
            if not self.has("servo"):
                return 4
            self.servo = v["angle"]
            return 0
        key = v["key"]
        if key in ("TARE", "CAL"):
            if not self.has("hx711"):
                return 4
            slot = self.slots.get(v["slot"])
            if slot is None:
                return 3
            if key == "TARE":
                slot.tare = slot.grams
            elif v["grams"] <= 0:
                return 3
            return 0
        if key == "MEMS_THR":
            if v["node"] not in self.mems_thr or v["mg"] <= 0:
                return 3
            self.mems_thr[v["node"]] = v["mg"]
            return 0
        if key == "MODE":
            # Binary mode is not final (PROTOCOL.md section 6): refuse, stay in text.
            return 0 if v["mode"] == "TXT" else 3
        if key == "ENABLE":
            if v["sensor"] not in ALL_SENSORS:
                return 3
            (self.enabled.add if v["on"] else self.enabled.discard)(v["sensor"])
            return 0
        return 2 if key not in CONFIG_KEYS else 3

    # ------------------------------------------------------------------ #
    # Scenarios (each schedules actions relative to now)
    # ------------------------------------------------------------------ #
    def scenario(self, name: str, **kwargs) -> int:
        """Schedule a named scenario; returns its duration in ms."""
        return SCENARIOS[name](self, **kwargs)


def _handling(node: VirtualNode, slot_id: str, delta_units: int, *, touch_peak: int = 412,
              settle_ms: int = 1400) -> int:
    """TOUCH -> unstable readings -> (new stable weight) -> SETTLED (docs/MEMS.md section 4)."""
    slot = node.slots[slot_id]
    rng = node.rng
    state: dict[str, float] = {}

    def start() -> None:
        # Read the weight when the episode starts, not when it was scheduled,
        # so back-to-back scenarios (empty_shelf) see each other's result.
        units = delta_units
        if units < 0:
            units = -min(-units, int(slot.grams // slot.unit_grams))
        state["before"] = slot.grams
        state["after"] = slot.grams + units * slot.unit_grams
        node.send_weight(slot_id)

    node.at(0, start)
    node.at(100, lambda: node.mems(node.shelf_mems, "S", "TOUCH", touch_peak + rng.randint(-60, 60),
                                   130 + rng.randint(-20, 20), 600 + rng.randint(-100, 200)))
    for i, t in enumerate((300, 600, 900)):
        swing = rng.randint(50, 400) * (-1 if i % 2 else 1)
        node.at(t, lambda s=swing: node.set_weight(slot_id, state["before"] + s, False))
    node.at(1200, lambda: node.set_weight(slot_id, state["after"], True))    # often before SETTLED
    node.at(1200 + settle_ms, lambda: node.mems(node.shelf_mems, "S", "SETTLED", 35, 12,
                                                1200 + settle_ms - 100))
    return 1300 + settle_ms


def pick(node: VirtualNode, slot: str = "1", units: int = 1) -> int:
    return _handling(node, slot, -units)


def put_back(node: VirtualNode, slot: str = "1", units: int = 1) -> int:
    return _handling(node, slot, units)


def lean(node: VirtualNode, slot: str = "1") -> int:
    """Touch, handle, put it down: no stock change."""
    return _handling(node, slot, 0, touch_peak=250)


def trolley_knock(node: VirtualNode, slot: str = "1", falls: bool = False) -> int:
    node.at(0, lambda: node.mems(node.shelf_mems, "S", "KNOCK", 1500, 380, 60))
    if falls:
        s = node.slots[slot]
        node.at(400, lambda: node.set_weight(slot, max(0.0, s.grams - s.unit_grams), True))
    return 1000


def shelf_tilt(node: VirtualNode, tilt_ddeg: int = 62) -> int:
    node.at(0, lambda: node.mems(node.shelf_mems, "S", "TILT", 90, 40, 2500, tilt_ddeg))
    return 3000


def camera_knock(node: VirtualNode) -> int:
    node.at(0, lambda: node.mems(node.camera_mems, "C", "KNOCK", 1850, 420, 40))
    return 500


def camera_tilt(node: VirtualNode, tilt_ddeg: int = 35) -> int:
    node.at(0, lambda: node.mems(node.camera_mems, "C", "TILT", 300, 90, 3000, tilt_ddeg))
    return 3500


def rush(node: VirtualNode, people: int = 8, gap_ms: int = 1500) -> int:
    """People through the door: two beams per crossing, PIR in the aisle."""
    def crossing(direction: str) -> None:
        if node.has("ir_beam"):
            first, second = ("b1", "b2") if direction == "IN" else ("b2", "b1")
            node.emit("B", [first, 0])
            node.emit("B", [second, 0])
            node.emit("D", [node.door, direction])
            node.emit("B", [first, 1])
            node.emit("B", [second, 1])

    def presence(active: bool) -> None:
        if node.has("pir"):
            node.emit("P", [node.zone, 1 if active else 0])

    node.at(0, lambda: presence(True))
    for i in range(people):
        direction = "IN" if i % 3 != 2 else "OUT"
        node.at(200 + i * gap_ms, lambda d=direction: crossing(d))
    end = 200 + people * gap_ms
    node.at(end + 5000, lambda: presence(False))
    return end + 5000


def empty_shelf(node: VirtualNode, slot: str = "1") -> int:
    total = 0
    s = node.slots[slot]
    for _ in range(int(s.grams // s.unit_grams)):
        # Schedule relative to now: shift each pick by the time already used.
        total += _shifted(node, total, lambda: pick(node, slot, 1)) + 2000
    return total


def _shifted(node: VirtualNode, offset_ms: int, schedule: Callable[[], int]) -> int:
    before = len(node._actions)
    duration = schedule()
    for action in node._actions[before:]:
        action.at_ms += offset_ms
    return duration


def restock(node: VirtualNode) -> int:
    def press() -> None:
        if node.has("restock_button"):
            node.emit("R", [node.shelf])
    for slot_id, s in node.slots.items():
        node.at(0, lambda i=slot_id, full=s.full_grams: node.set_weight(i, full, True))
    node.at(500, press)
    return 1000


def lights_off(node: VirtualNode, lux: int = 3) -> int:
    node.at(0, lambda: setattr(node, "lux", lux))
    return 100


def lights_on(node: VirtualNode, lux: int = 420) -> int:
    node.at(0, lambda: setattr(node, "lux", lux))
    return 100


def after_hours(node: VirtualNode) -> int:
    if node.has("pir"):
        node.at(0, lambda: node.emit("P", [node.zone, 1]))
        node.at(8000, lambda: node.emit("P", [node.zone, 0]))
    return 8000


def i2c_fault(node: VirtualNode, seconds: int = 20) -> int:
    node.at(0, lambda: setattr(node, "i2c_fault_until", node.ms + seconds * 1000))
    return seconds * 1000


def reboot(node: VirtualNode, cause: str = "IWDG") -> int:
    """Watchdog reset: uptime restarts, seq restarts, reset cause reported."""
    def go() -> None:
        node.reset_cause = cause
        node.boot_ms = node.ms
        node.seq = 0
        node.send_health()
    node.at(0, go)
    return 100


def demo(node: VirtualNode, cycles: int = 1) -> int:
    """A few minutes of a busy evening, for the dashboard."""
    steps: list[tuple[int, Callable[[], int]]] = [
        (2000, lambda: rush(node, 6)),
        (15000, lambda: pick(node, "1", 1)),
        (8000, lambda: lean(node, "2")),
        (8000, lambda: pick(node, "2", 2)),
        (8000, lambda: put_back(node, "2", 1)),
        (8000, lambda: trolley_knock(node, "1", falls=True)),
        (6000, lambda: camera_knock(node)),
        (8000, lambda: shelf_tilt(node)),
        (6000, lambda: lights_off(node)),
        (12000, lambda: lights_on(node)),
        (4000, lambda: empty_shelf(node, "1")),
        (30000, lambda: restock(node)),
    ]
    offset = 0
    for _ in range(cycles):
        for gap, step in steps:
            offset += gap
            offset += _shifted(node, offset, step)
    return offset


SCENARIOS: dict[str, Callable[..., int]] = {
    "pick": pick, "put_back": put_back, "lean": lean, "trolley_knock": trolley_knock,
    "shelf_tilt": shelf_tilt, "camera_knock": camera_knock, "camera_tilt": camera_tilt,
    "rush": rush, "empty_shelf": empty_shelf, "restock": restock, "lights_off": lights_off,
    "lights_on": lights_on, "after_hours": after_hours, "i2c_fault": i2c_fault,
    "reboot": reboot, "demo": demo,
}


# --------------------------------------------------------------------------- #
# Transports that carry a VirtualNode
# --------------------------------------------------------------------------- #


class LoopbackTransport:
    """In-memory "cable" for tests: each `read()` runs the node for `step_ms` of
    simulated time (plus `sleep_s` of real time, so threads don't spin)."""

    def __init__(self, node: VirtualNode, step_ms: int = 100, sleep_s: float = 0.002) -> None:
        self.name = f"loopback:{node.node_id}"
        self.node, self.step_ms, self.sleep_s = node, step_ms, sleep_s
        self.is_open = False
        self.fail_opens = 0            # tests: pretend the port is missing N times
        self.fail_next_read = False    # tests: pretend the USB cable was pulled
        self._lock = threading.Lock()

    def open(self) -> None:
        if self.fail_opens > 0:
            self.fail_opens -= 1
            raise OSError("port not found")
        self.is_open = True

    def read(self, size: int = 256) -> bytes:
        if not self.is_open:
            raise OSError("closed")
        if self.fail_next_read:
            self.fail_next_read = False
            self.is_open = False
            raise OSError("device disconnected")
        if self.sleep_s:
            time.sleep(self.sleep_s)
        with self._lock:
            return self.node.advance(self.step_ms)

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise OSError("closed")
        with self._lock:
            self.node.receive(data)

    def close(self) -> None:
        self.is_open = False


class SimulatorServer:
    """TCP server: the bridge connects with `--tcp host:port` (one client at a time)."""

    def __init__(self, node: VirtualNode, host: str = "127.0.0.1", port: int = 7777,
                 speed: float = 1.0, tick_s: float = 0.02,
                 scenario: str | None = "demo", repeat: bool = True) -> None:
        self.node, self.host, self.port = node, host, port
        self.speed, self.tick_s = speed, tick_s
        self.scenario, self.repeat = scenario, repeat
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._scenario_end_ms = 0
        self.clients = 0

    def start(self) -> SimulatorServer:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        server.settimeout(0.2)
        self.port = server.getsockname()[1]
        self._server = server
        self._thread = threading.Thread(target=self._serve, name="stm32-sim", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._server is not None:
            self._server.close()

    def _maybe_schedule(self) -> None:
        if self.scenario and self.node.ms >= self._scenario_end_ms and (self.repeat or not self._scenario_end_ms):
            duration = self.node.scenario(self.scenario)
            self._scenario_end_ms = self.node.ms + duration + 5000

    def _serve(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, addr = self._server.accept()
            except (TimeoutError, OSError):
                continue
            self.clients += 1
            log.info("simulator: client %s connected", addr)
            conn.settimeout(0.0)
            last = time.monotonic()
            try:
                while not self._stop.is_set():
                    time.sleep(self.tick_s)
                    now = time.monotonic()
                    self._maybe_schedule()
                    data = self.node.advance(int((now - last) * 1000 * self.speed))
                    last = now
                    if data:
                        conn.sendall(data)
                    try:
                        incoming = conn.recv(1024)
                        if not incoming:
                            break
                        self.node.receive(incoming)
                    except (BlockingIOError, TimeoutError):
                        pass
            except OSError as error:
                log.info("simulator: client gone (%s)", error)
            finally:
                conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser("storemind.sensors.simulator", description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--node", default="stm32-01")
    parser.add_argument("--scenario", default="demo", choices=sorted(SCENARIOS) + ["none"])
    parser.add_argument("--once", action="store_true", help="run the scenario once instead of looping")
    parser.add_argument("--speed", type=float, default=1.0, help="MCU seconds per real second")
    parser.add_argument("--disable", action="append", default=[], help="sensor not fitted (repeat)")
    parser.add_argument("--bad-checksum", type=float, default=0.0)
    parser.add_argument("--drop", type=float, default=0.0)
    parser.add_argument("--garbage", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    enabled = [s for s in DEFAULT_SENSORS if s not in set(args.disable)]
    node = VirtualNode(node_id=args.node, enabled=enabled, seed=args.seed,
                       noise=Noise(args.bad_checksum, args.drop, args.garbage))
    server = SimulatorServer(node, args.host, args.port, speed=args.speed,
                             scenario=None if args.scenario == "none" else args.scenario,
                             repeat=not args.once).start()
    print(f"STM32 simulator {args.node} on {args.host}:{server.port} scenario={args.scenario} "
          f"speed={args.speed}x (Ctrl-C to stop)", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
