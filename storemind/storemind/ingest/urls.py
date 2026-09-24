"""Camera URL templates (research/24 sections 3 and 6).

An installer should never have to type an RTSP URL.  They tell us the brand,
the IP and which channel, and we build the URL from the cheat-sheet in
research/24 section 3::

    cam = CameraEndpoint(brand="cpplus", ip="192.168.1.108", channel=2)
    cam.stream_url(username="storemind", password="ro-pass")
    # rtsp://storemind:ro-pass@192.168.1.108:554/cam/realmonitor?channel=2&subtype=1

Three rules the templates exist to enforce:

*   **Sub-stream by default.**  `stream="sub"` is the default everywhere because
    section 6 decodes sub-streams only; a main stream on four cameras would
    saturate the Pi.  Asking for `stream="main"` is possible but deliberate.
*   **Credentials are arguments, never state.**  `CameraEndpoint` holds no
    password.  They arrive from `load_secrets()` at call time, so a config dump
    or a log line cannot leak one.  `redact()` is there for anything that *is*
    logged.
*   **go2rtc owns the camera connection.**  Section 6: the recorder allows few
    clients, so go2rtc connects once and everything else reads the restream.
    `go2rtc_streams()` turns endpoints into the `streams:` block of
    go2rtc.yaml; the pipeline then opens `go2rtc_stream_url(name)`.

Firmware varies, so every template is a starting point to confirm on the actual
device (section 3 says so twice).  `path` overrides the template for the models
that disagree - some CP Plus units want `/live/channel0`, older Hikvision wants
`/h264/ch1/sub/av_stream` (available as brand `hikvision_legacy`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import quote, urlsplit, urlunsplit

Stream = Literal["main", "sub"]

DEFAULT_RTSP_PORT = 554
DEFAULT_HTTP_PORT = 80

# The go2rtc gateway, as configured in section 6.  1984 is go2rtc's own API
# port; 8554 is the RTSP it republishes on.
GO2RTC_HOST = "127.0.0.1"
GO2RTC_RTSP_PORT = 8554
GO2RTC_API_PORT = 1984


@dataclass(frozen=True)
class BrandTemplate:
    """One row of the research/24 section 3 cheat-sheet.

    `stream_path` and `snapshot_path` are `str.format` templates over
    `channel`, `subtype` (0 main / 1 sub, Dahua's numbering) and `code`
    (Hikvision's `<channel>0<1|2>`).
    """

    stream_path: str
    snapshot_path: str | None = None
    rtsp_port: int = DEFAULT_RTSP_PORT
    http_port: int = DEFAULT_HTTP_PORT
    note: str = ""

    def has_snapshot(self) -> bool:
        return self.snapshot_path is not None


# Confirm against the device; see research/24 section 3 for sources per brand.
BRANDS: dict[str, BrandTemplate] = {
    # Hikvision and Prama (Prama is Hikvision's Indian arm) share firmware.
    "hikvision": BrandTemplate(
        stream_path="/Streaming/Channels/{code}",
        snapshot_path="/ISAPI/Streaming/channels/{channel}01/picture",
        note="digest auth; code is <channel>01 main / <channel>02 sub",
    ),
    # Older Hikvision firmware predates the /Streaming/Channels scheme.
    "hikvision_legacy": BrandTemplate(
        stream_path="/h264/ch{channel}/{quality}/av_stream",
        snapshot_path="/ISAPI/Streaming/channels/{channel}01/picture",
        note="firmware before the ISAPI URL scheme",
    ),
    # CP Plus is the Indian market leader and most of its recorders speak Dahua.
    "dahua": BrandTemplate(
        stream_path="/cam/realmonitor?channel={channel}&subtype={subtype}",
        snapshot_path="/cgi-bin/snapshot.cgi?channel={channel}",
        note="subtype 0 = main, 1 = sub",
    ),
    "tapo": BrandTemplate(
        stream_path="/stream{stream_index}",
        snapshot_path=None,  # no HTTP snapshot; go2rtc's frame API instead
        note="needs a Camera Account in the Tapo app; ONVIF on 2020",
    ),
}

# Same firmware, different badge.  Kept separate from BRANDS so `brands()`
# reports what an installer can pick without listing every synonym.
ALIASES: dict[str, str] = {
    "prama": "hikvision",
    "cpplus": "dahua",
    "cp-plus": "dahua",
    "cp_plus": "dahua",
}

# Ports worth scanning on a shop network (research/24 section 3).
SCAN_PORTS: tuple[int, ...] = (554, 80, 443, 2020, 5543, 8000, 37777)


class UnknownBrandError(ValueError):
    """Raised for a brand with no template, with the supported list attached."""


def brands() -> list[str]:
    """Brand names an installer may write in store.yaml, aliases included."""
    return sorted([*BRANDS, *ALIASES])


def resolve_brand(brand: str) -> BrandTemplate:
    key = brand.strip().lower().replace(" ", "")
    key = ALIASES.get(key, key)
    try:
        return BRANDS[key]
    except KeyError:
        raise UnknownBrandError(
            f"no URL template for brand {brand!r}; known brands: {', '.join(brands())}. "
            "Use brand 'generic' with an explicit source, or run tools/discover.py "
            "to read the stream URI over ONVIF."
        ) from None


@dataclass
class CameraEndpoint:
    """How to reach one camera channel, before credentials are applied.

    `name` is the key everything else uses: the go2rtc stream name, the health
    panel label and the `cameras[].name` in store.yaml.
    """

    name: str
    brand: str
    ip: str
    channel: int = 1
    stream: Stream = "sub"
    port: int | None = None          # override the brand's RTSP port
    http_port: int | None = None
    path: str | None = None          # override the template outright
    role: str = "generic"
    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.channel < 1:
            raise ValueError(f"camera {self.name!r}: channel must be >= 1, got {self.channel}")
        if self.stream not in ("main", "sub"):
            raise ValueError(
                f"camera {self.name!r}: stream must be 'main' or 'sub', got {self.stream!r}"
            )
        if not self.ip.strip():
            raise ValueError(f"camera {self.name!r}: ip must not be empty")

    @property
    def template(self) -> BrandTemplate:
        return resolve_brand(self.brand)

    def _substitutions(self) -> dict[str, object]:
        sub = self.stream == "sub"
        return {
            "channel": self.channel,
            # Dahua: subtype 1 is the sub-stream.  Hikvision: <channel>02.
            "subtype": 1 if sub else 0,
            "code": f"{self.channel}0{2 if sub else 1}",
            "quality": "sub" if sub else "main",
            # Tapo numbers its streams from 1, high quality first.
            "stream_index": 2 if sub else 1,
            **self.extra,
        }

    def stream_path(self) -> str:
        if self.path is not None:
            return self.path if self.path.startswith("/") else f"/{self.path}"
        return self.template.stream_path.format(**self._substitutions())

    def snapshot_path(self) -> str | None:
        template = self.template.snapshot_path
        if template is None:
            return None
        return template.format(**self._substitutions())

    def stream_url(self, username: str | None = None, password: str | None = None) -> str:
        """RTSP URL for this channel, credentials inline if given.

        Inline credentials are what OpenCV and go2rtc both accept, so this is
        the one place a password ends up in a string - keep it out of logs and
        pass it through `redact()` if it has to be printed.
        """
        port = self.port if self.port is not None else self.template.rtsp_port
        return _build_url("rtsp", self.ip, port, self.stream_path(), username, password)

    def snapshot_url(self, username: str | None = None, password: str | None = None) -> str | None:
        """HTTP JPEG snapshot URL, or None when the brand has no snapshot API.

        Shelves use this: research/24 section 2 method C, one frame every 1-5
        minutes instead of a decoded video stream.  When it returns None, read
        `go2rtc_snapshot_url(name)` instead.
        """
        path = self.snapshot_path()
        if path is None:
            return None
        port = self.http_port if self.http_port is not None else self.template.http_port
        return _build_url("http", self.ip, port, path, username, password)


def _build_url(scheme: str, host: str, port: int, path: str, username: str | None,
               password: str | None) -> str:
    """Assemble a URL, percent-encoding credentials.

    DVR passwords routinely contain `@`, `/` and `:`, which silently truncate a
    hand-built URL at the wrong place - so quote them rather than interpolate.
    """
    netloc = host if _is_default_port(scheme, port) else f"{host}:{port}"
    if username:
        # safe="" so every reserved character is escaped, `@` included.
        credentials = quote(username, safe="")
        if password:
            credentials += f":{quote(password, safe='')}"
        netloc = f"{credentials}@{netloc}"
    path, _, query = path.partition("?")
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_default_port(scheme: str, port: int) -> bool:
    """Only `http://host:80` is worth shortening.

    `:554` stays in RTSP URLs even though it is the default, because that is how
    every vendor writes it and how an installer will recognise the URL when
    comparing ours against the recorder's own web UI.
    """
    return scheme == "http" and port == DEFAULT_HTTP_PORT


_CREDENTIALS = re.compile(r"(?<=//)[^/@\s]+(?=@)")


def redact(url: str) -> str:
    """Replace inline credentials with `***` so a URL is safe to log.

    Every log line, health event and error message that carries a camera URL
    goes through this.  research/24 section 8: credentials encrypted at rest,
    and a log file is not at rest.
    """
    return _CREDENTIALS.sub("***", url)


def go2rtc_stream_url(name: str, *, host: str = GO2RTC_HOST,
                      port: int = GO2RTC_RTSP_PORT) -> str:
    """Where the pipeline reads a camera from: go2rtc's restream, not the DVR."""
    return f"rtsp://{host}:{port}/{name}"


def go2rtc_snapshot_url(name: str, *, host: str = GO2RTC_HOST,
                        port: int = GO2RTC_API_PORT) -> str:
    """go2rtc's JPEG frame API - the snapshot path for brands without one."""
    return f"http://{host}:{port}/api/frame.jpeg?src={name}"


def go2rtc_streams(cameras: list[CameraEndpoint],
                   credentials: dict[str, dict[str, str]] | None = None) -> dict[str, str]:
    """The `streams:` mapping for go2rtc.yaml, one entry per camera.

    `credentials` is shaped like `load_secrets()["cameras"]`:
    `{camera_name: {"username": ..., "password": ...}}`.  Names missing from it
    produce a URL with no credentials, which is correct for a camera that needs
    none and obvious in go2rtc's log when it needs some.

    The result still contains passwords - it is written to go2rtc.yaml, which is
    git-ignored - so do not log it.  Use `redact()` per value if you must.
    """
    creds = credentials or {}
    streams: dict[str, str] = {}
    for cam in cameras:
        if cam.name in streams:
            raise ValueError(f"duplicate camera name {cam.name!r} in go2rtc streams")
        entry = creds.get(cam.name) or {}
        streams[cam.name] = cam.stream_url(entry.get("username"), entry.get("password"))
    return streams


def parse_source(source: str) -> CameraEndpoint | None:
    """Recover an endpoint from an RTSP URL already written in a config.

    Stores onboarded before the templates existed have a hand-typed
    `cameras[].source`.  This recognises the two URL shapes we generate so such
    a camera can still be listed in go2rtc and shown on the health panel.
    Returns None for anything else - a file path, a USB index, `csi:0` - which
    `open_source()` already handles.
    """
    parts = urlsplit(source.strip())
    if parts.scheme != "rtsp" or not parts.hostname:
        return None
    query = parts.query
    path = parts.path
    if "realmonitor" in path:
        channel = _int_param(query, "channel", 1)
        subtype = _int_param(query, "subtype", 1)
        brand, stream = "dahua", ("sub" if subtype == 1 else "main")
    elif path.startswith("/Streaming/Channels/"):
        code = path.rsplit("/", 1)[-1]
        if not code.isdigit() or len(code) < 3:
            return None
        channel, stream = int(code[:-2]), ("sub" if code.endswith("02") else "main")
        brand = "hikvision"
    else:
        return None
    return CameraEndpoint(
        name=parts.hostname,
        brand=brand,
        ip=parts.hostname,
        channel=channel,
        stream=stream,
        port=parts.port,
    )


def _int_param(query: str, key: str, default: int) -> int:
    match = re.search(rf"(?:^|&){re.escape(key)}=(\d+)", query)
    return int(match.group(1)) if match else default
