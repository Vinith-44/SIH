"""Pick / put-back fusion on simulated shelf-sensor events (bucket C, M6).

Three engines see the same events from `eval/sensor_sim.py`:

*   **v1** - the original `FusionEngine`: every load-cell reading is a SENSOR
    value and a drop between consecutive readings is a pickup (no stable flag,
    no MEMS).  Given the benefit of the doubt: a shopper is marked near the shelf
    at the start of every visit.
*   **weight-only** - `ShelfInteractionEngine` without MEMS: steps between
    *stable* readings while a shopper is there.
*   **M6** - `ShelfInteractionEngine` with MEMS gating (TOUCH ... SETTLED).

A predicted pick / put-back matches a true one on the same slot, same action,
within -2 s .. +12 s of the release.  Units are scored on matched picks.
Seeds 1-10 tune, 11-40 report.  Bucket C: logic, not accuracy.  The M6
acceptance on hardware (pick/put-back >= 90%, camera knock >= 9/10) needs the
board: docs/HARDWARE_TODO.md.

    python -m storemind.eval.eval_fusion --grid     # tuning seeds
    python -m storemind.eval.eval_fusion            # report
"""

from __future__ import annotations

import argparse
import itertools
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..core.clock import ManualClock
from ..core.events import EventType, make_event
from ..fusion.fusion import FusionEngine
from ..fusion.interaction import ShelfInteractionEngine, SlotInfo
from . import sensor_sim as sim
from .common import fmt, prf

RESULTS = Path(__file__).resolve().parent / "results"
TUNE_SEEDS = range(1, 11)
TEST_SEEDS = range(11, 41)
GRID = {"noise_g": (10.0, 15.0, 25.0), "settle_timeout_s": (2.0, 4.0, 6.0)}
TICK_S = 0.5


def _event(clock, kind: str, data: dict):
    return make_event(ts=clock.now(), store="sim", node=sim.NODE, type=EventType(kind), data=data)


def run_v1(events, truth) -> list[dict]:
    engine = FusionEngine(store="sim", cell_map={f"{sim.SHELF}/{s}": ch for s, (ch, _u) in sim.SLOTS.items()})
    for slot in sim.SLOTS:
        engine.register_zone(f"front-{slot}", sim.SHELF, slot)
    clock = ManualClock()
    starts = sorted(a for a, _b in truth.visits)
    out, i = [], 0
    for t, kind, data in events:
        clock.set(t)
        while i < len(starts) and starts[i] <= t:          # shopper arrives: benefit of the doubt
            for slot in sim.SLOTS:
                engine.on_event(_event(clock, "ZONE_VISIT", {"zone": f"front-{slot}", "zone_kind": "shelf_front",
                                                             "track": 1, "dwell_s": 1.0}), clock)
            i += 1
        if kind != "WEIGHT":
            continue
        sensor = _event(clock, "SENSOR", {"node": sim.NODE, "sensor": "weight", "channel": data["slot"],
                                          "value": data["grams"], "unit": "g"})
        for e in engine.on_event(sensor, clock):
            out.append({"t": t, "type": e.type.value, **e.data})
    return out


def run_interaction(events, truth, mems: bool, **params) -> tuple[list[dict], list]:
    slots = [SlotInfo(sim.SHELF, s, ch, unit) for s, (ch, unit) in sim.SLOTS.items()]
    clock = ManualClock()
    engine = ShelfInteractionEngine(slots, store="sim", mems_enabled=mems,
                                    person_at_shelf=lambda shelf: sim.person_at(truth, clock.monotonic_s()),
                                    **params)
    out, alerts = [], []
    next_tick = 0.0
    visit_ends = sorted(b for _a, b in truth.visits)
    for t, kind, data in events + [(events[-1][0] + 20.0, None, None)]:
        while next_tick <= t:
            clock.set(next_tick)
            while visit_ends and visit_ends[0] <= next_tick:   # the pipeline's ZONE_VISIT at visit end
                engine.note_zone_visit(sim.SHELF, visit_ends.pop(0))
            out += [{"t": next_tick, "type": e.type.value, **e.data} for e in engine.tick(clock)]
            alerts += engine.drain_alerts()
            next_tick += TICK_S
        if kind is None or (kind == "SHELF_MOTION" and not mems):
            continue
        clock.set(t)
        out += [{"t": t, "type": e.type.value, **e.data} for e in engine.on_event(_event(clock, kind, data), clock)]
    return out, alerts


