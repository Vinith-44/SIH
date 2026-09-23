"""Floor-plan heatmap.

Audit item S5: the legacy heatmap accumulated in image pixels and decayed by
0.998 **per frame**, so the picture depended on how fast the laptop happened to
run and could never be merged across cameras.

Here:

*   foot points are projected through a 4-point homography onto a **floor plan**
    (metres), so two cameras looking at the same aisle add up correctly;
*   each sample contributes the **time** it represents (`dt` since the previous
    processed frame), so the map is in *person-seconds per cell* and is identical
    whether the pipeline ran at 3 FPS or 30 FPS;
*   accumulation is bucketed per minute, which is also what `agg_minute` stores.

If no homography is configured the engine falls back to image space (still
time-weighted) and says so, rather than silently producing a different unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.clock import Clock
from ..core.geometry import Point, apply_homography, foot_point, homography_from_points, to_pixels
from ..tracking.tracker import Track


@dataclass
class HeatmapConfig:
    plan_width: float = 10.0
    plan_height: float = 10.0
    cell_size: float = 0.5
    image_points: list[Point] | None = None
    plan_points: list[Point] | None = None


@dataclass
class FloorHeatmap:
    config: HeatmapConfig
    homography: np.ndarray | None = None
    image_space: bool = False
    grid: np.ndarray = field(init=False)
    minute_grids: dict[int, np.ndarray] = field(default_factory=dict)
    _last_s: float | None = field(default=None, init=False)
    samples: int = field(default=0, init=False)
    dropped_outside: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.cols = max(1, int(round(self.config.plan_width / self.config.cell_size)))
        self.rows = max(1, int(round(self.config.plan_height / self.config.cell_size)))
        self.grid = np.zeros((self.rows, self.cols), dtype=np.float32)

    @classmethod
    def from_config(cls, floor_plan, frame_size: tuple[int, int] | None = None) -> "FloorHeatmap":
        """`floor_plan` is a `core.config.FloorPlanConfig` or None."""
        if floor_plan is None:
            config = HeatmapConfig(plan_width=1.0, plan_height=1.0, cell_size=1.0 / 64)
            heatmap = cls(config)
            heatmap.image_space = True
            return heatmap
        config = HeatmapConfig(
            plan_width=floor_plan.plan_width, plan_height=floor_plan.plan_height,
            cell_size=floor_plan.cell_size,
            image_points=[tuple(p) for p in floor_plan.image_points],
            plan_points=[tuple(p) for p in floor_plan.plan_points],
        )
        heatmap = cls(config)
        width, height = frame_size or (1, 1)
        src = [tuple(p) for p in to_pixels(config.image_points, width, height)]
        heatmap.homography = homography_from_points(src, config.plan_points)
        return heatmap

    def _cell(self, point: Point) -> tuple[int, int] | None:
        x, y = point
        if not np.isfinite(x) or not np.isfinite(y):
            return None
        col = int(x / self.config.cell_size)
        row = int(y / self.config.cell_size)
        if 0 <= row < self.rows and 0 <= col < self.cols:
            return row, col
        return None

    def update(self, tracks: list[Track], clock: Clock, frame_size: tuple[int, int] | None = None) -> None:
        now = clock.monotonic_s()
        if self._last_s is None:
            self._last_s = now
            return
        dt = now - self._last_s
        self._last_s = now
        # Guard against a pathological gap (camera reconnect) dumping minutes of
        # weight into one cell.
        if dt <= 0 or dt > 5.0:
            return
        minute = int(now // 60)
        minute_grid = self.minute_grids.setdefault(minute, np.zeros_like(self.grid))

        for track in tracks:
            point = foot_point(track.xyxy)
            if self.image_space:
                width, height = frame_size or (1, 1)
                plan = (point[0] / max(1, width), point[1] / max(1, height))
            else:
                assert self.homography is not None
                plan = apply_homography(self.homography, point)
            cell = self._cell(plan)
            self.samples += 1
            if cell is None:
                self.dropped_outside += 1
                continue
            self.grid[cell] += dt
            minute_grid[cell] += dt

    def as_png(self, path: str, scale: int = 16) -> str:
        """Render the accumulated grid to a PNG for the dashboard and the PPT.

        This is a *derived statistic*, not imagery - it contains no pixels from
        any frame, so it does not breach the no-video-on-disk rule.
        """
        import cv2

        grid = self.grid
        peak = float(grid.max())
        normalised = (grid / peak * 255.0) if peak > 0 else np.zeros_like(grid)
        small = normalised.astype(np.uint8)
        big = cv2.resize(small, (self.cols * scale, self.rows * scale), interpolation=cv2.INTER_CUBIC)
        coloured = cv2.applyColorMap(big, cv2.COLORMAP_JET)
        if peak <= 0:
            coloured[:] = (60, 40, 30)
        cv2.imwrite(path, coloured)
        return path

    def summary(self) -> dict:
        peak_index = int(np.argmax(self.grid)) if self.grid.size else 0
        row, col = divmod(peak_index, self.cols)
        return {
            "unit": "person_seconds_per_cell",
            "space": "image" if self.image_space else "floor_plan",
            "cell_size": self.config.cell_size,
            "rows": self.rows,
            "cols": self.cols,
            "total_person_seconds": float(self.grid.sum()),
            "peak_cell": [row, col],
            "peak_person_seconds": float(self.grid.max()) if self.grid.size else 0.0,
            "samples": self.samples,
            "dropped_outside_plan": self.dropped_outside,
        }
