"""go2rtc config generator and process manager (M2, research/24 section 6).

go2rtc keeps **one** connection to each camera and re-shares it locally, so the
pipeline, the dashboard and snapshots can all read a camera without opening
extra sessions.  That is not a nicety: section 6 records that NVRs refuse
clients once their limit is hit, and small DVRs allow far fewer than big ones.
Everything downstream therefore reads `rtsp://127.0.0.1:8564/<camera-name>`
instead of the recorder.

Ports: go2rtc's RTSP server defaults to 8554, which is also MediaMTX's default
and so what `tools/fake_cctv.py` uses.  `urls.GO2RTC_RTSP_PORT` moves go2rtc to
8564 so the demo can run both at once.  The API stays on 127.0.0.1:1984.

The generated config contains camera passwords, so it is written into a
git-ignored `runtime/` folder and never logged - `describe()` is the safe way
to print what is configured.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .urls import (
    GO2RTC_API_PORT,
    GO2RTC_RTSP_PORT,
    CameraEndpoint,
    go2rtc_streams,
    redact,
)

# go2rtc stream names end up in URLs and in the config file; keep them to
# characters that need no escaping in either.
_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

Credentials = dict[str, dict[str, str]]


def build_config(cameras: list[CameraEndpoint], credentials: Credentials | None = None,
                 api_port: int = GO2RTC_API_PORT, rtsp_port: int = GO2RTC_RTSP_PORT,
                 log_level: str = "warn") -> dict:
    """The go2rtc config, as a dict.  Stream names are the camera names.

    `credentials` is shaped like `load_secrets()["cameras"]`, so the passwords
    arrive at the moment the config is written and are not held anywhere else.
    """
    for cam in cameras:
        if not _NAME_OK.match(cam.name):
            raise ValueError(
                f"camera name {cam.name!r}: use letters, digits, _ or - only "
                "(the name becomes a URL path)"
            )
    return {
        "log": {"level": log_level},
        "api": {"listen": f"127.0.0.1:{api_port}"},
        "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
        # Nothing local needs WebRTC, and turning it off frees port 8555.
        "webrtc": {"listen": ""},
        "streams": {name: [url] for name, url in go2rtc_streams(cameras, credentials).items()},
    }


def write_config(config: dict, path: Path) -> Path:
    """Write the config.  JSON is valid YAML, so go2rtc reads it as-is.

    Using JSON avoids a YAML dependency *and* avoids yaml.safe_dump quoting
    surprises around passwords containing `:` or `#`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


@dataclass
class Go2rtc:
    """Run go2rtc as a child process for a list of cameras."""

    cameras: list[CameraEndpoint]
    credentials: Credentials | None = None
    binary: str | None = None
    workdir: Path = Path("runtime")
    api_port: int = GO2RTC_API_PORT
    rtsp_port: int = GO2RTC_RTSP_PORT
    proc: subprocess.Popen | None = field(default=None, init=False)

    def stream_url(self, name: str) -> str:
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{name}"

    def start(self, ready_timeout_s: float = 10.0) -> None:
        exe = self.binary or shutil.which("go2rtc")
        if not exe:
            raise FileNotFoundError(
                "go2rtc not found: put the binary in tools/bin (git-ignored) or pass "
                "binary=<path>."
            )
        conf = write_config(
            build_config(self.cameras, self.credentials, self.api_port, self.rtsp_port),
            self.workdir / "go2rtc.yaml",
        ).resolve()
        # Run from the config's own folder, so anything go2rtc writes beside its
        # config lands in the git-ignored runtime/ rather than the repo root.
        self.proc = subprocess.Popen(
            [exe, "-config", str(conf)], stdin=subprocess.DEVNULL, cwd=str(conf.parent),
        )
        deadline = time.monotonic() + ready_timeout_s
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"go2rtc exited early with code {self.proc.returncode}")
            if self.streams() is not None:
                return
            time.sleep(0.2)
        self.stop()
        raise TimeoutError(
            f"go2rtc did not answer on 127.0.0.1:{self.api_port} within {ready_timeout_s}s"
        )

    def streams(self) -> dict | None:
        """`GET /api/streams`, or None when the API is not reachable yet."""
        try:
            url = f"http://127.0.0.1:{self.api_port}/api/streams"
            with urllib.request.urlopen(url, timeout=1.0) as response:
                return json.loads(response.read() or b"{}")
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ensure_running(self) -> bool:
        """Restart go2rtc if it died.  True when a restart happened."""
        if self.alive():
            return False
        self.start()
        return True

    def describe(self) -> list[str]:
        """What is configured, with passwords masked - safe to log."""
        streams = go2rtc_streams(self.cameras, self.credentials)
        return [f"{name}: {redact(url)} -> {self.stream_url(name)}"
                for name, url in streams.items()]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    def __enter__(self) -> Go2rtc:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
