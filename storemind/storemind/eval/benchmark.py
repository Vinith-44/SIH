"""Throughput and latency per backend and input size.

The compute budget in research/04 section 6 is the difference between "one Pi
serves the whole store" and "buy three Pis".  It is quoted from Ultralytics'
published Raspberry Pi figures, and research/04 says in as many words: *measure
and report our own numbers, do not quote these as ours*.  This script produces
ours.

For each (backend, model, input size) it reports, over a real clip:

*   mean / median / p95 **inference** latency per frame;
*   mean **end-to-end** latency (decode + letterbox + inference + tracking),
    which is what actually limits camera count;
*   sustainable **FPS** and how many 8-FPS entrance streams that implies;
*   CPU percent and peak RSS during the run;
*   mean detections per frame, so a backend that is fast because it finds
    nothing is visible immediately.

    python -m storemind.eval.benchmark --source ../videos/other/vtest.avi --frames 120
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2

from ..core.config import DetectorConfig
from ..inference.detector import build_detector
from ..tracking.tracker import build_tracker
from ..core.config import TrackerConfig
from .common import machine_specs


def load_frames(source: str, count: int) -> list:
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"could not open {source}")
    frames = []
    while len(frames) < count:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise SystemExit(f"no frames decoded from {source}")
    return frames


def bench_one(frames: list, backend: str, model: str, imgsz: int, conf: float,
              warmup: int = 5, track: bool = True) -> dict:
    config = DetectorConfig(backend=backend, model=model, imgsz=imgsz, conf=conf)
    detector = build_detector(config)
    tracker = build_tracker(TrackerConfig(frame_rate=int(round(len(frames) / 10)) or 8)) \
        if track else None

    for frame in frames[:warmup]:
        detector.detect(frame)

    infer_ms: list[float] = []
    total_ms: list[float] = []
    detections_per_frame: list[int] = []

    try:
        import psutil
        process = psutil.Process()
        process.cpu_percent(None)
        rss_before = process.memory_info().rss
    except Exception:
        process = None
        rss_before = 0

    started = time.perf_counter()
    for frame in frames:
        t0 = time.perf_counter()
        detections = detector.detect(frame)
        t1 = time.perf_counter()
        if tracker is not None:
            tracker.update(detections)
        t2 = time.perf_counter()
        infer_ms.append((t1 - t0) * 1000.0)
        total_ms.append((t2 - t0) * 1000.0)
        detections_per_frame.append(len(detections))
    wall = time.perf_counter() - started

    cpu = rss = None
    if process is not None:
        cpu = process.cpu_percent(None)
        rss = max(0, process.memory_info().rss - rss_before)

    ordered = sorted(infer_ms)
    fps = len(frames) / wall if wall > 0 else 0.0
    return {
        "backend": backend,
        "model": Path(model).name if model else "",
        "imgsz": imgsz,
        "frames": len(frames),
        "infer_ms_mean": round(statistics.mean(infer_ms), 2),
        "infer_ms_median": round(statistics.median(infer_ms), 2),
        "infer_ms_p95": round(ordered[int(0.95 * (len(ordered) - 1))], 2),
        "end_to_end_ms_mean": round(statistics.mean(total_ms), 2),
        "fps": round(fps, 2),
        "streams_at_8fps": round(fps / 8.0, 2),
        "cpu_percent": round(cpu, 1) if cpu is not None else None,
        "rss_delta_mb": round(rss / 1e6, 1) if rss else None,
        "detections_per_frame": round(statistics.mean(detections_per_frame), 2),
    }


DEFAULT_MATRIX = [
    ("ultralytics", "../models/yolo11n.pt", 320),
    ("ultralytics", "../models/yolo11n.pt", 416),
    ("ultralytics", "../models/yolo11n.pt", 640),
    ("ultralytics", "../models/yolo26n.pt", 640),
    ("litert", "../models/efficientdet_lite0_coco_legacy.tflite", 320),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="../videos/other/vtest.avi")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--conf", type=float, default=0.35)
    parser.add_argument("--backend", action="append", default=None,
                        help="backend:model:imgsz (repeatable); default is the standard matrix")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    frames = load_frames(args.source, args.frames)
    height, width = frames[0].shape[:2]

    matrix = DEFAULT_MATRIX
    if args.backend:
        matrix = []
        for spec in args.backend:
            backend, model, imgsz = spec.split(":")
            matrix.append((backend, model, int(imgsz)))

    specs = machine_specs()
    print("machine: " + ", ".join(f"{k}={v}" for k, v in specs.items()))
    print(f"clip: {args.source}  {width}x{height}  {len(frames)} frames\n")
    print("| backend | model | imgsz | infer ms (mean) | p95 | end-to-end ms | FPS | "
          "8-FPS streams | CPU % | det/frame |")
    print("|---|---|---|---|---|---|---|---|---|---|")

    rows = []
    for backend, model, imgsz in matrix:
        if model and not Path(model).is_file():
            print(f"| {backend} | {Path(model).name} | {imgsz} | model file missing - skipped "
                  f"|||||||")
            continue
        try:
            row = bench_one(frames, backend, model, imgsz, args.conf)
        except SystemExit as error:
            print(f"| {backend} | {Path(model).name} | {imgsz} | {error} |||||||")
            continue
        rows.append(row)
        print(f"| {row['backend']} | {row['model']} | {row['imgsz']} | {row['infer_ms_mean']} "
              f"| {row['infer_ms_p95']} | {row['end_to_end_ms_mean']} | {row['fps']} "
              f"| {row['streams_at_8fps']} | {row['cpu_percent']} | {row['detections_per_frame']} |")

    report = {"machine": specs, "source": args.source, "resolution": f"{width}x{height}",
              "frames": len(frames), "rows": rows}
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nNote: these are laptop numbers.  Raspberry Pi 5 figures must be produced by "
          "running this same script on the Pi (deploy/pi/README_PI.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
