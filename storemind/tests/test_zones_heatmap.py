"""Zone dwell and the FPS-independent floor heatmap."""

from __future__ import annotations

import pytest

from storemind.analytics.heatmap import FloorHeatmap, HeatmapConfig
from storemind.analytics.zones import ZoneEngine, ZoneSpec
from storemind.core.clock import ManualClock
from storemind.core.geometry import Polygon
from storemind.tracking.tracker import Track

W, H = 640, 480
PROMO = [(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)]


def make_engine(min_dwell_s=3.0, gap_tolerance_s=2.0) -> ZoneEngine:
    spec = ZoneSpec(polygon=Polygon("promo", PROMO, "promo").resolve(W, H),
                    min_dwell_s=min_dwell_s, gap_tolerance_s=gap_tolerance_s)
    return ZoneEngine([spec], cam="entrance")


def at(track_id: int, x: float, y: float) -> Track:
    return Track(track_id, (x - 15, y - 90, x + 15, y), 0.9)


INSIDE = (0.5 * W, 0.5 * H)
OUTSIDE = (0.05 * W, 0.05 * H)


def run(engine, clock, tracks, seconds, step=0.2):
    events = []
    for _ in range(int(seconds / step)):
        events += engine.update(tracks, clock)
        clock.advance(step)
    return events


def test_dwell_is_measured_and_published_on_exit():
    engine, clock = make_engine(), ManualClock()
    events = run(engine, clock, [at(1, *INSIDE)], 10.0)
    assert events == []                       # nothing published while still inside
    events = run(engine, clock, [at(1, *OUTSIDE)], 5.0)
    visits = [e for e in events if e.type.value == "ZONE_VISIT"]
    assert len(visits) == 1
    assert visits[0].data["dwell_s"] == pytest.approx(9.8, abs=0.5)
    assert visits[0].data["zone"] == "promo"
    assert visits[0].data["zone_kind"] == "promo"


def test_short_pass_through_is_not_a_visit():
    engine, clock = make_engine(min_dwell_s=3.0), ManualClock()
    events = run(engine, clock, [at(1, *INSIDE)], 1.0)
    events += run(engine, clock, [at(1, *OUTSIDE)], 5.0)
    assert [e for e in events if e.type.value == "ZONE_VISIT"] == []


def test_detection_gap_does_not_split_a_visit():
    """Audit S6: the legacy dwell timer reset on any tracking gap."""
    engine, clock = make_engine(gap_tolerance_s=2.0), ManualClock()
    run(engine, clock, [at(1, *INSIDE)], 6.0)
    run(engine, clock, [], 1.0)               # 1 s gap
    run(engine, clock, [at(1, *INSIDE)], 6.0)
    events = run(engine, clock, [at(1, *OUTSIDE)], 5.0)
    visits = [e for e in events if e.type.value == "ZONE_VISIT"]
    assert len(visits) == 1
    assert visits[0].data["dwell_s"] == pytest.approx(13.0, abs=1.0)


def test_long_absence_closes_the_visit():
    engine, clock = make_engine(gap_tolerance_s=2.0), ManualClock()
    run(engine, clock, [at(1, *INSIDE)], 6.0)
    events = run(engine, clock, [], 6.0)
    assert len([e for e in events if e.type.value == "ZONE_VISIT"]) == 1


def test_flush_closes_visits_at_end_of_stream():
    engine, clock = make_engine(), ManualClock()
    run(engine, clock, [at(1, *INSIDE)], 8.0)
    events = engine.flush(clock)
    assert len(events) == 1
    assert events[0].data["dwell_s"] == pytest.approx(7.8, abs=0.5)


def test_two_shoppers_dwelling_at_once():
    engine, clock = make_engine(), ManualClock()
    run(engine, clock, [at(1, *INSIDE), at(2, 0.45 * W, 0.6 * H)], 8.0)
    assert engine.occupancy("promo") == 2
    events = run(engine, clock, [], 5.0)
    assert len([e for e in events if e.type.value == "ZONE_VISIT"]) == 2


# --------------------------------------------------------------------------- #

def make_heatmap() -> FloorHeatmap:
    config = HeatmapConfig(plan_width=4.0, plan_height=4.0, cell_size=1.0,
                           image_points=[(0, 0), (640, 0), (640, 480), (0, 480)],
                           plan_points=[(0, 0), (4, 0), (4, 4), (0, 4)])
    heatmap = FloorHeatmap(config)
    from storemind.core.geometry import homography_from_points

    heatmap.homography = homography_from_points(config.image_points, config.plan_points)
    return heatmap


def test_heatmap_is_independent_of_frame_rate():
    """Audit S5: the legacy heatmap decayed per FRAME, so the picture depended on
    how fast the laptop ran.  Ours accumulates person-seconds."""
    totals = []
    for step in (0.1, 0.5, 1.0):
        heatmap, clock = make_heatmap(), ManualClock()
        for _ in range(int(20.0 / step)):
            heatmap.update([at(1, 320, 240)], clock, (W, H))
            clock.advance(step)
        totals.append(heatmap.grid.sum())
    assert max(totals) - min(totals) < 1.5      # within a sample of each other
    assert all(t > 17.0 for t in totals)


def test_heatmap_puts_the_person_in_the_right_cell():
    heatmap, clock = make_heatmap(), ManualClock()
    for _ in range(20):
        heatmap.update([at(1, 560, 420)], clock, (W, H))   # bottom-right quadrant
        clock.advance(0.5)
    row, col = heatmap.summary()["peak_cell"]
    assert (row, col) == (3, 3)


def test_heatmap_ignores_a_huge_time_gap():
    """A camera reconnect must not dump minutes of weight into one cell."""
    heatmap, clock = make_heatmap(), ManualClock()
    heatmap.update([at(1, 320, 240)], clock, (W, H))
    clock.advance(600.0)
    heatmap.update([at(1, 320, 240)], clock, (W, H))
    assert heatmap.grid.sum() == 0.0


def test_heatmap_counts_points_outside_the_plan_separately():
    heatmap, clock = make_heatmap(), ManualClock()
    heatmap.config.plan_width = 4.0
    for _ in range(10):
        heatmap.update([at(1, -500, 240)], clock, (W, H))
        clock.advance(0.5)
    summary = heatmap.summary()
    assert summary["dropped_outside_plan"] > 0
    assert summary["total_person_seconds"] == 0.0
