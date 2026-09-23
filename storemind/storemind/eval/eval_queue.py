"""Queue length, wait time and service time accuracy.

Metrics (research/06 section 6):

*   **Queue-length MAE** - our smoothed queue length sampled at each ground-truth
    instant.  Target <= 1 person.
*   **Wait-time MAE and median error** - matched per customer where ground truth
    follows individuals, otherwise compared as distributions (median and mean).
    Target <= 20% error.
*   **Service-time MAE** - same treatment.

Reporting the **median** matters: research/01 Q6 notes the old code reported a
mean that a single stuck track could move by minutes.

    python -m storemind.eval.eval_queue --config configs/demo.yaml --camera counter-1 \
        --source ../videos/queue/synthetic_queue.mp4 --backend scripted \
        --model ../videos/queue/synthetic_queue_detections.json --fps 25
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from ..core.events import EventType
from .common import (
    event_time,
    fmt,
    load_queue_length_gt,
    load_waits_gt,
    mae,
    match_events,
    prf,
    run_pipeline,
)


def sample_at(series: list[tuple[float, float]], t: float) -> float | None:
    """Last reported value at or before `t` (QUEUE_STATE is a step function)."""
    value = None
    for when, v in series:
        if when <= t + 1e-6:
            value = v
        else:
            break
    return value


def evaluate(config: str, camera: str, source: str, **run_kwargs) -> dict:
    video = Path(source)
    length_gt = load_queue_length_gt(video)
    waits_gt = load_waits_gt(video)
    result = run_pipeline(config, camera=camera, source=source, **run_kwargs)
    base = min((e.dt for e in result.events), default=None)

    states = result.of(EventType.QUEUE_STATE)
    dones = result.of(EventType.SERVICE_DONE)

    per_counter: dict[str, list[tuple[float, float]]] = {}
    for event in states:
        per_counter.setdefault(event.data["counter"], []).append(
            (event_time(event, base), float(event.data["queue_len_smooth"])))
    for series in per_counter.values():
        series.sort()

    report: dict = {
        "video": str(video),
        "camera": camera,
        "command": result.command,
        "detector": result.summary["detector"],
        "video_seconds": result.summary["video_seconds"],
        "counters": result.summary["cameras"][camera].get("counters", {}),
        "services_detected": len(dones),
        "metrics": {},
        "notes": [],
    }

    # --- queue length -------------------------------------------------- #
    if length_gt:
        errors: list[tuple[float, float]] = []
        detail: dict[str, dict] = {}
        for counter, truth_series in length_gt.items():
            ours = per_counter.get(counter, [])
            pairs = []
            for t, truth_value in truth_series:
                predicted = sample_at(ours, t)
                if predicted is None:
                    continue
                pairs.append((predicted, truth_value))
            errors += pairs
            detail[counter] = {"samples": len(pairs), "mae": mae(pairs)}
        report["metrics"]["queue_length_mae"] = mae(errors)
        report["metrics"]["queue_length_samples"] = len(errors)
        report["metrics"]["queue_length_per_counter"] = detail
    else:
        report["notes"].append("no *_gt_queue_length.csv - queue-length MAE not measured yet")

    # --- waits and service times ----------------------------------------- #
    if waits_gt:
        predicted_waits = [float(e.data["wait_s"]) for e in dones
                           if e.data.get("wait_s") is not None]
        predicted_services = [float(e.data["service_s"]) for e in dones]
        truth_waits = [float(r["wait_s"]) for r in waits_gt]
        truth_services = [float(r["service_s"]) for r in waits_gt]

        # Match a detected service to a ground-truth customer by when service
        # started, so the MAE is per customer rather than distribution-to-
        # distribution.
        predicted_starts = [event_time(e, base) - float(e.data["service_s"]) for e in dones]
        truth_starts = [float(r["service_start_s"]) for r in waits_gt]
        tp, fp, fn, _ = match_events(predicted_starts, truth_starts, tolerance_s=10.0)

        paired_waits, paired_services = [], []
        used = [False] * len(waits_gt)
        for done, start in zip(dones, predicted_starts):
            best, best_delta = None, None
            for i, truth_start in enumerate(truth_starts):
                if used[i]:
                    continue
                delta = abs(start - truth_start)
                if delta <= 10.0 and (best_delta is None or delta < best_delta):
                    best, best_delta = i, delta
            if best is None:
                continue
            used[best] = True
            if done.data.get("wait_s") is not None:
                paired_waits.append((float(done.data["wait_s"]), truth_waits[best]))
            paired_services.append((float(done.data["service_s"]), truth_services[best]))

        wait_mae = mae(paired_waits)
        service_mae = mae(paired_services)
        report["metrics"].update({
            "customers_ground_truth": len(waits_gt),
            "customers_detected": len(dones),
            "service_event": prf(tp, fp, fn),
            "wait_mae_s": wait_mae,
            "wait_mae_pct": (wait_mae / statistics.mean(truth_waits) * 100.0
                             if wait_mae is not None and truth_waits
                             and statistics.mean(truth_waits) > 0 else None),
            "service_mae_s": service_mae,
            "service_mae_pct": (service_mae / statistics.mean(truth_services) * 100.0
                                if service_mae is not None and truth_services
                                and statistics.mean(truth_services) > 0 else None),
            "median_wait_predicted_s": (statistics.median(predicted_waits)
                                        if predicted_waits else None),
            "median_wait_truth_s": statistics.median(truth_waits) if truth_waits else None,
            "median_service_predicted_s": (statistics.median(predicted_services)
                                           if predicted_services else None),
            "median_service_truth_s": (statistics.median(truth_services)
                                       if truth_services else None),
            "paired_customers": len(paired_services),
        })
    else:
        report["notes"].append("no *_gt_waits.csv - wait/service accuracy not measured yet")

    return report


def print_report(report: dict) -> None:
    print(f"\n=== queue: {Path(report['video']).name} ===")
    print(f"command: {report['command']}")
    print(f"detector: {report['detector']}   clip: {report['video_seconds']}s")
    for name, stats in report["counters"].items():
        print(f"{name}: services {stats['services_completed']}, joins {stats['joins']}, "
              f"median wait {fmt(stats['median_wait_s'], 1, ' s')}, "
              f"median service {fmt(stats['median_service_s'], 1, ' s')}")
    metrics = report["metrics"]
    if "queue_length_mae" in metrics:
        print(f"queue length MAE     : {fmt(metrics['queue_length_mae'], 2, ' people')} "
              f"over {metrics['queue_length_samples']} samples")
    if "wait_mae_s" in metrics:
        print(f"customers            : {metrics['customers_detected']} detected / "
              f"{metrics['customers_ground_truth']} in ground truth "
              f"({metrics['paired_customers']} matched)")
        service_event = metrics["service_event"]
        print(f"service event P/R/F1 : {fmt(service_event['precision'])} / "
              f"{fmt(service_event['recall'])} / {fmt(service_event['f1'])}")
        print(f"wait MAE             : {fmt(metrics['wait_mae_s'], 2, ' s')} "
              f"({fmt(metrics['wait_mae_pct'], 1, '%')} of the mean wait)")
        print(f"service MAE          : {fmt(metrics['service_mae_s'], 2, ' s')} "
              f"({fmt(metrics['service_mae_pct'], 1, '%')})")
        print(f"median wait          : ours {fmt(metrics['median_wait_predicted_s'], 1, ' s')}"
              f"   truth {fmt(metrics['median_wait_truth_s'], 1, ' s')}")
        print(f"median service       : ours {fmt(metrics['median_service_predicted_s'], 1, ' s')}"
              f"   truth {fmt(metrics['median_service_truth_s'], 1, ' s')}")
    for note in report["notes"]:
        print(f"NOTE: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--camera", default="counter-1")
    parser.add_argument("--source", required=True)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    report = evaluate(args.config, args.camera, args.source, backend=args.backend,
                      model=args.model, fps=args.fps)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
