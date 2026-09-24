"""Staff exclusion: keep employees out of customer counts, never identify anyone.

Two independent signals, either one marks a track as staff for the rest of its
life (research/23 section 3.1, the Xovis "staff badge" idea):

*   **Staff zones** - the cashier's side of the counter, the stock-room door.
    A track whose foot point stays inside one for `zone_dwell_s` is staff.
*   **Printed badge** - an ArUco marker (OpenCV `aruco`, no model, no
    training) printed on a lanyard card.  A marker whose centre falls inside a
    person box marks that track as staff.

What is kept is one boolean per session-random track id.  The marker id is only
compared against the allowed list and then dropped: the badge says "staff", not
"which member of staff".
"""

from __future__ import annotations

import numpy as np

from ..core.geometry import Polygon, foot_point
from ..tracking.tracker import Track


class StaffFilter:
    def __init__(self, config, width: int, height: int) -> None:
        """`config` is `core.config.StaffConfig`; geometry is normalised."""
        self.zones = [Polygon(f"staff-{i}", [tuple(p) for p in pts], "staff").resolve(width, height)
                      for i, pts in enumerate(config.zones)]
        self.zone_dwell_s = config.zone_dwell_s
        self.badge_ids = set(config.badge_ids)
        self.badge_every_n = max(1, config.badge_every_n)
        self._detector = self._make_aruco(config.aruco_dict) if config.badge else None
        self._zone_since: dict[int, float] = {}
        self.staff: set[int] = set()
        self.badges_seen = 0
        self._frames = 0

    @staticmethod
    def _make_aruco(name: str):
        import cv2

        dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, name))
        return cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

    def _badge_centres(self, image: np.ndarray) -> list[tuple[float, float]]:
        import cv2

        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        corners, ids, _rejected = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        centres = []
        for quad, marker in zip(corners, ids.flatten()):
            if self.badge_ids and int(marker) not in self.badge_ids:
                continue
            pts = quad.reshape(-1, 2)
            centres.append((float(pts[:, 0].mean()), float(pts[:, 1].mean())))
        return centres

    def update(self, image: np.ndarray | None, tracks: list[Track], now_s: float) -> set[int]:
        """Returns the set of track ids that are staff (grows, never shrinks)."""
        self._frames += 1
        live = {t.track_id for t in tracks}
        for track in tracks:
            if track.track_id in self.staff or not self.zones:
                continue
            foot = foot_point(track.xyxy)
            if any(zone.contains(foot) for zone in self.zones):
                since = self._zone_since.setdefault(track.track_id, now_s)
                if now_s - since >= self.zone_dwell_s:
                    self.staff.add(track.track_id)
            else:
                self._zone_since.pop(track.track_id, None)

        if (self._detector is not None and image is not None
                and self._frames % self.badge_every_n == 0):
            for cx, cy in self._badge_centres(image):
                self.badges_seen += 1
                owners = [t for t in tracks
                          if t.xyxy[0] <= cx <= t.xyxy[2] and t.xyxy[1] <= cy <= t.xyxy[3]]
                if owners:  # the smallest box is the person wearing it, not someone behind
                    owner = min(owners, key=lambda t: (t.xyxy[2] - t.xyxy[0]) * (t.xyxy[3] - t.xyxy[1]))
                    self.staff.add(owner.track_id)

        for stale in [tid for tid in self._zone_since if tid not in live]:
            del self._zone_since[stale]
        return self.staff
