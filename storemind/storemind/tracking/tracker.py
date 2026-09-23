"""Multi-object tracking.

ByteTrack via `supervision` (MIT).  Motion only, no appearance features - that is
both the cheap choice and the privacy choice (N7: no re-identification, ever).
Track IDs are session-random and never leave the process except as integers in
events.

The legacy greedy centroid tracker (audit S2) is deliberately not ported: a fixed
100 px matching radius means one thing at 480p and another at 1080p, it has no
motion model, and greedy assignment produced the ID switches that made the old
entry counter return IN=0, OUT=0.

`SimpleTracker` is a dependency-free fallback used by tests and by
`--backend stub`; it is IoU-based with a small motion prior and is good enough for
synthetic clips, not for real footage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..inference.detector import Detection


@dataclass
class Track:
    track_id: int
    xyxy: tuple[float, float, float, float]
    conf: float
    age: int = 0
    misses: int = 0
    history: list[tuple[float, float]] = field(default_factory=list)


class Tracker:
    def update(self, detections: list[Detection]) -> list[Track]:
        raise NotImplementedError


class ByteTrackTracker(Tracker):
    """ByteTrack.

    `supervision.ByteTrack` is deprecated from supervision 0.28 and disappears in
    0.31, replaced by `trackers.ByteTrackTracker`.  A fresh `pip install` on the
    Pi would therefore break a pinned-to-supervision implementation, so the new
    package is preferred and the old class is only a fallback.
    """

    def __init__(self, track_activation_threshold: float = 0.25, lost_track_buffer: int = 30,
                 minimum_matching_threshold: float = 0.8, frame_rate: int = 8) -> None:
        import supervision as sv

        self._sv = sv
        try:
            from trackers import ByteTrackTracker as _ByteTrack

            self.tracker = _ByteTrack(
                track_activation_threshold=track_activation_threshold,
                lost_track_buffer=lost_track_buffer,
                minimum_iou_threshold=1.0 - minimum_matching_threshold,
                frame_rate=float(max(1, frame_rate)),
                minimum_consecutive_frames=1,
            )
            self._update = self.tracker.update
            self.impl = "trackers.ByteTrackTracker"
        except ImportError:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.tracker = sv.ByteTrack(
                    track_activation_threshold=track_activation_threshold,
                    lost_track_buffer=lost_track_buffer,
                    minimum_matching_threshold=minimum_matching_threshold,
                    frame_rate=max(1, int(frame_rate)),
                )
            self._update = self.tracker.update_with_detections
            self.impl = "supervision.ByteTrack"

    def update(self, detections: list[Detection]) -> list[Track]:
        sv = self._sv
        if detections:
            xyxy = np.array([d.xyxy for d in detections], dtype=np.float32)
            conf = np.array([d.conf for d in detections], dtype=np.float32)
            cls = np.array([d.cls for d in detections], dtype=int)
        else:
            xyxy = np.zeros((0, 4), dtype=np.float32)
            conf = np.zeros((0,), dtype=np.float32)
            cls = np.zeros((0,), dtype=int)
        result = self._update(sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls))
        tracks: list[Track] = []
        for i in range(len(result)):
            track_id = result.tracker_id[i]
            # `trackers` marks tentative (not yet confirmed) detections with id
            # -1 and reuses that value for every such detection.  Letting them
            # through makes every unconfirmed blob in the frame look like one
            # persistent shopper, which is how a 7-person queue came out as 14
            # arrivals.  Confirmed tracks only.
            if track_id is None or int(track_id) < 0:
                continue
            box = result.xyxy[i]
            confidence = float(result.confidence[i]) if result.confidence is not None else 1.0
            tracks.append(Track(int(track_id), tuple(float(v) for v in box), confidence))
        return tracks

    def reset(self) -> None:
        reset = getattr(self.tracker, "reset", None)
        if reset is not None:
            reset()


class SimpleTracker(Tracker):
    """IoU + centre-distance association.  No external dependency."""

    def __init__(self, iou_threshold: float = 0.25, max_misses: int = 15,
                 max_centre_move_frac: float = 0.25) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        self.max_centre_move_frac = max_centre_move_frac
        self._tracks: dict[int, Track] = {}
        self._next_id = 1

    @staticmethod
    def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 1e-9 else 0.0

    @staticmethod
    def _centre(box: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)

    def update(self, detections: list[Detection]) -> list[Track]:
        unmatched = set(range(len(detections)))
        # Global best-first assignment beats the legacy greedy-by-track-order loop.
        pairs: list[tuple[float, int, int]] = []
        for track_id, track in self._tracks.items():
            for i, det in enumerate(detections):
                score = self._iou(track.xyxy, det.xyxy)
                if score <= 0:
                    cx_t, cy_t = self._centre(track.xyxy)
                    cx_d, cy_d = self._centre(det.xyxy)
                    diagonal = max(1.0, np.hypot(track.xyxy[2] - track.xyxy[0],
                                                 track.xyxy[3] - track.xyxy[1]))
                    distance = float(np.hypot(cx_t - cx_d, cy_t - cy_d))
                    if distance < diagonal * self.max_centre_move_frac:
                        score = 0.01
                if score >= min(self.iou_threshold, 0.01):
                    pairs.append((score, track_id, i))
        pairs.sort(reverse=True)
        used_tracks: set[int] = set()
        for _score, track_id, det_index in pairs:
            if track_id in used_tracks or det_index not in unmatched:
                continue
            track = self._tracks[track_id]
            det = detections[det_index]
            track.xyxy, track.conf = det.xyxy, det.conf
            track.misses = 0
            track.age += 1
            used_tracks.add(track_id)
            unmatched.discard(det_index)

        for track_id, track in list(self._tracks.items()):
            if track_id not in used_tracks:
                track.misses += 1
                if track.misses > self.max_misses:
                    del self._tracks[track_id]

        for det_index in sorted(unmatched):
            det = detections[det_index]
            self._tracks[self._next_id] = Track(self._next_id, det.xyxy, det.conf)
            self._next_id += 1

        return [t for t in self._tracks.values() if t.misses == 0]

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1


def build_tracker(config, fallback: bool = False) -> Tracker:
    """`config` is a `storemind.core.config.TrackerConfig`."""
    if fallback:
        return SimpleTracker()
    try:
        return ByteTrackTracker(
            track_activation_threshold=config.track_activation_threshold,
            lost_track_buffer=config.lost_track_buffer,
            minimum_matching_threshold=config.minimum_matching_threshold,
            frame_rate=config.frame_rate,
        )
    except ImportError:
        return SimpleTracker()
