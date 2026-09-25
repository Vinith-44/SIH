"""The C protocol library (firmware/stm32/protocol) and protocol.py must agree.

The host C tests run in CI (`firmware` job).  Here we only check what Python can:
the golden-vector header the C tests use is generated from the *current*
docs/PROTOCOL.md, and the Python CRC-16 / COBS match their published definitions.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from storemind.sensors.protocol import ProtocolError, cobs_decode, cobs_encode, crc16_ccitt

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "firmware" / "stm32" / "tools" / "gen_golden.py"


def test_golden_vectors_header_matches_protocol_md():
    result = subprocess.run([sys.executable, str(GEN), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_crc16_ccitt_false_check_value():
    # The standard check value for CRC-16/CCITT-FALSE over "123456789".
    assert crc16_ccitt(b"123456789") == 0x29B1
    assert crc16_ccitt(b"") == 0xFFFF


@pytest.mark.parametrize("data, encoded", [
    (b"", b"\x01"),
    (b"\x00", b"\x01\x01"),
    (b"\x00\x00", b"\x01\x01\x01"),
    (b"\x11\x22\x00\x33", b"\x03\x11\x22\x02\x33"),
    (b"\x11\x00\x00\x00", b"\x02\x11\x01\x01\x01"),
])
def test_cobs_known_vectors(data, encoded):
    # Vectors from the COBS paper / Wikipedia examples.
    assert cobs_encode(data) == encoded
    assert cobs_decode(encoded) == data


@pytest.mark.parametrize("size", [253, 254, 255, 508, 600])
def test_cobs_round_trip_across_block_boundaries(size):
    data = bytes((i % 255) + 1 for i in range(size))      # no zeros: longest blocks
    encoded = cobs_encode(data)
    assert 0 not in encoded
    assert cobs_decode(encoded) == data
    mixed = bytes(i % 7 for i in range(size))
    assert cobs_decode(cobs_encode(mixed)) == mixed


def test_cobs_rejects_corrupt_frames():
    with pytest.raises(ProtocolError):
        cobs_decode(b"\x03\x11\x00")
    with pytest.raises(ProtocolError):
        cobs_decode(b"\x05\x11")
