"""Shelf engine under changing light - synthetic timelines (bucket C).

Replays `tools/shelf_synth.py` timelines through the shelf engine and scores the
voted slot state after every step against the true state.

*   Dark steps (simulated lux 3) are scored separately: the right answer there
    is **UNKNOWN** ("too dark to tell"), and the failure that matters is a false
    EMPTY - a restock alert for a shelf that is simply unlit.
*   Every other step is scored per state (P/R/F1) and split by lighting.
*   Seeds 1-10 are for tuning, seeds 11-40 are reported.  Never tune on 11+.

Bucket C: this proves the logic copes with the lighting changes it was built for.
It is not an accuracy claim - only photos of a real shelf (bucket B) can be.

    python -m storemind.eval.eval_shelf_lighting               # report seeds 11-40
    python -m storemind.eval.eval_shelf_lighting --tune        # seeds 1-10
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from ..core.clock import ManualClock
from ..core.config import ShelfConfig
from ..core.geometry import Polygon
from .common import fmt, pct, prf

REPO = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"
TUNE_SEEDS = range(1, 11)
TEST_SEEDS = range(11, 41)


def _synth():
    spec = importlib.util.spec_from_file_location("shelf_synth", REPO / "tools" / "shelf_synth.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["shelf_synth"] = module
    spec.loader.exec_module(module)
    return module


def build_engine(version: str, shelf_overrides: dict | None = None):
    from ..analytics.shelf import ShelfEngine, SlotSpec

    synth = _synth()
    config = ShelfConfig(name="synth", vote_k=2, vote_n=3, **(shelf_overrides or {}))
    if version == "v1":
        # Everything v2 added, switched off: exactly the engine that shipped.
        config = config.model_copy(update=V1_SWITCHES)
    slots = [SlotSpec(polygon=Polygon(name, pts, "slot").resolve(synth.WIDTH, synth.HEIGHT))
             for name, pts in synth.slot_polygons().items()]
    return ShelfEngine([("synth", slots, config)], cam="synth")


V1_SWITCHES = {"empty_threshold": 0.15, "low_threshold": 0.4, "white_balance": False, "texture": "canny", "clahe": False, "canny": "fixed", "use_ssim": False, "glare_mask": False,
               "reference_bank": 1, "dark_lux": None, "dark_brightness": None,
               "lux_jump_ratio": None, "drift_alpha": 0.0, "rectify": False}


_TIMELINES: dict[int, list] = {}


def _timeline(seed: int) -> list:
    """Rendered once per process: every engine variant sees the same images."""
    if seed not in _TIMELINES:
        _TIMELINES[seed] = _synth().timeline(seed)
    return _TIMELINES[seed]


def run_seed(seed: int, version: str, use_lux: bool = True, overrides: dict | None = None) -> list[dict]:
    engine = build_engine(version, overrides)
    clock = ManualClock()
    rows = []
    for i, step in enumerate(_timeline(seed)):
        clock.set(i * 60.0)
        if version != "v1" and use_lux:
            engine.observe_lux(step.lux)
        if step.restock:
            engine.capture_references(step.image)
        else:
            engine.update(step.image, [], clock)
        for name, runtime in engine.shelves["synth"]["slots"].items():
            rows.append({"seed": seed, "step": i, "slot": name, "lighting": step.lighting,
                         "truth": step.truth[name], "state": runtime.state.value,
                         "restock": step.restock})
    return rows


def score(rows: list[dict]) -> dict:
    lit = [r for r in rows if r["lighting"] != "dark" and not r["restock"]]
    dark = [r for r in rows if r["lighting"] == "dark"]
    out: dict = {"steps_scored": len(lit), "accuracy": (sum(r["truth"] == r["state"] for r in lit)
                                                         / len(lit)) if lit else None}
    for state in ("EMPTY", "LOW", "WRONG_ITEM", "FULL"):
        tp = sum(r["truth"] == state and r["state"] == state for r in lit)
        fp = sum(r["truth"] != state and r["state"] == state for r in lit)
        fn = sum(r["truth"] == state and r["state"] != state for r in lit)
        out[state] = prf(tp, fp, fn)
    by_light = defaultdict(list)
    for r in lit:
        by_light[r["lighting"]].append(r)
    out["empty_f1_by_lighting"] = {}
    for light, subset in sorted(by_light.items()):
        tp = sum(r["truth"] == "EMPTY" and r["state"] == "EMPTY" for r in subset)
        fp = sum(r["truth"] != "EMPTY" and r["state"] == "EMPTY" for r in subset)
        fn = sum(r["truth"] == "EMPTY" and r["state"] != "EMPTY" for r in subset)
        out["empty_f1_by_lighting"][light] = prf(tp, fp, fn)["f1"]
    out["dark"] = {
        "slot_steps": len(dark),
        "false_empty": sum(r["state"] == "EMPTY" and r["truth"] != "EMPTY" for r in dark),
        "unknown": sum(r["state"] == "UNKNOWN" for r in dark),
        "states": dict(Counter(r["state"] for r in dark)),
    }
    return out


# Searched on the tuning seeds only (1-10).  The chosen values become the
# ShelfConfig / shelf-module defaults; the test seeds are then scored once.
GRID = {
    "empty_threshold": (0.15, 0.22, 0.28),
    "low_threshold": (0.4, 0.5),
    "dark_lux": (15.0, 60.0),
    "texture_blur": (3, 5),
    "texture_threshold": (0.35, 0.5),
}


def _grid_point(setting: dict) -> dict:
    from ..analytics import shelf as shelf_module

    shelf_module.TEXTURE_BLUR = setting["texture_blur"]
    shelf_module.TEXTURE_THRESHOLD = setting["texture_threshold"]
    overrides = {k: setting[k] for k in ("empty_threshold", "low_threshold", "dark_lux")}
    # The brightness fallback gets the dark cut-off that matches the lux one.
    overrides["dark_brightness"] = 0.12 if setting["dark_lux"] <= 15 else 0.2
    result = score(sum((run_seed(seed, "v2", True, overrides) for seed in TUNE_SEEDS), []))
    return {"setting": setting, "empty_f1": result["EMPTY"]["f1"] or 0.0,
            "accuracy": result["accuracy"] or 0.0, "dark_false_empty": result["dark"]["false_empty"]}


def tune_grid(workers: int = 8) -> list[dict]:
    import itertools
    from concurrent.futures import ProcessPoolExecutor

    settings = [dict(zip(GRID, values)) for values in itertools.product(*GRID.values())]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(_grid_point, settings))
    for row in rows:
        print(row["setting"], f"EMPTY F1 {row['empty_f1']:.3f} acc {row['accuracy']:.3f}", flush=True)
    rows.sort(key=lambda r: (r["empty_f1"], r["accuracy"]), reverse=True)
    return rows


def evaluate(seeds, overrides: dict | None = None) -> dict:
    report = {}
    for version, use_lux in (("v1", False), ("v2-no-lux", False), ("v2", True)):
        rows = []
        for seed in seeds:
            rows += run_seed(seed, "v1" if version == "v1" else "v2", use_lux, overrides)
        report[version] = score(rows)
    return report


def render(report: dict, seeds) -> str:
    lines = [f"# Shelf engine under changing light (synthetic, bucket C) - seeds "
             f"{seeds.start}-{seeds.stop - 1}", "",
             "| engine | state acc (lit) | EMPTY P/R/F1 | LOW F1 | WRONG_ITEM F1 | "
             "false EMPTY when dark | UNKNOWN when dark |", "|---|---|---|---|---|---|---|"]
    for version, s in report.items():
        e = s["EMPTY"]
        lines.append(f"| {version} | {pct(s['accuracy'])} | {fmt(e['precision'])}/{fmt(e['recall'])}/"
                     f"{fmt(e['f1'])} | {fmt(s['LOW']['f1'])} | {fmt(s['WRONG_ITEM']['f1'])} | "
                     f"{s['dark']['false_empty']} of {s['dark']['slot_steps']} | "
                     f"{s['dark']['unknown']} of {s['dark']['slot_steps']} |")
    lights = sorted({k for s in report.values() for k in s["empty_f1_by_lighting"]})
    lines += ["", "EMPTY F1 by lighting:", "", "| engine | " + " | ".join(lights) + " |",
              "|---|" + "---|" * len(lights)]
    for version, s in report.items():
        lines.append(f"| {version} | " + " | ".join(fmt(s["empty_f1_by_lighting"].get(k))
                                                    for k in lights) + " |")
    lines += ["", "`v1` = the engine as shipped before M3 (single reference, fixed Canny, no CLAHE). "
              "`v2-no-lux` = M3 engine without the BH1750 (brightness fallback). `v2` = with lux.", "",
              "Command: `python -m storemind.eval.eval_shelf_lighting`", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tune", action="store_true", help="score the tuning seeds instead")
    parser.add_argument("--grid", action="store_true", help="search GRID on the tuning seeds")
    parser.add_argument("--out", default=str(RESULTS / "shelf_lighting.json"))
    args = parser.parse_args(argv)
    if args.grid:
        rows = tune_grid()
        (RESULTS / "shelf_lighting_tuning.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print("best on tuning seeds:", rows[0])
        return 0
    seeds = TUNE_SEEDS if args.tune else TEST_SEEDS
    report = evaluate(seeds)
    text = render(report, seeds)
    print(text)
    if not args.tune:
        Path(args.out).write_text(json.dumps({"seeds": [seeds.start, seeds.stop - 1],
                                              "report": report}, indent=2), encoding="utf-8")
        Path(args.out).with_suffix(".md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
