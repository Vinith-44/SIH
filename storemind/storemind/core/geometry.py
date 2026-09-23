"""Geometry helpers shared by every analytics engine.

Coordinates in config files are **normalised** (0..1 of frame width/height) so a
calibration made on a 1080p phone clip still works on a 640x360 CCTV sub-stream.
They are converted to pixels once, when the frame size is first known.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]


def foot_point(xyxy: tuple[float, float, float, float]) -> Point:
    """Bottom-centre of a box.

    Audit item S3: the box *centre* jitters across a counting line and is plain
    wrong for half-visible people.  Where a person stands on the floor is the
    bottom-centre, which is also what a floor-plan homography expects.
    """
    x1, y1, x2, y2 = xyxy
    return ((x1 + x2) / 2.0, y2)


def to_pixels(points: list[Point], width: int, height: int) -> np.ndarray:
    """Normalised (or already-pixel) points -> float32 pixel array.

    A point is treated as normalised when both coordinates are <= 1.0; a polygon
    drawn in pixels on a 640x360 frame never has both coords <= 1.
    """
    array = np.asarray(points, dtype=np.float32)
    if array.size and float(np.max(np.abs(array))) <= 1.0:
        array = array * np.array([width, height], dtype=np.float32)
    return array


@dataclass
class Line:
    """A counting line with a hysteresis band (audit S3)."""

    name: str
    a: Point
    b: Point
    margin_px: float = 12.0

    _pa: np.ndarray | None = None
    _pb: np.ndarray | None = None

    def resolve(self, width: int, height: int) -> "Line":
        pts = to_pixels([self.a, self.b], width, height)
        self._pa, self._pb = pts[0], pts[1]
        return self

    def signed_distance(self, point: Point) -> float:
        """Perpendicular distance in pixels; sign says which side of the line."""
        assert self._pa is not None and self._pb is not None, "call resolve() first"
        ax, ay = self._pa
        bx, by = self._pb
        px, py = point
        dx, dy = bx - ax, by - ay
        length = float(np.hypot(dx, dy))
        if length < 1e-6:
            return 0.0
        return float((dx * (py - ay) - dy * (px - ax)) / length)

    def side(self, point: Point) -> int:
        """+1 / -1 outside the dead band, 0 inside it (undecided)."""
        distance = self.signed_distance(point)
        if distance > self.margin_px:
            return 1
        if distance < -self.margin_px:
            return -1
        return 0


@dataclass
class Polygon:
    name: str
    points: list[Point]
    kind: str = "zone"

    _px: np.ndarray | None = None

    def resolve(self, width: int, height: int) -> "Polygon":
        self._px = to_pixels(self.points, width, height)
        return self

    @property
    def pixels(self) -> np.ndarray:
        assert self._px is not None, "call resolve() first"
        return self._px

    def contains(self, point: Point) -> bool:
        """Even-odd ray casting.  Pure numpy so there is no OpenCV dependency in
        the pure-logic modules the tests exercise."""
        polygon = self.pixels
        if len(polygon) < 3:
            return False
        x, y = point
        inside = False
        n = len(polygon)
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if (yi > y) != (yj > y):
                x_cross = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
                if x < x_cross:
                    inside = not inside
            j = i
        return inside


def homography_from_points(src: list[Point], dst: list[Point]) -> np.ndarray:
    """4-point homography without an OpenCV dependency (DLT + SVD).

    `src` = image points (pixels), `dst` = floor-plan points (metres or plan px).
    """
    if len(src) < 4 or len(dst) < 4:
        raise ValueError("need at least 4 point correspondences")
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.asarray(rows, dtype=np.float64))
    h = vt[-1].reshape(3, 3)
    if abs(h[2, 2]) > 1e-12:
        h = h / h[2, 2]
    return h


def apply_homography(h: np.ndarray, point: Point) -> Point:
    vec = h @ np.array([point[0], point[1], 1.0])
    if abs(vec[2]) < 1e-12:
        return (float("nan"), float("nan"))
    return (float(vec[0] / vec[2]), float(vec[1] / vec[2]))
