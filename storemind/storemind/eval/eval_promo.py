"""Promotion analytics on simulated shoppers (bucket C): passers-by, stoppers, stop rate, dwell, picks.

    python -m storemind.eval.eval_promo --grid     # tuning seeds 1-10: approach band x stitching
    python -m storemind.eval.eval_promo            # report seeds 11-40 with the defaults in analytics/promo.py

The simulator and its truth definition are in `eval/promo_sim.py`.  "v1" is what the system could say before
promotion analytics: the zone engine's ZONE_VISIT count (stoppers only; passers-by were dropped, so no stop rate).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean, median

from ..analytics import promo as promo_mod
from ..analytics.promo import PromoEngine, PromoSpec
from ..analytics.zones import ZoneEngine, ZoneSpec
from ..core.clock import ManualClock
from ..core.events import EventType, PickupData, make_event
from ..tracking.tracker import Track
from . import promo_sim as sim

RESULTS = Path(__file__).resolve().parent / "results"
TUNE_SEEDS = list(range(1, 11))
TEST_SEEDS = list(range(11, 41))
BANDS = [0.04, 0.06, 0.08, 0.10, 0.12]


def run(seed: int, noisy: bool, band: float, stitch: bool) -> dict:
    people, truth = sim.simulate(seed)
    polygon = sim.zone_polygon()
    spec = PromoSpec(zone="promo-endcap", promo="Test offer", polygon=polygon, frame_height=sim.H,
                     min_dwell_s=sim.MIN_DWELL_S, approach_band=band, report_every_s=300.0,
                     linked_slot=f"{sim.SHELF}/{sim.SLOT}", has_scale=True, stitch=stitch)
    engine = PromoEngine([spec])
    zones = ZoneEngine([ZoneSpec(polygon=polygon, min_dwell_s=sim.MIN_DWELL_S)])
    clock = ManualClock()
    events = []
    pending = sim.pickups(people)
    for t, dets in sim.frames(people, seed, noisy):
        clock.set(t)
        while pending and pending[0][0] <= t:
            _t, action, units = pending.pop(0)
            engine.on_pickup(make_event(ts=clock.now(), store="s", node="n", type=EventType.PICKUP,
                                        data=PickupData(shelf=sim.SHELF, slot=sim.SLOT, evidence="sim",
                                                        action=action, units=units)), clock)
        tracks = [Track(tid, xyxy, 0.9) for tid, xyxy in dets]
        events += engine.update(tracks, clock)
        zones.update(tracks, clock)
    events += engine.flush(clock)
    zones.flush(clock)
    states = [e.data for e in events if e.type is EventType.PROMO_STATE]
    dwell_total = sum(s["dwell_total_s"] or 0.0 for s in states)
    stoppers = sum(s["stoppers"] for s in states)
    v1 = [v.dwell_s for v in zones.completed]
    return {
        "seed": seed, "noisy": noisy,
        "truth": {"passers_by": truth.passers_by, "stoppers": truth.stoppers,
                  "dwell_mean_s": mean(truth.dwells) if truth.dwells else None,
                  "dwell_median_s": median(truth.dwells) if truth.dwells else None,
                  "picks": truth.picks, "put_backs": truth.put_backs, "units": truth.units},
        "promo": {"passers_by": sum(s["passers_by"] for s in states), "stoppers": stoppers,
                  "dwell_mean_s": dwell_total / stoppers if stoppers else None,
                  "picks": sum(s["picks"] or 0 for s in states), "put_backs": sum(s["put_backs"] or 0 for s in states),
                  "units": sum(s["units_picked"] or 0 for s in states), "windows": len(states)},
        "v1": {"stoppers": len(v1), "dwell_mean_s": mean(v1) if v1 else None},
    }


def _rel(a: float, b: float) -> float:
    return abs(a - b) / b if b else 0.0


def summarise(rows: list[dict]) -> dict:
    t = {k: sum(r["truth"][k] for r in rows) for k in ("passers_by", "stoppers", "picks", "put_backs", "units")}
    p = {k: sum(r["promo"][k] for r in rows) for k in ("passers_by", "stoppers", "picks", "put_backs", "units")}
    rate_true = [r["truth"]["stoppers"] / (r["truth"]["stoppers"] + r["truth"]["passers_by"]) for r in rows]
    rate_ours = [r["promo"]["stoppers"] / max(1, r["promo"]["stoppers"] + r["promo"]["passers_by"]) for r in rows]
    true_dwells = [r["truth"]["dwell_mean_s"] for r in rows if r["truth"]["dwell_mean_s"]]
    our_dwells = [r["promo"]["dwell_mean_s"] for r in rows if r["promo"]["dwell_mean_s"]]
    return {
        "seeds": [rows[0]["seed"], rows[-1]["seed"]],
        "truth": t, "counted": p,
        "passers_err": mean(_rel(r["promo"]["passers_by"], r["truth"]["passers_by"]) for r in rows),
        "stoppers_err": mean(_rel(r["promo"]["stoppers"], r["truth"]["stoppers"]) for r in rows),
        "stop_rate_true": mean(rate_true), "stop_rate_ours": mean(rate_ours),
        "stop_rate_err_pp": mean(abs(a - b) for a, b in zip(rate_ours, rate_true)) * 100,
        "dwell_mean_err": _rel(mean(our_dwells), mean(true_dwells)) if our_dwells else None,
        "v1_stoppers": sum(r["v1"]["stoppers"] for r in rows),
        "v1_stoppers_err": mean(_rel(r["v1"]["stoppers"], r["truth"]["stoppers"]) for r in rows),
    }


def grid() -> list[dict]:
    rows = []
    for band in BANDS:
        for stitch in (False, True):
            scores = {}
            for noisy in (False, True):
                s = summarise([run(seed, noisy, band, stitch) for seed in TUNE_SEEDS])
                scores["noisy" if noisy else "clean"] = s
            objective = mean((s["passers_err"] + s["stoppers_err"]) / 2 for s in scores.values())
            rows.append({"approach_band": band, "stitch": stitch, "objective": objective,
                         "clean": scores["clean"], "noisy": scores["noisy"]})
            print(f"band {band:.2f} stitch {stitch!s:5}: objective {objective:.3f}  "
                  f"(clean {scores['clean']['passers_err']:.3f}/{scores['clean']['stoppers_err']:.3f}, "
                  f"noisy {scores['noisy']['passers_err']:.3f}/{scores['noisy']['stoppers_err']:.3f})", flush=True)
    rows.sort(key=lambda r: r["objective"])
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid", action="store_true", help="tune on seeds 1-10 and write promo_tuning.json")
    args = parser.parse_args(argv)
    if args.grid:
        rows = grid()
        (RESULTS / "promo_tuning.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        best = rows[0]
        print(f"best: approach_band {best['approach_band']}, stitch {best['stitch']}")
        return 0
    band, stitch = promo_mod.DEFAULT_APPROACH_BAND, promo_mod.DEFAULT_STITCH
    report = {}
    for noisy in (False, True):
        s = summarise([run(seed, noisy, band, stitch) for seed in TEST_SEEDS])
        report["noisy" if noisy else "clean"] = s
        print(f"{'noisy' if noisy else 'clean'}: passers {s['counted']['passers_by']} (truth {s['truth']['passers_by']}, "
              f"err {s['passers_err']:.1%}); stoppers {s['counted']['stoppers']} (truth {s['truth']['stoppers']}, "
              f"err {s['stoppers_err']:.1%}); stop rate {s['stop_rate_ours']:.1%} vs {s['stop_rate_true']:.1%} "
              f"({s['stop_rate_err_pp']:.1f} pp); dwell mean err {s['dwell_mean_err']:.1%}; "
              f"picks {s['counted']['picks']}/{s['truth']['picks']} units {s['counted']['units']}/{s['truth']['units']}; "
              f"v1 stoppers {s['v1_stoppers']} (err {s['v1_stoppers_err']:.1%}), passers-by not measured")
    out = {"bucket": "C", "seeds": [TEST_SEEDS[0], TEST_SEEDS[-1]], "approach_band": band, "stitch": stitch,
           "report": report}
    (RESULTS / "promo.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", RESULTS / "promo.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
