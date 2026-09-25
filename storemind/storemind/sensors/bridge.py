"""Serial bridge: STM32 sensor node <-> event bus (M5, Person B).

Analogy: the bridge is the node's interpreter.  The STM32 speaks short serial
lines (docs/PROTOCOL.md); the rest of StoreMind speaks events
(docs/INTERFACES.md).  The bridge translates both ways and keeps score of every
line that arrived broken.

    python -m storemind.sensors.bridge --config configs/sensors_demo.yaml --tcp 127.0.0.1:7777 --print
    python -m storemind.sensors.bridge --config configs/pi5.yaml            # sensors.port, MQTT

Design (research/26 section 4.8):

*   A dedicated **reader thread** owns the port: pyserial (or TCP to the
    simulator).  A `LineAssembler` tolerates partial lines; every line is parsed
    and validated by `protocol.py`.  If the USB-UART re-enumerates, the port is
    reopened with exponential backoff.  Nothing here ever blocks the vision loop:
    the pipeline only calls non-blocking methods (`set_light`, `buzz`, ...).
*   A **command worker** sends Pi->MCU lines, waits for the `$K` with the same
    `cmd_seq`, and resends up to `cmd_retries` times after `cmd_timeout_ms`.
    It also sends `$S` time sync at connect and every `time_sync_s`.
*   **Counters** (PROTOCOL.md section 5): bad checksum -> `crc_err`; framing,
    fields, unknown type and lost lines (`seq` gaps) -> `uart_err` on top of the
    MCU's own count.  No `$H` for `link_timeout_s` -> `NODE_HEALTH link=down` and
    an ALERT.
*   **Time**: `ms` on each line is MCU uptime.  `ClockMapper` turns it into wall
    time from the smallest observed (receive time - ms), so serial latency never
    makes an event look later than it happened.
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
from collections import deque
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..core.bus import EventBus
from ..core.config import MemsNodeConfig, StoreMindConfig
from ..core.events import (
    AlertData,
    BeamCrossData,
    CameraMountData,
    EnvironmentData,
    Event,
    EventType,
    NodeHealthData,
    PresenceData,
    SensorData,
    Severity,
    ShelfMotionData,
    WeightData,
    make_event,
)
from .protocol import (
    PATTERNS,
    LineAssembler,
    Message,
    ProtocolError,
    SeqTracker,
    encode,
    parse_line,
)

log = logging.getLogger(__name__)

LINK_TIMEOUT_S = 30.0          # PROTOCOL.md section 5: no $H for 30 s -> link down
BACKOFF_START_S = 0.5
BACKOFF_MAX_S = 10.0

# Alert severity -> LED / buzzer pattern (TowerLightSink calls set_light / buzz).
LIGHT_PATTERNS = {"G": "ON", "A": "SLOW", "R": "ALERT", "OFF": "OFF"}


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


class Transport(Protocol):
    name: str

    def open(self) -> None: ...
    def read(self, size: int = 256) -> bytes: ...     # b"" on timeout
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


class SerialTransport:
    """pyserial: `COM5` on the laptop, `/dev/storemind-mcu` on the Pi."""

    def __init__(self, port: str, baud: int = 115200, timeout_s: float = 0.1) -> None:
        self.name = f"serial:{port}@{baud}"
        self.port, self.baud, self.timeout_s = port, baud, timeout_s
        self._serial = None

    def open(self) -> None:
        import serial  # pyserial; imported lazily so tests need no port

        self._serial = serial.Serial(self.port, self.baud, timeout=self.timeout_s,
                                     write_timeout=1.0)

    def read(self, size: int = 256) -> bytes:
        if self._serial is None:
            raise OSError("port closed")
        waiting = self._serial.in_waiting
        return self._serial.read(max(1, min(size, waiting or 1)))

    def write(self, data: bytes) -> None:
        if self._serial is None:
            raise OSError("port closed")
        self._serial.write(data)

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None


class TcpTransport:
    """TCP to the simulator (`sensors.tcp: host:port`), same bytes as the UART."""

    def __init__(self, address: str, timeout_s: float = 0.1) -> None:
        host, _, port = address.rpartition(":")
        self.name = f"tcp:{address}"
        self.host, self.port, self.timeout_s = host or "127.0.0.1", int(port), timeout_s
        self._sock: socket.socket | None = None

    def open(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=2.0)
        sock.settimeout(self.timeout_s)
        self._sock = sock

    def read(self, size: int = 256) -> bytes:
        if self._sock is None:
            raise OSError("socket closed")
        try:
            data = self._sock.recv(size)
        except TimeoutError:
            return b""
        if not data:
            raise OSError("simulator closed the connection")
        return data

    def write(self, data: bytes) -> None:
        if self._sock is None:
            raise OSError("socket closed")
        self._sock.sendall(data)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None


def transport_from_config(config: StoreMindConfig, port: str | None = None,
                          tcp: str | None = None) -> Transport:
    """CLI flags win; then `sensors.tcp` (simulator); then `sensors.port`."""
    if tcp or (not port and config.sensors.tcp):
        return TcpTransport(tcp or config.sensors.tcp)
    return SerialTransport(port or config.sensors.port, config.sensors.baud)


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


class ClockMapper:
    """MCU uptime ms -> wall-clock ms.

    receive_time = send_time + latency, latency >= 0, so the smallest
    (receive - ms) seen is the best estimate of the offset.  It may creep up by
    `drift_ppm` so a slow MCU crystal cannot pin it forever; an MCU reboot
    (ms going backwards) resets it.
    """

    def __init__(self, drift_ppm: float = 200.0) -> None:
        self.drift = drift_ppm * 1e-6
        self.offset_ms: float | None = None
        self._last_mcu: int | None = None
        self._last_wall: float | None = None

    def to_wall_ms(self, mcu_ms: int, wall_ms: float) -> float:
        candidate = wall_ms - mcu_ms
        rebooted = self._last_mcu is not None and mcu_ms + 1000 < self._last_mcu
        if self.offset_ms is None or rebooted:
            self.offset_ms = candidate
        else:
            allowance = self.drift * max(0.0, wall_ms - (self._last_wall or wall_ms))
            self.offset_ms = min(candidate, self.offset_ms + allowance)
        self._last_mcu, self._last_wall = mcu_ms, wall_ms
        return mcu_ms + self.offset_ms


# --------------------------------------------------------------------------- #
# Stats and commands
# --------------------------------------------------------------------------- #


@dataclass
class BridgeStats:
    lines: int = 0
    events: int = 0
    crc_err: int = 0          # bad checksum (Pi side)
    rx_err: int = 0           # framing / too long / fields / unknown type (Pi side)
    lost: int = 0             # lines missing from seq gaps
    reconnects: int = 0
    assembler_dropped: int = 0
    cmd_sent: int = 0
    cmd_retries: int = 0
    cmd_timeouts: int = 0
    cmd_errors: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self.__dict__.items()}


@dataclass
class CommandResult:
    cmd: str               # the line type, e.g. "L"
    cmd_seq: int
    status: str            # OK | ERR | TIMEOUT
    code: int | None       # $K code (PROTOCOL.md), None on timeout
    attempts: int
    line: str = ""


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    status: str = "TIMEOUT"
    code: int | None = None


# MQTT command topic suffix -> how to build the serial line (INTERFACES.md 3.4)
def command_line_fields(command: str, payload: dict[str, Any]) -> tuple[str, list[Any]]:
    """Translate a `cmd/<COMMAND>` JSON payload into (type, fields).  Raises ValueError."""
    if command == "LED":
        return "L", [str(payload["pattern"]).upper()]
    if command == "BUZZER":
        return "Z", [str(payload["pattern"]).upper()]
    if command == "SERVO":
        return "V", [int(payload["angle"])]
    if command == "TARE":
        return "C", ["TARE", payload["slot"]]
    if command == "CAL":
        return "C", ["CAL", payload["slot"], int(payload["grams"])]
    if command == "CONFIG":
        return "C", [str(payload["key"]).upper(), *payload.get("args", [])]
    raise ValueError(f"unknown command {command!r}")


# --------------------------------------------------------------------------- #
# The bridge
# --------------------------------------------------------------------------- #


class SensorBridge:
    """Reads the node, publishes events, sends commands.  See module docstring."""

    def __init__(self, config: StoreMindConfig, bus: EventBus, transport: Transport | None = None,
                 *, link_timeout_s: float = LINK_TIMEOUT_S,
                 wall_ms: Callable[[], float] | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self.bus = bus
        self.transport = transport or transport_from_config(config)
        self.node = config.sensors.node.id
        self.store = config.store
        self.link_timeout_s = link_timeout_s
        self._wall_ms = wall_ms or (lambda: time.time() * 1000.0)
        self._mono = monotonic
        self.mems: dict[str, MemsNodeConfig] = {m.id: m for m in config.sensors.mems_nodes}

        self.stats = BridgeStats()
        self.clock = ClockMapper()
        self._asm = LineAssembler()
        self._seq = SeqTracker()
        self._rejected_since_good = 0     # bad lines still used up a seq number
        self._last_ms: int | None = None
        self._write_lock = threading.Lock()
        self._down_seq = 0
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._commands: queue.Queue[tuple[str, list[Any]] | None] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._connected = threading.Event()
        self._warned: set[str] = set()

        self.link = "down"
        self.last_health: dict[str, Any] | None = None
        self._last_h_mono: float | None = None
        self._link_alerted = False
        self.latest: dict[str, dict[str, Any]] = {}     # "WEIGHT:A1" -> payload + ts
        self.patterns = {"LED": "OFF", "BUZZER": "OFF"}
        self.on_command_result: Callable[[CommandResult], None] | None = None
        self.acks: deque[tuple[int, str, int]] = deque(maxlen=64)   # every $K seen (HIL test)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> SensorBridge:
        self._stop.clear()
        for name, target in (("sensor-bridge-rx", self._reader_loop),
                             ("sensor-bridge-cmd", self._command_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._commands.put_nowait(None)
        except queue.Full:
            pass
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        self.transport.close()

    close = stop

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    # ------------------------------------------------------------------ #
    # Receive path
    # ------------------------------------------------------------------ #
    def _reader_loop(self) -> None:
        backoff = BACKOFF_START_S
        first = True
        while not self._stop.is_set():
            if not self._connected.is_set():
                try:
                    self.transport.open()
                except Exception as error:  # port missing, simulator not up yet
                    log.warning("sensor node %s: cannot open %s (%s); retry in %.1fs",
                                self.node, self.transport.name, error, backoff)
                    self._stop.wait(backoff)
                    backoff = min(BACKOFF_MAX_S, backoff * 2)
                    self.check_link()
                    continue
                if not first:
                    self.stats.reconnects += 1
                first = False
                backoff = BACKOFF_START_S
                self._asm = LineAssembler()
                self._seq = SeqTracker()        # a new connection may be a new / rebooted node
                self._rejected_since_good = 0
                self._connected.set()
                log.info("sensor node %s connected via %s", self.node, self.transport.name)
                self.sync_time()
            try:
                chunk = self.transport.read(256)
            except Exception as error:
                log.warning("sensor node %s: link lost (%s); reopening", self.node, error)
                self._connected.clear()
                self.transport.close()
                continue
            if chunk:
                self.feed(chunk)
            self.check_link()

    def feed(self, chunk: bytes) -> list[Event]:
        """Bytes in, events out (published).  Used by the reader thread and by tests."""
        events: list[Event] = []
        before = self._asm.dropped
        for line in self._asm.feed(chunk):
            events += self.handle_line(line)
        self.stats.assembler_dropped += self._asm.dropped - before
        return events

    def handle_line(self, line: str) -> list[Event]:
        self.stats.lines += 1
        try:
            message = parse_line(line)
        except ProtocolError as error:
            self._rejected_since_good += 1
            self.stats.reasons[error.reason] = self.stats.reasons.get(error.reason, 0) + 1
            if error.reason == "checksum":
                self.stats.crc_err += 1
            else:
                self.stats.rx_err += 1
            log.debug("dropped line %r: %s", line, error)
            return []
        if message.direction != "up":
            self.stats.rx_err += 1
            self.stats.reasons["wrong_direction"] = self.stats.reasons.get("wrong_direction", 0) + 1
            return []
        # A gap counts lines that never arrived; lines that arrived broken were
        # already counted above, so they are not "lost" as well.
        if self._last_ms is not None and message.ms + 1000 < self._last_ms:
            self._seq = SeqTracker()            # MCU rebooted: seq restarts at 0
            self._rejected_since_good = 0
        self._last_ms = message.ms
        gap = self._seq.update(message.seq)
        self.stats.lost += max(0, gap - self._rejected_since_good)
        self._rejected_since_good = 0
        events = self._to_events(message)
        for event in events:
            self.stats.events += 1
            self.bus.publish(event)
        return events

    def _ts(self, message: Message) -> datetime:
        wall = self.clock.to_wall_ms(message.ms, self._wall_ms())
        return datetime.fromtimestamp(wall / 1000.0).astimezone()

    def _event(self, message: Message, event_type: EventType, data: Any, cam: str | None = None) -> Event:
        event = make_event(ts=self._ts(message), store=self.store, node=self.node,
                           type=event_type, data=data, cam=cam)
        key = f"{event_type.value}:{self._key_of(event.data)}"
        self.latest[key] = {"ts": event.ts, **event.data}
        return event

    @staticmethod
    def _key_of(data: dict[str, Any]) -> str:
        for name in ("slot", "door", "zone", "cam", "shelf", "channel"):
            if data.get(name) not in (None, ""):
                return f"{data.get('node', '')}/{data[name]}"
        return str(data.get("node", ""))

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(message)

    def _to_events(self, message: Message) -> list[Event]:
        v = message.values
        t = message.type
        if t == "W":
            return [self._event(message, EventType.WEIGHT, WeightData(
                node=self.node, slot=v["slot"], grams=float(v["grams"]), stable=v["stable"]))]
        if t == "M":
            return self._mems_event(message)
        if t == "D":
            return [self._event(message, EventType.BEAM_CROSS, BeamCrossData(
                node=self.node, door=v["door"], direction=v["direction"].lower(), t_ms_mcu=message.ms))]
        if t == "P":
            return [self._event(message, EventType.PRESENCE, PresenceData(
                node=self.node, zone=v["zone"], active=v["active"]))]
        if t == "E":
            return [self._event(message, EventType.ENVIRONMENT, EnvironmentData(
                node=self.node, lux=None if v["lux"] is None else float(v["lux"]),
                temp_c=v["temp_c"], rh_pct=v["rh_pct"], pressure_hpa=v["pressure_hpa"]))]
        if t == "R":
            # INTERFACES.md (M3): the restock button is a SENSOR event, not a new type.
            return [self._event(message, EventType.SENSOR, SensorData(
                node=self.node, sensor="restock", channel=v["shelf"], value=1.0, unit="press"))]
        if t == "H":
            return [self._health_event(message)]
        if t == "K":
            self._resolve(v["cmd_seq"], v["status"], v["code"])
            return []
        if t == "Q":
            if not self.config.sensors.has("ld2450"):
                return []
            return [self._event(message, EventType.SENSOR, SensorData(
                node=self.node, sensor="ld2450", channel="targets", value=float(v["n"]), unit="count"))]
        # $B raw beam edges are for debugging only (PROTOCOL.md): log, no event.
        log.debug("beam %s %s", v.get("beam"), "clear" if v.get("clear") else "blocked")
        return []

    def _mems_event(self, message: Message) -> list[Event]:
        v = message.values
        mount = self.mems.get(v["node"])
        if v["role"] == "S":
            if mount is None or mount.role != "shelf":
                self._warn_once(f"mems:{v['node']}",
                                f"MEMS node {v['node']!r} (shelf) is not in sensors.mems_nodes; "
                                "publishing it with shelf = node id")
            shelf = mount.shelf if mount is not None and mount.shelf else v["node"]
            slot = mount.slot if mount is not None else None
            return [self._event(message, EventType.SHELF_MOTION, ShelfMotionData(
                node=v["node"], shelf=shelf, slot=slot, kind=v["event"],
                peak_mg=float(v["peak_mg"]), rms_mg=float(v["rms_mg"]), dur_ms=v["dur_ms"]))]
        # role C = camera mount: TOUCH / SETTLED are dropped (PROTOCOL.md table)
        if v["event"] not in ("KNOCK", "TILT"):
            return []
        if mount is None or mount.role != "camera_mount":
            self._warn_once(f"mems:{v['node']}",
                            f"MEMS node {v['node']!r} (camera) is not in sensors.mems_nodes; "
                            "publishing it with cam = node id")
        cam = mount.cam if mount is not None and mount.cam else v["node"]
        return [self._event(message, EventType.CAMERA_MOUNT, CameraMountData(
            node=v["node"], cam=cam, kind=v["event"], peak_mg=float(v["peak_mg"]),
            tilt_deg=v["tilt_deg"]), cam=cam)]

    def _health_event(self, message: Message) -> Event:
        v = message.values
        self._last_h_mono = self._mono()
        self.link = "up"
        if self._link_alerted:
            self._link_alerted = False
            log.info("sensor node %s: link back up", self.node)
        self.last_health = dict(v)
        return self._event(message, EventType.NODE_HEALTH, NodeHealthData(
            node=self.node, uptime_s=v["uptime_s"], free_heap=v["free_heap"],
            min_stack_words=v["min_stack_words"], i2c_err=v["i2c_err"],
            uart_err=v["uart_err"] + self.stats.rx_err + self.stats.lost,
            crc_err=self.stats.crc_err, reset_cause=v["reset_cause"], link="up"))

    # ------------------------------------------------------------------ #
    # Link watchdog
    # ------------------------------------------------------------------ #
    def check_link(self) -> list[Event]:
        """No `$H` for `link_timeout_s` -> NODE_HEALTH link=down + ALERT (once)."""
        now = self._mono()
        if self._last_h_mono is None:
            self._last_h_mono = now          # grace period from start-up
            return []
        if self._link_alerted or now - self._last_h_mono < self.link_timeout_s:
            return []
        self._link_alerted = True
        self.link = "down"
        h = self.last_health or {}
        ts = datetime.now().astimezone()
        silent = round(now - self._last_h_mono)
        events = [
            make_event(ts=ts, store=self.store, node=self.node, type=EventType.NODE_HEALTH,
                       data=NodeHealthData(
                           node=self.node, uptime_s=int(h.get("uptime_s", 0)),
                           free_heap=int(h.get("free_heap", 0)),
                           min_stack_words=int(h.get("min_stack_words", 0)),
                           i2c_err=int(h.get("i2c_err", 0)),
                           uart_err=int(h.get("uart_err", 0)) + self.stats.rx_err + self.stats.lost,
                           crc_err=self.stats.crc_err,
                           reset_cause=str(h.get("reset_cause", "unknown")), link="down")),
            make_event(ts=ts, store=self.store, node=self.node, type=EventType.ALERT,
                       data=AlertData(
                           severity=Severity.WARN, message_key=f"SENSOR_LINK:{self.node}",
                           message=f"Sensor node {self.node} silent for {silent} s - check the cable / power",
                           alert_id=f"SENSOR_LINK:{self.node}@{int(time.time())}",
                           context={"node": self.node, "silent_s": silent,
                                    "transport": self.transport.name})),
        ]
        for event in events:
            self.bus.publish(event)
        log.warning("sensor node %s: no heartbeat for %ss -> link down", self.node, silent)
        return events

    # ------------------------------------------------------------------ #
    # Commands (Pi -> MCU)
    # ------------------------------------------------------------------ #
    def _next_seq(self) -> int:
        with self._pending_lock:
            seq = self._down_seq
            self._down_seq = (self._down_seq + 1) % 256
            return seq

    def _resolve(self, cmd_seq: int, status: str, code: int) -> None:
        self.acks.append((cmd_seq, status, code))
        with self._pending_lock:
            pending = self._pending.get(cmd_seq)
        if pending is None:
            return            # a late $K for a command we gave up on, or a duplicate
        pending.status, pending.code = status, code
        pending.done.set()

    def send_command(self, msg_type: str, fields: list[Any]) -> CommandResult:
        """Send one command and wait for its `$K` (blocking; call off the vision loop)."""
        node_cfg = self.config.sensors.node
        seq = self._next_seq()
        try:
            line = encode(msg_type, seq, int(self._mono() * 1000) & 0xFFFFFFFF, fields)
            parse_line(line)                     # validate our own arguments first
        except (ProtocolError, ValueError) as error:
            self.stats.cmd_errors += 1
            log.warning("refusing command %s %s: %s", msg_type, fields, error)
            return CommandResult(msg_type, seq, "ERR", 3, 0)
        pending = _Pending()
        with self._pending_lock:
            self._pending[seq] = pending
        attempts = 0
        try:
            for attempt in range(1 + node_cfg.cmd_retries):
                attempts = attempt + 1
                if attempt:
                    self.stats.cmd_retries += 1
                try:
                    with self._write_lock:
                        self.transport.write(line.encode("ascii"))
                    self.stats.cmd_sent += 1
                except Exception as error:
                    log.debug("command write failed: %s", error)
                if pending.done.wait(node_cfg.cmd_timeout_ms / 1000.0):
                    break
        finally:
            with self._pending_lock:
                self._pending.pop(seq, None)
        if not pending.done.is_set():
            self.stats.cmd_timeouts += 1
        elif pending.status != "OK":
            self.stats.cmd_errors += 1
        result = CommandResult(msg_type, seq, pending.status if pending.done.is_set() else "TIMEOUT",
                               pending.code, attempts, line.strip())
        if result.status == "OK" and msg_type in ("L", "Z"):
            self.patterns["LED" if msg_type == "L" else "BUZZER"] = str(fields[0])
        if self.on_command_result is not None:
            self.on_command_result(result)
        return result

    def submit(self, msg_type: str, fields: list[Any]) -> bool:
        """Queue a command for the worker thread (never blocks).  False if the queue is full."""
        try:
            self._commands.put_nowait((msg_type, list(fields)))
            return True
        except queue.Full:
            log.warning("sensor command queue full; dropping %s %s", msg_type, fields)
            return False

    def _command_loop(self) -> None:
        next_sync = self._mono() + self.config.sensors.node.time_sync_s
        while not self._stop.is_set():
            try:
                item = self._commands.get(timeout=0.5)
            except queue.Empty:
                item = False
            if item is None:
                return
            if item and self._connected.is_set():
                self.send_command(*item)
            if self._connected.is_set() and self._mono() >= next_sync:
                next_sync = self._mono() + self.config.sensors.node.time_sync_s
                self.sync_time()

    def sync_time(self) -> None:
        self.submit("S", [int(self._wall_ms())])

    # Convenience API (non-blocking), also what alerts.TowerLightSink calls.
    def set_led(self, pattern: str) -> bool:
        return self.submit("L", [pattern.upper()])

    def set_buzzer(self, pattern: str) -> bool:
        return self.submit("Z", [pattern.upper()])

    def set_servo(self, angle: int) -> bool:
        return self.submit("V", [int(angle)])

    def tare(self, slot: str) -> bool:
        return self.submit("C", ["TARE", slot])

    def calibrate(self, slot: str, grams: int) -> bool:
        return self.submit("C", ["CAL", slot, int(grams)])

    def set_light(self, colour: str) -> bool:
        """TowerLightSink: G/A/R -> steady / slow blink / alert pattern."""
        pattern = LIGHT_PATTERNS.get(colour.upper(), colour.upper())
        return self.set_led(pattern) if pattern in PATTERNS else False

    def buzz(self, times: int = 1) -> bool:
        """TowerLightSink: a short alarm; the MCU's ALERT pattern ends on its own."""
        return self.set_buzzer("ALERT" if times > 1 else "FAST")

    # ------------------------------------------------------------------ #
    # Dashboard
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "transport": self.transport.name,
            "connected": self.connected,
            "link": self.link,
            "health": self.last_health,
            "stats": self.stats.as_dict(),
            "latest": dict(self.latest),
            "patterns": dict(self.patterns),
        }


