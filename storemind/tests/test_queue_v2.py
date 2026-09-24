"""M4 queue v2: dwell membership, parties, stitching, balk/renege, bent lanes,
tail overflow, Little's law - and the simulator that evaluates them."""

from __future__ import annotations

import pytest

from storemind.analytics.queue import LanePath, QueueEngine, counter_spec_from_config
from storemind.core.clock import ManualClock
from storemind.core.config import CounterConfig
from storemind.tracking.tracker import Track

W, H = 640, 360
BILLING = [(0.45, 0.20), (0.62, 0.20), (0.62, 0.36), (0.45, 0.36)]
POLYLINE = [(0.535, 0.40), (0.535, 0.80), (0.30, 0.80)]


def engine(**overrides) -> QueueEngine:
    config = CounterConfig(name="c1", billing=BILLING, membership="dwell",
                           lane_polyline=POLYLINE, **overrides)
    return QueueEngine([counter_spec_from_config(config, W, H)])


def person(track_id: int, x: float, y: float) -> Track:
    """A box whose foot point is at normalised (x, y)."""
    fx, fy = x * W, y * H
    return Track(track_id, (fx - 18, fy - 120, fx + 18, fy), 0.9)


def run(e: QueueEngine, frames, clock=None, dt=0.2):
    clock = clock or ManualClock()
    for tracks in frames:
        e.update(tracks, clock)
        clock.advance(dt)
    return clock


def stand(track_id, x, y, seconds, dt=0.2):
    return [[person(track_id, x, y)] for _ in range(int(seconds / dt))]


# --------------------------------------------------------------------------- #

def test_lane_path_distance_and_progress():
    path = LanePath([(0, 0), (0, 100), (100, 100)], width_px=20)
    assert path.locate((5, 50)) == pytest.approx((5.0, 0.25))
    assert path.contains((5, 50)) and not path.contains((30, 50))
    assert path.locate((100, 100))[1] == pytest.approx(1.0)


def test_someone_standing_in_the_lane_joins_with_a_backdated_wait():
    e = engine()
    run(e, stand(1, 0.535, 0.60, 4.0))
    state = e.counters["c1"]
    assert state.queue_len == 1
    assert state.shoppers[1].joined_s == pytest.approx(0.0)   # wait counts from arrival


def test_a_passer_by_walking_along_the_lane_never_joins():
    e = engine()
    frames = [[person(1, 0.25 + 0.02 * i, 0.80)] for i in range(20)]   # 0.36 frame heights/s
    run(e, frames)
    state = e.counters["c1"]
    assert state.queue_len == 0 and state.joins == [] and state.balks == 0


def test_v1_polygon_membership_counts_the_same_passer_by():
    config = CounterConfig(name="c1", billing=BILLING,
                           lane=[(0.2, 0.74), (0.6, 0.74), (0.6, 0.86), (0.2, 0.86)])
    e = QueueEngine([counter_spec_from_config(config, W, H)])
    run(e, [[person(1, 0.25 + 0.02 * i, 0.80)] for i in range(20)])
    assert len(e.counters["c1"].joins) == 1


def test_two_people_standing_together_are_one_party():
    e = engine()
    frames = [[person(1, 0.520, 0.60), person(2, 0.550, 0.60), person(3, 0.535, 0.75)]
              for _ in range(30)]
    run(e, frames)
    state = e.counters["c1"]
    assert state.queue_len == 3 and state.parties == 2


def test_an_id_switch_hands_over_the_place_and_timer_instead_of_a_renege():
    e = engine()
    clock = run(e, stand(1, 0.535, 0.60, 6.0))
    joined = e.counters["c1"].shoppers[1].joined_s
    run(e, stand(99, 0.536, 0.605, 3.0), clock)          # tracker renamed the person
    state = e.counters["c1"]
    assert list(state.shoppers) == [99] and state.shoppers[99].joined_s == joined
    assert state.reneges == 0 and state.stitched == 1 and len(state.joins) == 1


def test_leaving_the_queue_without_service_is_a_renege_after_the_stitch_window():
    e = engine()
    clock = run(e, stand(1, 0.535, 0.60, 6.0))
    run(e, [[] for _ in range(40)], clock)               # 8 s, nobody takes over
    assert e.counters["c1"].reneges == 1


def test_stopping_briefly_and_leaving_is_a_balk():
    e = engine(join_dwell_s=5.0)
    clock = run(e, stand(1, 0.535, 0.78, 2.6))           # stood 2.6 s, did not join
    run(e, [[] for _ in range(20)], clock)
    state = e.counters["c1"]
    assert state.balks == 1 and state.joins == []


def test_the_queue_reaching_the_tail_raises_overflow_after_holding():
    e = engine()
    frames = [[person(i, 0.535, 0.44 + 0.05 * i) for i in range(8)] + [person(50, 0.32, 0.80)]
              for _ in range(60)]                         # 12 s, someone at the far end
    run(e, frames)
    assert e.counters["c1"].tail_overflow


def test_littles_law_matches_a_steady_queue():
    """One person joins every 20 s and each waits 60 s: L = 3, lambda = 3/min -> W = 60 s."""
    e = engine(join_dwell_s=0.5)
    clock, dt = ManualClock(), 1.0
    for second in range(400):
        tracks = []
        for k in range(second // 20 + 1):
            joined = k * 20
            if joined <= second < joined + 60:
                tracks.append(person(k + 1, 0.535, 0.44 + 0.06 * ((second - joined) % 6 + k % 3)))
        e.update(tracks, clock)
        clock.advance(dt)
    wait, arrivals = e.counters["c1"].littles(clock.monotonic_s())
    assert arrivals == pytest.approx(3.0, rel=0.1)
    assert wait == pytest.approx(60.0, rel=0.15)


def test_the_simulator_is_deterministic_and_its_truth_is_consistent():
    from storemind.eval import queue_sim

    frames_a, truth_a = queue_sim.simulate(3, duration_s=300)
    frames_b, truth_b = queue_sim.simulate(3, duration_s=300)
    assert len(frames_a) == 300 * queue_sim.FPS
    assert truth_a.samples == truth_b.samples and truth_a.waits == truth_b.waits
    assert all(s["parties"] <= s["people"] for s in truth_a.samples)
    assert truth_a.passers > 0
