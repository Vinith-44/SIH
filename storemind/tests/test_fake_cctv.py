"""Fake CCTV: the ffmpeg command and binary lookup, without the binaries.

Starting MediaMTX and ffmpeg is what tools/e2e_smoke.py does; it needs the two
binaries in tools/bin and so cannot run in a plain checkout. What *can* be
pinned here is the part that silently ruins a demo if it drifts: the encoding
flags that make a CAVIAR .mpg, an .mp4 and an .avi all play the same way.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str):
    # By file path: the repo also has a top-level tools/ folder, so
    # "import tools.x" would be ambiguous.
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fake_cctv = _load("fake_cctv")


def test_file_source_loops_forever():
    cmd = fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/cam1", Path("clip.mp4"))
    assert "-stream_loop" in cmd and cmd[cmd.index("-stream_loop") + 1] == "-1"
    # -re paces the file at real speed; without it ffmpeg blasts the whole clip
    # in a second and the "camera" is a burst, not a stream.
    assert "-re" in cmd


def test_test_pattern_needs_no_file():
    cmd = fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/cam1", None, fps=15,
                                       size="640x360")
    assert "lavfi" in cmd
    assert any("testsrc2=size=640x360:rate=15" in part for part in cmd)


def test_reencodes_without_b_frames():
    # CAVIAR ships MPEG-1; copying the codec through would make some sources
    # play and others not. Re-encoding to H.264 with -bf 0 makes them uniform,
    # and zerolatency keeps the lag honest for queue timing.
    cmd = fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/cam1", Path("a.mpg"))
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-bf") + 1] == "0"
    assert cmd[cmd.index("-tune") + 1] == "zerolatency"


def test_publishes_over_tcp():
    # research/24 section 6: RTSP over TCP everywhere; UDP drops frames on a
    # busy LAN and the demo looks broken.
    cmd = fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/cam1", None)
    assert cmd[cmd.index("-rtsp_transport") + 1] == "tcp"
    assert cmd[-1] == "rtsp://x/cam1"


def test_keyframe_interval_follows_the_frame_rate():
    # research/24 section 5 step 5 asks for an I-frame interval of about 2x FPS.
    cmd = fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/cam1", None, fps=10)
    assert cmd[cmd.index("-g") + 1] == "20"


def test_audio_is_dropped():
    # Nothing in StoreMind listens, and CLAUDE.md privacy rules would make
    # carrying shop audio around a liability.
    assert "-an" in fake_cctv.ffmpeg_publish_cmd("ffmpeg", "rtsp://x/c", None)


def test_missing_binary_says_where_to_put_it(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="not found"):
        fake_cctv.find_binary("definitely-not-a-real-binary", tmp_path)


def test_urls_follow_the_camN_convention():
    cctv = fake_cctv.FakeCCTV([None, None], port=8554)
    assert cctv.url(1) == "rtsp://127.0.0.1:8554/cam1"
    assert cctv.url(2) == "rtsp://127.0.0.1:8554/cam2"


def test_mediamtx_config_disables_everything_but_rtsp():
    config = fake_cctv.MEDIAMTX_CONFIG.format(port=8554)
    assert "rtspAddress: :8554" in config
    for protocol in ("rtmp", "hls", "webrtc", "srt"):
        assert f"{protocol}: no" in config


def test_wait_for_port_gives_up_rather_than_hanging():
    with pytest.raises(TimeoutError, match="nothing listening"):
        fake_cctv.wait_for_port("127.0.0.1", 1, timeout_s=0.5)
