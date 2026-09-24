"""go2rtc config generation: ports, stream names, and password handling.

Starting the real binary is covered by tools/e2e_smoke.py, not here - these
tests are about the config we hand it, which is where a wrong port or a leaked
password would come from.
"""

from __future__ import annotations

import json

import pytest

from storemind.ingest.go2rtc import Go2rtc, build_config, write_config
from storemind.ingest.urls import CameraEndpoint

SECRETS = {"entrance": {"username": "storemind", "password": "ro-pass"}}


def _cams() -> list[CameraEndpoint]:
    return [
        CameraEndpoint(name="cam1", brand="mediamtx", ip="127.0.0.1", channel=1),
        CameraEndpoint(name="entrance", brand="hikvision", ip="192.168.1.64", channel=1),
    ]


def test_config_lists_every_camera_as_a_stream():
    config = build_config(_cams(), SECRETS)
    assert config["streams"]["cam1"] == ["rtsp://127.0.0.1:8554/cam1"]
    assert config["streams"]["entrance"] == [
        "rtsp://storemind:ro-pass@192.168.1.64:554/Streaming/Channels/102"
    ]


def test_go2rtc_avoids_the_mediamtx_port():
    # 8554 is MediaMTX's (tools/fake_cctv.py); go2rtc must not fight it for the
    # port, or the demo in research/24 section 9 cannot run.
    config = build_config(_cams())
    assert config["rtsp"]["listen"] == "127.0.0.1:8564"
    assert config["api"]["listen"] == "127.0.0.1:1984"


def test_webrtc_is_disabled():
    # Nothing local needs it, and leaving it on holds port 8555 for no reason.
    assert build_config(_cams())["webrtc"]["listen"] == ""


def test_everything_listens_on_loopback_only():
    # research/24 section 8: no port forwarding, nothing exposed on the shop LAN
    # beyond the dashboard.
    config = build_config(_cams())
    for section in ("api", "rtsp"):
        assert config[section]["listen"].startswith("127.0.0.1:")


def test_camera_without_a_secret_gets_a_credential_free_url():
    config = build_config(_cams(), SECRETS)
    assert "@" not in config["streams"]["cam1"]


@pytest.mark.parametrize("name", ["bad name", "has/slash", "", "a" * 65, "qu?ery"])
def test_rejects_names_that_would_break_the_url(name: str):
    cams = [CameraEndpoint(name=name, brand="tapo", ip="192.0.2.10")]
    with pytest.raises(ValueError, match="camera name"):
        build_config(cams)


def test_rejects_duplicate_camera_names():
    cams = [CameraEndpoint(name="a", brand="tapo", ip="192.0.2.10"),
            CameraEndpoint(name="a", brand="tapo", ip="192.0.2.11")]
    with pytest.raises(ValueError, match="duplicate"):
        build_config(cams)


def test_written_config_is_json_that_go2rtc_can_read(tmp_path):
    path = write_config(build_config(_cams(), SECRETS), tmp_path / "rt" / "go2rtc.yaml")
    # JSON is valid YAML, which is why no YAML dependency is needed here.
    assert json.loads(path.read_text(encoding="utf-8"))["streams"]["cam1"]


def test_describe_masks_passwords():
    lines = Go2rtc(_cams(), SECRETS).describe()
    assert all("ro-pass" not in line for line in lines)
    assert any("rtsp://127.0.0.1:8564/entrance" in line for line in lines)


def test_stream_url_points_at_the_gateway():
    assert Go2rtc(_cams()).stream_url("entrance") == "rtsp://127.0.0.1:8564/entrance"


def test_start_without_a_binary_says_where_to_put_one():
    with pytest.raises(FileNotFoundError, match="tools/bin"):
        Go2rtc(_cams(), binary=None, workdir=None).start()  # type: ignore[arg-type]


def test_alive_is_false_before_start():
    assert Go2rtc(_cams()).alive() is False
