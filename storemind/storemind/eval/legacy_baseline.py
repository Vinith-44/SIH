"""The old pipeline, re-implemented faithfully, so "before vs after" is a
measurement rather than a claim.

Why re-implement instead of running `legacy/Shopper_analytics/.../live_shopper_analytics.py`
directly: that script opens a webcam with `cv2.CAP_DSHOW`, needs `tflite_runtime`
or full TensorFlow, writes JSONL, and shows an OpenCV window - it cannot be
pointed at a file and scored. So this module reproduces its **algorithm**,
parameter for parameter, on top of our own ingest and the *same* model file:

| legacy behaviour | source |
|---|---|
| EfficientDet-Lite0 `1.tflite`, threshold 0.45, person class 0 | `shopper_analytics_config.json` |
| frame **squashed** to 320x320, no letterbox (audit S1) | `PersonDetector.detect` |
| greedy nearest-centroid tracker, fixed 100 px radius, 12 misses (audit S2) | `CentroidTracker` |
| line crossing on the **box centre**, no dead band (audit S3) | `point_side` loop |
| a new track starts with `last_side = None` (audit S4) | `CentroidTracker.update` |
| 2 s crossing cooldown | `entry_line.crossing_cooldown_seconds` |

Two deliberate deviations, both of which make the legacy look **better** than it
really was, so the comparison cannot be accused of stacking the deck:

1.  It is given the **same counting line** as the new pipeline, not the
    uncalibrated placeholder from its own config - otherwise we would be
    measuring line placement, not the algorithm.
2.  Its cooldown uses **video time**, not `time.monotonic()`. On a replay that
    runs faster than real time the original would have let crossings through
    that the real system would have suppressed.

    python -m storemind.eval.legacy_baseline --config configs/demo.yaml \\
        --camera entrance --source ../videos/entrance/synthetic_entrance.mp4
"""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.config import load_config
from ..core.geometry import Line
from ..ingest.sources import FpsScheduler, open_source
from ..inference.detector import Detection, LiteRTDetector
from .common import accuracy_from_counts, fmt, load_entries_gt, mae, match_events, pct, prf
from .eval_counting import evaluate as evaluate_new
from .eval_counting import occupancy_series

# Straight from legacy/Shopper_analytics/Shopper analytics/shopper_analytics_config.json
LEGACY = {
    "detection_threshold": 0.45,
    "person_class_id": 0,
    "num_threads": 4,
    "tracker_max_distance_px": 100,
    "tracker_max_misses": 12,
    "crossing_cooldown_seconds": 2.0,
    "model": "../models/efficientdet_lite0_coco_legacy.tflite",
}


