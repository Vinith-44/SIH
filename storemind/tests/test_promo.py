"""Promotion analytics (analytics/promo.py): passers-by, stoppers, dwell, windows, dates, picks,
track stitching, and the pipeline wiring."""

from __future__ import annotations

from datetime import date, datetime

from storemind.analytics.promo import PromoEngine, PromoSpec, distance_to_polygon, promo_spec_from_config
from storemind.core.clock import IST, ManualClock
from storemind.core.config import ZoneConfig
from storemind.core.events import EventType, PickupData, make_event
from storemind.core.geometry import Polygon
from storemind.tracking.tracker import Track

W = H = 1000
SQUARE = [(0.4, 0.4), (0.6, 0.4), (0.6, 0.6), (0.4, 0.6)]
FPS = 10


def spec(**kw) -> PromoSpec:
    base = dict(zone="endcap", promo="Diwali offer", polygon=Polygon("endcap", SQUARE, "promo").resolve(W, H),
                frame_height=H, report_every_s=60.0, linked_slot="endcap/E1", has_scale=True)
    return PromoSpec(**(base | kw))


def at(track_id: int, x: float, y: float) -> Track:
    """A track whose foot point is (x, y) in pixels."""
    return Track(track_id, (x - 20, y - 100, x + 20, y), 0.9)


def run(engine: PromoEngine, clock: ManualClock, frames: list[list[Track]], flush: bool = True) -> list:
    events = []
    for tracks in frames:
        events += engine.update(tracks, clock)
        clock.advance(1 / FPS)
    if flush:
        events += engine.flush(clock)
    return [e for e in events if e.type is EventType.PROMO_STATE]


def walk(track_id: int, y: float, seconds: float = 10.0) -> list[list[Track]]:
    steps = int(seconds * FPS)
    return [[at(track_id, i * W / steps, y)] for i in range(steps + 1)]


def stay(track_id: int, x: float, y: float, seconds: float) -> list[list[Track]]:
    return [[at(track_id, x, y)] for _ in range(int(seconds * FPS) + 1)]


def totals(states) -> dict:
    return {k: sum(s.data[k] or 0 for s in states) for k in ("passers_by", "stoppers", "picks", "units_picked")}


def test_distance_to_polygon():
    square = Polygon("s", SQUARE, "promo").resolve(W, H).pixels
    assert distance_to_polygon((500, 300), square) == 100.0
    assert distance_to_polygon((300, 300), square) == 100.0 * 2 ** 0.5


def test_walker_in_the_band_is_a_passer_by_and_a_far_walker_is_not():
    engine = PromoEngine([spec()])
    clock = ManualClock()
    states = run(engine, clock, walk(1, 650) + walk(2, 900))     # 50 px and 300 px below the zone
    assert totals(states)["passers_by"] == 1
    assert totals(states)["stoppers"] == 0


def test_a_short_pause_is_a_passer_by_and_a_long_one_is_a_stopper():
    engine = PromoEngine([spec()])
    clock = ManualClock()
    states = run(engine, clock, stay(1, 500, 500, 2.0) + [[]] * 30 + stay(2, 500, 500, 8.0))
    t = totals(states)
    assert (t["passers_by"], t["stoppers"]) == (1, 1)
    last = [s for s in states if s.data["stoppers"]][0]
    assert abs(last.data["dwell_mean_s"] - 8.0) < 0.2
    assert last.data["stop_rate"] == 0.5


def test_windows_add_up_and_are_stamped_at_their_end():
    engine = PromoEngine([spec(report_every_s=10.0)])
    clock = ManualClock()
    frames = []
    for i in range(4):                                    # one stopper every 10 s
        frames += stay(10 + i, 500, 500, 5.0) + [[]] * 50
    states = run(engine, clock, frames)
    assert totals(states)["stoppers"] == 4
    assert all(s.data["window_s"] == 10.0 for s in states[:-1])
    start = datetime(2026, 1, 1, 9, 0, 0, tzinfo=IST)
    assert [(datetime.fromisoformat(s.ts) - start).total_seconds() for s in states[:3]] == [10.0, 20.0, 30.0]


