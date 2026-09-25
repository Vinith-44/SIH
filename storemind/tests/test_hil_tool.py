"""tools/hil_test.py runs end to end against the simulator (the board run is manual)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "hil_test.py"


def _load():
    spec = importlib.util.spec_from_file_location("hil_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hil_tool_passes_against_the_simulator(capsys):
    hil = _load()
    code = hil.main(["--simulate", "--speed", "50", "--minutes", "0.05", "--rtt-rounds", "5",
                     "--sensor-seconds", "0", "--no-write"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "corrupted command answered with ERR 1: yes" in out
    assert "soak checksum errors: 0" in out and "RESULT: PASS" in out
