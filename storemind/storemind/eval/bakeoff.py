"""M1 tracker bake-off and counter tuning on CAVIAR (bucket A).

Protocol (version 2 - see "history" below for why it changed):

1.  Every variant replays the **same cached detections** (`eval/detcache.py`,
    YOLO11n @640, conf 0.25 at replay), so only the tracker or counter changes.
2.  Each tracker runs **once per clip** through the real pipeline; its tracks
    are logged, and every counter setting replays that log (exact: the counter
    sees only tracks and times).
3.  **2-fold cross-validation over the two camera views.**  Fold 1 tunes on the
    corridor view and tests on the front view; fold 2 the reverse.  Within a
    fold, the (tracker, counter setting) pair is chosen on the tuning view by
    the **mean of entry and exit event F1** (3 s tolerance); ties -> higher
    total count accuracy -> higher tracker IDF1 -> the least restrictive
    setting, with the current default (ByteTrack) first.
4.  **The number to quote is the pooled held-out result**: every one of the
    51 ground-truth crossings is scored by a setting chosen without it.
5.  The configuration that ships is then chosen the same way on all 16 clips.
    Its score on those clips is in-sample and is labelled so; it is not an
    accuracy claim.

History: run 1 (`results/tracker_bakeoff_run1.md`) followed a rule fixed before
any result: tracker by corridor IDF1, then counter on the corridor, report the
front.  The four trackers' corridor IDF1 spanned 0.749-0.765 and SORT won by
0.002 - noise - while having the most ID switches.  The front view also has
only 18 crossings, so one crossing moves its exit accuracy by 12.5 points.
The protocol was therefore revised to the cross-validated one above *after*
run 1 had been seen; both runs are published.

    python -m storemind.eval.detcache            # once, GPU recommended
    python -m storemind.eval.bakeoff             # writes results/tracker_bakeoff.{json,md}
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from pathlib import Path

from ..analytics.footfall import build_counter
from ..core.clock import ManualClock
from ..core.config import load_config
from ..core.geometry import Line
from .caviar import SCENARIOS, gt_crossings, parse_cvml
from .common import accuracy_from_counts, fmt, match_events, pct, prf

RESULTS = Path(__file__).resolve().parent / "results"
CONFIG = "configs/caviar.yaml"
TRACKERS = ("bytetrack", "ocsort", "botsort", "sort")
# Detector variants: name -> (weights, input size, score threshold at replay).
# The first one is today's default; ties go to earlier (cheaper) entries.
DETECTORS = {
    "yolo11n@640 c0.25": ("../models/yolo11n.pt", 640, 0.25),
    "yolo11n@640 c0.15": ("../models/yolo11n.pt", 640, 0.15),
    "yolo26n@640 c0.25": ("../models/yolo26n.pt", 640, 0.25),
    "yolo26n@640 c0.15": ("../models/yolo26n.pt", 640, 0.15),
    "yolo11n@960 c0.25": ("../models/yolo11n.pt", 960, 0.25),
    "yolo11n@960 c0.15": ("../models/yolo11n.pt", 960, 0.15),
    "yolo11s@640 c0.25": ("../models/yolo11s.pt", 640, 0.25),
    "yolo11s@640 c0.15": ("../models/yolo11s.pt", 640, 0.15),
}
DEFAULT_DETECTOR = next(iter(DETECTORS))
VIEWS = ("corridor", "front")
WIDTH, HEIGHT = 384, 288          # every CAVIAR clip

V1 = {"mode": "single"}
GRID = {                           # ordered least -> most restrictive
    "gate_px": (8.0, 12.0, 16.0, 24.0),
    "min_track_age_s": (0.0, 0.5, 1.0),
    "direction_mode": ("off", "balanced", "strict"),
    "confirm_s": (0.0, 0.5),
}
LINES = [V1] + [{"mode": "gate", **dict(zip(GRID, values))}
                for values in itertools.product(*GRID.values())]


# --------------------------------------------------------------------------- #
# Step 1: one pipeline run per (tracker, clip) -> tracking metrics + track log
# --------------------------------------------------------------------------- #

def track_clip(tracker: str, scenario: str, view: str, cache: Path, conf: float = 0.25) -> dict:
    from ..inference.cached import CachedDetector
    from .eval_caviar import run_clip

    log: list = []

    def configure(config) -> None:
        config.tracker.type = tracker

    report = run_clip(scenario, view, write_csv=False, detector=CachedDetector(cache, conf=conf),
                      configure=configure, timeline=True, track_log=log)
    return {"scenario": scenario, "view": view, "tracking": report["tracking"],
            "v1_pipeline": {"entries": report["predicted"]["entries"],
                            "exits": report["predicted"]["exits"]},
            "log": log}


def gt_for(scenario: str, view: str, config) -> tuple[list, dict]:
    from .eval_caviar import CAVIAR_DIR

    corridor_xml, front_xml = SCENARIOS[scenario]
    clip = parse_cvml(CAVIAR_DIR / (front_xml if view == "front" else corridor_xml))
    line = config.camera(view).line
    truth = gt_crossings(clip, (line.a[0] * WIDTH, line.a[1] * HEIGHT),
                         (line.b[0] * WIDTH, line.b[1] * HEIGHT), line.entry_direction)
    return truth, line


# --------------------------------------------------------------------------- #
# Step 2: replay any counter setting over a track log
# --------------------------------------------------------------------------- #

def count_clip(log: list, truth: list, base_line, setting: dict) -> dict:
    line_config = base_line.model_copy(update=setting)
    line = Line(line_config.name, tuple(line_config.a), tuple(line_config.b),
                line_config.margin_px).resolve(WIDTH, HEIGHT)
    counter = build_counter(line, line_config, store="eval", node="eval", cam="eval")
    clock = ManualClock()
    for video_s, tracks in log:
        clock.set(video_s)
        counter.update(tracks, clock)
    pred_in = [c.at_s for c in counter.crossings if c.direction == "in"]
    pred_out = [c.at_s for c in counter.crossings if c.direction == "out"]
    gt_in = [t for t, _track, d in truth if d == "in"]
    gt_out = [t for t, _track, d in truth if d == "out"]
    tp_i, fp_i, fn_i, _ = match_events(pred_in, gt_in, 3.0)
    tp_o, fp_o, fn_o, _ = match_events(pred_out, gt_out, 3.0)
    return {"gt": {"entries": len(gt_in), "exits": len(gt_out)},
            "predicted": {"entries": len(pred_in), "exits": len(pred_out)},
            "entry_event": prf(tp_i, fp_i, fn_i), "exit_event": prf(tp_o, fp_o, fn_o),
            "rejected": dict(getattr(counter, "rejected", {}) or {})}


def summarize(counts: list[dict], tracking: list[dict] | None = None) -> dict:
    def pooled(key):
        return prf(sum(r[key]["tp"] for r in counts), sum(r[key]["fp"] for r in counts),
                   sum(r[key]["fn"] for r in counts))

    gt_in = sum(r["gt"]["entries"] for r in counts)
    gt_out = sum(r["gt"]["exits"] for r in counts)
    p_in = sum(r["predicted"]["entries"] for r in counts)
    p_out = sum(r["predicted"]["exits"] for r in counts)
    entry, exit_ = pooled("entry_event"), pooled("exit_event")
    rejected: dict[str, int] = {}
    for r in counts:
        for reason, n in (r.get("rejected") or {}).items():
            rejected[reason] = rejected.get(reason, 0) + n
    out = {"clips": len(counts), "gt_entries": gt_in, "gt_exits": gt_out,
           "entries": p_in, "exits": p_out,
           "entry_acc": accuracy_from_counts(p_in, gt_in),
           "exit_acc": accuracy_from_counts(p_out, gt_out),
           "total_acc": accuracy_from_counts(p_in + p_out, gt_in + gt_out),
           "entry_event": entry, "exit_event": exit_,
           "event_f1_mean": statistics.mean([entry["f1"] or 0.0, exit_["f1"] or 0.0]),
           "rejected": rejected}
    if tracking:
        idtp = sum(t.get("IDTP", 0) for t in tracking)
        dets = sum(t.get("gt_dets", 0) + t.get("pred_dets", 0) for t in tracking)
        mota = [(t["MOTA"], t["gt_dets"]) for t in tracking if t.get("gt_dets")]
        out.update({"IDF1": 2 * idtp / dets if dets else None,
                    "IDSW": sum(t.get("IDSW", 0) for t in tracking),
                    "MOTA": sum(m * n for m, n in mota) / sum(n for _, n in mota) if mota else None})
    return out


# --------------------------------------------------------------------------- #

def pipes() -> list[tuple[str, str]]:
    """(detector, tracker) pairs, cheapest / current default first."""
    return [(d, t) for d in DETECTORS for t in TRACKERS]


def select(runs: dict, clips: list, truths: dict, lines: dict) -> tuple[tuple[str, str], dict, dict]:
    """Best ((detector, tracker), setting) on `clips` under the protocol's ordering."""
    candidates = []
    for p_index, pipe in enumerate(pipes()):
        tracking = [runs[(pipe, c)]["tracking"] for c in clips]
        idf1 = summarize([], tracking)["IDF1"] if tracking else 0.0
        for l_index, setting in enumerate(LINES):
            summary = summarize([count_clip(runs[(pipe, c)]["log"], truths[c], lines[c], setting)
                                 for c in clips])
            candidates.append(((summary["event_f1_mean"], summary["total_acc"] or 0.0, idf1 or 0.0,
                                -p_index, -l_index), pipe, setting, summary))
    candidates.sort(key=lambda c: c[0], reverse=True)
    _key, pipe, setting, summary = candidates[0]
    return pipe, setting, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(RESULTS / "tracker_bakeoff.json"))
    parser.add_argument("--quick", action="store_true", help="3 scenarios (smoke test)")
    args = parser.parse_args(argv)

    from .detcache import caviar_caches

    caches = {name: caviar_caches(model=model, imgsz=imgsz)
              for name, (model, imgsz, _conf) in DETECTORS.items()}
    config = load_config(CONFIG)
    scenarios = list(SCENARIOS)[:3] if args.quick else list(SCENARIOS)
    clips = [(s, v) for s in scenarios for v in VIEWS]
    truths, lines = {}, {}
    for clip in clips:
        truths[clip], lines[clip] = gt_for(*clip, config)

    runs = {}
    for pipe in pipes():
        detector, tracker = pipe
        print(f"  tracking: {detector} + {tracker}", flush=True)
        for clip in clips:
            runs[(pipe, clip)] = track_clip(tracker, *clip, caches[detector][clip],
                                            conf=DETECTORS[detector][2])

    # Sanity: the log replay must reproduce the pipeline's own v1 counts exactly.
    for (pipe, clip), run in runs.items():
        replay = count_clip(run["log"], truths[clip], lines[clip], V1)["predicted"]
        assert replay == run["v1_pipeline"], (pipe, clip, replay, run["v1_pipeline"])

    # Stage 1 (descriptive): every (detector, tracker) with the v1 counter.
    stage1 = []
    for pipe in pipes():
        views = {}
        for view in VIEWS:
            vclips = [c for c in clips if c[1] == view]
            views[view] = summarize(
                [count_clip(runs[(pipe, c)]["log"], truths[c], lines[c], V1) for c in vclips],
                [runs[(pipe, c)]["tracking"] for c in vclips])
        stage1.append({"detector": pipe[0], "tracker": pipe[1], "counter": "v1", "views": views})

    # Stage 2: 2-fold cross-validation over views.
    folds, held_out = [], []
    for tune, test in (("corridor", "front"), ("front", "corridor")):
        tune_clips = [c for c in clips if c[1] == tune]
        test_clips = [c for c in clips if c[1] == test]
        pipe, setting, tune_summary = select(runs, tune_clips, truths, lines)
        test_counts = [count_clip(runs[(pipe, c)]["log"], truths[c], lines[c], setting)
                       for c in test_clips]
        held_out += test_counts
        folds.append({"tune": tune, "test": test, "detector": pipe[0], "tracker": pipe[1],
                      "setting": setting,
                      "tune_summary": tune_summary, "test_summary": summarize(test_counts)})
        print(f"  fold tune={tune}: {pipe} {setting} -> test F1 "
              f"{folds[-1]['test_summary']['event_f1_mean']:.3f}", flush=True)

    baseline = summarize([count_clip(runs[((DEFAULT_DETECTOR, "bytetrack"), c)]["log"], truths[c],
                                     lines[c], V1) for c in clips])
    cv = summarize(held_out)

    # Stage 3: what ships = the same selection on every clip (in-sample).
    ship_pipe, ship_setting, ship_summary = select(runs, clips, truths, lines)

    payload = {"protocol": __doc__, "detectors": {k: list(v) for k, v in DETECTORS.items()},
               "scenarios": scenarios, "grid_size": len(LINES) * len(pipes()),
               "stage1": stage1, "folds": folds, "cv_held_out": cv,
               "baseline_bytetrack_v1": baseline,
               "shipped": {"detector": ship_pipe[0], "tracker": ship_pipe[1], "setting": ship_setting,
                           "in_sample": ship_summary}}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


