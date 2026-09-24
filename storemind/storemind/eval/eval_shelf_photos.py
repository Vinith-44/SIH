"""Shelf engine on our own labelled shelf photos (bucket B, M3 acceptance).

Input: a folder from `tools/shelf_capture.py` (JPEGs + `captures.csv` with
time and optional BH1750 lux) labelled with `tools/shelf_label.py`
(`labels.csv`), and the config whose camera defines the slots.

Photos are replayed in time order, exactly as the engine would see them live:
*   the first photo labelled all-FULL is the "Restocked" press (reference);
*   later, a photo labelled all-FULL right after one that was not is treated as
    another Restocked press (in the store, staff press the button);
*   `UNKNOWN` labels ("can't tell") are not scored.

Reports per-state precision / recall / F1 of the voted slot state, and EMPTY F1
split by the labelled lighting.  The M3 acceptance is EMPTY F1 >= 0.85 in both
day and evening light.

    python -m storemind.eval.eval_shelf_photos --photos ../videos/shelf_real/day1 \
        --config configs/myshelf.yaml --camera shelf-cam
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import cv2

from ..core.clock import ManualClock
from ..core.config import load_config
from ..core.geometry import Polygon
from .common import fmt, prf


def _read(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def evaluate(photos: Path, config_path: str, camera_name: str, version: str = "v2") -> dict:
    from ..analytics.shelf import ShelfEngine, SlotSpec
    from .eval_shelf_lighting import V1_SWITCHES

    config = load_config(config_path)
    camera = config.camera(camera_name)
    labels = {row["file"]: row for row in _read(photos / "labels.csv")}
    captures = {row["file"]: row for row in _read(photos / "captures.csv")}
    files = sorted(labels, key=lambda f: captures.get(f, {}).get("time", f))
    if not files:
        raise SystemExit(f"no labels.csv in {photos} - run tools/shelf_label.py first")

    first = cv2.imread(str(photos / files[0]))
    height, width = first.shape[:2]
    shelves = []
    for shelf in camera.shelves:
        shelf_config = shelf.model_copy(update=V1_SWITCHES) if version == "v1" else shelf
        slots = [SlotSpec(polygon=Polygon(s.name, [tuple(p) for p in s.points], "slot").resolve(width, height),
                          sku=s.sku, price=s.price) for s in shelf.slots]
        shelves.append((shelf.name, slots, shelf_config))
    engine = ShelfEngine(shelves, cam=camera_name, embed_threshold=config.shelf.embed_threshold)
    keys = [f"{s.name}/{slot.name}" for s in camera.shelves for slot in s.slots]

    clock = ManualClock()
    rows, have_reference, previous_all_full = [], False, False
    for i, name in enumerate(files):
        image = cv2.imread(str(photos / name))
        if image is None:
            continue
        label = labels[name]
        lux = captures.get(name, {}).get("lux")
        if version != "v1" and lux:
            engine.observe_lux(float(lux))
        all_full = all(label.get(k) == "FULL" for k in keys)
        clock.set(i * 60.0)
        if all_full and (not have_reference or not previous_all_full):
            engine.capture_references(image)
            have_reference = True
            previous_all_full = True
            continue
        previous_all_full = all_full
        if not have_reference:
            continue
        engine.update(image, [], clock)
        for shelf_name, entry in engine.shelves.items():
            for slot_name, runtime in entry["slots"].items():
                truth = label.get(f"{shelf_name}/{slot_name}")
                if truth and truth != "UNKNOWN":
                    rows.append({"lighting": label.get("lighting", "?"), "truth": truth,
                                 "state": runtime.state.value})
    return score(rows)


def score(rows: list[dict]) -> dict:
    out: dict = {"samples": len(rows)}
    for state in ("EMPTY", "LOW", "FULL", "WRONG_ITEM"):
        tp = sum(r["truth"] == state and r["state"] == state for r in rows)
        fp = sum(r["truth"] != state and r["state"] == state for r in rows)
        fn = sum(r["truth"] == state and r["state"] != state for r in rows)
        out[state] = prf(tp, fp, fn)
    by_light = defaultdict(list)
    for r in rows:
        by_light[r["lighting"]].append(r)
    out["empty_f1_by_lighting"] = {}
    for light, subset in sorted(by_light.items()):
        tp = sum(r["truth"] == "EMPTY" and r["state"] == "EMPTY" for r in subset)
        fp = sum(r["truth"] != "EMPTY" and r["state"] == "EMPTY" for r in subset)
        fn = sum(r["truth"] == "EMPTY" and r["state"] != "EMPTY" for r in subset)
        out["empty_f1_by_lighting"][light] = prf(tp, fp, fn)["f1"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--camera", required=True)
    args = parser.parse_args(argv)
    for version in ("v1", "v2"):
        report = evaluate(Path(args.photos), args.config, args.camera, version)
        empty = report["EMPTY"]
        print(f"{version}: {report['samples']} slot-photos  EMPTY P/R/F1 {fmt(empty['precision'])}/"
              f"{fmt(empty['recall'])}/{fmt(empty['f1'])}  by lighting "
              f"{json.dumps(report['empty_f1_by_lighting'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
