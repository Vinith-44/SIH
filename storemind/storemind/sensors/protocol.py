"""Serial protocol STM32 <-> Pi, demo (text) mode.  Pure Python, no I/O.

`docs/PROTOCOL.md` is the single source of truth; this module is its executable
half, and `tests/test_protocol.py` parses every example line in that document.
If the two disagree, the document wins and this file is the bug.

Frame::

    $<T>,<seq>,<ms>,<field>,<field>...*<XX>\r\n

*   `T`   one upper-case letter, the message type (table below).
*   `seq` rolling 0-255 counter per direction; a gap means lost lines.
*   `ms`  sender uptime in milliseconds (MCU->Pi); the Pi maps it to wall-clock
    time with the `$S` sync.
*   `XX`  XOR of every byte between `$` and `*`, two upper-case hex digits.
*   At most 96 bytes including `\r\n`.  Every value is an integer (scaled),
    because newlib-nano printf has no floats.  An *empty* field means "not
    measured" (e.g. `$E` without a BME280 fitted).

The binary production mode (COBS + CRC16-CCITT, switched by `$C,MODE,BIN`) is
specified in `docs/PROTOCOL.md` and implemented by Person B in M5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_LINE_BYTES = 96

# --------------------------------------------------------------------------- #
# Field types
# --------------------------------------------------------------------------- #


class ProtocolError(ValueError):
    """A line that must be dropped.  `reason` is counted in NODE_HEALTH."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason  # framing | too_long | checksum | unknown_type | fields


def _int(value: str) -> int:
    if value == "" or not value.lstrip("-").isdigit():
        raise ValueError(f"expected an integer, got {value!r}")
    return int(value)


def _opt_int(value: str) -> int | None:
    return None if value == "" else _int(value)


def _bit(value: str) -> bool:
    if value not in ("0", "1"):
        raise ValueError(f"expected 0 or 1, got {value!r}")
    return value == "1"


def _name(value: str) -> str:
    if not value or not all(c.isalnum() or c in "-_." for c in value):
        raise ValueError(f"expected an id like 'A1' or 'shelf-a', got {value!r}")
    return value


def _one_of(*choices: str):
    def check(value: str) -> str:
        if value not in choices:
            raise ValueError(f"expected one of {'/'.join(choices)}, got {value!r}")
        return value
    return check


def _scaled(divisor: int):
    """Integer on the wire, real number after decoding (e.g. temp_c x 10)."""
    def check(value: str) -> float | None:
        number = _opt_int(value)
        return None if number is None else number / divisor
    return check


PATTERNS = ("OFF", "ON", "SLOW", "FAST", "ALERT")
MEMS_EVENTS = ("TOUCH", "SETTLED", "TILT", "KNOCK")
RESET_CAUSES = ("POR", "PIN", "IWDG", "WWDG", "SW", "BOR", "LPWR", "UNKNOWN")
CONFIG_KEYS = ("TARE", "CAL", "MEMS_THR", "MODE", "ENABLE")

# type -> (direction, [(field name, converter)], variable-length tail?)
# direction: "up" = MCU -> Pi, "down" = Pi -> MCU.
SPEC: dict[str, tuple[str, list[tuple[str, Any]]]] = {
    "W": ("up", [("slot", _name), ("grams", _int), ("stable", _bit)]),
    "M": ("up", [("node", _name), ("role", _one_of("S", "C")), ("event", _one_of(*MEMS_EVENTS)),
                 ("peak_mg", _int), ("rms_mg", _int), ("dur_ms", _int),
                 ("tilt_deg", _scaled(10))]),
    "B": ("up", [("beam", _name), ("clear", _bit)]),
    "D": ("up", [("door", _name), ("direction", _one_of("IN", "OUT"))]),
    "P": ("up", [("zone", _name), ("active", _bit)]),
    "E": ("up", [("lux", _opt_int), ("temp_c", _scaled(10)), ("rh_pct", _scaled(10)),
                 ("pressure_hpa", _scaled(10))]),
    "R": ("up", [("shelf", _name)]),
    "Q": ("up", [("n", _int)]),  # followed by n x (x_mm, y_mm, v_cms); see _parse_radar
    "H": ("up", [("uptime_s", _int), ("free_heap", _int), ("min_stack_words", _int),
                 ("i2c_err", _int), ("uart_err", _int), ("reset_cause", _one_of(*RESET_CAUSES))]),
    "K": ("up", [("cmd_seq", _int), ("status", _one_of("OK", "ERR")), ("code", _int)]),
    "S": ("down", [("epoch_ms", _int)]),
    "L": ("down", [("pattern", _one_of(*PATTERNS))]),
    "Z": ("down", [("pattern", _one_of(*PATTERNS))]),
    "V": ("down", [("angle", _int)]),
    "C": ("down", [("key", _one_of(*CONFIG_KEYS))]),  # followed by key-specific args
}