def score(predicted: list[dict], truth, alerts=None) -> dict:
    result = {}
    for action in ("pick", "put_back"):
        true = [a for a in truth.actions if a["action"] == action]
        pred = [p for p in predicted if p["type"] == "PICKUP" and p.get("action", "pick") == action]
        used, tp, units_ok = set(), 0, 0
        for a in true:
            match = next((i for i, p in enumerate(pred) if i not in used and p["slot"] == a["slot"]
                          and a["t"] - 2 <= p["t"] <= a["t"] + 12), None)
            if match is not None:
                used.add(match)
                tp += 1
                units_ok += pred[match].get("units") == a["units"]
        result[action] = {**prf(tp, len(pred) - tp, len(true) - tp),
                          "units_correct": units_ok, "matched": tp}
    touches_true = sum(a["action"] == "touch" for a in truth.actions)
    result["touch_events"] = {"predicted": sum(p.get("action") == "touch" for p in predicted
                                               if p["type"] == "PICKUP"), "true": touches_true}
    result["shrink_flags"] = sum(p["type"] == "SHRINK_FLAG" for p in predicted)
    result["fallen_true"] = len(truth.fallen)
    result["fallen_alerts"] = sum(key.startswith("FALLEN_STOCK") for key, *_ in (alerts or []))
    return result


def run_seed(args) -> dict:
    seed, params = args
    events, truth = sim.simulate(seed)
    m6, m6_alerts = run_interaction(events, truth, True, **params)
    weight_only, _ = run_interaction(events, truth, False, **params)
    return {"v1": score(run_v1(events, truth), truth), "weight-only": score(weight_only, truth),
            "M6": score(m6, truth, m6_alerts)}


def pool(rows: list[dict]) -> dict:
    out = {}
    for engine in rows[0]:
        agg = {}
        for action in ("pick", "put_back"):
            tp = sum(r[engine][action]["tp"] for r in rows)
            fp = sum(r[engine][action]["fp"] for r in rows)
            fn = sum(r[engine][action]["fn"] for r in rows)
            units = sum(r[engine][action]["units_correct"] for r in rows)
            agg[action] = {**prf(tp, fp, fn), "units_accuracy": units / tp if tp else None}
        agg["touch_predicted"] = sum(r[engine]["touch_events"]["predicted"] for r in rows)
        agg["touch_true"] = sum(r[engine]["touch_events"]["true"] for r in rows)
        agg["shrink_flags"] = sum(r[engine]["shrink_flags"] for r in rows)
        agg["fallen_true"] = sum(r[engine]["fallen_true"] for r in rows)
        agg["fallen_alerts"] = sum(r[engine]["fallen_alerts"] for r in rows)
        out[engine] = agg
    return out


def evaluate(seeds, params: dict | None = None, workers: int = 8) -> dict:
    with ProcessPoolExecutor(workers) as executor:
        return pool(list(executor.map(run_seed, [(s, params or {}) for s in seeds])))


def render(report: dict, seeds) -> str:
    lines = [f"# Pick / put-back fusion on simulated shelf sensors (bucket C) - seeds {seeds.start}-{seeds.stop - 1}",
             "", "| engine | pick P / R / F1 | pick units correct | put-back P / R / F1 | touches (true) | "
             "shrink flags | fallen-stock alerts (true) |", "|---|---|---|---|---|---|---|"]
    for name, s in report.items():
        p, b = s["pick"], s["put_back"]
        units = f"{s['pick']['units_accuracy'] * 100:.1f}%" if s["pick"]["units_accuracy"] is not None else "-"
        lines.append(f"| {name} | {fmt(p['precision'])} / {fmt(p['recall'])} / {fmt(p['f1'])} | {units} | "
                     f"{fmt(b['precision'])} / {fmt(b['recall'])} / {fmt(b['f1'])} | "
                     f"{s['touch_predicted']} ({s['touch_true']}) | {s['shrink_flags']} | "
                     f"{s['fallen_alerts']} ({s['fallen_true']}) |")
    lines += ["", "v1 has no put-back, touch or fallen-stock logic, and no units.", "",
              "Command: `python -m storemind.eval.eval_fusion`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--tune", action="store_true")
    args = parser.parse_args(argv)
    if args.grid:
        rows = []
        for values in itertools.product(*GRID.values()):
            params = dict(zip(GRID, values))
            m6 = evaluate(TUNE_SEEDS, params)["M6"]
            rows.append({"params": params, "pick_f1": m6["pick"]["f1"], "put_back_f1": m6["put_back"]["f1"],
                         "units": m6["pick"]["units_accuracy"]})
            print(rows[-1], flush=True)
        rows.sort(key=lambda r: ((r["pick_f1"] or 0) + (r["put_back_f1"] or 0), r["units"] or 0), reverse=True)
        (RESULTS / "fusion_tuning.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print("best:", rows[0])
        return 0
    seeds = TUNE_SEEDS if args.tune else TEST_SEEDS
    report = evaluate(seeds)
    text = render(report, seeds)
    print(text)
    if not args.tune:
        out = RESULTS / "fusion.json"
        out.write_text(json.dumps({"seeds": [seeds.start, seeds.stop - 1], "report": report}, indent=2),
                       encoding="utf-8")
        out.with_suffix(".md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
