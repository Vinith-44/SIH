"""Probe one camera stream (research/24 section 5 step 4).

Step 4 of onboarding is "probe each channel": the doc reaches for
`ffprobe -rtsp_transport tcp "<url>"` for codec and resolution, and notes that
our own check should also measure **real** FPS and lag.  That distinction is the
whole point of this tool.  A recorder will happily *claim* 25 FPS in its stream
header while delivering 6, and the claim is what ffprobe prints.  Section 5 step
5 then asks the installer to set 8-10 FPS for entrance cameras - a number they
cannot verify without measuring.

    python tools/probe.py rtsp://user:pass@192.168.1.108:554/cam/realmonitor?channel=1&subtype=1
    python tools/probe.py videos/entrance/synthetic_entrance.mp4 --seconds 3 --json

What it reports, and why each one is worth the seconds it costs:

*   **declared vs measured FPS** - the gap above.  `--expect-fps` turns it into a
    pass/fail so the installer does not have to eyeball it.
*   **resolution** - section 5 step 5 wants 640x360 to 1280x720.  Bigger means
    somebody pointed us at the main stream, which is the single easiest way to
    melt a Pi that is serving four cameras.
*   **lag** - how far behind real time the newest frame is, which is what decides
    whether a queue timer means anything.
*   **darkness** - section 6's night mode: an IR/greyscale frame needs a
    different confidence threshold, so it is worth knowing at onboarding.

The result maps onto the `CAMERA_HEALTH` contract (docs/INTERFACES.md), so the
same judgement the health panel makes later is the one the installer sees now.
Credentials in the URL are redacted in every line of output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np

from storemind.core.events import CameraHealthData
from storemind.health.monitor import FpsMeter
from storemind.ingest.urls import redact

# Section 5 step 5: the sub-stream the installer is told to configure.
MIN_SUB_WIDTH = 640
MAX_SUB_WIDTH = 1280

# Mean pixel value below which a frame reads as night/IR (section 6 night mode).
DARK_MEAN = 40.0
# A frame this uniform is a covered lens or a dead feed, not a dark room.
FLAT_STD = 3.0

DEFAULT_SECONDS = 5.0
# A live stream that produces nothing for this long is stale, matching the
# 5 s stale-frame watchdog in section 6.
STALE_AFTER_S = 5.0


@dataclass
class ProbeResult:
    source: str                     # already redacted
    opened: bool = False
    frames: int = 0
    width: int = 0
    height: int = 0
    declared_fps: float = 0.0
    measured_fps: float = 0.0
    elapsed_s: float = 0.0
    mean_brightness: float | None = None
    std_brightness: float | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_dark(self) -> bool:
        return self.mean_brightness is not None and self.mean_brightness < DARK_MEAN

    @property
    def is_flat(self) -> bool:
        """Uniform frame: lens covered, or a decoder handing back blank buffers."""
        return self.std_brightness is not None and self.std_brightness < FLAT_STD

    @property
    def fps_gap(self) -> float:
        """How far the stream's own claim is from what it delivered."""
        return self.declared_fps - self.measured_fps

    def state(self) -> str:
        """The CAMERA_HEALTH state this stream would report right now.

        Ordering matters: a stream that never opened is not 'dark', and a
        covered lens is worth saying out loud rather than filing as darkness.
        """
        if not self.opened or self.frames == 0:
            return "stale"
        if self.is_flat:
            return "tampered"
        if self.is_dark:
            return "dark"
        return "ok"

    def to_camera_health(self, cam: str, lag_ms: float | None = None) -> CameraHealthData:
        """The contract payload (docs/INTERFACES.md CAMERA_HEALTH)."""
        return CameraHealthData(
            cam=cam,
            state=self.state(),
            fps=round(self.measured_fps, 2),
            lag_ms=lag_ms,
            reconnects=0,
        )

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["state"] = self.state()
        data["fps_gap"] = round(self.fps_gap, 2)
        return data


def check_resolution(width: int, height: int) -> list[str]:
    """Warnings about a resolution that is not the sub-stream we asked for."""
    warnings: list[str] = []
    if width <= 0 or height <= 0:
        return ["could not read frame size"]
    if width > MAX_SUB_WIDTH:
        warnings.append(
            f"{width}x{height} is wider than {MAX_SUB_WIDTH}px - this looks like the "
            "main stream. Point at the sub-stream (section 5 step 5) or a Pi serving "
            "four cameras will not keep up."
        )
    elif width < MIN_SUB_WIDTH:
        warnings.append(
            f"{width}x{height} is narrower than {MIN_SUB_WIDTH}px - people will be too "
            "small to detect reliably at the far end of the aisle."
        )
    return warnings


