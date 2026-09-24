"""Exported detectors: fidelity, laptop-CPU speed, and CAVIAR counting (M8).

1.  **Fidelity** (bucket S - no ground truth): on OpenCV's `vtest.avi` (frames
    used for INT8 calibration are skipped), each export's person boxes are matched
    to the FP32 PyTorch model's boxes (IoU >= 0.5).  Recall/precision *against
    the reference*, not against people: it answers "did quantisation change
    what the detector sees?".
2.  **Laptop CPU speed** (bucket S): median / p95 ms on this laptop's CPU,
    4 threads.  A Pi 5 number comes only from `tools/bench_pi.py` on the Pi.
3.  **Counting on real footage** (`--caviar`, bucket A): each export runs over
    all 16 CAVIAR clips, cached like M1, then through the shipped counter
    (ByteTrack + gate defaults).  Entry/exit counts and event F1 per format show
    whether INT8 costs accuracy where it matters.

    python -m storemind.eval.eval_export                 # 1 + 2 (minutes)
    python -m storemind.eval.eval_export --caviar        # + 3 (CPU inference over 16 clips per format)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..core.config import DetectorConfig
from ..inference.detector import build_detector
from .common import accuracy_from_counts, fmt, pct

RESULTS = Path(__file__).resolve().parent / "results"
MODELS = Path(__file__).resolve().parents[3] / "models"
VIDEOS = Path(__file__).resolve().parents[3] / "videos"
CONF = 0.25


def variants(stem: str) -> list[tuple[str, str, str]]:
    """(label, backend, path) for every export of `stem` that exists."""
    rows = [("pytorch fp32", "ultralytics", MODELS / f"{stem}.pt"),
            ("onnx fp32", "onnx", MODELS / f"{stem}.onnx"),
            ("onnx int8", "onnx", MODELS / f"{stem}_int8.onnx"),
            ("ncnn fp32", "ultralytics", MODELS / f"{stem}_ncnn_model")]
    tflite = sorted((MODELS / f"{stem}_saved_model").glob("*int8*.tflite"))
    if tflite:
        rows.append(("litert int8", "litert", tflite[0]))
    return [(label, backend, str(path)) for label, backend, path in rows if Path(path).exists()]


def detector(backend: str, path: str):
    os.environ["STOREMIND_DEVICE"] = "cpu"
    return build_detector(DetectorConfig(backend=backend, model=path, imgsz=640, conf=CONF, num_threads=4))


def vtest_frames(count: int = 100, calib: Path | None = None):
    import cv2

    skip = set()
    if calib and calib.is_dir():
        skip = {int(p.stem.rsplit("_", 1)[1]) for p in calib.glob("vtest_*.jpg")}
    capture = cv2.VideoCapture(str(VIDEOS / "other" / "vtest.avi"))
    frames, index = [], 0
    while len(frames) < count:
        ok, frame = capture.read()
        if not ok:
            break
        if index not in skip:
            frames.append(frame)
        index += 1
    capture.release()
    return frames


def _iou(a, b) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def fidelity(stem: str, frames) -> list[dict]:
    rows = []
    reference = None
    for label, backend, path in variants(stem):
        det = detector(backend, path)
        for frame in frames[:5]:
            det.detect(frame)                                   # warm-up
        boxes, times = [], []
        for frame in frames:
            started = time.perf_counter()
            boxes.append([d.xyxy for d in det.detect(frame)])
            times.append((time.perf_counter() - started) * 1000.0)
        if reference is None:
            reference = boxes
        matched = ref_total = pred_total = 0
        for ref, pred in zip(reference, boxes):
            used = set()
            for r in ref:
                best = max(((i, _iou(r, p)) for i, p in enumerate(pred) if i not in used),
                           key=lambda x: x[1], default=(None, 0.0))
                if best[0] is not None and best[1] >= 0.5:
                    used.add(best[0])
                    matched += 1
            ref_total += len(ref)
            pred_total += len(pred)
        rows.append({"model": stem, "format": label, "path": path,
                     "size_mb": round(sum(f.stat().st_size for f in Path(path).rglob("*")) / 1e6
                                      if Path(path).is_dir() else Path(path).stat().st_size / 1e6, 2),
                     "recall_vs_fp32": matched / ref_total if ref_total else None,
                     "precision_vs_fp32": matched / pred_total if pred_total else None,
                     "cpu_ms_median": round(statistics.median(times), 1),
                     "cpu_ms_p95": round(sorted(times)[int(0.95 * (len(times) - 1))], 1),
                     "boxes_per_frame": round(pred_total / len(frames), 2)})
        print(rows[-1], flush=True)
    return rows


# --------------------------------------------------------------------------- #
# CAVIAR counting per format (bucket A)
# --------------------------------------------------------------------------- #

def _cache_job(args):
    label, backend, path, scenario, view = args
    from .detcache import CACHE_DIR, build_cache
    from .eval_caviar import CAVIAR_DIR

    video = CAVIAR_DIR / f"{scenario}{'front' if view == 'front' else 'cor'}.mpg"
    tag = label.replace(" ", "-")
    out = CACHE_DIR / f"{video.stem}__{Path(path).stem}__{tag}_640_8fps.json"
    det = detector(backend, path)
    det.conf = 0.05 if hasattr(det, "conf") else None
    return (label, scenario, view), str(build_cache(video, model=path, imgsz=640, fps=8, detector=det,
                                                    conf_floor=0.05, out=out))


def caviar_counts(stem: str, workers: int = 4) -> list[dict]:
    from .bakeoff import count_clip, gt_for, summarize, track_clip
    from .caviar import SCENARIOS
    from ..core.config import load_config

    config = load_config("configs/caviar.yaml")
    jobs = [(label, backend, path, s, v) for label, backend, path in variants(stem)
            for s in SCENARIOS for v in ("corridor", "front")]
    with ProcessPoolExecutor(workers) as pool:
        caches = dict(pool.map(_cache_job, jobs))
    rows = []
    for label, _backend, _path in variants(stem):
        counts = []
        for scenario in SCENARIOS:
            for view in ("corridor", "front"):
                truth, line = gt_for(scenario, view, config)
                run = track_clip("bytetrack", scenario, view, Path(caches[(label, scenario, view)]), conf=CONF)
                counts.append(count_clip(run["log"], truth, line, {"mode": "gate"}))
        s = summarize(counts)
        rows.append({"model": stem, "format": label, "entries": s["entries"], "exits": s["exits"],
                     "gt_entries": s["gt_entries"], "gt_exits": s["gt_exits"],
                     "entry_acc": s["entry_acc"], "exit_acc": s["exit_acc"],
                     "entry_f1": s["entry_event"]["f1"], "exit_f1": s["exit_event"]["f1"]})
        print(rows[-1], flush=True)
    return rows


def render(fid: list[dict], caviar: list[dict] | None) -> str:
    lines = ["# Exported detectors (M8)", "",
             "## Fidelity to the FP32 PyTorch model and laptop-CPU speed (bucket S)", "",
             "vtest.avi, 100 frames not used for calibration, conf 0.25. Recall/precision are **against the "
             "FP32 model's boxes**, not against people. Speeds are this laptop's CPU (4 threads) - not a Pi.", "",
             "| model | format | size MB | recall vs FP32 | precision vs FP32 | CPU ms median / p95 |",
             "|---|---|---|---|---|---|"]
    for r in fid:
        lines.append(f"| {r['model']} | {r['format']} | {r['size_mb']} | {pct(r['recall_vs_fp32'])} | "
                     f"{pct(r['precision_vs_fp32'])} | {r['cpu_ms_median']} / {r['cpu_ms_p95']} |")
    if caviar:
        lines += ["", "## Counting on CAVIAR with each format (bucket A, 16 clips, shipped counter, in-sample settings)", "",
                  "| model | format | entries (30) | exits (21) | entry acc | exit acc | entry F1 | exit F1 |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in caviar:
            lines.append(f"| {r['model']} | {r['format']} | {r['entries']} | {r['exits']} | {pct(r['entry_acc'])} | "
                         f"{pct(r['exit_acc'])} | {fmt(r['entry_f1'])} | {fmt(r['exit_f1'])} |")
        lines += ["", "Compare rows *within* a model: the question is how much each export changes the result, "
                  "not the absolute accuracy (that is the cross-validated number in docs/COUNTING.md)."]
    lines += ["", "Command: `python -m storemind.eval.eval_export --caviar`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["yolo11n", "yolo26n"])
    parser.add_argument("--calib", default=None, help="calibration folder, to skip its vtest frames")
    parser.add_argument("--caviar", action="store_true")
    args = parser.parse_args(argv)
    frames = vtest_frames(calib=Path(args.calib) if args.calib else None)
    fid = [row for stem in args.models for row in fidelity(stem, frames)]
    caviar = [row for stem in args.models for row in caviar_counts(stem)] if args.caviar else None
    payload = {"fidelity": fid, "caviar": caviar}
    (RESULTS / "model_export.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (RESULTS / "model_export.md").write_text(render(fid, caviar), encoding="utf-8")
    print(render(fid, caviar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