# --------------------------------------------------------------------------- #
# MQTT commands (separate-process deployment on the Pi)
# --------------------------------------------------------------------------- #


class MqttCommandListener:
    """`storemind/{store}/{node}/cmd/{COMMAND}` -> serial command -> `.../ack`.

    Runs its own paho client so the event bus stays a plain publisher.  Commands
    are executed on a worker thread: each can take up to 4 x 200 ms.
    """

    def __init__(self, bridge: SensorBridge, host: str, port: int,
                 username: str | None = None, password: str | None = None,
                 client: Any | None = None) -> None:
        self.bridge = bridge
        self.prefix = f"storemind/{bridge.store}/{bridge.node}"
        if client is None:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"storemind-{bridge.store}-{bridge.node}-cmd")
        self.client = client
        if username:
            self.client.username_pw_set(username, password or "")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._work: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=64)
        self._thread = threading.Thread(target=self._worker, name="sensor-bridge-mqtt-cmd", daemon=True)
        self._thread.start()
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client: Any, _userdata: Any, _flags: Any, reason_code: Any,
                    _properties: Any = None) -> None:
        if not getattr(reason_code, "is_failure", False):
            client.subscribe(f"{self.prefix}/cmd/+", qos=1)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        command = message.topic.rsplit("/", 1)[-1]
        try:
            payload = json.loads(message.payload or b"{}")
        except ValueError:
            payload = {}
        try:
            self._work.put_nowait((command, payload))
        except queue.Full:
            self._ack(command, None, "ERR", 5)            # 5 = busy

    def handle(self, command: str, payload: dict[str, Any]) -> CommandResult:
        try:
            msg_type, fields = command_line_fields(command, payload)
        except (KeyError, ValueError, TypeError):
            result = CommandResult(command, -1, "ERR", 3, 0)
        else:
            result = self.bridge.send_command(msg_type, fields)
        self._ack(command, result.cmd_seq, result.status, result.code)
        return result

    def _ack(self, command: str, cmd_seq: int | None, status: str, code: int | None) -> None:
        self.client.publish(f"{self.prefix}/ack",
                            json.dumps({"cmd": command, "cmd_seq": cmd_seq, "status": status, "code": code}),
                            qos=1)

    def _worker(self) -> None:
        while True:
            command, payload = self._work.get()
            try:
                self.handle(command, payload)
            except Exception:
                log.exception("MQTT command %s failed", command)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_bus(config: StoreMindConfig, no_mqtt: bool = False) -> EventBus:
    if config.mqtt.enabled and not no_mqtt:
        from ..core.bus import MqttBus

        # listen=True: the bridge also hears ALERT events from the pipeline (LED / buzzer).
        return MqttBus(config.mqtt.host, config.mqtt.port, config.mqtt.username, config.mqtt.password,
                       store=config.store, node=config.sensors.node.id, listen=True)
    return EventBus()


