"""Camera URL templates, and a contract test over docs/CCTV_ONBOARDING.md.

The cheat-sheet in the doc is what an installer reads, so it is the thing that
must not drift.  Every line of it is replayed through the builder here, the same
way test_protocol.py replays docs/PROTOCOL.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from storemind.ingest.urls import (
    BRANDS,
    GO2RTC_RTSP_PORT,
    SCAN_PORTS,
    CameraEndpoint,
    UnknownBrandError,
    brands,
    go2rtc_snapshot_url,
    go2rtc_stream_url,
    go2rtc_streams,
    parse_source,
    redact,
    resolve_brand,
)

DOC = Path(__file__).resolve().parents[2] / "docs" / "CCTV_ONBOARDING.md"
DOC_IP = "192.0.2.10"

# "brand channel stream url", as written in the doc's ```text blocks.
ROW = re.compile(r"^(\S+)\s+(\d+)\s+(main|sub)\s+(\S+)$")


def _doc_rows(scheme: str) -> list[tuple[str, int, str, str]]:
    text = DOC.read_text(encoding="utf-8")
    rows: list[tuple[str, int, str, str]] = []
    for block in re.findall(r"```text\n(.*?)```", text, flags=re.DOTALL):
        for line in block.splitlines():
            match = ROW.match(line.strip())
            if match and match.group(4).startswith(f"{scheme}://"):
                rows.append((match.group(1), int(match.group(2)), match.group(3), match.group(4)))
    return rows


def test_doc_has_rows_to_check():
    # A typo in the fence or the table would otherwise make the contract tests
    # below pass by checking nothing at all.
    assert len(_doc_rows("rtsp")) >= 10
    assert len(_doc_rows("http")) >= 3


@pytest.mark.parametrize(("brand", "channel", "stream", "expected"), _doc_rows("rtsp"))
def test_doc_stream_urls_match_builder(brand: str, channel: int, stream: str, expected: str):
    cam = CameraEndpoint(name="cam", brand=brand, ip=DOC_IP, channel=channel, stream=stream)
    assert cam.stream_url() == expected


@pytest.mark.parametrize(("brand", "channel", "stream", "expected"), _doc_rows("http"))
def test_doc_snapshot_urls_match_builder(brand: str, channel: int, stream: str, expected: str):
    cam = CameraEndpoint(name="cam", brand=brand, ip=DOC_IP, channel=channel, stream=stream)
    assert cam.snapshot_url() == expected


def test_sub_stream_is_the_default():
    # research/24 section 6 decodes sub-streams only; a default of 'main' would
    # quietly quadruple the decode cost of every new camera.
    assert CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP).stream == "sub"
    assert "subtype=1" in CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP).stream_url()


def test_credentials_are_inlined_and_percent_encoded():
    cam = CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP)
    url = cam.stream_url("storemind", "p@ss/word:1")
    # An unescaped '@' would end the userinfo early and point RTSP at host
    # "ss/word:1", which fails with a DNS error rather than an auth error.
    assert url.startswith("rtsp://storemind:p%40ss%2Fword%3A1@192.0.2.10:554/")
    assert url.count("@") == 1


def test_username_without_password_is_allowed():
    url = CameraEndpoint(name="c", brand="tapo", ip=DOC_IP).stream_url("viewer")
    assert url == "rtsp://viewer@192.0.2.10:554/stream2"


def test_endpoint_holds_no_credentials():
    # A config dump or a repr() in a log must not be able to leak a password.
    cam = CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP)
    cam.stream_url("storemind", "ro-pass")
    assert "ro-pass" not in repr(cam)


@pytest.mark.parametrize("url", [
    "rtsp://storemind:ro-pass@192.0.2.10:554/cam/realmonitor?channel=1&subtype=1",
    "rtsp://viewer@192.0.2.10:554/stream2",
    "http://storemind:ro-pass@192.0.2.10/cgi-bin/snapshot.cgi?channel=1",
])
def test_redact_removes_credentials(url: str):
    clean = redact(url)
    assert "ro-pass" not in clean
    assert "storemind" not in clean
    assert "viewer" not in clean
    assert "192.0.2.10" in clean  # the useful part survives


def test_redact_leaves_a_clean_url_alone():
    url = "rtsp://127.0.0.1:8554/entrance"
    assert redact(url) == url


def test_non_default_port_is_kept_and_default_is_omitted():
    # Some CP Plus recorders answer on 5543 (research/24 section 3).
    odd = CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP, port=5543)
    assert odd.stream_url().startswith("rtsp://192.0.2.10:5543/")
    plain = CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP, http_port=80)
    assert plain.snapshot_url() == f"http://{DOC_IP}/cgi-bin/snapshot.cgi?channel=1"


def test_path_override_wins_over_the_template():
    cam = CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP, path="/live/channel0")
    assert cam.stream_url() == f"rtsp://{DOC_IP}:554/live/channel0"
    # A leading slash is optional in the config; the URL must not gain '//'.
    assert CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP,
                          path="live/channel0").stream_url().endswith("/live/channel0")


def test_tapo_has_no_http_snapshot():
    cam = CameraEndpoint(name="shelf", brand="tapo", ip=DOC_IP)
    assert cam.snapshot_url() is None
    assert go2rtc_snapshot_url("shelf").endswith("?src=shelf")


def test_aliases_resolve_to_the_same_template():
    assert resolve_brand("cpplus") is resolve_brand("dahua")
    assert resolve_brand("CP-Plus") is resolve_brand("dahua")
    assert resolve_brand("Prama") is resolve_brand("hikvision")
    assert resolve_brand(" hikvision ") is BRANDS["hikvision"]


def test_unknown_brand_names_the_known_ones():
    with pytest.raises(UnknownBrandError) as caught:
        CameraEndpoint(name="c", brand="acme", ip=DOC_IP).stream_url()
    message = str(caught.value)
    assert "acme" in message
    for brand in ("cpplus", "hikvision", "tapo"):
        assert brand in message
    assert "discover.py" in message  # tell the installer what to do next


def test_brands_lists_aliases_too():
    listed = brands()
    assert "cpplus" in listed and "prama" in listed and "dahua" in listed


@pytest.mark.parametrize(("channel", "stream"), [(0, "sub"), (-1, "sub")])
def test_bad_channel_is_rejected(channel: int, stream: str):
    with pytest.raises(ValueError, match="channel"):
        CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP, channel=channel, stream=stream)


def test_bad_stream_and_empty_ip_are_rejected():
    with pytest.raises(ValueError, match="stream"):
        CameraEndpoint(name="c", brand="cpplus", ip=DOC_IP, stream="substream")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ip"):
        CameraEndpoint(name="c", brand="cpplus", ip="   ")


def test_go2rtc_urls_point_at_the_gateway_not_the_dvr():
    assert go2rtc_stream_url("entrance") == f"rtsp://127.0.0.1:{GO2RTC_RTSP_PORT}/entrance"
    assert go2rtc_snapshot_url("entrance") == (
        "http://127.0.0.1:1984/api/frame.jpeg?src=entrance"
    )


def test_go2rtc_streams_applies_per_camera_credentials():
    cameras = [
        CameraEndpoint(name="entrance", brand="cpplus", ip="192.0.2.10", channel=1),
        CameraEndpoint(name="counter", brand="hikvision", ip="192.0.2.11", channel=2),
        CameraEndpoint(name="shelf", brand="tapo", ip="192.0.2.12"),
    ]
    secrets = {
        "entrance": {"username": "storemind", "password": "ro-pass"},
        "counter": {"username": "storemind", "password": "other"},
    }
    streams = go2rtc_streams(cameras, secrets)
    assert set(streams) == {"entrance", "counter", "shelf"}
    assert streams["entrance"].startswith("rtsp://storemind:ro-pass@192.0.2.10:554/cam/")
    assert streams["counter"].endswith("/Streaming/Channels/202")
    # No secret for the shelf camera: a URL without credentials, not a crash.
    assert streams["shelf"] == "rtsp://192.0.2.12:554/stream2"


def test_go2rtc_streams_rejects_duplicate_names():
    cameras = [
        CameraEndpoint(name="entrance", brand="cpplus", ip="192.0.2.10"),
        CameraEndpoint(name="entrance", brand="cpplus", ip="192.0.2.11"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        go2rtc_streams(cameras)


def test_go2rtc_streams_without_secrets_is_credential_free():
    streams = go2rtc_streams([CameraEndpoint(name="e", brand="cpplus", ip="192.0.2.10")])
    assert "@" not in streams["e"]


@pytest.mark.parametrize(("source", "brand", "channel", "stream"), [
    ("rtsp://u:p@192.0.2.10:554/cam/realmonitor?channel=2&subtype=1", "dahua", 2, "sub"),
    ("rtsp://192.0.2.10:554/cam/realmonitor?channel=1&subtype=0", "dahua", 1, "main"),
    ("rtsp://192.0.2.10:554/Streaming/Channels/302", "hikvision", 3, "sub"),
    ("rtsp://192.0.2.10:554/Streaming/Channels/101", "hikvision", 1, "main"),
])
def test_parse_source_recovers_hand_typed_urls(source: str, brand: str, channel: int,
                                               stream: str):
    cam = parse_source(source)
    assert cam is not None
    assert (cam.brand, cam.channel, cam.stream) == (brand, channel, stream)
    assert cam.ip == "192.0.2.10"


@pytest.mark.parametrize("source", [
    "videos/entrance/synthetic_entrance.mp4",  # file replay
    "0",                                       # USB index
    "csi:0",                                   # Pi camera
    "http://192.0.2.10:8080/video",            # phone camera
    "rtsp://192.0.2.10:554/some/unknown/path",
])
def test_parse_source_returns_none_for_everything_else(source: str):
    # open_source() already handles these; guessing a brand would be worse than
    # admitting we do not know.
    assert parse_source(source) is None


def test_parse_source_round_trips_what_we_generate():
    original = CameraEndpoint(name="c", brand="cpplus", ip="192.0.2.10", channel=4)
    again = parse_source(original.stream_url())
    assert again is not None
    assert again.stream_url() == original.stream_url()


def test_scan_ports_cover_the_documented_set():
    for port in (554, 80, 2020, 5543, 8000, 37777):
        assert port in SCAN_PORTS
