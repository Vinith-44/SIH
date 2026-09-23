"""Forecast lead time - does the door really warn us before the queue does?

This is the measurement behind novelty N1.  Everything else in the queue module
is competent engineering; this is the claim that is actually new, so it needs the
harshest test we can write.

Method

1.  Run the entrance camera and the checkout camera over the same timeline
    (`configs/rush.yaml`).  Both are ordinary pipeline runs - the forecaster only
    ever sees ENTRY events and QUEUE_STATE/SERVICE_DONE events, exactly as it
    would live.
2.  Ground-truth **congestion onset** = the first time the true queue length at
    any counter reaches `congestion_len` and stays there.
3.  Our **first warning** = the first FORECAST event whose recommended counter
    count exceeds the number currently open.
4.  **Lead time** = congestion onset - first warning.  Positive means we warned
    in advance; negative means we were late and we say so.

We also report:

*   the **estimated lag** versus the true shopping-trip lag built into the scene;
*   **false-alarm rate**: warnings issued during a period when the queue never
    became congested within the forecast horizon.  Research/01 Q2 records the old
    system predicting 78 people from a queue of 1 - a lead-time number means
    nothing without a false-alarm number beside it.

    python -m storemind.eval.eval_forecast --config configs/rush.yaml \\
        --entrance-camera entrance --counter-camera checkout --backend scripted
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.clock import VideoClock
from ..core.config import load_config
from ..core.events import EventType
from ..fusion.forecast import Forecaster
from .common import event_time, fmt, load_queue_length_gt, run_pipeline


def congestion_onset(series: dict[str, list[tuple[float, float]]], threshold: float,
                     sustain_s: float = 20.0) -> float | None:
    """First time any counter reaches `threshold` and is still there `sustain_s`
    later.  The sustain check stops one noisy sample from defining the onset."""
    best: float | None = None
    for points in series.values():
        for i, (t, value) in enumerate(points):
            if value < threshold:
                continue
            later = [v for later_t, v in points[i:] if later_t <= t + sustain_s]
            if later and min(later) >= threshold:
                best = t if best is None else min(best, t)
                break
    return best


def evaluate(config_path: str, entrance_camera: str, counter_camera: str,
             backend: str | None = None, entrance_model: str | None = None,
             counter_model: str | None = None, fps: float | None = None) -> dict:
    config = load_config(config_path)
    entrance_cfg = config.camera(entrance_camera)
    counter_cfg = config.camera(counter_camera)
    entrance_video = Path(entrance_cfg.source)
    counter_video = Path(counter_cfg.source)

    if backend == "scripted":
        entrance_model = entrance_model or str(
            entrance_video.with_name(f"{entrance_video.stem}_detections.json"))
        counter_model = counter_model or str(
            counter_video.with_name(f"{counter_video.stem}_detections.json"))

    entrance_run = run_pipeline(config_path, camera=entrance_camera, backend=backend,
                                model=entrance_model, fps=fps)
    counter_run = run_pipeline(config_path, camera=counter_camera, backend=backend,
                               model=counter_model, fps=fps)

    entrance_base = min((e.dt for e in entrance_run.events), default=None)
    counter_base = min((e.dt for e in counter_run.events), default=None)

    entry_times = sorted(event_time(e, entrance_base)
                         for e in entrance_run.of(EventType.ENTRY))
    # Arrivals to checkout are the moment a track joins a lane, which is what the
    # live pipeline feeds the forecaster.  Reconstructing them from SERVICE_DONE
    # instead would only see shoppers who were actually served - during a rush
    # that is exactly the population the queue is holding back, and it dragged
    # the estimated lag down to half its true value.
    checkout_joins = counter_run.checkout_arrivals()
    queue_states: dict[str, list[tuple[float, float]]] = {}
    for event in counter_run.of(EventType.QUEUE_STATE):
        queue_states.setdefault(event.data["counter"], []).append(
            (event_time(event, counter_base), float(event.data["queue_len_smooth"])))
    for series in queue_states.values():
        series.sort()

    service_times = [float(e.data["service_s"]) for e in counter_run.of(EventType.SERVICE_DONE)]
    mu = 60.0 / (sorted(service_times)[len(service_times) // 2]) if service_times else None
    open_counters = sum(1 for c in counter_cfg.counters if c.open)

    # --- replay the forecaster over the merged, measured timeline ---------- #
    forecaster = Forecaster(
        target_wait_min=config.forecast.target_wait_min,
        max_prob_over_target=config.forecast.max_prob_over_target,
        max_counters=config.forecast.max_counters,
        min_lag_min=config.forecast.min_lag_min,
        max_lag_min=config.forecast.max_lag_min,
        conversion=config.forecast.conversion,
        default_service_s=config.forecast.default_service_s,
        period_s=config.forecast.period_s,
    )
    for t in entry_times:
        forecaster.note_entry(t)
    for t in checkout_joins:
        forecaster.note_checkout_arrival(t)

    duration = max(entrance_run.summary["video_seconds"], counter_run.summary["video_seconds"])
    clock = VideoClock()
    warnings: list[tuple[float, dict]] = []
    t = 0.0
    while t <= duration:
        clock.advance_to(t)
        # Only feed the forecaster what it would legitimately know by time t.
        live = Forecaster(
            target_wait_min=forecaster.target_wait_min,
            max_prob_over_target=forecaster.max_prob_over_target,
            max_counters=forecaster.max_counters,
            min_lag_min=forecaster.min_lag_min,
            max_lag_min=forecaster.max_lag_min,
            conversion=forecaster.conversion,
            default_service_s=forecaster.default_service_s,
            period_s=forecaster.period_s,
        )
        minute = int(t // 60)
        live.entries_per_min = {m: v for m, v in forecaster.entries_per_min.items() if m <= minute}
        live.checkout_per_min = {m: v for m, v in forecaster.checkout_per_min.items()
                                 if m <= minute}
        events = live.step(t, clock, mu_per_min=mu, open_counters=open_counters)
        for event in events:
            if event.data["recommended_counters"] > event.data["open_counters"]:
                warnings.append((t, dict(event.data)))
        t += config.forecast.period_s

    # --- ground truth ------------------------------------------------------ #
    threshold = min((c.congestion_len for c in counter_cfg.counters), default=5)
    truth_series = load_queue_length_gt(counter_video)
    truth_onset = congestion_onset(truth_series, threshold) if truth_series else None
    measured_onset = congestion_onset(queue_states, threshold)

    first_warning = warnings[0][0] if warnings else None
    reference_onset = truth_onset if truth_onset is not None else measured_onset
    lead_min = ((reference_onset - first_warning) / 60.0
                if (first_warning is not None and reference_onset is not None) else None)

    # A warning is "early but justified" if congestion did arrive later in the
    # clip; a warning with no congestion at all in the rest of the clip is a
    # false alarm.
    false_alarms = 0
    if reference_onset is not None:
        false_alarms = sum(1 for at, _ in warnings if at > reference_onset + 600)
    elif warnings:
        false_alarms = len(warnings)

    lag_truth = None
    truth_json = entrance_video.with_name(f"{entrance_video.stem}_truth.json")
    if truth_json.is_file():
        lag_truth = json.loads(truth_json.read_text(encoding="utf-8")).get("true_lag_min")

    return {
        "config": str(config_path),
        "entrance_video": str(entrance_video),
        "counter_video": str(counter_video),
        "command": (f"python -m storemind.eval.eval_forecast --config {config_path} "
                    f"--entrance-camera {entrance_camera} --counter-camera {counter_camera}"
                    + (f" --backend {backend}" if backend else "")),
        "detector": entrance_run.summary["detector"],
        "video_seconds": duration,
        "entries_detected": len(entry_times),
        "checkout_arrivals_detected": len(checkout_joins),
        "services_detected": len(service_times),
        "mu_per_min_measured": mu,
        "open_counters": open_counters,
        "congestion_threshold": threshold,
        "congestion_onset_truth_s": truth_onset,
        "congestion_onset_measured_s": measured_onset,
        "first_warning_s": first_warning,
        "lead_time_min": lead_min,
        "warnings": len(warnings),
        "false_alarms": false_alarms,
        "estimated_lag_min": forecaster.estimate_lag(duration),
        "true_lag_min": lag_truth,
        "first_warning_payload": warnings[0][1] if warnings else None,
    }


def print_report(report: dict) -> None:
    print(f"\n=== forecast lead time: {Path(report['entrance_video']).name} + "
          f"{Path(report['counter_video']).name} ===")
    print(f"command: {report['command']}")
    print(f"detector: {report['detector']}   clip: {report['video_seconds']:.0f}s")
    print(f"entries detected      : {report['entries_detected']}")
    print(f"services detected     : {report['services_detected']}  "
          f"(mu {fmt(report['mu_per_min_measured'], 2, '/min')})")
    print(f"congestion threshold  : {report['congestion_threshold']} waiting")
    print(f"congestion onset (GT) : {fmt(report['congestion_onset_truth_s'], 0, ' s')}")
    print(f"congestion onset (us) : {fmt(report['congestion_onset_measured_s'], 0, ' s')}")
    print(f"first warning         : {fmt(report['first_warning_s'], 0, ' s')}")
    print(f"LEAD TIME             : {fmt(report['lead_time_min'], 1, ' min')}")
    print(f"warnings / false alarms: {report['warnings']} / {report['false_alarms']}")
    print(f"lag estimated / true  : {fmt(report['estimated_lag_min'], 0, ' min')} / "
          f"{fmt(report['true_lag_min'], 1, ' min')}")
    if report["first_warning_payload"]:
        payload = report["first_warning_payload"]
        print(f"first warning said    : open {payload['recommended_counters']} counters "
              f"(lambda-hat {payload['lambda_hat_per_min']}/min, "
              f"mu {payload['mu_per_min_per_counter']}/min)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/rush.yaml")
    parser.add_argument("--entrance-camera", default="entrance")
    parser.add_argument("--counter-camera", default="checkout")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--entrance-model", default=None)
    parser.add_argument("--counter-model", default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    report = evaluate(args.config, args.entrance_camera, args.counter_camera,
                      backend=args.backend, entrance_model=args.entrance_model,
                      counter_model=args.counter_model, fps=args.fps)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
