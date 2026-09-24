"""Stream probing: measured-vs-declared FPS, resolution and darkness checks.

The end-to-end tests write a small mp4 with cv2.VideoWriter rather than reading
a fixture, so they run on a fresh checkout where videos/ is empty - the media is
git-ignored, and a test that skips itself on the machine that most needs it is
not much of a test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from probe import (                        # noqa: E402
    DARK_MEAN,
    MAX_SUB_WIDTH,
    ProbeResult,
    check_fps,
    check_resolution,
    format_report,
    main,
    probe,
)


def _write_clip(path: Path, frames: int = 30, size: tuple[int, int] = (640, 360),
                fps: float = 10.0, value: int | None = None) -> Path:
    """A tiny mp4 with known size and frame count."""
    width, height = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():  # pragma: no cover - depends on the OpenCV build
        pytest.skip("this OpenCV build cannot write mp4")
    rng = np.random.default_rng(1)
    for index in range(frames):
        if value is None:
            frame = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
        else:
            frame = np.full((height, width, 3), value, dtype=np.uint8)
        # A moving block keeps consecutive frames from being identical, which
        # some encoders would otherwise collapse.
        if value is None:
            frame[index % height: index % height + 10, :, :] = 255
        writer.write(frame)
    writer.release()
    return path


# --------------------------------------------------------------------------- #
# Pure checks
# --------------------------------------------------------------------------- #


def test_resolution_warns_about_the_main_stream():
    warnings = check_resolution(1920, 1080)
    assert warnings and "main stream" in warnings[0]


def test_resolution_warns_when_too_small():
    warnings = check_resolution(320, 240)
    assert warnings and "narrower" in warnings[0]


@pytest.mark.parametrize("width", [640, 1280])
def test_resolution_accepts_the_documented_sub_stream_range(width: int):
    assert check_resolution(width, int(width * 9 / 16)) == []


def test_resolution_reports_an_unreadable_size():
    assert check_resolution(0, 0) == ["could not read frame size"]


def test_fps_warns_when_the_header_overclaims():
    # The failure this tool exists for: 25 claimed, 6 delivered.
    warnings = check_fps(declared=25.0, measured=6.0, expect=None)
    assert warnings and "not a promise" in warnings[0]


def test_fps_tolerates_a_small_gap():
    assert check_fps(declared=10.0, measured=9.5, expect=None) == []


def test_fps_checks_the_role_requirement():
    warnings = check_fps(declared=10.0, measured=4.0, expect=8.0)
    assert any("below the 8.0" in w for w in warnings)


def test_fps_reports_nothing_measured():
    assert check_fps(declared=10.0, measured=0.0, expect=None) == ["no frames measured"]


# --------------------------------------------------------------------------- #
# State mapping onto the CAMERA_HEALTH contract
# --------------------------------------------------------------------------- #


def test_state_is_stale_when_the_stream_never_opened():
    assert ProbeResult(source="x").state() == "stale"


def test_state_is_stale_when_no_frames_arrive():
    assert ProbeResult(source="x", opened=True, frames=0).state() == "stale"


def test_state_is_tampered_for_a_uniform_frame():
    # A covered lens is also dark; saying "tampered" is more use to the installer.
    result = ProbeResult(source="x", opened=True, frames=10,
                         mean_brightness=5.0, std_brightness=0.2)
    assert result.state() == "tampered"


def test_state_is_dark_for_a_night_frame():
    result = ProbeResult(source="x", opened=True, frames=10,
                         mean_brightness=DARK_MEAN - 5, std_brightness=25.0)
    assert result.state() == "dark"


def test_state_is_ok_for_a_normal_frame():
    result = ProbeResult(source="x", opened=True, frames=10,
                         mean_brightness=120.0, std_brightness=40.0)
    assert result.state() == "ok"


def test_camera_health_payload_matches_the_contract():
    result = ProbeResult(source="x", opened=True, frames=10, measured_fps=9.83,
                         mean_brightness=120.0, std_brightness=40.0)
    payload = result.to_camera_health("entrance", lag_ms=120.0)
    assert payload.cam == "entrance"
    assert payload.state == "ok"
    assert payload.fps == 9.83
    assert payload.lag_ms == 120.0
    assert payload.reconnects == 0
    # Round-trips through the contract model, so a schema change breaks here.
    assert payload.model_dump(mode="json")["state"] == "ok"


def test_fps_gap_is_declared_minus_measured():
    assert ProbeResult(source="x", declared_fps=25.0, measured_fps=6.0).fps_gap == 19.0


# --------------------------------------------------------------------------- #
# End to end, against a real file
# --------------------------------------------------------------------------- #


def test_probe_reads_a_real_clip(tmp_path: Path):
    clip = _write_clip(tmp_path / "clip.mp4", frames=30, size=(640, 360), fps=10.0)
    result = probe(str(clip), seconds=5.0)
    assert result.opened
    assert result.frames > 0
    assert (result.width, result.height) == (640, 360)
    assert result.state() == "ok"
    assert result.mean_brightness is not None


def test_probe_warns_when_the_clip_is_main_stream_sized(tmp_path: Path):
    clip = _write_clip(tmp_path / "big.mp4", frames=10, size=(1920, 1080), fps=10.0)
    result = probe(str(clip), seconds=5.0)
    assert any("main stream" in w for w in result.warnings)


def test_probe_flags_a_uniform_clip_as_tampered(tmp_path: Path):
    clip = _write_clip(tmp_path / "flat.mp4", frames=10, value=0)
    result = probe(str(clip), seconds=5.0)
    assert result.state() == "tampered"
    assert any("lens covered" in w for w in result.warnings)


def test_probe_reports_a_source_it_cannot_open(tmp_path: Path):
    result = probe(str(tmp_path / "does-not-exist.mp4"), seconds=1.0)
    assert result.opened is False
    assert result.error is not None
    assert "discover.py" in result.error  # point at the previous onboarding step


def test_probe_redacts_credentials_in_the_reported_source(monkeypatch):
    """The URL must be redacted before it reaches the result or the report.

    VideoCapture is stubbed rather than pointed at a real RTSP URL: an
    unroutable address makes OpenCV block on its connect timeout for over a
    minute, which would dominate the whole suite for no extra coverage.
    """
    class _NeverOpens:
        def __init__(self, *args, **kwargs):
            pass

        def isOpened(self):  # camelCase matches the cv2 API being stubbed
            return False

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", _NeverOpens)
    result = probe("rtsp://user:secret@192.0.2.10:554/none", seconds=0.2)
    assert result.opened is False
    assert "secret" not in result.source
    assert "user" not in result.source
    assert "192.0.2.10" in result.source  # the diagnosable part survives
    assert "secret" not in format_report(result)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_returns_zero_for_a_clean_clip(tmp_path: Path, capsys):
    clip = _write_clip(tmp_path / "ok.mp4", frames=30, size=(640, 360), fps=10.0)
    code = main([str(clip), "--seconds", "5"])
    out = capsys.readouterr().out
    assert "640x360" in out
    assert code in (0, 1)  # 1 if the encoder's declared fps drifts from measured
    if code == 0:
        assert "calibrate.py" in out


def test_cli_returns_two_when_the_source_will_not_open(tmp_path: Path, capsys):
    assert main([str(tmp_path / "nope.mp4")]) == 2
    assert "FAILED" in capsys.readouterr().out


def test_cli_emits_the_camera_health_payload(tmp_path: Path, capsys):
    clip = _write_clip(tmp_path / "ok.mp4", frames=30, size=(640, 360), fps=10.0)
    main([str(clip), "--seconds", "5", "--cam", "entrance", "--json"])
    payload = capsys.readouterr().out
    assert '"camera_health"' in payload
    assert '"cam": "entrance"' in payload


def test_cli_json_includes_the_state_and_gap(tmp_path: Path, capsys):
    clip = _write_clip(tmp_path / "ok.mp4", frames=20, size=(640, 360), fps=10.0)
    main([str(clip), "--seconds", "5", "--json"])
    out = capsys.readouterr().out
    assert '"state"' in out and '"fps_gap"' in out
    assert str(MAX_SUB_WIDTH) not in out  # the constant is not leaked into output