def box_centre(xyxy: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass
class _LegacyTrack:
    centre: tuple[float, float]
    box: tuple[float, float, float, float]
    misses: int = 0
    last_side: int | None = None
    last_cross: float = 0.0


class LegacyCentroidTracker:
    """The original greedy nearest-centroid tracker, kept bug-for-bug.

    Each existing track, in insertion order, grabs the nearest unclaimed
    detection within a fixed pixel radius. Insertion order decides who wins a
    contested detection, which is the mechanism behind the ID switches in audit
    item S2.
    """

    def __init__(self, max_distance: float, max_misses: int) -> None:
        self.max_distance = max_distance
        self.max_misses = max_misses
        self.next_id = 1
        self.tracks: dict[int, _LegacyTrack] = {}

    def update(self, boxes: list[tuple[float, float, float, float]]) -> dict[int, _LegacyTrack]:
        centres = [box_centre(b) for b in boxes]
        available = set(range(len(boxes)))
        for _track_id, track in list(self.tracks.items()):
            distances = [
                math.dist(track.centre, centre) if index in available else float("inf")
                for index, centre in enumerate(centres)
            ]
            best = int(np.argmin(distances)) if distances else -1
            if best >= 0 and distances[best] <= self.max_distance:
                track.centre = centres[best]
                track.box = boxes[best]
                track.misses = 0
                available.discard(best)
            else:
                track.misses += 1
        for track_id, track in list(self.tracks.items()):
            if track.misses > self.max_misses:
                del self.tracks[track_id]
        for index in sorted(available):
            self.tracks[self.next_id] = _LegacyTrack(centre=centres[index], box=boxes[index])
            self.next_id += 1
        return self.tracks


def legacy_point_side(point: tuple[float, float], a: np.ndarray, b: np.ndarray) -> int:
    cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    return 1 if cross >= 0 else -1


@dataclass
class LegacyResult:
    entries: int = 0
    exits: int = 0
    crossings: list[tuple[float, str]] = field(default_factory=list)
    frames: int = 0
    infer_ms: list[float] = field(default_factory=list)
    track_ids_created: int = 0
    video_seconds: float = 0.0

    @property
    def occupancy(self) -> int:
        return max(0, self.entries - self.exits)


def build_legacy_detector(backend: str = "litert", model: str | None = None):
    """The detector half of the legacy stack.

    `litert` is the real thing: the legacy `1.tflite` with the squash
    preprocessing. `scripted` substitutes perfect detections, which isolates the
    *tracking and counting* half - on a synthetic clip the legacy detector fails
    for reasons that have nothing to do with its algorithm, and blaming its
    counting logic for that would be dishonest.
    """
    if backend == "scripted":
        from ..inference.scripted import ScriptedDetector

        if not model:
            raise SystemExit("--legacy-backend scripted needs --legacy-model <detections.json>")
        return ScriptedDetector(model)
    return LiteRTDetector(
        model or LEGACY["model"],
        conf=LEGACY["detection_threshold"],
        person_class=LEGACY["person_class_id"],
        num_threads=LEGACY["num_threads"],
        letterbox_input=False,          # the squash bug, reproduced on purpose
    )


def run_legacy(source: str, line_config, fps: float | None = None,
               model: str | None = None, max_seconds: float | None = None,
               backend: str = "litert") -> LegacyResult:
    detector = build_legacy_detector(backend, model)
    tracker = LegacyCentroidTracker(LEGACY["tracker_max_distance_px"],
                                    LEGACY["tracker_max_misses"])
    # "positive_to_negative" / "negative_to_positive" in the legacy config; our
    # config says "pos"/"neg" for the same thing.
    enter_direction = ("negative_to_positive" if line_config.entry_direction == "pos"
                       else "positive_to_negative")

    result = LegacyResult()
    source_handle = open_source(source)
    scheduler = FpsScheduler(fps or 0.0)
    line: Line | None = None
    try:
        while True:
            frame = source_handle.read()
            if frame is None:
                break
            if max_seconds is not None and frame.video_s > max_seconds:
                break
            if not scheduler.should_process(frame.video_s):
                continue
            width, height = frame.size
            if line is None:
                line = Line("door", tuple(line_config.a), tuple(line_config.b),
                            line_config.margin_px).resolve(width, height)
            now = frame.video_s
            result.video_seconds = now

            seek = getattr(detector, "seek", None)
            if seek is not None:
                seek(frame.index)
            started = time.perf_counter()
            detections: list[Detection] = detector.detect(frame.image)
            result.infer_ms.append((time.perf_counter() - started) * 1000.0)
            result.frames += 1

            tracks = tracker.update([d.xyxy for d in detections])
            for track in tracks.values():
                if track.misses:
                    continue    # the legacy loop skipped missed tracks entirely
                side = legacy_point_side(track.centre, line._pa, line._pb)
                previous = track.last_side
                if (previous is not None and side != previous
                        and now - track.last_cross >= LEGACY["crossing_cooldown_seconds"]):
                    direction = ("positive_to_negative" if previous > side
                                 else "negative_to_positive")
                    if direction == enter_direction:
                        result.entries += 1
                        result.crossings.append((now, "in"))
                    else:
                        result.exits += 1
                        result.crossings.append((now, "out"))
                    track.last_cross = now
                track.last_side = side
    finally:
        source_handle.close()
    result.track_ids_created = tracker.next_id - 1
    return result


def compare(config_path: str, camera: str, source: str, fps: float | None = None,
            backend: str | None = None, model: str | None = None,
            legacy_backend: str = "litert", legacy_model: str | None = None,
            tolerance_s: float = 3.0) -> dict:
    config = load_config(config_path)
    camera_config = config.camera(camera)
    if camera_config.line is None:
        raise SystemExit(f"camera {camera!r} has no counting line configured")

    video = Path(source)
    truth = load_entries_gt(video)

    legacy = run_legacy(source, camera_config.line, fps=fps or camera_config.fps,
                        backend=legacy_backend, model=legacy_model)
    new = evaluate_new(config_path, camera, source, backend=backend, model=model, fps=fps)

    report: dict = {
        "video": str(video),
        "camera": camera,
        "legacy": {
            "entries": legacy.entries,
            "exits": legacy.exits,
            "occupancy": legacy.occupancy,
            "frames": legacy.frames,
            "track_ids_created": legacy.track_ids_created,
            "infer_ms_mean": (round(sum(legacy.infer_ms) / len(legacy.infer_ms), 2)
                              if legacy.infer_ms else None),
            "detector": (
                "perfect detections + centroid tracker + box centre"
                if legacy_backend == "scripted"
                else "EfficientDet-Lite0 (squashed 320x320) + centroid tracker + box centre"),
            "backend": legacy_backend,
        },
        "new": {
            "entries": new["predicted"]["entries"],
            "exits": new["predicted"]["exits"],
            "occupancy": new["predicted"]["occupancy"],
            "frames": new["frames_processed"],
            "infer_ms_mean": new.get("infer_ms_mean"),
            "detector": new["detector"],
        },
        "ground_truth": None,
        "command": (f"python -m storemind.eval.legacy_baseline --config {config_path} "
                    f"--camera {camera} --source {source}"),
    }

    if truth is None:
        report["note"] = ("no ground truth CSV beside this clip - both columns are system "
                          "output only, neither is an accuracy measurement")
        return report

    report["ground_truth"] = {"entries": truth["entries"], "exits": truth["exits"],
                              "source": truth["source"]}
    gt_in = [t for t, d in truth["crossings"] if d == "in"]
    gt_out = [t for t, d in truth["crossings"] if d == "out"]
    legacy_in = [t for t, d in legacy.crossings if d == "in"]
    legacy_out = [t for t, d in legacy.crossings if d == "out"]

    tp_in, fp_in, fn_in, _ = match_events(legacy_in, gt_in, tolerance_s)
    tp_out, fp_out, fn_out, _ = match_events(legacy_out, gt_out, tolerance_s)

    gt_occ = occupancy_series(truth["crossings"], legacy.video_seconds)
    legacy_occ = []
    for t, _ in gt_occ:
        legacy_occ.append(max(0, sum(1 for at in legacy_in if at <= t)
                              - sum(1 for at in legacy_out if at <= t)))

    report["legacy"]["metrics"] = {
        "entry_count_accuracy": accuracy_from_counts(legacy.entries, truth["entries"]),
        "exit_count_accuracy": accuracy_from_counts(legacy.exits, truth["exits"]),
        "entry_event": prf(tp_in, fp_in, fn_in),
        "exit_event": prf(tp_out, fp_out, fn_out),
        "occupancy_mae": mae(list(zip(legacy_occ, [v for _, v in gt_occ]))),
    }
    report["new"]["metrics"] = new["metrics"]
    return report


def print_report(report: dict) -> None:
    print(f"\n=== before / after: {Path(report['video']).name} ===")
    print(f"command: {report['command']}")
    legacy, new = report["legacy"], report["new"]
    truth = report["ground_truth"]

    print(f"\nlegacy : {legacy['detector']}")
    print(f"new    : {new['detector']} + ByteTrack + foot point + hysteresis")
    print()
    header = f"| {'metric':26} | {'legacy':>16} | {'StoreMind':>16} |"
    if truth:
        header += f" {'ground truth':>14} |"
    print(header)
    print("|" + "-" * 28 + "|" + "-" * 18 + "|" + "-" * 18 + "|"
          + ("-" * 16 + "|" if truth else ""))

    def row(label: str, a, b, g=None) -> None:
        line = f"| {label:26} | {str(a):>16} | {str(b):>16} |"
        if truth:
            line += f" {str(g if g is not None else ''):>14} |"
        print(line)

    row("entries counted", legacy["entries"], new["entries"],
        truth["entries"] if truth else None)
    row("exits counted", legacy["exits"], new["exits"], truth["exits"] if truth else None)
    row("occupancy at end", legacy["occupancy"], new["occupancy"],
        (truth["entries"] - truth["exits"]) if truth else None)
    row("track IDs created", legacy["track_ids_created"], "-")
    row("inference ms/frame", legacy["infer_ms_mean"], new["infer_ms_mean"])

    if truth:
        lm, nm = legacy["metrics"], new["metrics"]
        row("entry count accuracy", pct(lm["entry_count_accuracy"]),
            pct(nm["entry_count_accuracy"]), "100.0%")
        row("exit count accuracy", pct(lm["exit_count_accuracy"]),
            pct(nm["exit_count_accuracy"]), "100.0%")
        row("entry event F1", fmt(lm["entry_event"]["f1"]), fmt(nm["entry_event"]["f1"]), "1.00")
        row("entry false positives", lm["entry_event"]["fp"], nm["entry_event"]["fp"], "0")
        row("entry missed", lm["entry_event"]["fn"], nm["entry_event"]["fn"], "0")
        row("occupancy MAE", fmt(lm["occupancy_mae"]), fmt(nm["occupancy_mae"]), "0.00")
    else:
        print(f"\nNOTE: {report['note']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--camera", default="entrance")
    parser.add_argument("--source", required=True)
    parser.add_argument("--backend", default=None, help="backend for the NEW pipeline")
    parser.add_argument("--model", default=None, help="model for the NEW pipeline")
    parser.add_argument("--legacy-backend", default="litert", choices=["litert", "scripted"],
                        help="'scripted' compares the ALGORITHMS on identical perfect "
                             "detections; 'litert' compares the whole stacks")
    parser.add_argument("--legacy-model", default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    report = compare(args.config, args.camera, args.source, fps=args.fps,
                     backend=args.backend, model=args.model,
                     legacy_backend=args.legacy_backend, legacy_model=args.legacy_model)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
