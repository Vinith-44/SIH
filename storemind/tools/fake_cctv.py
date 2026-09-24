"""Fake CCTV: serve looping test videos as RTSP cameras via MediaMTX + ffmpeg (M2).

    python storemind/tools/fake_cctv.py --video a.mp4 --video b.mp4
    -> rtsp://127.0.0.1:8554/cam1, rtsp://127.0.0.1:8554/cam2  (Ctrl+C to stop)

    python storemind/tools/fake_cctv.py --testsrc 2   # no video files needed

Binaries are looked up in tools/bin (gitignored), then on PATH.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BIN_DIR = REPO_ROOT / "tools" / "bin"

MEDIAMTX_CONFIG = """\
logLevel: warn
rtspAddress: :{port}
rtmp: no
hls: no
webrtc: no
srt: no
paths:
  all_others:
"""


def find_binary(name: str, bin_dir: Path = DEFAULT_BIN_DIR) -> str:
    """Find a binary in tools/bin first (handles .exe on Windows), then PATH."""
    found = shutil.which(name, path=str(bin_dir)) or shutil.which(name)
    if not found:
        raise FileNotFoundError(
            f"{name} not found in {bin_dir} or on PATH. "
            f"Download it and put it in {bin_dir}."
        )
    return found


def wait_for_port(host: str, port: int, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"nothing listening on {host}:{port} after {timeout_s}s")


def ffmpeg_publish_cmd(ffmpeg: str, url: str, video: Path | None, fps: int = 15,
                       size: str = "640x360") -> list[str]:
    """ffmpeg command that loops a file (or a test pattern) into an RTSP URL.

    Re-encodes to H.264 with no B-frames so every source (CAVIAR MPEG, MP4, AVI)
    plays reliably through MediaMTX and go2rtc.
    """
    if video is None:
        src = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}"]
    else:
        src = ["-re", "-stream_loop", "-1", "-i", str(video)]
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", *src,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-pix_fmt", "yuv420p", "-bf", "0", "-g", str(fps * 2), "-r", str(fps),
        "-f", "rtsp", "-rtsp_transport", "tcp", url,
    ]


class FakeCCTV:
    """Start MediaMTX and one ffmpeg publisher per camera; stop them cleanly."""

    def __init__(self, videos: list[Path | None], port: int = 8554,
                 bin_dir: Path = DEFAULT_BIN_DIR, fps: int = 15) -> None:
        self.videos = videos
        self.port = port
        self.bin_dir = bin_dir
        self.fps = fps
        self.mediamtx: subprocess.Popen | None = None
        self.publishers: dict[str, subprocess.Popen] = {}
        self._tmp = tempfile.TemporaryDirectory(prefix="fake_cctv_")

    def url(self, index: int) -> str:
        return f"rtsp://127.0.0.1:{self.port}/cam{index}"

    def start(self) -> list[str]:
        conf = Path(self._tmp.name) / "mediamtx.yml"
        conf.write_text(MEDIAMTX_CONFIG.format(port=self.port), encoding="utf-8")
        # Run MediaMTX from the temporary config folder.  Its MoQ module
        # generates a self-signed auto.crt/auto.key in the working directory on
        # first start, and with the inherited cwd that dropped an EC *private
        # key* into the repo root - one `git add .` from being committed.  In
        # the temp folder it is thrown away with everything else.
        self.mediamtx = subprocess.Popen(
            [find_binary("mediamtx", self.bin_dir), str(conf)], cwd=self._tmp.name,
        )
        wait_for_port("127.0.0.1", self.port)
        for i in range(1, len(self.videos) + 1):
            self.start_camera(i)
        return [self.url(i) for i in range(1, len(self.videos) + 1)]

    def start_camera(self, index: int) -> None:
        """(Re)start the publisher for camN. Used by e2e tests to simulate outages."""
        ffmpeg = find_binary("ffmpeg", self.bin_dir)
        cmd = ffmpeg_publish_cmd(ffmpeg, self.url(index), self.videos[index - 1], self.fps)
        self.publishers[f"cam{index}"] = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)

    def stop_camera(self, index: int) -> None:
        proc = self.publishers.pop(f"cam{index}", None)
        if proc:
            _terminate(proc)

    def check(self) -> None:
        """Raise if MediaMTX died; restart any publisher that exited."""
        if self.mediamtx and self.mediamtx.poll() is not None:
            raise RuntimeError(f"mediamtx exited with code {self.mediamtx.returncode}")
        for name, proc in list(self.publishers.items()):
            if proc.poll() is not None:
                print(f"[fake_cctv] {name} publisher exited ({proc.returncode}), restarting")
                self.start_camera(int(name[3:]))

    def stop(self) -> None:
        for proc in self.publishers.values():
            _terminate(proc)
        self.publishers.clear()
        if self.mediamtx:
            _terminate(self.mediamtx)
            self.mediamtx = None
        self._tmp.cleanup()

    def __enter__(self) -> FakeCCTV:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def _terminate(proc: subprocess.Popen, timeout_s: float = 5.0) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--video", action="append", type=Path, default=[],
                    help="video file (repeat for more cameras)")
    ap.add_argument("--testsrc", type=int, default=0, help="number of test-pattern cameras to add")
    ap.add_argument("--port", type=int, default=8554)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR)
    args = ap.parse_args(argv)

    for v in args.video:
        if not v.is_file():
            ap.error(f"video not found: {v}")
    videos: list[Path | None] = [*args.video, *([None] * args.testsrc)]
    if not videos:
        ap.error("give at least one --video or --testsrc N")

    cctv = FakeCCTV(videos, port=args.port, bin_dir=args.bin_dir, fps=args.fps)
    try:
        for url in cctv.start():
            print(f"[fake_cctv] serving {url}")
        print("[fake_cctv] Ctrl+C to stop")
        while True:
            time.sleep(2)
            cctv.check()
    except KeyboardInterrupt:
        pass
    finally:
        cctv.stop()
        print("[fake_cctv] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
