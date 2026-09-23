"""Queue engine: wait timers, service detection, smoothing."""

from __future__ import annotations

import pytest

from storemind.analytics.queue import CounterSpec, QueueEngine
from storemind.core.clock import ManualClock
from storemind.core.geometry import Polygon
from storemind.tracking.tracker import Track

W, H = 640, 480
LANE = [(0.3, 0.5), (0.7, 0.5), (0.7, 1.0), (0.3, 1.0)]
BILLING = [(0.35, 0.2), (0.65, 0.2), (0.65, 0.45), (0.35, 0.45)]

LANE_Y = 0.75 * H          # 360 - inside the lane
BILLING_Y = 0.33 * H       # 158 - inside the billing polygon
OUTSIDE_Y = 0.05 * H


def make_engine(**kwargs) -> QueueEngine:
    spec = CounterSpec(
        name="counter-1",
        lane=Polygon("lane", LANE).resolve(W, H),
        billing=Polygon("billing", BILLING).resolve(W, H),
        **kwargs,
    )
    return QueueEngine([spec], cam="counter-1")


def person(track_id: int, y: float, x: float = 0.5 * W) -> Track:
    return Track(track_id, (x - 20, y - 100, x + 20, y), 0.9)


def advance(engine, clock, tracks, seconds, step=0.2):
    events = []
    steps = int(seconds / step)
    for _ in range(steps):
        events += engine.update(tracks, clock)
        clock.advance(step)
    return events


def test_wait_and_service_are_measured_correctly():
    engine, clock = make_engine(min_service_s=3.0), ManualClock()
    state = engine.counters["counter-1"]

    advance(engine, clock, [person(1, LANE_Y)], 20.0)          # waiting 20 s
    advance(engine, clock, [person(1, BILLING_Y)], 15.0)       # served 15 s
    advance(engine, clock, [], 5.0)                            # gone

    assert len(state.completed_services) == 1
    assert state.completed_services[0] == pytest.approx(15.0, abs=0.5)
    assert state.completed_waits[0] == pytest.approx(20.0, abs=0.5)


def test_brief_visit_to_the_billing_spot_is_not_a_service():
    """Audit Q3: ID flicker produced `service_seconds: 0.4` in the old logs."""
    engine, clock = make_engine(min_service_s=3.0), ManualClock()
    state = engine.counters["counter-1"]

    advance(engine, clock, [person(1, LANE_Y)], 5.0)
    advance(engine, clock, [person(1, BILLING_Y)], 1.0)        # only 1 s at the counter
    advance(engine, clock, [person(1, LANE_Y)], 5.0)
    advance(engine, clock, [], 5.0)

    assert state.completed_services == []


def test_detection_gap_does_not_restart_the_wait():
    """Audit Q6: the old timer reset whenever a track blinked out."""
    engine, clock = make_engine(gap_tolerance_s=2.0), ManualClock()
    state = engine.counters["counter-1"]

    advance(engine, clock, [person(1, LANE_Y)], 10.0)
    advance(engine, clock, [], 1.0)                            # 1 s gap, inside tolerance
    advance(engine, clock, [person(1, LANE_Y)], 10.0)
    advance(engine, clock, [person(1, BILLING_Y)], 6.0)
    advance(engine, clock, [], 5.0)

    assert state.completed_waits[0] == pytest.approx(21.0, abs=1.0)


def test_leaving_the_lane_for_longer_than_tolerance_ends_the_visit():
    engine, clock = make_engine(gap_tolerance_s=2.0), ManualClock()
    state = engine.counters["counter-1"]
    advance(engine, clock, [person(1, LANE_Y)], 6.0)
    advance(engine, clock, [person(1, OUTSIDE_Y)], 6.0)        # walked away
    assert state.queue_len == 0
    assert 1 not in state.shoppers


def test_queue_length_counts_only_those_still_waiting():
    engine, clock = make_engine(min_service_s=3.0), ManualClock()
    state = engine.counters["counter-1"]
    tracks = [person(1, BILLING_Y), person(2, LANE_Y, 0.4 * W), person(3, LANE_Y, 0.6 * W)]
    advance(engine, clock, tracks, 6.0)
    # #1 is being served, #2 and #3 are waiting.
    assert state.queue_len == 2


def test_smoothed_length_ignores_a_single_frame_spike():
    engine, clock = make_engine(), ManualClock()
    state = engine.counters["counter-1"]
    steady = [person(1, LANE_Y, 0.4 * W)]
    advance(engine, clock, steady, 4.0)
    spike = steady + [person(i, LANE_Y, 0.45 * W + i) for i in range(2, 8)]
    engine.update(spike, clock)
    assert state.queue_len == 7           # raw count sees the spike
    assert state.queue_len_smooth <= 2.0   # median does not


def test_queue_state_events_are_emitted_on_a_schedule():
    engine, clock = make_engine(), ManualClock()
    engine.state_period_s = 5.0
    events = advance(engine, clock, [person(1, LANE_Y)], 21.0)
    states = [e for e in events if e.type.value == "QUEUE_STATE"]
    assert len(states) == 5              # t = 0, 5, 10, 15, 20
    assert states[0].data["counter"] == "counter-1"


def test_service_done_event_payload():
    engine, clock = make_engine(min_service_s=3.0), ManualClock()
    events = []
    events += advance(engine, clock, [person(1, LANE_Y)], 10.0)
    events += advance(engine, clock, [person(1, BILLING_Y)], 12.0)
    events += advance(engine, clock, [], 5.0)
    done = [e for e in events if e.type.value == "SERVICE_DONE"]
    assert len(done) == 1
    assert done[0].data["counter"] == "counter-1"
    assert done[0].data["service_s"] == pytest.approx(12.0, abs=0.5)
    assert done[0].data["wait_s"] == pytest.approx(10.0, abs=0.5)


def test_mu_is_derived_from_median_service_time():
    engine, clock = make_engine(min_service_s=3.0), ManualClock()
    for track_id in (1, 2, 3):
        advance(engine, clock, [person(track_id, LANE_Y)], 4.0)
        advance(engine, clock, [person(track_id, BILLING_Y)], 30.0)
        advance(engine, clock, [], 4.0)
    state = engine.counters["counter-1"]
    assert state.median_service_s == pytest.approx(30.0, abs=1.0)
    assert state.mu_per_min == pytest.approx(2.0, abs=0.1)


def test_a_person_outside_the_lane_is_never_in_the_queue():
    """Audit Q1: the legacy zone covered 84% of the frame."""
    engine, clock = make_engine(), ManualClock()
    advance(engine, clock, [person(1, LANE_Y, 0.05 * W)], 10.0)
    assert engine.counters["counter-1"].queue_len == 0
