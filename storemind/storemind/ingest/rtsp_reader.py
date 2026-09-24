"""RTSP frame reader thread with the watchdog and PIR gate built in (M2).

    reader = RtspReader("cam1", go2rtc.stream_url("cam1"), on_frame=handle,
                        on_health=publish_camera_health, pir=pir_wake)
    reader.start()   # background thread
    ...
    reader.stop()

on_frame(camera, frame, ts) gets BGR numpy frames (only those the PIR gate allows).
on_health(payload) gets the watchdog's health dict on every state change and
every health_every_s seconds: that is what you publish as CAMERA_HEALTH.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable

from .pir_wake import PirWake
from .urls import redact
from .watchdog import StreamWatchdog

log = logging.getLogger(__name__)

# Force TCP for RTSP inside OpenCV's FFmpeg backend (UDP drops packets on Wi-Fi/busy LANs).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def _open_capture(url: str, open_timeout_ms: int, read_timeout_ms: int):
    import cv2  # imported lazily so the rest of the package works without OpenCV

    params: list[int] = []
    for name, value in (("CAP_PROP_OPEN_TIMEOUT_MSEC", open_timeout_ms),
                        ("CAP_PROP_READ_TIMEOUT_MSEC", read_timeout_ms)):
        prop = getattr(cv2, name, None)  # OpenCV >= 4.6
        if prop is not None:
            params += [prop, value]
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, params) if params else cv2.VideoCapture(url)
    return cap


class RtspReader(threading.Thread):
    def __init__(self, camera: str, url: str,
                 on_frame: Callable[[str, object, float], None] | None = None,
                 on_health: Callable[[dict], None] | None = None,
                 pir: PirWake | None = None, stale_after_s: float = 5.0,
                 health_every_s: float = 10.0, open_timeout_ms: int = 5000) -> None:
        super().__init__(name=f"rtsp-{camera}", daemon=True)
        self.camera = camera
        self.url = url
        self.on_frame = on_frame
        self.on_health = on_health
        self.pir = pir
        self.health_every_s = health_every_s
        self.open_timeout_ms = open_timeout_ms
        # A blocked read() must give up no later than the stale threshold.
        self.read_timeout_ms = int(stale_after_s * 1000)
        self.watchdog = StreamWatchdog(camera, stale_after_s=stale_after_s,
                                       on_change=self._emit_health)
        self._stop_event = threading.Event()
        self._last_health = 0.0

    def stop(self, join_timeout_s: float = 10.0) -> None:
        self._stop_event.set()
        if self.is_alive():
            self.join(join_timeout_s)

    def run(self) -> None:
        cap = None
        first = True
        while not self._stop_event.is_set():
            if cap is None:
                if not first:
                    if not self.watchdog.should_reconnect():
                        self._tick()
                        self._stop_event.wait(0.2)
                        continue
                    self.watchdog.on_reconnect_attempt()
                first = False
                log.info("opening %s (%s)", self.camera, redact(self.url))
                cap = _open_capture(self.url, self.open_timeout_ms, self.read_timeout_ms)
                if not cap.isOpened():
                    cap.release()
                    cap = None
                    self.watchdog.on_open_failed()
                    continue
            ok, frame = cap.read()
            if ok and frame is not None:
                self.watchdog.on_frame()
                if self.on_frame and (self.pir is None or self.pir.allow(self.camera)):
                    try:
                        self.on_frame(self.camera, frame, time.time())
                    except Exception:  # a pipeline bug must not kill the reader
                        log.exception("on_frame failed for %s", self.camera)
            else:
                self.watchdog.check()
                if self.watchdog.state != "online" or not ok:
                    log.warning("%s: read failed, reconnecting", self.camera)
                    cap.release()
                    cap = None
                    if self.watchdog.state == "online":
                        self.watchdog.on_open_failed()
            self._tick()
        if cap is not None:
            cap.release()

    def _tick(self) -> None:
        self.watchdog.check()
        now = time.monotonic()
        if now - self._last_health >= self.health_every_s:
            self._emit_health(self.watchdog.health())

    def _emit_health(self, payload: dict) -> None:
        self._last_health = time.monotonic()
        if self.on_health:
            try:
                self.on_health(payload)
            except Exception:
                log.exception("on_health failed for %s", self.camera)