# `$C` arguments per key.
CONFIG_ARGS: dict[str, list[tuple[str, Any]]] = {
    "TARE": [("slot", _name)],
    "CAL": [("slot", _name), ("grams", _int)],
    "MEMS_THR": [("node", _name), ("mg", _int)],
    "MODE": [("mode", _one_of("TXT", "BIN"))],
    "ENABLE": [("sensor", _name), ("on", _bit)],
}


# --------------------------------------------------------------------------- #
# Framing
# --------------------------------------------------------------------------- #


def checksum(body: str) -> int:
    """XOR of the bytes between `$` and `*`."""
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return value


@dataclass(frozen=True)
class Frame:
    type: str
    seq: int
    ms: int
    fields: list[str]


@dataclass
class Message:
    """A decoded, validated line."""

    type: str
    seq: int
    ms: int
    direction: str
    values: dict[str, Any] = field(default_factory=dict)


def encode(msg_type: str, seq: int, ms: int, fields: list[Any] | tuple[Any, ...] = ()) -> str:
    """Build one line, including `\\r\\n`.  `None` becomes an empty field."""
    if msg_type not in SPEC:
        raise ProtocolError("unknown_type", msg_type)
    parts = [msg_type, str(seq % 256), str(int(ms))]
    parts += ["" if value is None else str(value) for value in fields]
    body = ",".join(parts)
    line = f"${body}*{checksum(body):02X}\r\n"
    if len(line.encode("ascii")) > MAX_LINE_BYTES:
        raise ProtocolError("too_long", f"{len(line)} bytes > {MAX_LINE_BYTES}")
    return line


def parse_frame(line: str | bytes) -> Frame:
    """Check framing, length and checksum.  Does not look at the fields."""
    if isinstance(line, bytes):
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError as error:
            raise ProtocolError("framing", "non-ASCII bytes") from error
    if len(line.encode("ascii")) > MAX_LINE_BYTES:
        raise ProtocolError("too_long", f"{len(line)} bytes > {MAX_LINE_BYTES}")
    line = line.rstrip("\r\n")
    if not line.startswith("$") or line.count("*") != 1 or line.count("$") != 1:
        raise ProtocolError("framing", repr(line))
    body, _, given = line[1:].partition("*")
    if len(given) != 2 or any(c not in "0123456789ABCDEF" for c in given):
        raise ProtocolError("framing", f"checksum must be 2 upper-case hex digits: {given!r}")
    if int(given, 16) != checksum(body):
        raise ProtocolError("checksum", f"got {given}, expected {checksum(body):02X}")
    parts = body.split(",")
    if len(parts) < 3 or len(parts[0]) != 1:
        raise ProtocolError("framing", f"need <T>,<seq>,<ms>: {body!r}")
    try:
        seq, ms = _int(parts[1]), _int(parts[2])
    except ValueError as error:
        raise ProtocolError("framing", str(error)) from error
    if not 0 <= seq <= 255 or ms < 0:
        raise ProtocolError("framing", f"seq {seq} / ms {ms} out of range")
    return Frame(parts[0], seq, ms, parts[3:])


def _convert(spec: list[tuple[str, Any]], raw: list[str], msg_type: str) -> dict[str, Any]:
    if len(raw) != len(spec):
        raise ProtocolError("fields", f"${msg_type} needs {len(spec)} fields "
                                      f"({', '.join(n for n, _ in spec)}), got {len(raw)}")
    out: dict[str, Any] = {}
    for (name, convert), value in zip(spec, raw):
        try:
            out[name] = convert(value)
        except ValueError as error:
            raise ProtocolError("fields", f"${msg_type}.{name}: {error}") from error
    return out