def main(argv: list[str] | None = None) -> int:
    from ..core.config import load_config

    parser = argparse.ArgumentParser("storemind.sensors.bridge", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--port", default=None, help="serial port (overrides sensors.port)")
    parser.add_argument("--tcp", default=None, help="host:port of the simulator (overrides sensors.tcp)")
    parser.add_argument("--no-mqtt", action="store_true", help="in-process bus only (prints with --print)")
    parser.add_argument("--print", action="store_true", help="print every event as a JSON line")
    parser.add_argument("--seconds", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--stats-every", type=float, default=30.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    bus = build_bus(config, args.no_mqtt)
    if args.print:
        bus.subscribe_all(lambda event: print(event.to_json(), flush=True))
    bridge = SensorBridge(config, bus, transport_from_config(config, args.port, args.tcp))

    listener = None
    if config.mqtt.enabled and not args.no_mqtt:
        listener = MqttCommandListener(bridge, config.mqtt.host, config.mqtt.port,
                                       config.mqtt.username, config.mqtt.password)
    from ..health.systemd import Notifier

    notifier = Notifier()
    bridge.start()
    notifier.ready(f"bridge {bridge.node} via {bridge.transport.name}")
    started = time.monotonic()
    next_stats = started + args.stats_every
    try:
        while args.seconds is None or time.monotonic() - started < args.seconds:
            time.sleep(0.5)
            notifier.watchdog()
            if time.monotonic() >= next_stats:
                next_stats += args.stats_every
                log.info("bridge stats: %s", json.dumps(bridge.stats.as_dict()))
    except KeyboardInterrupt:
        pass
    finally:
        notifier.stopping()
        bridge.stop()
        if listener is not None:
            listener.close()
        close = getattr(bus, "close", None)
        if close is not None:
            close()
    print(json.dumps({"stats": bridge.stats.as_dict(), "link": bridge.link}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
