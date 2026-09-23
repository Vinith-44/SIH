"""Shelf slot-state precision / recall / F1.

Scored two ways, because they answer different questions:

*   **Per-sample state accuracy** - sample the timeline every `--step` seconds and
    compare our current slot state with the ground-truth state.  This is what a
    shopkeeper experiences: what fraction of the day was the shelf status on the
    dashboard correct?
*   **Per-event detection** - treat each ground-truth transition into EMPTY (and
    LOW) as an event, and match it to our SLOT_STATE event within a tolerance.
    This gives precision / recall / F1 for EMPTY and LOW (target F1 >= 0.85,
    research/06 section 6) and the **detection delay**, which matters because a
    slot detected empty 4 minutes late is nearly as bad as one not detected.

Samples taken while the slot is occluded by a shopper are excluded from the
per-sample score and counted separately - the engine deliberately refuses to
guess during occlusion, and scoring a refusal as a wrong answer would reward
guessing.

    python -m storemind.eval.eval_shelf --config configs/demo.yaml --camera shelf-a \\
        --source ../videos/shelf/synthetic_shelf.mp4 --backend scripted \\
        --model ../videos/shelf/synthetic_shelf_detections.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..core.events import EventType
from .common import event_time, fmt, load_slots_gt, match_events, pct, prf, run_pipeline, state_at


def our_state_at(changes: list[tuple[float, str]], t: float) -> str:
    state = "UNKNOWN"
    for when, value in changes:
        if when <= t:
            state = value
        else:
            break
    return state


def evaluate(config: str, camera: str, source: str, step_s: float = 10.0,
             tolerance_s: float = 60.0, **run_kwargs) -> dict:
    video = Path(source)
    truth = load_slots_gt(video)
    result = run_pipeline(config, camera=camera, source=source, **run_kwargs)
    base = min((e.dt for e in result.events), default=None)

    ours: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for event in result.of(EventType.SLOT_STATE):
        key = (event.data["shelf"], event.data["slot"])
        ours.setdefault(key, []).append((event_time(event, base), event.data["state"]))
    for series in ours.values():
        series.sort()

    report: dict = {
        "video": str(video),
        "camera": camera,
        "command": result.command,
        "detector": result.summary["detector"],
        "video_seconds": result.summary["video_seconds"],
        "slot_state_events": len(result.of(EventType.SLOT_STATE)),
        "final_states": result.summary["cameras"][camera].get("shelf", {}),
        "metrics": {},
        "notes": [],
    }
    if truth is None:
        report["notes"].append("no *_gt_slots.csv next to this clip - shelf accuracy "
                               "NOT measured yet; states above are system output only")
        return report

    duration = result.summary["video_seconds"]
    # The engine cannot know anything before its first reference frame, and the
    # first observation needs K votes; exclude a short settling window so the
    # score measures steady-state behaviour, and say how long it was.
    settle_s = run_kwargs.pop("settle_s", 30.0) if "settle_s" in run_kwargs else 30.0

    correct = total = 0
    confusion: dict[str, dict[str, int]] = {}
    per_slot: dict[str, dict] = {}
    for key, truth_series in truth.items():
        slot_correct = slot_total = 0
        t = settle_s
        while t <= duration:
            expected = state_at(truth_series, t)
            if expected is None:
                t += step_s
                continue
            got = our_state_at(ours.get(key, []), t)
            confusion.setdefault(expected, {}).setdefault(got, 0)
            confusion[expected][got] += 1
            slot_total += 1
            if got == expected:
                slot_correct += 1
            t += step_s
        correct += slot_correct
        total += slot_total
        per_slot["/".join(key)] = {
            "samples": slot_total,
            "accuracy": (slot_correct / slot_total) if slot_total else None,
        }

    event_metrics: dict[str, dict] = {}
    for state in ("EMPTY", "LOW", "WRONG_ITEM"):
        predicted, expected = [], []
        for key, truth_series in truth.items():
            previous = None
            for when, value in truth_series:
                if value == state and previous != state:
                    expected.append(when)
                previous = value
            for when, value in ours.get(key, []):
                if value == state:
                    predicted.append(when)
        if not expected and not predicted:
            continue
        tp, fp, fn, errors = match_events(predicted, expected, tolerance_s)
        metrics = prf(tp, fp, fn)
        metrics["mean_detection_delay_s"] = (sum(errors) / len(errors)) if errors else None
        metrics["tolerance_s"] = tolerance_s
        event_metrics[state] = metrics

    report["metrics"] = {
        "sample_step_s": step_s,
        "settle_s": settle_s,
        "state_accuracy": (correct / total) if total else None,
        "samples": total,
        "per_slot": per_slot,
        "confusion": confusion,
        "events": event_metrics,
        "occlusion_gated_frames": sum(
            slot.get("occluded_frames", 0)
            for shelf in report["final_states"].values()
            for slot in shelf["slots"].values()),
    }
    return report


def print_report(report: dict) -> None:
    print(f"\n=== shelf: {Path(report['video']).name} ===")
    print(f"command: {report['command']}")
    print(f"detector: {report['detector']}   clip: {report['video_seconds']}s   "
          f"SLOT_STATE events: {report['slot_state_events']}")
    for shelf, entry in report["final_states"].items():
        for slot, info in entry["slots"].items():
            print(f"  {shelf}/{slot:4s} {info['state']:10s} fill={fmt(info['fill'])}"
                  f"  conf={fmt(info['confidence'])}  obs={info['observations']}"
                  f"  occluded={info['occluded_frames']}  sku={info['sku']}")
    metrics = report["metrics"]
    if not metrics:
        for note in report["notes"]:
            print(f"NOTE: {note}")
        return
    print(f"state accuracy       : {pct(metrics['state_accuracy'])} "
          f"over {metrics['samples']} samples every {metrics['sample_step_s']}s "
          f"(after a {metrics['settle_s']}s settle)")
    for state, values in metrics["events"].items():
        print(f"{state:10s} P/R/F1    : {fmt(values['precision'])} / {fmt(values['recall'])} / "
              f"{fmt(values['f1'])}   (tp {values['tp']}, fp {values['fp']}, fn {values['fn']}) "
              f"delay {fmt(values['mean_detection_delay_s'], 1, ' s')}")
    print(f"frames gated by occlusion: {metrics['occlusion_gated_frames']}")
    for note in report["notes"]:
        print(f"NOTE: {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--camera", default="shelf-a")
    parser.add_argument("--source", required=True)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--step", type=float, default=10.0)
    parser.add_argument("--tolerance", type=float, default=60.0)
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    report = evaluate(args.config, args.camera, args.source, step_s=args.step,
                      tolerance_s=args.tolerance, backend=args.backend,
                      model=args.model, fps=args.fps)
    print_report(report)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
