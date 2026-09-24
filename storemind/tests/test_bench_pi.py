"""M8: Pi benchmark telemetry parsers (real vcgencmd output formats) and a smoke run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "bench_pi.py"
spec = importlib.util.spec_from_file_location("bench_pi", TOOL)
bench_pi = importlib.util.module_from_spec(spec)
sys.modules["bench_pi"] = bench_pi
spec.loader.exec_module(bench_pi)

PMIC = """\
     3V7_WL_SW_A current(0)=0.00390372A
       3V3_SYS_A current(1)=0.05269680A
        1V8_SYS_A current(2)=0.18152800A
       VDD_CORE_A current(7)=1.20000000A
     3V7_WL_SW_V volt(8)=3.71462400V
       3V3_SYS_V volt(9)=3.31207900V
        1V8_SYS_V volt(10)=1.80109800V
       VDD_CORE_V volt(15)=0.84000000V
      EXT5V_V volt(24)=5.13480000V
"""


def test_pmic_sums_v_times_i_over_rails_with_both_readings():
    watts = bench_pi.parse_pmic(PMIC)
    expected = 0.00390372 * 3.714624 + 0.0526968 * 3.312079 + 0.181528 * 1.801098 + 1.2 * 0.84
    assert watts == pytest.approx(expected)           # EXT5V has a voltage only: not a rail sum term
    assert bench_pi.corrected_watts(watts) == pytest.approx(1.1451 * expected + 0.5879)
    assert bench_pi.parse_pmic("") is None and bench_pi.corrected_watts(None) is None


def test_temperature_and_throttle_flags():
    assert bench_pi.parse_temp("temp=52.1'C\n") == pytest.approx(52.1)
    assert bench_pi.parse_temp("61250\n") == pytest.approx(61.25)
    assert bench_pi.parse_throttled("throttled=0x0\n") == []
    assert bench_pi.parse_throttled("throttled=0x50005\n") == [
        "under-voltage now", "throttled now", "under-voltage occurred", "throttling occurred"]


def test_bench_runs_off_the_pi_with_telemetry_unavailable(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    video = tmp_path / "v.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (64, 48))
    for _ in range(4):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    out = tmp_path / "bench.json"
    monkeypatch.setattr(bench_pi, "read_power", lambda: None)
    monkeypatch.setattr(bench_pi, "read_temp", lambda: None)
    assert bench_pi.main(["--models", "stub:none", "--source", str(video), "--frames", "6",
                          "--warmup", "1", "--idle-s", "0", "--out", str(out)]) == 0
    import json

    report = json.loads(out.read_text())
    row = report["results"][0]
    assert report["bucket"] == "S" and row["frames"] == 6 and row["mj_per_frame"] is None
    assert row["ms_median"] >= 0 and row["fps"] > 0
