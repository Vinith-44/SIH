"""A detector that replays known-correct boxes.

Used with the synthetic clips from `tools/make_synthetic_video.py`, where the
exact person boxes per frame are known.  Running the pipeline with this backend
measures the **analytics logic** (line crossing, hysteresis, queue timers, shelf
voting) with the detector removed as a variable.

This is a testing tool, and RESULTS.md labels every number produced with it as
"logic only, perfect detections".  It must never be used to claim detection
accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .detector import Detection, Detector


class ScriptedDetector(Detector):
    name = "scripted"

    def __init__(self, detections_json: str | Path, noise_px: float = 0.0,
                 drop_rate: float = 0.0, seed: int = 5) -> None:
        payload = json.loads(Path(detections_json).read_text(encoding="utf-8"))
        self.frames: dict[int, list[list[float]]] = {
            int(k): v for k, v in payload["frames"].items()
        }
        self.fps = payload.get("fps", 25)
        self.noise_px = noise_px
        self.drop_rate = drop_rate
        self._rng = np.random.default_rng(seed)
        self._index = 0

    def detect(self, image: np.ndarray) -> list[Detection]:
        boxes = self.frames.get(self._index, [])
        self._index += 1
        out: list[Detection] = []
        for box in boxes:
            if self.drop_rate and self._rng.random() < self.drop_rate:
                continue  # simulate a missed detection
            values = np.array(box, dtype=np.float32)
            if self.noise_px:
                values = values + self._rng.normal(0.0, self.noise_px, size=4).astype(np.float32)
            out.append(Detection((float(values[0]), float(values[1]),
                                  float(values[2]), float(values[3])), 0.95, 0))
        return out

    def seek(self, frame_index: int) -> None:
        self._index = frame_index