def test_a_gap_in_frames_still_stamps_each_window_at_its_end():
    engine = PromoEngine([spec(report_every_s=10.0)])
    clock = ManualClock()
    engine.update([], clock)
    clock.set(35.0)
    states = engine.update([], clock)
    start = datetime(2026, 1, 1, 9, 0, 0, tzinfo=IST)
    assert [(datetime.fromisoformat(s.ts) - start).total_seconds() for s in states] == [10.0, 20.0, 30.0]


def test_outside_its_dates_a_promo_counts_nothing():
    engine = PromoEngine([spec(start_date=date(2026, 2, 1))])     # ManualClock day is 2026-01-01
    clock = ManualClock()
    states = run(engine, clock, stay(1, 500, 500, 8.0))
    assert states and all(s.data["active"] is False and s.data["stoppers"] is None for s in states)


def pickup(clock, action="pick", units=2, slot="E1"):
    return make_event(ts=clock.now(), store="s", node="n", type=EventType.PICKUP,
                      data=PickupData(shelf="endcap", slot=slot, evidence="test", action=action, units=units))


def test_picks_count_at_the_linked_slot_only():
    engine = PromoEngine([spec()])
    clock = ManualClock()
    engine.update([], clock)
    engine.on_pickup(pickup(clock), clock)
    engine.on_pickup(pickup(clock, action="put_back", units=1), clock)
    engine.on_pickup(pickup(clock, slot="E2"), clock)
    engine.on_pickup(pickup(clock, action="touch"), clock)
    clock.advance(1)
    [state] = engine.flush(clock)
    assert (state.data["picks"], state.data["put_backs"], state.data["units_picked"]) == (1, 1, 2)


def test_no_load_cell_means_picks_are_not_reported():
    engine = PromoEngine([spec(has_scale=False)])
    clock = ManualClock()
    engine.update([], clock)
    engine.on_pickup(pickup(clock), clock)
    clock.advance(1)
    [state] = engine.flush(clock)
    assert state.data["picks"] is None


def test_an_id_switch_mid_stop_is_stitched_into_one_stopper():
    frames = stay(1, 500, 500, 4.0) + stay(2, 505, 500, 4.0)
    stitched = totals(run(PromoEngine([spec()]), ManualClock(), frames))
    assert (stitched["passers_by"], stitched["stoppers"]) == (0, 1)
    plain = totals(run(PromoEngine([spec(stitch=False)]), ManualClock(), stay(1, 500, 500, 2.0)
                       + stay(2, 505, 500, 2.0)))
    assert (plain["passers_by"], plain["stoppers"]) == (2, 0)


def test_spec_from_config_uses_defaults_and_the_load_cell_map():
    zone = ZoneConfig(name="endcap", points=[list(p) for p in SQUARE], kind="promo", shelf="endcap", slot="E1",
                      start_date="2026-10-15")
    s = promo_spec_from_config(zone, W, H, scales={"endcap/E1"})
    assert (s.promo, s.linked_slot, s.has_scale, s.approach_band, s.report_every_s) == \
        ("endcap", "endcap/E1", True, 0.08, 300.0)
    assert promo_spec_from_config(zone, W, H).has_scale is False
    assert not s.active(date(2026, 10, 14)) and s.active(date(2026, 10, 15))


def test_pipeline_builds_the_engine_and_forwards_picks(tmp_path):
    import numpy as np
    import pytest

    cv2 = pytest.importorskip("cv2")
    from storemind.core.config import StoreMindConfig
    from storemind.eval.common import _NullStore
    from storemind.inference.detector import StubDetector
    from storemind.pipeline import Pipeline

    video = tmp_path / "p.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 8, (64, 48))
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    config = StoreMindConfig(
        cameras=[{"name": "aisle", "source": str(video),
                  "zones": [{"name": "endcap", "points": [list(p) for p in SQUARE], "kind": "promo",
                             "promo_name": "Diwali offer", "shelf": "endcap", "slot": "E1"},
                            {"name": "aisle", "points": [list(p) for p in SQUARE]}]}],
        sensors={"cell_map": {"endcap/E1": "0"}})
    pipeline = Pipeline(config, store=_NullStore(), detector=StubDetector())
    pipeline.run(progress=False)
    camera = pipeline.cameras[0]
    assert list(camera.promo.specs) == ["endcap"] and camera.promo.specs["endcap"].has_scale
    pipeline.bus.publish(pickup(ManualClock()))
    assert pipeline.summary()["cameras"]["aisle"]["promo"]["endcap"]["picks"] == 1
    pipeline.close()