def _row(name: str, s: dict) -> str:
    e, x = s["entry_event"], s["exit_event"]
    return (f"| {name} | {s['entries']} ({s['gt_entries']}) | {s['exits']} ({s['gt_exits']}) | "
            f"{pct(s['entry_acc'])} | {pct(s['exit_acc'])} | "
            f"{fmt(e['precision'])}/{fmt(e['recall'])}/{fmt(e['f1'])} | "
            f"{fmt(x['precision'])}/{fmt(x['recall'])}/{fmt(x['f1'])} |")


def render(p: dict) -> str:
    head = ("| | entries (truth) | exits (truth) | entry acc | exit acc | entry P/R/F1 | "
            "exit P/R/F1 |\n|---|---|---|---|---|---|---|")
    lines = ["# M1 tracker bake-off and counter tuning (CAVIAR, bucket A)", "",
             "Protocol v2 (2-fold cross-validation over the two views). Every row replays the same "
             "cached YOLO11n detections. Run 1 and the reason the protocol changed: "
             "`tracker_bakeoff_run1.md` and the docstring of `eval/bakeoff.py`.", "",
             "## Headline: held-out (cross-validated) vs today's default", "", head,
             _row("YOLO11n@640 + ByteTrack + v1 counter (today)", p["baseline_bytetrack_v1"]),
             _row("**counting v2, held-out (CV)**", p["cv_held_out"]), "",
             "## Folds", "",
             "| tune on | test on | chosen detector | chosen tracker | chosen counter | tune F1 | test F1 |",
             "|---|---|---|---|---|---|---|"]
    for f in p["folds"]:
        setting = ", ".join(f"{k}={v}" for k, v in f["setting"].items())
        lines.append(f"| {f['tune']} | {f['test']} | {f['detector']} | {f['tracker']} | {setting} | "
                     f"{fmt(f['tune_summary']['event_f1_mean'])} | "
                     f"{fmt(f['test_summary']['event_f1_mean'])} |")
    lines += ["", "## Detectors x trackers (descriptive, v1 counter)", "",
              "| detector | tracker | view | IDF1 | IDSW | MOTA | entry F1 | exit F1 |",
              "|---|---|---|---|---|---|---|---|"]
    for row in p["stage1"]:
        if row["counter"] == "v1":
            for view, s in row["views"].items():
                lines.append(f"| {row['detector']} | {row['tracker']} | {view} | {fmt(s['IDF1'], 3)} | {s['IDSW']} | "
                             f"{fmt(s['MOTA'])} | {fmt(s['entry_event']['f1'])} | "
                             f"{fmt(s['exit_event']['f1'])} |")
    shipped = p["shipped"]
    setting = ", ".join(f"{k}={v}" for k, v in shipped["setting"].items())
    lines += ["", "## What ships", "",
              f"**{shipped['detector']} + {shipped['tracker']}**, `{setting}` - chosen the same way on all {2 * len(p['scenarios'])} clips.", "",
              head, _row("shipped, **in-sample** (not an accuracy claim)", shipped["in_sample"]), "",
              f"Grid: {p['grid_size']} (tracker, counter) pairs. Command: "
              "`python -m storemind.eval.detcache && python -m storemind.eval.bakeoff`", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
