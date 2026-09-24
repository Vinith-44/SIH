"""Queue v1 vs v2 on simulated tracks with exact truth (bucket C, M4).

Both engines see identical tracks from `eval/queue_sim.py` (an L-shaped queue
with passers-by, parties of 2, balkers, reneges and a rush), clean and with
tracker noise (jitter, 3% missed detections, 0.3 ID switches per person-minute).

*   v1 = `membership: polygon` on the L-shaped lane polygon (the shipped engine);
*   v2 = `membership: dwell` on the lane polyline (same area) with defaults.

Metrics: queue-length MAE (people, the smoothed value every 5 s), party-count
MAE (v1 has no parties: its people count is used), median-wait error, Little's
law W vs the true mean time in queue, joins counted vs true joins (passers-by
inflate this), balks / reneges vs truth, and tail-overflow agreement.

Seeds 1-10 tune (`--grid`), seeds 11-40 report.  Bucket C: logic, not accuracy.
The M4 acceptance on real video (queue MAE <= 1, wait error <= 20% on our own
canteen clip) is not measured until the team records one.

    python -m storemind.eval.eval_queue_v2 --grid       # tuning seeds
    python -m storemind.eval.eval_queue_v2              # report
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..analytics.queue import QueueEngine, counter_spec_from_config
from ..core.clock import ManualClock
from ..core.config import CounterConfig
from . import queue_sim as sim
from .common import fmt, pct

RESULTS = Path(__file__).resolve().parent / "results"
TUNE_SEEDS = range(1, 11)
TEST_SEEDS = range(11, 41)
GRID = {
    "join_dwell_s": (3.0, 5.0, 8.0),
    "max_join_speed": (0.1, 0.15, 0.25),
    "party_dist": (0.06, 0.08, 0.12),
}


def counter_config(version: str, **overrides) -> CounterConfig:
    if version == "v1":
        return CounterConfig(name="c1", lane=sim.LANE_POLYGON, billing=sim.BILLING)
    return CounterConfig(name="c1", billing=sim.BILLING, membership="dwell",
                         lane_polyline=sim.POLYLINE, lane_width=sim.LANE_WIDTH, **overrides)


def run(seed: int, version: str, noise: bool, overrides: dict | None = None) -> dict:
    frames, truth = sim.simulate(seed, noise=noise)
    spec = counter_spec_from_config(counter_config(version, **(overrides or {})), sim.WIDTH, sim.HEIGHT)
    engine = QueueEngine([spec], state_period_s=5.0)
    state = engine.counters["c1"]
    clock = ManualClock()
    predicted = {}
    for t, tracks in frames:
        clock.set(t)
        engine.update(tracks, clock)
        if round(t * sim.FPS) % (5 * sim.FPS) == 0:
            predicted[round(t, 3)] = (state.queue_len_smooth,
                                      state.parties if state.parties is not None else state.queue_len_smooth,
                                      state.tail_overflow)
    pairs = [(predicted[round(s["t"], 3)], s) for s in truth.samples if round(s["t"], 3) in predicted]
    littles_w, _ = state.littles(frames[-1][0])
    return {
        "queue_abs_err": [abs(p[0] - s["people"]) for p, s in pairs],
        "party_abs_err": [abs(p[1] - s["parties"]) for p, s in pairs],
        "tail_pairs": [(p[2], s["tail"]) for p, s in pairs],
        "tail_true": sum(s["tail"] for s in truth.samples),
        "median_wait_pred": state.median_wait_s, "median_wait_true": (statistics.median(truth.waits)
                                                                      if truth.waits else None),
        "littles_w": littles_w, "mean_sojourn_true": (statistics.mean(truth.sojourns)
                                                      if truth.sojourns else None),
        "joins_pred": len(state.joins), "joins_true": truth.joins_people,
        "balks_pred": state.balks, "balks_true": truth.balks,
        "reneges_pred": state.reneges, "reneges_true": truth.reneges,
    }


def _ratio(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def summarize(rows: list[dict]) -> dict:
    def rel(pred_key, true_key):
        errs = [abs(r[pred_key] - r[true_key]) / r[true_key] for r in rows
                if r[pred_key] is not None and r[true_key]]
        return statistics.mean(errs) if errs else None

    return {
        "queue_mae": statistics.mean(e for r in rows for e in r["queue_abs_err"]),
        "party_mae": statistics.mean(e for r in rows for e in r["party_abs_err"]),
        "median_wait_err": rel("median_wait_pred", "median_wait_true"),
        "littles_err": rel("littles_w", "mean_sojourn_true"),
        "joins_pred": sum(r["joins_pred"] for r in rows), "joins_true": sum(r["joins_true"] for r in rows),
        "balks_pred": sum(r["balks_pred"] for r in rows), "balks_true": sum(r["balks_true"] for r in rows),
        "reneges_pred": sum(r["reneges_pred"] for r in rows),
        "reneges_true": sum(r["reneges_true"] for r in rows),
        "tail_recall": _ratio([p for r in rows for p, s in r["tail_pairs"] if s]),
        "tail_false_alarm": _ratio([p for r in rows for p, s in r["tail_pairs"] if not s]),
        "tail_true_samples": sum(r["tail_true"] for r in rows),
    }


def _job(args):
    seed, version, noise, overrides = args
    return run(seed, version, noise, overrides)


def evaluate(seeds, overrides: dict | None = None, workers: int = 8) -> dict:
    report = {}
    with ProcessPoolExecutor(workers) as pool:
        for noise in (False, True):
            for version in ("v1", "v2"):
                jobs = [(s, version, noise, overrides if version == "v2" else None) for s in seeds]
                report[f"{version}{' noisy' if noise else ''}"] = summarize(list(pool.map(_job, jobs)))
    return report


def _grid_point(setting: dict) -> dict:
    rows = [run(seed, "v2", noisy, setting) for seed in TUNE_SEEDS for noisy in (False, True)]
    summary = summarize(rows)
    return {"setting": setting, **{k: summary[k] for k in ("queue_mae", "party_mae", "median_wait_err")}}


def tune_grid(workers: int = 8) -> list[dict]:
    settings = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(_grid_point, settings))
    # Queue MAE first (the acceptance metric), then parties, then wait error.
    rows.sort(key=lambda r: (round(r["queue_mae"], 2), round(r["party_mae"], 2), r["median_wait_err"] or 9))
    return rows


def render(report: dict, seeds) -> str:
    lines = [f"# Queue v1 vs v2 on simulated tracks (bucket C) - seeds {seeds.start}-{seeds.stop - 1}", "",
             "| engine | queue MAE (people) | party MAE | median-wait err | Little's-law W err | "
             "joins (truth) | balks (truth) | reneges (truth) | walked away unserved (truth) | "
             "tail overflow recall / false alarm |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in report.items():
        lines.append(f"| {name} | {fmt(s['queue_mae'])} | {fmt(s['party_mae'])} | {pct(s['median_wait_err'])} | "
                     f"{pct(s['littles_err'])} | {s['joins_pred']} ({s['joins_true']}) | "
                     f"{s['balks_pred']} ({s['balks_true']}) | {s['reneges_pred']} ({s['reneges_true']}) | "
                     f"{s['balks_pred'] + s['reneges_pred']} ({s['balks_true'] + s['reneges_true']}) | "
                     f"{pct(s['tail_recall'])} / {pct(s['tail_false_alarm'])} "
                     f"({s['tail_true_samples']} true-overflow samples) |")
    lines += ["", "v1 has no party, balk or renege logic: its party MAE uses its people count, and its "
              "balk/renege counts are 0 by construction.",
              "",
              "**The balk / renege split is not reliable:** with the tuned 3 s join time, people who stop "
              "3-6 s and leave are counted as joining and then reneging. Their sum (walked away unserved) is "
              "the usable number. Tail overflow is barely exercised by this simulator (see the true-overflow "
              "sample count); it is covered by unit tests only.", "",
              "Command: `python -m storemind.eval.eval_queue_v2`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--tune", action="store_true", help="report on the tuning seeds")
    args = parser.parse_args(argv)
    if args.grid:
        rows = tune_grid()
        (RESULTS / "queue_v2_tuning.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        for row in rows[:8]:
            print(row)
        return 0
    seeds = TUNE_SEEDS if args.tune else TEST_SEEDS
    report = evaluate(seeds)
    text = render(report, seeds)
    print(text)
    if not args.tune:
        out = RESULTS / "queue_v2.json"
        out.write_text(json.dumps({"seeds": [seeds.start, seeds.stop - 1], "report": report}, indent=2),
                       encoding="utf-8")
        out.with_suffix(".md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
