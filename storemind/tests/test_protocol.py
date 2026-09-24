"""Contract test: every example in docs/PROTOCOL.md must agree with the parser."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from storemind.sensors.protocol import (
    MAX_LINE_BYTES,
    LineAssembler,
    ProtocolError,
    SeqTracker,
    checksum,
    encode,
    parse_frame,
    parse_line,
)

DOC = Path(__file__).resolve().parents[2] / "docs" / "PROTOCOL.md"


def _blocks(lang: str) -> list[str]:
    text = DOC.read_text(encoding="utf-8")
    lines: list[str] = []
    for block in re.findall(rf"```{lang}\n(.*?)```", text, flags=re.DOTALL):
        lines += [line.strip() for line in block.splitlines() if line.strip()]
    return lines


VALID = _blocks("text")
INVALID = _blocks("invalid")


def test_doc_has_examples_for_every_message_type():
    types = {line[1] for line in VALID}
    assert types == set("WMBDPERQHKSLZVC")
    assert len(INVALID) >= 5


@pytest.mark.parametrize("line", VALID)
def test_every_valid_example_in_protocol_md_parses(line):
    message = parse_line(line + "\r\n")
    assert message.type == line[1]
    # Round trip: re-encoding the raw fields gives the same bytes.
    frame = parse_frame(line)
    assert encode(frame.type, frame.seq, frame.ms, frame.fields) == line + "\r\n"


@pytest.mark.parametrize("entry", INVALID)
def test_every_invalid_example_in_protocol_md_is_rejected_for_the_stated_reason(entry):
    line, _, reason = entry.partition("#")
    with pytest.raises(ProtocolError) as caught:
        parse_line(line.strip())
    assert caught.value.reason == reason.strip()


def test_scaled_and_missing_values_decode():
    env = parse_line("$E,27,672000,3,,,*70").values
    assert env == {"lux": 3, "temp_c": None, "rh_pct": None, "pressure_hpa": None}
    assert parse_line("$E,26,612000,420,284,615,10093*45").values["temp_c"] == pytest.approx(28.4)
    assert parse_line("$M,21,530002,m1,S,TILT,90,40,2500,62*62").values["tilt_deg"] == pytest.approx(6.2)


def test_encode_matches_doc_and_checksum_is_xor():
    assert encode("W", 17, 523040, ["A1", 1840, 1]) == "$W,17,523040,A1,1840,1*31\r\n"
    assert checksum("W,17,523040,A1,1840,1") == 0x31


def test_encode_refuses_lines_over_96_bytes():
    with pytest.raises(ProtocolError) as caught:
        encode("R", 1, 1, ["x" * 90])
    assert caught.value.reason == "too_long"


def test_line_assembler_handles_partial_lines_and_noise():
    asm = LineAssembler()
    assert asm.feed(b"\x00\xffgarbage$W,17,5230") == []
    assert asm.feed(b"40,A1,1840,1*31\r\n$D,24,610140,do") == ["$W,17,523040,A1,1840,1*31\r\n"]
    assert asm.feed(b"or1,IN*60\n") == ["$D,24,610140,door1,IN*60\n"]
    asm.feed(b"x" * (MAX_LINE_BYTES + 5))  # runaway line with no terminator
    assert asm.dropped == 1


def test_seq_tracker_counts_gaps_across_wraparound():
    tracker = SeqTracker()
    for seq in (253, 254, 255, 0, 3):
        tracker.update(seq)
    assert tracker.lost == 2  # 1 and 2 missing
