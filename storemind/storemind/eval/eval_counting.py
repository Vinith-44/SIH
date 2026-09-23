"""Entry/exit count accuracy and occupancy error.

Metrics (research/06 section 6):

*   **Count accuracy** = 1 - |predicted - truth| / truth, reported separately for
    entries and exits.  Target >= 90%.
*   **Event-level precision / recall** with a matching tolerance, which is much
    harsher than totals: a clip where we miss five entries and invent five
    others scores 100% on totals and is exposed here.
*   **Occupancy MAE** against the running entries-minus-exits implied by the
    ground truth, sampled every 10 s.  Target <= 1-2 people.

    python -m storemind.eval.eval_counting --config configs/demo.yaml --camera entrance \
        --source ../videos/entrance/synthetic_entrance.mp4 --backend scripted \
        --model ../videos/entrance/synthetic_entrance_detections.json --fps 25
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.events import EventType
from .common import (
    accuracy_from_counts,
    event_time,
    fmt,
    load_entries_gt,
    mae,
    match_events,
    pct,
    prf,
    run_pipeline,
)


def occupancy_series(crossings: list[tuple[float, str]], until: float,
                     step: float = 10.0) -> list[tuple[float, int]]:
    series = []
    t = 0.0
    while t <= until:
        entries = sum(1 for at, d in crossings if at <= t and d == "in")
        exits = sum(1 for at, d in crossings if at <= t and d == "out")
        series.append((t, max(0, entries - exits)))
        t += step
    return series


def evaluate(config: str, camera: str, source: str, **run_kwargs) -> dict:
    video = Path(source)
    truth = load_entries_gt(video)
    tolerance = run_kwargs.pop("tolerance_s", 3.0)
    result = run_pipeline(config, camera=camera, source=source, **run_kwargs)

    # Events carry ISO timestamps anchored to the VideoClock start; recover the
    # clip-relative time by subtracting the first event's datetime.
    all_events = result.events
    if not all_events:
        base = None
    else:
        base = min(e.dt for e in all_events)

    predicted_in = [event_time(e, base) for e in result.of(EventType.ENTRY)]
    predicted_out = [event_time(e, base) for e in result.of(EventType.EXIT)]

    report: dict = {
        "video": str(video),
        "camera": camera,
        "command": result.command,
        "detector": result.summary["detector"],
        "video_seconds": result.summary["video_seconds"],
        "frames_processed": result.summary["cameras"][camera]["frames_processed"],
        "predicted": {"entries": len(predicted_in), "exits": len(predicted_out),
                      "occupancy": max(0, len(predicted_in) - len(predicted_out))},
        "ground_truth": None,
        "metrics": {},
    }
    if result.summary["cameras"][camera].get("infer_ms_mean") is not None:
        report["infer_ms_mean"] = result.summary["cameras"][camera]["infer_ms_mean"]

    if truth is None:
        report["note"] = ("no ground truth CSV next to this clip - counts above are system "
                          "output only, accuracy NOT measured yet")
        return report

    report["ground_truth"] = {"entries": truth["entries"], "exits": truth["exits"],
                              "source": truth["source"]}
    gt_in = [t for t, d in truth["crossings"] if d == "in"]
    gt_out = [t for t, d in truth["crossings"] if d == "out"]

    tp_in, fp_in, fn_in, err_in = match_events(predicted_in, gt_in, tolerance)
    tp_out, fp_out, fn_out, err_out = match_events(predicted_out, gt_out, tolerance)

    duration = result.summary["video_seconds"]
    gt_occ = occupancy_series(truth["crossings"], duration)
    # Rebuild our own occupancy over time from our own events.
    ours = []
    for t, _ in gt_occ:
        entries = sum(1 for at in predicted_in if at <= t)
        exits = sum(1 for at in predicted_out if at <= t)
        ours.append(max(0, entries - exits))

    report["metrics"] = {
        "entry_count_accuracy": accuracy_from_counts(len(predicted_in), truth["entries"]),
        "exit_count_accuracy": accuracy_from_counts(len(predicted_out), truth["exits"]),
        "total_count_accuracy": accuracy_from_counts(len(predicted_in) + len(predicted_out),
                                                     truth["entries"] + truth["exits"]),
        "entry_event": prf(tp_in, fp_in, fn_in),
        "exit_event": prf(tp_out, fp_out, fn_out),
        "match_tolerance_s": tolerance,
        "mean_timing_error_s": (sum(err_in + err_out) / len(err_in + err_out)
                                if (err_in + err_out) else None),
        "occupancy_mae": mae(list(zip(ours, [v for _, v in gt_occ]))),
        "occupancy_samples": len(gt_occ),
    }
    return report


def print_report(report: dict) -> None:
    print(f"\n=== entry/exit counting: {Path(report['video']).name} ===")
    print(f"command: {report['command']}")
    print(f"detector: {report['detector']}   frames: {report['frames_processed']}"
          f"   clip: {report['video_seconds']}s")
    predicted = report["predicted"]
    print(f"predicted: IN {predicted['entries']}  OUT {predicted['exits']}"
          f"  occupancy {predicted['occupancy']}")
    if report["ground_truth"] is None:
        print(f"NOTE: {report['note']}")
        return
    truth = report["ground_truth"]
    print(f"truth:     IN {truth['entries']}  OUT {truth['exits']}   ({truth['source']})")
    metrics = report["metrics"]
    print(f"entry count accuracy : {pct(metrics['entry_count_accuracy'])}")
    print(f"exit  count accuracy : {pct(metrics['exit_count_accuracy'])}")
    print(f"entry event P/R/F1   : {fmt(metrics['entry_event']['precision'])} / "
          f"{fmt(metrics['entry_event']['recall'])} / {fmt(metrics['entry_event']['f1'])}"
          f"   (tp {metrics['entry_event']['tp']}, fp {metrics['entry_event']['fp']},"
          f" fn {metrics['entry_event']['fn']})")
    print(f"exit  event P/R/F1   : {fmt(metrics['exit_event']['precision'])} / "
          f"{fmt(metrics['exit_event']['recall'])} / {fmt(metrics['exit_event']['f1'])}")
    print(f"mean timing error    : {fmt(metrics['mean_timing_error_s'], 2, ' s')}"
          f"  (tolerance {metrics['match_tolerance_s']} s)")
    print(f"occupancy MAE        : {fmt(metrics['occupancy_mae'], 2, ' people')}"
          f"  over {metrics['occupancy_samples']} samples")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--camera", default="entrance")
    parser.add_argument("--source", required=True)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--imgsz", type=int, default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    report = evaluate(args.config, args.camera, args.source, backend=args.backend,
                      model=args.model, fps=args.fps, imgsz=args.imgsz)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
