"""Entry/exit counting, including the bugs it was written to fix."""

from __future__ import annotations

from storemind.analytics.footfall import FootfallCounter
from storemind.core.clock import ManualClock
from storemind.core.geometry import Line
from storemind.tracking.tracker import Track

WIDTH, HEIGHT = 640, 480


def make_counter(**kwargs) -> FootfallCounter:
    line = Line("door", (0.0, 0.5), (1.0, 0.5), margin_px=10).resolve(WIDTH, HEIGHT)
    return FootfallCounter(line, cam="entrance", **kwargs)


def track_at(track_id: int, foot_y: float, foot_x: float = 320.0) -> Track:
    return Track(track_id, (foot_x - 20, foot_y - 120, foot_x + 20, foot_y), 0.9)


def walk(counter: FootfallCounter, clock: ManualClock, track_id: int,
         ys: list[float], step_s: float = 0.1) -> list:
    events = []
    for y in ys:
        events += counter.update([track_at(track_id, y)], clock)
        clock.advance(step_s)
    return events


def test_single_crossing_downwards_counts_one_entry():
    counter, clock = make_counter(), ManualClock()
    walk(counter, clock, 1, [100, 150, 200, 235, 245, 280, 330, 400])
    assert (counter.entries, counter.exits) == (1, 0)
    assert counter.occupancy == 1


def test_single_crossing_upwards_counts_one_exit():
    counter, clock = make_counter(), ManualClock()
    walk(counter, clock, 1, [400, 330, 280, 245, 235, 200, 150, 100])
    assert (counter.entries, counter.exits) == (0, 1)
    assert counter.occupancy == 0  # occupancy is floored at zero


def test_entry_direction_can_be_inverted_by_config():
    line = Line("door", (0.0, 0.5), (1.0, 0.5), margin_px=10).resolve(WIDTH, HEIGHT)
    counter = FootfallCounter(line, entry_direction="neg")
    clock = ManualClock()
    walk(counter, clock, 1, [100, 200, 280, 400])
    assert (counter.entries, counter.exits) == (0, 1)


def test_loitering_on_the_line_never_counts():
    """The bug that made the legacy centre-point counter unusable: a person
    standing on the line jitters across it and pumps the counter."""
    counter, clock = make_counter(), ManualClock()
    jitter = [239, 241, 238, 242, 240, 243, 237, 241, 239, 240] * 4
    walk(counter, clock, 1, [120, 180, 220] + jitter + [220, 180, 120])
    assert (counter.entries, counter.exits) == (0, 0)


def test_approach_and_retreat_without_crossing_counts_nothing():
    counter, clock = make_counter(), ManualClock()
    walk(counter, clock, 1, [100, 160, 200, 225, 200, 160, 100])
    assert (counter.entries, counter.exits) == (0, 0)


def test_cooldown_blocks_a_second_count_by_the_same_track():
    counter, clock = make_counter(cooldown_s=5.0), ManualClock()
    walk(counter, clock, 1, [100, 200, 300, 400], step_s=0.1)   # in
    walk(counter, clock, 1, [400, 300, 200, 100], step_s=0.1)   # out (different direction, allowed)
    walk(counter, clock, 1, [100, 200, 300, 400], step_s=0.1)   # in again, inside cooldown
    assert counter.entries == 1
    assert counter.exits == 1


def test_cooldown_expires():
    counter, clock = make_counter(cooldown_s=1.0), ManualClock()
    walk(counter, clock, 1, [100, 200, 300, 400], step_s=0.1)
    clock.advance(5.0)
    walk(counter, clock, 1, [400, 300, 200, 100], step_s=0.1)
    clock.advance(5.0)
    walk(counter, clock, 1, [100, 200, 300, 400], step_s=0.1)
    assert counter.entries == 2
    assert counter.exits == 1


def test_detection_gap_does_not_lose_the_crossing():
    """Audit S4: with a per-frame `last_side` a gap mid-crossing lost the count.
    The side is remembered on the track, so a gap is harmless."""
    counter, clock = make_counter(), ManualClock()
    walk(counter, clock, 1, [100, 160, 220])
    for _ in range(8):                      # eight frames with no detections
        counter.update([], clock)
        clock.advance(0.1)
    walk(counter, clock, 1, [300, 380])
    assert (counter.entries, counter.exits) == (1, 0)


def test_two_tracks_crossing_together_are_counted_separately():
    counter, clock = make_counter(), ManualClock()
    for y in [100, 160, 220, 260, 320, 400]:
        counter.update([track_at(1, y, 280.0), track_at(2, y, 360.0)], clock)
        clock.advance(0.1)
    assert (counter.entries, counter.exits) == (2, 0)


def test_events_carry_the_right_type_and_payload():
    counter, clock = make_counter(), ManualClock()
    events = walk(counter, clock, 7, [100, 200, 300, 400])
    assert len(events) == 1
    event = events[0]
    assert event.type.value == "ENTRY"
    assert event.data["direction"] == "in"
    assert event.data["track"] == 7
    assert event.data["line"] == "door"
    assert event.cam == "entrance"


def test_stale_track_state_is_forgotten():
    counter, clock = make_counter(forget_after_s=2.0), ManualClock()
    walk(counter, clock, 1, [100, 200])
    assert 1 in counter._tracks
    clock.advance(10.0)
    counter.update([track_at(2, 100)], clock)
    assert 1 not in counter._tracks
