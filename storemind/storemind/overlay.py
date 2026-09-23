"""Debug overlay - only ever drawn when `--show` is passed.

Two rules from CLAUDE.md are enforced here:

*   the overlay window is opt-in, so the Pi runs headless by default;
*   the preview is **blurred** inside every person box before anything is drawn,
    so an engineer watching the debug window is not watching identifiable
    shoppers.  (The old PPT claimed blurring that the code did not do.  It does
    now, and only here - the analytics see the unblurred frame in RAM.)

Nothing in this module writes to disk.
"""

from __future__ import annotations

import cv2
import numpy as np

from .core.geometry import Line, Polygon, foot_point
from .tracking.tracker import Track

GREEN = (80, 220, 100)
AMBER = (60, 190, 255)
RED = (70, 70, 240)
BLUE = (230, 170, 60)
WHITE = (245, 245, 245)


def blur_people(image: np.ndarray, tracks: list[Track], strength: int = 25) -> np.ndarray:
    out = image.copy()
    height, width = out.shape[:2]
    for track in tracks:
        x1, y1, x2, y2 = (int(max(0, track.xyxy[0])), int(max(0, track.xyxy[1])),
                          int(min(width, track.xyxy[2])), int(min(height, track.xyxy[3])))
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        roi = out[y1:y2, x1:x2]
        kernel = max(3, (strength // 2) * 2 + 1)
        out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (kernel, kernel), 0)
    return out


def draw(image: np.ndarray, *, tracks: list[Track], line: Line | None = None,
         zones: list[Polygon] | None = None, lanes: list[Polygon] | None = None,
         billings: list[Polygon] | None = None, slots: list[Polygon] | None = None,
         hud: list[str] | None = None, blur: bool = True) -> np.ndarray:
    canvas = blur_people(image, tracks) if blur else image.copy()

    for polygon, colour in ((zones or [], BLUE), (lanes or [], AMBER),
                            (billings or [], GREEN), (slots or [], WHITE)):
        for poly in polygon:
            points = poly.pixels.astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(canvas, [points], True, colour, 2)
            if len(poly.pixels):
                anchor = poly.pixels.min(axis=0).astype(int)
                cv2.putText(canvas, poly.name, (int(anchor[0]), max(14, int(anchor[1]) - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)

    if line is not None and line._pa is not None and line._pb is not None:
        a = tuple(int(v) for v in line._pa)
        b = tuple(int(v) for v in line._pb)
        cv2.line(canvas, a, b, RED, 2)
        # Show the hysteresis band so calibration mistakes are visible.
        direction = np.array(line._pb) - np.array(line._pa)
        norm = np.linalg.norm(direction)
        if norm > 1e-6:
            normal = np.array([-direction[1], direction[0]]) / norm * line.margin_px
            for sign in (1, -1):
                offset = normal * sign
                cv2.line(canvas,
                         tuple(int(v) for v in (np.array(line._pa) + offset)),
                         tuple(int(v) for v in (np.array(line._pb) + offset)),
                         RED, 1, cv2.LINE_AA)

    for track in tracks:
        x1, y1, x2, y2 = (int(v) for v in track.xyxy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), GREEN, 2)
        fx, fy = foot_point(track.xyxy)
        cv2.circle(canvas, (int(fx), int(fy)), 4, RED, -1)
        cv2.putText(canvas, f"#{track.track_id}", (x1, max(12, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, GREEN, 1, cv2.LINE_AA)

    if hud:
        panel_height = 18 * len(hud) + 12
        panel = canvas[0:panel_height, 0:340].copy()
        canvas[0:panel_height, 0:340] = cv2.addWeighted(
            panel, 0.35, np.zeros_like(panel), 0.65, 0)
        for i, text in enumerate(hud):
            cv2.putText(canvas, text, (10, 22 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, WHITE, 1, cv2.LINE_AA)
    return canvas
