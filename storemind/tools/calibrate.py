"""Draw the store on a snapshot; write the config.

Audit item S8: the legacy config had zones like `[0.08, 0.55]` that were typed by
hand and matched no real camera, so every zone number in the system was fiction.
Calibration is not a nice-to-have - it is the difference between "the queue lane"
and "84% of the frame" (audit Q1).

    python tools/calibrate.py --source 0                       --camera entrance
    python tools/calibrate.py --source rtsp://user:pass@ip/... --camera counter-1
    python tools/calibrate.py --source ../videos/entrance/clip.mp4 --camera entrance

What you draw, and what it becomes:

    1  counting line     two clicks              -> line (entry/exit counting)
    2  zone              N clicks, Enter         -> zones (dwell, promo)
    3  queue lane        N clicks, Enter         -> counters[].lane
    4  billing spot      N clicks, Enter         -> counters[].billing
    5  shelf slot        N clicks, Enter         -> shelves[].slots (asks SKU + price)
    6  floor plan        exactly 4 clicks        -> floor_plan (heatmap homography)

Keys
    1-6     choose what you are drawing
    click   add a point
    Enter   finish the current shape
    u       undo the last point (or the last finished shape)
    d       delete the last finished shape
    r       save the reference frame for camera-tamper detection
    f       grab a fresh frame from the source
    s       save the YAML
    q/Esc   quit (asks to save)

Names, SKUs and prices are typed in the **terminal**, not the window - OpenCV has
no text input worth the name. The window stays open while you type.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storemind.ingest.sources import open_source        # noqa: E402

MODES = {
    ord("1"): "line",
    ord("2"): "zone",
    ord("3"): "lane",
    ord("4"): "billing",
    ord("5"): "slot",
    ord("6"): "floor",
}
COLOURS = {
    "line": (70, 70, 240),
    "zone": (230, 170, 60),
    "lane": (60, 190, 255),
    "billing": (80, 220, 100),
    "slot": (245, 245, 245),
    "floor": (200, 120, 240),
}
POINTS_NEEDED = {"line": 2, "floor": 4}


@dataclass
class Shape:
    kind: str
    points: list[tuple[float, float]]           # normalised
    name: str = ""
    sku: str | None = None
    price: float | None = None
    extra: dict = field(default_factory=dict)


class Calibrator:
    def __init__(self, frame: np.ndarray, camera: str) -> None:
        self.frame = frame
        self.camera = camera
        self.height, self.width = frame.shape[:2]
        self.mode = "line"
        self.pending: list[tuple[int, int]] = []
        self.shapes: list[Shape] = []
        self.message = "1 line  2 zone  3 lane  4 billing  5 slot  6 floor plan"

    # -- geometry ------------------------------------------------------- #
    def normalise(self, point: tuple[int, int]) -> tuple[float, float]:
        return (round(point[0] / self.width, 4), round(point[1] / self.height, 4))

    def denormalise(self, point: tuple[float, float]) -> tuple[int, int]:
        return (int(point[0] * self.width), int(point[1] * self.height))

    # -- interaction ----------------------------------------------------- #
    def on_mouse(self, event: int, x: int, y: int, _flags: int, _param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            self.pending.append((x, y))
            needed = POINTS_NEEDED.get(self.mode)
            if needed and len(self.pending) == needed:
                self.finish()

    def finish(self) -> None:
        needed = POINTS_NEEDED.get(self.mode)
        minimum = needed or 3
        if len(self.pending) < minimum:
            self.message = f"{self.mode} needs at least {minimum} points"
            return
        points = [self.normalise(p) for p in self.pending]
        self.pending = []
        shape = Shape(kind=self.mode, points=points)

        print()
        if self.mode == "line":
            shape.name = ask("counting line name", "door")
            direction = ask("which side is INSIDE the store? [pos/neg]", "pos")
            shape.extra["entry_direction"] = "neg" if direction.startswith("n") else "pos"
            shape.extra["margin_px"] = float(ask("hysteresis band in pixels", "12"))
        elif self.mode == "zone":
            shape.name = ask("zone name", f"zone-{len(self.shapes) + 1}")
            kind = ask("kind [zone/promo/shelf_front]", "zone")
            shape.extra["kind"] = kind if kind in ("zone", "promo", "shelf_front") else "zone"
            if shape.extra["kind"] == "shelf_front":
                shape.extra["shelf"] = ask("which shelf does it front?", "shelf-a")
                shape.extra["slot"] = ask("which slot?", "A1")
            shape.extra["min_dwell_s"] = float(ask("minimum dwell to count, seconds", "3"))
        elif self.mode in ("lane", "billing"):
            shape.name = ask(f"counter name for this {self.mode}", "counter-1")
        elif self.mode == "slot":
            shape.extra["shelf"] = ask("shelf name", "shelf-a")
            shape.name = ask("slot name", f"A{sum(1 for s in self.shapes if s.kind == 'slot') + 1}")
            shape.sku = ask("SKU / product name", "") or None
            price = ask("price in rupees (blank if unknown)", "")
            shape.price = float(price) if price.strip() else None
        elif self.mode == "floor":
            print("  Now the same four points on the FLOOR PLAN, in metres,")
            print("  in the same order you clicked them.")
            plan = []
            for index in range(4):
                raw = ask(f"    plan point {index + 1} as 'x,y' metres", "0,0")
                try:
                    x_str, y_str = raw.split(",")
                    plan.append([float(x_str), float(y_str)])
                except ValueError:
                    plan.append([0.0, 0.0])
            shape.extra["plan_points"] = plan
            shape.extra["plan_width"] = float(ask("floor plan width, metres", "10"))
            shape.extra["plan_height"] = float(ask("floor plan height, metres", "10"))
            shape.extra["cell_size"] = float(ask("heatmap cell size, metres", "0.5"))

        self.shapes.append(shape)
        self.message = f"added {shape.kind} '{shape.name}'"
        print(f"  -> added {shape.kind} '{shape.name}'")

    # -- drawing ---------------------------------------------------------- #
    def render(self) -> np.ndarray:
        canvas = self.frame.copy()
        for shape in self.shapes:
            colour = COLOURS[shape.kind]
            points = np.array([self.denormalise(p) for p in shape.points], dtype=np.int32)
            if shape.kind == "line":
                cv2.line(canvas, tuple(points[0]), tuple(points[1]), colour, 2)
            else:
                cv2.polylines(canvas, [points.reshape(-1, 1, 2)], True, colour, 2)
            label = shape.name + (f" ({shape.sku})" if shape.sku else "")
            cv2.putText(canvas, label, tuple(points[0] + np.array([4, -6])),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)

        colour = COLOURS[self.mode]
        for index, point in enumerate(self.pending):
            cv2.circle(canvas, point, 4, colour, -1)
            if index:
                cv2.line(canvas, self.pending[index - 1], point, colour, 1)

        counts: dict[str, int] = {}
        for shape in self.shapes:
            counts[shape.kind] = counts.get(shape.kind, 0) + 1
        summary = "  ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "nothing yet"

        panel = canvas[0:74, 0:canvas.shape[1]].copy()
        canvas[0:74, 0:canvas.shape[1]] = cv2.addWeighted(
            panel, 0.35, np.zeros_like(panel), 0.65, 0)
        for index, text in enumerate([
            f"camera '{self.camera}'   drawing: {self.mode.upper()}"
            f"   points: {len(self.pending)}",
            summary,
            self.message,
        ]):
            cv2.putText(canvas, text, (10, 22 + 20 * index), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (245, 245, 245), 1, cv2.LINE_AA)
        return canvas

    # -- output ------------------------------------------------------------ #
    def to_config(self, source: str, role: str, fps: float,
                  reference_frame: str | None) -> dict:
        camera: dict = {"name": self.camera, "source": source, "role": role, "fps": fps}
        if reference_frame:
            camera["reference_frame"] = reference_frame

        for shape in self.shapes:
            if shape.kind == "line":
                camera["line"] = {
                    "name": shape.name or "door",
                    "a": list(shape.points[0]),
                    "b": list(shape.points[1]),
                    "margin_px": shape.extra.get("margin_px", 12.0),
                    "entry_direction": shape.extra.get("entry_direction", "pos"),
                    "cooldown_s": 3.0,
                }
            elif shape.kind == "zone":
                zone = {
                    "name": shape.name,
                    "points": [list(p) for p in shape.points],
                    "kind": shape.extra.get("kind", "zone"),
                    "min_dwell_s": shape.extra.get("min_dwell_s", 3.0),
                }
                if shape.extra.get("shelf"):
                    zone["shelf"] = shape.extra["shelf"]
                    zone["slot"] = shape.extra.get("slot")
                camera.setdefault("zones", []).append(zone)
            elif shape.kind == "floor":
                camera["floor_plan"] = {
                    "image_points": [list(p) for p in shape.points],
                    "plan_points": shape.extra.get("plan_points", [[0, 0]] * 4),
                    "plan_width": shape.extra.get("plan_width", 10.0),
                    "plan_height": shape.extra.get("plan_height", 10.0),
                    "cell_size": shape.extra.get("cell_size", 0.5),
                }

        # Lanes and billing spots pair up by counter name.
        counters: dict[str, dict] = {}
        for shape in self.shapes:
            if shape.kind in ("lane", "billing"):
                entry = counters.setdefault(shape.name, {"name": shape.name})
                entry[shape.kind] = [list(p) for p in shape.points]
        for name, entry in counters.items():
            if "lane" not in entry or "billing" not in entry:
                print(f"  ! counter '{name}' is missing its "
                      f"{'lane' if 'lane' not in entry else 'billing spot'} - skipped")
                continue
            entry.update({"min_service_s": 3.0, "gap_tolerance_s": 2.0,
                          "open": True, "congestion_len": 5})
            camera.setdefault("counters", []).append(entry)

        shelves: dict[str, dict] = {}
        for shape in self.shapes:
            if shape.kind != "slot":
                continue
            shelf_name = shape.extra.get("shelf", "shelf-a")
            shelf = shelves.setdefault(shelf_name, {
                "name": shelf_name, "low_threshold": 0.45, "empty_threshold": 0.18,
                "vote_k": 3, "vote_n": 5, "occlusion_iou": 0.05,
                "auto_reference_s": None, "slots": [],
            })
            slot = {"name": shape.name, "points": [list(p) for p in shape.points]}
            if shape.sku:
                slot["sku"] = shape.sku
            if shape.price is not None:
                slot["price"] = shape.price
            shelf["slots"].append(slot)
        if shelves:
            camera["shelves"] = list(shelves.values())
            camera["role"] = "shelf"
            camera["shelf_period_s"] = 30.0

        return camera


def ask(prompt: str, default: str = "") -> str:
    try:
        value = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
    except EOFError:
        return default
    return value or default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="0 / rtsp://... / path.mp4 / csi:0")
    parser.add_argument("--camera", default="entrance")
    parser.add_argument("--role", default="entrance",
                        choices=["entrance", "counter", "shelf", "zone", "generic"])
    parser.add_argument("--fps", type=float, default=8.0)
    parser.add_argument("--out", default=None, help="YAML to write (default configs/<camera>.yaml)")
    parser.add_argument("--snapshot", default=None, help="use this image instead of a camera")
    parser.add_argument("--reference-dir", default="data/reference",
                        help="where the tamper-detection reference frame is saved")
    args = parser.parse_args()

    if args.snapshot:
        frame = cv2.imread(args.snapshot)
        if frame is None:
            raise SystemExit(f"could not read {args.snapshot}")
        source_handle = None
    else:
        source_handle = open_source(args.source)
        frame = None
        for _ in range(200):          # a live camera needs a moment to produce one
            got = source_handle.read()
            if got is not None:
                frame = got.image
                break
            cv2.waitKey(30)
        if frame is None:
            raise SystemExit(f"no frame from {args.source}")

    calibrator = Calibrator(frame, args.camera)
    window = f"StoreMind calibration - {args.camera}"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, calibrator.on_mouse)

    out_path = Path(args.out) if args.out else Path("configs") / f"{args.camera}.yaml"
    reference_path: str | None = None
    print(__doc__.split("Keys")[1] if "Keys" in __doc__ else "")

    while True:
        cv2.imshow(window, calibrator.render())
        key = cv2.waitKey(30) & 0xFF
        if key == 255:
            continue
        if key in MODES:
            calibrator.mode = MODES[key]
            calibrator.pending = []
            calibrator.message = f"drawing {calibrator.mode}"
        elif key in (13, 10):
            calibrator.finish()
        elif key == ord("u"):
            if calibrator.pending:
                calibrator.pending.pop()
            elif calibrator.shapes:
                removed = calibrator.shapes.pop()
                calibrator.message = f"removed {removed.kind} '{removed.name}'"
        elif key == ord("d") and calibrator.shapes:
            removed = calibrator.shapes.pop()
            calibrator.message = f"deleted {removed.kind} '{removed.name}'"
        elif key == ord("f") and source_handle is not None:
            got = source_handle.read()
            if got is not None:
                calibrator.frame = got.image
                calibrator.message = "grabbed a fresh frame"
        elif key == ord("r"):
            directory = Path(args.reference_dir)
            directory.mkdir(parents=True, exist_ok=True)
            reference_path = str(directory / f"{args.camera}_reference.png")
            cv2.imwrite(reference_path, calibrator.frame)
            calibrator.message = f"reference frame saved: {reference_path}"
            print(f"  -> {calibrator.message}")
        elif key == ord("s"):
            save(calibrator, out_path, args, reference_path)
            calibrator.message = f"saved {out_path}"
        elif key in (ord("q"), 27):
            if calibrator.shapes and ask("save before quitting? [y/N]", "y").lower().startswith("y"):
                save(calibrator, out_path, args, reference_path)
            break

    cv2.destroyAllWindows()
    if source_handle is not None:
        source_handle.close()
    return 0


def save(calibrator: Calibrator, out_path: Path, args, reference_path: str | None) -> None:
    camera = calibrator.to_config(args.source, args.role, args.fps, reference_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Merge into an existing file rather than clobbering the other cameras.
    document: dict = {}
    if out_path.is_file():
        document = yaml.safe_load(out_path.read_text(encoding="utf-8")) or {}
    cameras = document.get("cameras", [])
    cameras = [c for c in cameras if c.get("name") != camera["name"]]
    cameras.append(camera)
    document["cameras"] = cameras
    document.setdefault("store", "demo-store")
    document.setdefault("node", "pi5-01")

    out_path.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8")
    print(f"\n  wrote {out_path}")
    print("  Check it with:")
    print(f"    python -m storemind.run --config {out_path} --camera {camera['name']} --show\n")


if __name__ == "__main__":
    raise SystemExit(main())
