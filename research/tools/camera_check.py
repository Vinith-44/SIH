"""StoreMind camera check: open any camera, measure real FPS, save a calibration snapshot.

Works on Raspberry Pi 5, Windows and Linux laptops.

Examples
  python camera_check.py --source csi:0                      # Pi Camera Module (Picamera2)
  python camera_check.py --source 0                          # USB webcam index 0
  python camera_check.py --source "rtsp://user:pass@192.168.1.64:554/Streaming/Channels/102"
  python camera_check.py --source http://192.168.1.50:8080/video   # phone (IP Webcam app)
  python camera_check.py --source test_video.mp4 --show

Install:  pip install opencv-python numpy      (on the Pi, Picamera2 is preinstalled)
Privacy:  only ONE snapshot is saved (for drawing zones/slots). Delete it if people are visible.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import cv2

# RTSP over TCP = fewer broken/grey frames on Wi-Fi. Must be set before VideoCapture is created.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


class LatestFrameReader:
    """Reads frames in a background thread and keeps ONLY the newest one (no lag build-up)."""

    def __init__(self, source: str, width: int, height: int) -> None:
        self.source, self.width, self.height = source, width, height
        self.frame, self.frame_time, self.frames_read = None, 0.0, 0
        self.lock, self.running = threading.Lock(), True
        self.picam = None
        self.cap = None
        self._open()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _open(self) -> None:
        if self.source.startswith("csi:"):
            from picamera2 import Picamera2  # only on Raspberry Pi OS
            self.picam = Picamera2(int(self.source.split(":", 1)[1]))
            config = self.picam.create_video_configuration(
                main={"size": (self.width, self.height), "format": "RGB888"})  # BGR byte order
            self.picam.configure(config)
            self.picam.start()
            return
        if self.source.isdigit():
            backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_V4L2
            self.cap = cv2.VideoCapture(int(self.source), backend)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        else:
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            raise SystemExit(f"Cannot open source: {self.source}")

    def _read_once(self):
        if self.picam is not None:
            return True, self.picam.capture_array()
        return self.cap.read()

    def _loop(self) -> None:
        is_file = os.path.isfile(self.source)
        while self.running:
            ok, frame = self._read_once()
            if not ok:
                if is_file:           # end of a video file: stop
                    self.running = False
                    break
                time.sleep(0.5)       # live stream hiccup: retry (a real service would reconnect)
                continue
            with self.lock:
                self.frame, self.frame_time = frame, time.monotonic()
                self.frames_read += 1
            if is_file:               # play files at roughly real time
                time.sleep(1 / 30)

    def latest(self):
        with self.lock:
            return (None, 0.0) if self.frame is None else (self.frame.copy(), self.frame_time)

    def close(self) -> None:
        self.running = False
        self.thread.join(timeout=2)
        if self.picam is not None:
            self.picam.stop()
        if self.cap is not None:
            self.cap.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="csi:0 | 0 | rtsp://... | http://... | file.mp4")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--seconds", type=float, default=10.0, help="how long to measure")
    parser.add_argument("--snapshot", default="calibration_snapshot.jpg")
    parser.add_argument("--show", action="store_true", help="open a preview window (needs a display)")
    args = parser.parse_args()

    reader = LatestFrameReader(args.source, args.width, args.height)
    start = time.monotonic()
    while reader.latest()[0] is None:
        if time.monotonic() - start > 15 or not reader.running:
            reader.close()
            raise SystemExit("No frame received in 15 s - check URL/cable/permissions.")
        time.sleep(0.05)

    first_count, t0 = reader.frames_read, time.monotonic()
    ages = []
    while time.monotonic() - t0 < args.seconds and reader.running:
        frame, frame_time = reader.latest()
        ages.append(time.monotonic() - frame_time)
        if args.show:
            cv2.imshow("camera_check (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        else:
            time.sleep(0.05)
    elapsed = max(1e-6, time.monotonic() - t0)
    frame, _ = reader.latest()
    fps = (reader.frames_read - first_count) / elapsed
    cv2.imwrite(args.snapshot, frame)
    reader.close()
    if args.show:
        cv2.destroyAllWindows()

    h, w = frame.shape[:2]
    print(f"source        : {args.source}")
    print(f"resolution    : {w}x{h}")
    print(f"camera FPS    : {fps:.1f} (measured over {elapsed:.1f} s)")
    print(f"frame age     : median {sorted(ages)[len(ages) // 2] * 1000:.0f} ms (how stale the newest frame is)")
    print(f"snapshot      : {os.path.abspath(args.snapshot)}  <- use this to draw lines/zones/slots")


if __name__ == "__main__":
    main()
