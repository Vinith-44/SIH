"""Camera / video ingest.

Every engine must run on a recorded file exactly as on a live camera
(CLAUDE.md engineering rules).  Two behaviours are therefore deliberately
different:

*   **Live** sources run a grab thread that keeps only the newest frame.  Audit
    item S9: OpenCV buffers RTSP frames, so a single-threaded reader drifts
    seconds behind reality.
*   **File** sources are read sequentially with *no* dropping, because dropping
    frames during replay would make the same video score differently on every
    run and our accuracy numbers would not be reproducible.

`open_source()` accepts:
    path/to/file.mp4     file replay
    0, 1, ...            USB camera index
    rtsp://...           IP camera / DVR sub-stream (forced onto TCP)
    http://...           phone camera (IP Webcam / DroidCam)
    csi:0                Raspberry Pi camera via Picamera2 (imported only on Pi)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

# Force RTSP over TCP before the first VideoCapture is created (05 section 5.3).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


@dataclass
class Frame:
    image: np.ndarray
    index: int
    video_s: float          # seconds since the start of the stream/file
    wall_s: float           # time.monotonic() when the frame was produced

    @property
    def size(self) -> tuple[int, int]:
        height, width = self.image.shape[:2]
        return width, height


class FrameSource:
    name: str = "source"
    is_live: bool = False
    fps_hint: float = 0.0
    frame_count: int = 0

    def read(self) -> Frame | None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self) -> "FrameSource":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FileSource(FrameSource):
    """Deterministic sequential replay."""

    is_live = False

    def __init__(self, path: str | Path, realtime: bool = False, rotate: int = 0) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise SystemExit(f"video file not found: {self.path}")
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            raise SystemExit(f"could not open video: {self.path}")
        self.name = self.path.name
        self.fps_hint = float(self.cap.get(cv2.CAP_PROP_FPS)) or 25.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.realtime = realtime
        self.rotate = rotate
        self._index = 0
        self._t0 = time.monotonic()

    def read(self) -> Frame | None:
        ok, image = self.cap.read()
        if not ok or image is None:
            return None
        # CAP_PROP_POS_MSEC is unreliable on some containers (returns 0 forever),
        # so fall back to frame index / fps.
        pos_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
        video_s = (pos_ms / 1000.0) if pos_ms and pos_ms > 0 else (self._index / self.fps_hint)
        if self.rotate:
            image = _rotate(image, self.rotate)
        if self.realtime:
            target = self._t0 + video_s
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        frame = Frame(image=image, index=self._index, video_s=video_s, wall_s=time.monotonic())
        self._index += 1
        return frame

    def close(self) -> None:
        self.cap.release()


class ThreadedLiveSource(FrameSource):
    """Grab thread that keeps only the newest frame, with auto-reconnect."""

    is_live = True

    def __init__(self, spec: str | int, name: str | None = None, rotate: int = 0,
                 api_preference: int | None = None, reconnect_s: float = 2.0) -> None:
        self.spec = spec
        self.name = name or str(spec)
        self.rotate = rotate
        self.api_preference = api_preference
        self.reconnect_s = reconnect_s
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._last_taken = -1
        self._index = 0
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self.connected = False
        self._thread = threading.Thread(target=self._loop, name=f"grab-{self.name}", daemon=True)
        self._thread.start()

    def _open(self) -> cv2.VideoCapture:
        if self.api_preference is not None:
            return cv2.VideoCapture(self.spec, self.api_preference)
        return cv2.VideoCapture(self.spec)

    def _loop(self) -> None:
        cap = self._open()
        while not self._stop.is_set():
            if not cap.isOpened():
                self.connected = False
                log.warning("camera %s not open, retrying in %.1fs", self.name, self.reconnect_s)
                cap.release()
                time.sleep(self.reconnect_s)
                cap = self._open()
                continue
            ok, image = cap.read()
            if not ok or image is None:
                self.connected = False
                log.warning("camera %s read failed, reconnecting", self.name)
                cap.release()
                time.sleep(self.reconnect_s)
                cap = self._open()
                continue
            self.connected = True
            if self.rotate:
                image = _rotate(image, self.rotate)
            now = time.monotonic()
            with self._lock:
                self._latest = Frame(image=image, index=self._index,
                                     video_s=now - self._t0, wall_s=now)
                self._index += 1
        cap.release()

    def read(self) -> Frame | None:
        """Return the newest frame, or None if no new frame has arrived yet."""
        with self._lock:
            frame = self._latest
            if frame is None or frame.index == self._last_taken:
                return None
            self._last_taken = frame.index
            return frame

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)


class CsiSource(FrameSource):
    """Raspberry Pi camera.  Picamera2 is imported here and nowhere else, so the
    module imports cleanly on Windows."""

    is_live = True

    def __init__(self, index: int = 0, size: tuple[int, int] = (1280, 720), rotate: int = 0) -> None:
        from picamera2 import Picamera2  # Pi-only dependency

        self.cam = Picamera2(index)
        self.cam.configure(self.cam.create_video_configuration(main={"size": size, "format": "RGB888"}))
        self.cam.start()
        self.name = f"csi:{index}"
        self.rotate = rotate
        self._index = 0
        self._t0 = time.monotonic()

    def read(self) -> Frame | None:
        image = self.cam.capture_array()  # already BGR byte order
        if self.rotate:
            image = _rotate(image, self.rotate)
        now = time.monotonic()
        frame = Frame(image=image, index=self._index, video_s=now - self._t0, wall_s=now)
        self._index += 1
        return frame

    def close(self) -> None:
        try:
            self.cam.stop()
        except Exception:
            pass


def _rotate(image: np.ndarray, degrees: int) -> np.ndarray:
    if degrees == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if degrees == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if degrees == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return image


def open_source(spec: str | int, *, name: str | None = None, realtime: bool = False,
                rotate: int = 0) -> FrameSource:
    text = str(spec).strip()
    if text.startswith("csi:"):
        return CsiSource(int(text.split(":", 1)[1] or 0), rotate=rotate)
    if text.startswith(("rtsp://", "rtmp://")):
        return ThreadedLiveSource(text, name=name or text, rotate=rotate,
                                  api_preference=cv2.CAP_FFMPEG)
    if text.startswith(("http://", "https://")):
        return ThreadedLiveSource(text, name=name or text, rotate=rotate)
    if text.isdigit():
        # CAP_DSHOW on Windows opens USB cameras far faster than the default
        # backend; on Linux/Pi the default (V4L2) is correct.  The legacy code
        # hard-coded CAP_DSHOW and so could not run on the Pi at all.
        preference = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_V4L2
        return ThreadedLiveSource(int(text), name=name or f"usb{text}", rotate=rotate,
                                  api_preference=preference)
    return FileSource(text, realtime=realtime, rotate=rotate)


class FpsScheduler:
    """Per-task frame budget (novelty N6: task-aware scheduling).

    Entrance cameras need 8-12 FPS, counters 3-5, shelves one frame every 30-60
    seconds.  This is how one Pi 5 serves many cameras.
    """

    def __init__(self, target_fps: float) -> None:
        self.target_fps = float(target_fps)
        self.period = 1.0 / self.target_fps if self.target_fps > 0 else 0.0
        self._next_due = 0.0
        self.accepted = 0
        self.skipped = 0

    def should_process(self, timestamp_s: float) -> bool:
        if self.period <= 0.0:
            self.accepted += 1
            return True
        if timestamp_s + 1e-9 >= self._next_due:
            # Anchor to the actual timestamp so a long stall does not cause a
            # burst of catch-up frames afterwards.
            self._next_due = timestamp_s + self.period
            self.accepted += 1
            return True
        self.skipped += 1
        return False
