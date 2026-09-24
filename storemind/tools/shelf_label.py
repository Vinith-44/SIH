"""Label shelf photos slot by slot, in a few key presses (M3 bucket-B set).

Shows each photo from `tools/shelf_capture.py` with the slots from your config
drawn on it.  For the highlighted slot press:

    f = FULL   l = LOW   e = EMPTY   w = WRONG_ITEM   u = UNKNOWN (can't tell)
    backspace = previous slot   s = skip photo   q = save and quit

Then press the lighting for the photo: 1 = day, 2 = evening, 3 = night/tube,
4 = dark.  (The lighting split is what the evaluation reports by.)
Labels go to `labels.csv` next to the photos; re-running resumes where you
stopped.  A teammate who doesn't code can do this - it takes ~5 s per photo.

    python tools/shelf_label.py --photos ../videos/shelf_real/day1 --config configs/myshelf.yaml --camera shelf-cam
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KEYS = {ord("f"): "FULL", ord("l"): "LOW", ord("e"): "EMPTY", ord("w"): "WRONG_ITEM",
        ord("u"): "UNKNOWN"}
LIGHT = {ord("1"): "day", ord("2"): "evening", ord("3"): "night", ord("4"): "dark"}


def load_labels(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["file"]: row for row in csv.DictReader(handle)}


def main(argv: list[str] | None = None) -> int:
    from storemind.core.config import load_config

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--photos", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--camera", required=True)
    args = parser.parse_args(argv)

    folder = Path(args.photos)
    camera = load_config(args.config).camera(args.camera)
    slots = [(shelf.name, slot.name, slot.points) for shelf in camera.shelves for slot in shelf.slots]
    if not slots:
        raise SystemExit(f"camera {args.camera} has no shelf slots in {args.config}")
    columns = ["file", "lighting"] + [f"{s}/{n}" for s, n, _ in slots]
    labels_path = folder / "labels.csv"
    labels = load_labels(labels_path)
    photos = sorted(p for p in folder.glob("*.jpg") if p.name not in labels)

    def save() -> None:
        with labels_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(labels.values())

    for photo in photos:
        image = cv2.imread(str(photo))
        if image is None:
            continue
        h, w = image.shape[:2]
        row: dict[str, str] = {"file": photo.name}
        i = 0
        while i < len(slots):
            shelf, name, points = slots[i]
            canvas = image.copy()
            for j, (_s, _n, pts) in enumerate(slots):
                poly = (np.array(pts) * [w, h]).astype(np.int32)
                cv2.polylines(canvas, [poly], True, (0, 255, 255) if j == i else (120, 120, 120),
                              3 if j == i else 1)
            cv2.putText(canvas, f"{photo.name}  {shelf}/{name}: f/l/e/w/u", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("shelf_label", canvas)
            key = cv2.waitKey(0) & 0xFF
            if key == ord("q"):
                save()
                return 0
            if key == ord("s"):
                break
            if key == 8 and i > 0:
                i -= 1
                continue
            if key in KEYS:
                row[f"{shelf}/{name}"] = KEYS[key]
                i += 1
        else:
            cv2.putText(image, "lighting: 1 day  2 evening  3 night/tube  4 dark", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("shelf_label", image)
            while True:
                key = cv2.waitKey(0) & 0xFF
                if key in LIGHT:
                    row["lighting"] = LIGHT[key]
                    break
            labels[photo.name] = row
            save()
    cv2.destroyAllWindows()
    print(f"labels: {labels_path} ({len(labels)} photos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
