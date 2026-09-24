"""tools/mems_test.py runs end to end on the simulator (the shelf run is manual)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "mems_test.py"


def test_mems_tool_dry_run_passes(capsys):
    spec = importlib.util.spec_from_file_location("mems_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mems_test"] = module          # dataclasses look their module up here
    spec.loader.exec_module(module)
    code = module.main(["--simulate", "--scale", "0.1", "--idle-minutes", "1", "--speed", "200", "--no-write"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "pick: detected: 3/3" in out
    assert "wrongly counted as pick/put-back: 0" in out