def decode(frame: Frame) -> Message:
    if frame.type not in SPEC:
        raise ProtocolError("unknown_type", frame.type)
    direction, spec = SPEC[frame.type]
    if frame.type == "Q":
        values = _convert(spec, frame.fields[:1], "Q")
        values["targets"] = _parse_radar(values["n"], frame.fields[1:])
    elif frame.type == "C":
        values = _convert(spec, frame.fields[:1], "C")
        values.update(_convert(CONFIG_ARGS[values["key"]], frame.fields[1:], f"C.{values['key']}"))
    else:
        values = _convert(spec, frame.fields, frame.type)
    if frame.type == "V" and not 0 <= values["angle"] <= 180:
        raise ProtocolError("fields", f"$V.angle {values['angle']} not in 0..180")
    return Message(frame.type, frame.seq, frame.ms, direction, values)


def _parse_radar(n: int, raw: list[str]) -> list[dict[str, int]]:
    if not 0 <= n <= 3 or len(raw) != 3 * n:
        raise ProtocolError("fields", f"$Q: n={n} needs {3 * n} target fields, got {len(raw)}")
    try:
        numbers = [_int(v) for v in raw]
    except ValueError as error:
        raise ProtocolError("fields", f"$Q: {error}") from error
    return [{"x_mm": numbers[i], "y_mm": numbers[i + 1], "v_cms": numbers[i + 2]}
            for i in range(0, len(numbers), 3)]


def parse_line(line: str | bytes) -> Message:
    """Framing + checksum + fields.  Raises `ProtocolError` for anything bad."""
    return decode(parse_frame(line))


# --------------------------------------------------------------------------- #
# Stream helpers (the bridge wraps these around pyserial)
# --------------------------------------------------------------------------- #


class LineAssembler:
    """Turns arbitrary byte chunks into complete lines.

    Tolerates partial lines, garbage before `$`, and runaway lines with no
    terminator (dropped once they exceed the maximum length).
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, chunk: bytes) -> list[str]:
        self._buf.extend(chunk)
        lines: list[str] = []
        while True:
            end = self._buf.find(b"\n")
            if end < 0:
                if len(self._buf) > MAX_LINE_BYTES:
                    self._buf.clear()
                    self.dropped += 1
                return lines
            raw = bytes(self._buf[:end + 1])
            del self._buf[:end + 1]
            start = raw.rfind(b"$")
            if start < 0:
                self.dropped += 1
                continue
            lines.append(raw[start:].decode("ascii", errors="replace"))


class SeqTracker:
    """Counts lines lost in transit from gaps in the rolling 0-255 `seq`."""

    def __init__(self) -> None:
        self.last: int | None = None
        self.lost = 0

    def update(self, seq: int) -> int:
        gap = 0 if self.last is None else (seq - self.last - 1) % 256
        self.lost += gap
        self.last = seq
        return gap


# --------------------------------------------------------------------------- #
# Binary production mode building blocks (docs/PROTOCOL.md section 6).  The
# firmware has the same functions in C (firmware/stm32/protocol); the host C
# tests check both give identical bytes (tests/golden_vectors.h).
# --------------------------------------------------------------------------- #


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no final XOR."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    """COBS: the output has no 0x00 bytes; the caller appends the 0x00 delimiter."""
    out = bytearray([0])
    code_at, code = 0, 1
    for byte in data:
        if byte == 0:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
            continue
        out.append(byte)
        code += 1
        if code == 0xFF:
            out[code_at] = code
            code_at, code = len(out), 1
            out.append(0)
    out[code_at] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        i += 1
        if code == 0 or i + code - 1 > len(data):
            raise ProtocolError("framing", "corrupt COBS block")
        block = data[i:i + code - 1]
        if 0 in block:
            raise ProtocolError("framing", "zero inside a COBS frame")
        out += block
        i += code - 1
        if code != 0xFF and i < len(data):
            out.append(0)
    return bytes(out)