def check_fps(declared: float, measured: float, expect: float | None) -> list[str]:
    """Warnings about the delivered frame rate, including the header's claim."""
    warnings: list[str] = []
    if measured <= 0.0:
        return ["no frames measured"]
    if declared > 0 and declared - measured > max(1.0, 0.25 * declared):
        warnings.append(
            f"stream claims {declared:.1f} FPS but delivered {measured:.1f}. "
            "Trust the measured number; the header is not a promise."
        )
    if expect is not None and measured < expect:
        warnings.append(
            f"delivered {measured:.1f} FPS, below the {expect:.1f} this role needs."
        )
    return warnings


def probe(source: str, seconds: float = DEFAULT_SECONDS, *,
          expect_fps: float | None = None, warmup_frames: int = 3) -> ProbeResult:
    """Open a source, read for `seconds`, and report what actually arrived.

    `warmup_frames` are read but not timed: the first frame of an RTSP stream
    waits for a keyframe and would drag the average down for reasons that say
    nothing about the stream's steady state.
    """
    result = ProbeResult(source=redact(source))
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        capture.release()
        result.error = (
            "could not open the stream. Check the URL, the read-only user, and that "
            "the recorder is reachable (tools/discover.py finds it on the LAN)."
        )
        result.warnings.append(result.error)
        return result

    result.opened = True
    result.declared_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    meter = FpsMeter()
    samples: list[np.ndarray] = []
    try:
        for _ in range(max(0, warmup_frames)):
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            result.height, result.width = frame.shape[:2]

        start = time.monotonic()
        deadline = start + max(0.0, seconds)
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if not ok or frame is None:
                # A file simply ended; a live stream going quiet is the
                # stale-frame case, and either way there is nothing more to time.
                break
            now = time.monotonic()
            meter.tick(now)
            result.frames += 1
            result.height, result.width = frame.shape[:2]
            if len(samples) < 5:
                samples.append(frame)
        result.elapsed_s = time.monotonic() - start
    finally:
        capture.release()

    result.measured_fps = meter.fps
    if samples:
        grey = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in samples]
        result.mean_brightness = float(np.mean([g.mean() for g in grey]))
        result.std_brightness = float(np.mean([g.std() for g in grey]))

    if result.frames == 0:
        result.error = "opened the stream but no frames arrived"
        result.warnings.append(result.error)
        return result

    result.warnings += check_resolution(result.width, result.height)
    result.warnings += check_fps(result.declared_fps, result.measured_fps, expect_fps)
    if result.is_flat:
        result.warnings.append(
            "frames are almost uniform - lens covered, or the decoder is handing back "
            "blank buffers."
        )
    elif result.is_dark:
        result.warnings.append(
            "very dark frames (IR or night). Expect a lower confidence threshold to be "
            "needed for this camera."
        )
    return result


def format_report(result: ProbeResult) -> str:
    lines = [f"source : {result.source}"]
    if not result.opened:
        lines.append(f"FAILED : {result.error}")
        return "\n".join(lines)
    lines += [
        f"size   : {result.width}x{result.height}",
        f"fps    : {result.measured_fps:.1f} measured, {result.declared_fps:.1f} declared",
        f"frames : {result.frames} in {result.elapsed_s:.1f}s",
        f"state  : {result.state()}",
    ]
    if result.mean_brightness is not None:
        lines.append(f"light  : mean {result.mean_brightness:.0f}, variation "
                     f"{result.std_brightness:.0f}")
    if result.warnings:
        lines.append("")
        lines += [f"  ! {w}" for w in result.warnings]
    else:
        lines.append("")
        lines.append("  looks good - next: step 8, calibrate with tools/calibrate.py")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe a camera stream for resolution, real FPS and darkness "
                    "(research/24 section 5 step 4).",
    )
    parser.add_argument("source", help="RTSP URL, file path, or USB index")
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS,
                        help="how long to measure for")
    parser.add_argument("--expect-fps", type=float, default=None,
                        help="fail if the measured rate is below this (entrance 8-10, "
                             "counter 3-5)")
    parser.add_argument("--cam", default=None,
                        help="camera name; prints the CAMERA_HEALTH payload too")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = probe(args.source, args.seconds, expect_fps=args.expect_fps)
    payload = result.as_dict()
    if args.cam:
        payload["camera_health"] = result.to_camera_health(args.cam).model_dump(mode="json")
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(result))
        if args.cam:
            print(f"\nCAMERA_HEALTH: {json.dumps(payload['camera_health'])}")
    if not result.opened or result.frames == 0:
        return 2
    return 1 if result.warnings else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
