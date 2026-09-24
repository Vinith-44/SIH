"""Synthetic shelf timelines under changing light (bucket C test data for M3).

Why: the shelf engine's hardest real-world problem is lighting (research/23
section 3.3) - sun through the door, tube lights, evening, glare on glossy packs.
Until the team photographs a real shelf across a day (`tools/shelf_capture.py`,
`docs/HARDWARE_TODO.md`), this is the only way to know the *true* state of every
slot under every lighting condition.  It proves the logic, not the accuracy.

A scene is a shelf with 2 rows x 3 slots.  A timeline is a list of steps; each
step has a lighting condition, a simulated BH1750 lux value, the true state of
every slot and an image.  Timelines always start with a restock in daylight and
then see the full shelf under other lighting before things start disappearing -
exactly what a real deployment sees in its first day.

    python tools/shelf_synth.py --out ../videos/shelf_lighting --scenes 2   # look at it
    (the evaluation generates the same scenes in memory from their seeds)
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT = 640, 360
ROWS, COLS = 2, 3
LIGHTING = {
    #          gain  (b, g, r) tint     gamma  noise  lux
    "day":     (1.00, (1.00, 1.00, 1.00), 1.00, 3.0, 450),
    "evening": (0.60, (0.78, 0.95, 1.12), 1.25, 5.0, 140),
    "tube":    (0.90, (1.12, 1.04, 0.92), 0.95, 4.0, 330),
    "dim":     (0.32, (0.95, 1.00, 1.05), 1.10, 7.0, 45),
    "dark":    (0.07, (1.00, 1.00, 1.00), 1.00, 9.0, 3),
    "glare":   (1.00, (1.00, 1.00, 1.00), 1.00, 3.0, 520),
}
STATES = ("FULL", "LOW", "EMPTY", "WRONG_ITEM")


@dataclass
class Step:
    lighting: str
    lux: float
    truth: dict[str, str]           # slot -> FULL | LOW | EMPTY | WRONG_ITEM
    image: np.ndarray
    restock: bool = False


def slot_names() -> list[str]:
    return [f"{'AB'[r]}{c + 1}" for r in range(ROWS) for c in range(COLS)]


def slot_polygons() -> dict[str, list[tuple[float, float]]]:
    """Normalised 4-point polygons, as `tools/calibrate.py` would write them."""
    out = {}
    for r in range(ROWS):
        for c in range(COLS):
            x1, x2 = 0.06 + c * 0.31, 0.06 + c * 0.31 + 0.26
            y1, y2 = 0.10 + r * 0.45, 0.10 + r * 0.45 + 0.33
            out[f"{'AB'[r]}{c + 1}"] = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    return out


def _packet(rng: np.random.Generator, w: int, h: int, hue: int) -> np.ndarray:
    """One product face: saturated colour, a border, stripes and 'text' blocks."""
    face = np.zeros((h, w, 3), np.uint8)
    hsv = np.full((h, w, 3), (hue, 200 + rng.integers(0, 55), 150 + rng.integers(0, 90)), np.uint8)
    face[:] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    cv2.rectangle(face, (1, 1), (w - 2, h - 2), (20, 20, 20), 1)
    band_y = int(h * rng.uniform(0.25, 0.55))
    cv2.rectangle(face, (2, band_y), (w - 3, band_y + max(3, h // 8)), (245, 245, 245), -1)
    for _ in range(3):
        tx, ty = int(rng.integers(3, max(4, w - 12))), int(rng.integers(3, max(4, h - 6)))
        cv2.rectangle(face, (tx, ty), (tx + int(rng.integers(4, 10)), ty + 2), (15, 15, 15), -1)
    return face


class Scene:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        rng = np.random.default_rng(seed)
        self.hues = {name: int(rng.integers(0, 180)) for name in slot_names()}
        self.wrong_hues = {name: (h + 60 + int(rng.integers(0, 40))) % 180
                           for name, h in self.hues.items()}
        self.panel = np.array([175, 185, 190]) + rng.integers(-15, 15, 3)
        self.glare_slot = str(rng.choice(slot_names()))
        self.polys = slot_polygons()
        self._faces = {}
        for name in slot_names():
            self._faces[name] = [_packet(np.random.default_rng(seed * 100 + i), 30, 70, self.hues[name])
                                 for i in range(6)]
            self._faces[name + "_wrong"] = [
                _packet(np.random.default_rng(seed * 100 + 50 + i), 30, 70, self.wrong_hues[name])
                for i in range(6)]

    def render(self, truth: dict[str, str], lighting: str, rng: np.random.Generator) -> np.ndarray:
        img = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        img[:] = (92, 104, 112)                                   # wall
        for r in range(ROWS):                                     # shelf boards
            y = int((0.10 + r * 0.45 + 0.33) * HEIGHT)
            cv2.rectangle(img, (0, y), (WIDTH, y + 10), (60, 75, 95), -1)
        for name, poly in self.polys.items():
            (x1, y1), (x2, _), (_, y2) = poly[0], poly[1], poly[2]
            x1, x2, y1, y2 = int(x1 * WIDTH), int(x2 * WIDTH), int(y1 * HEIGHT), int(y2 * HEIGHT)
            img[y1:y2, x1:x2] = self.panel                        # back panel
            img[y1:y2, x1:x2] = np.clip(img[y1:y2, x1:x2].astype(int) +
                                        rng.integers(-4, 5, (y2 - y1, x2 - x1, 3)), 0, 255)
            state = truth[name]
            faces = self._faces[name + ("_wrong" if state == "WRONG_ITEM" else "")]
            count = {"FULL": 5, "WRONG_ITEM": 5, "LOW": 1, "EMPTY": 0}[state]
            for i in range(count):
                fx = x1 + 4 + i * 32
                face = faces[i]
                fh = min(face.shape[0], y2 - y1 - 4)
                if fx + face.shape[1] <= x2:
                    img[y2 - 2 - fh:y2 - 2, fx:fx + face.shape[1]] = face[-fh:]
        return apply_lighting(img, lighting, rng, glare_poly=self.polys[self.glare_slot])


def apply_lighting(img: np.ndarray, lighting: str, rng: np.random.Generator,
                   glare_poly=None) -> np.ndarray:
    gain, tint, gamma, noise, _lux = LIGHTING[lighting]
    out = img.astype(np.float32) / 255.0
    out = np.power(out, gamma) * gain * np.array(tint, np.float32)
    out = out * 255.0 + rng.normal(0.0, noise, out.shape)
    if lighting == "glare" and glare_poly is not None:
        (x1, y1), (x2, _), (_, y2) = glare_poly[0], glare_poly[1], glare_poly[2]
        cx, cy = int((x1 + x2) / 2 * WIDTH), int((y1 + y2) / 2 * HEIGHT)
        mask = np.zeros(out.shape[:2], np.float32)
        cv2.ellipse(mask, (cx, cy), (int(0.07 * WIDTH), int(0.09 * HEIGHT)), 20, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 6)
        out = out * (1 - mask[..., None]) + 255.0 * mask[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def timeline(seed: int, steps: int = 60) -> list[Step]:
    """Restock in daylight, see the full shelf under every non-dark light, then
    slots change state while the light keeps changing; one mid-way restock."""
    scene = Scene(seed)
    rng = np.random.default_rng(seed + 7)
    names = slot_names()
    truth = {n: "FULL" for n in names}
    out: list[Step] = [Step("day", LIGHTING["day"][4], dict(truth),
                            scene.render(truth, "day", rng), restock=True)]
    warmup = ["day", "evening", "tube", "dim", "day", "evening", "tube", "glare"]
    for lighting in warmup:
        out.append(Step(lighting, LIGHTING[lighting][4], dict(truth), scene.render(truth, lighting, rng)))
    light_cycle = ["day", "day", "tube", "evening", "evening", "dim", "dark", "glare", "tube"]
    # Light comes in runs (lights switched on, the sun moving, closing time), not
    # a new condition every snapshot: 2-4 steps for dark, 3-7 for the rest.
    # (The first version changed the light at random on every step. That is not
    # how a store behaves, and it was replaced before any test seed was scored -
    # docs/SHELF.md.)
    remaining = steps - len(out)
    lighting, run_left = "day", 0
    for i in range(remaining):
        # Change a slot every ~4 steps; states persist so voting has time to settle.
        if i % 4 == 0:
            slot = str(rng.choice(names))
            truth[slot] = str(rng.choice(["LOW", "EMPTY", "EMPTY", "WRONG_ITEM", "FULL"]))
        restock = i == remaining // 2
        if restock:
            truth = {n: "FULL" for n in names}
        if run_left <= 0:
            lighting = str(rng.choice(light_cycle))
            run_left = int(rng.integers(2, 5)) if lighting == "dark" else int(rng.integers(3, 8))
        run_left -= 1
        if restock:
            lighting = "day"
        lux = LIGHTING[lighting][4] * float(rng.uniform(0.9, 1.1))
        out.append(Step(lighting, lux, dict(truth), scene.render(truth, lighting, rng), restock=restock))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--scenes", type=int, default=2)
    parser.add_argument("--first-seed", type=int, default=1)
    args = parser.parse_args(argv)
    for seed in range(args.first_seed, args.first_seed + args.scenes):
        folder = Path(args.out) / f"scene_{seed:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        meta = []
        for i, step in enumerate(timeline(seed)):
            cv2.imwrite(str(folder / f"{i:03d}_{step.lighting}.jpg"), step.image)
            meta.append({"step": i, "lighting": step.lighting, "lux": round(step.lux, 1),
                         "restock": step.restock, "truth": step.truth})
        (folder / "truth.json").write_text(json.dumps({"polygons": slot_polygons(), "steps": meta},
                                                      indent=1), encoding="utf-8")
        print(f"wrote {folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
