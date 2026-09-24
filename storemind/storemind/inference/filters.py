"""Per-zone detection filters (Frigate-style), applied before tracking.

A CCTV view has places where the detector is reliably wrong: a mannequin by the
door, a poster, a reflection in the glass, a trolley bay.  Rather than raise the
confidence threshold for the whole frame (and lose small, distant people), a
filter tightens the rules only where its polygon is.  A filter with no polygon
applies to the whole frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.geometry import Polygon, foot_point
from .detector import Detection


@dataclass
class _Filter:
    polygon: Polygon | None
    min_score: float | None
    min_area_px: float
    max_area_px: float
    classes: set[int] | None

    def applies(self, det: Detection) -> bool:
        return self.polygon is None or self.polygon.contains(foot_point(det.xyxy))

    def passes(self, det: Detection) -> bool:
        x1, y1, x2, y2 = det.xyxy
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if self.min_score is not None and det.conf < self.min_score:
            return False
        if area < self.min_area_px or area > self.max_area_px:
            return False
        return self.classes is None or det.cls in self.classes


class DetectionFilter:
    def __init__(self, configs, width: int, height: int) -> None:
        """`configs` are `core.config.DetectionFilterConfig`; geometry is normalised."""
        self._filters = [
            _Filter(
                polygon=(Polygon(f"filter-{i}", [tuple(p) for p in c.points], "filter")
                         .resolve(width, height) if c.points else None),
                min_score=c.min_score,
                min_area_px=c.min_area_px,
                max_area_px=c.max_area_frac * width * height,
                classes=set(c.classes) if c.classes is not None else None,
            )
            for i, c in enumerate(configs)
        ]
        self.dropped = 0

    def __call__(self, detections: list[Detection]) -> list[Detection]:
        kept = [d for d in detections
                if all(f.passes(d) for f in self._filters if f.applies(d))]
        self.dropped += len(detections) - len(kept)
        return kept
