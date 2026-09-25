"""Session-random track ids (CLAUDE.md privacy rules): a secret offset per run."""

from __future__ import annotations

from storemind.core.config import StoreMindConfig
from storemind.inference.detector import Detection
from storemind.tracking.tracker import ID_OFFSET_MIN, SessionIdTracker, SimpleTracker, build_tracker


def _walk(tracker, steps: int = 10) -> set[int]:
    ids: set[int] = set()
    for step in range(steps):
        y = 100 + 10 * step
        ids |= {t.track_id for t in tracker.update([Detection((300, y, 340, y + 120), 0.9, 0)])}
    return ids


def test_ids_differ_between_sessions_and_stay_stable_within_one():
    config = StoreMindConfig().tracker.model_copy(update={"type": "simple"})
    sessions = [_walk(build_tracker(config)) for _ in range(5)]
    assert all(len(ids) == 1 for ids in sessions)          # one person, one id, all run long
    assert len({next(iter(ids)) for ids in sessions}) > 1   # a run's "track 1" is not the next run's
    assert all(next(iter(ids)) > ID_OFFSET_MIN for ids in sessions)


def test_every_tracker_kind_is_wrapped():
    for kind in ("bytetrack", "ocsort", "botsort", "sort", "simple"):
        config = StoreMindConfig().tracker.model_copy(update={"type": kind})
        assert isinstance(build_tracker(config), SessionIdTracker), kind


def test_fixed_offset_and_attribute_passthrough():
    tracker = SessionIdTracker(SimpleTracker(), offset=5_000_000)
    assert _walk(tracker) == {5_000_001}
    assert tracker.max_misses == SimpleTracker().max_misses
    tracker.reset()
    assert _walk(tracker, 1) == {5_000_001}
