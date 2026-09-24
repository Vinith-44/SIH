"""M6 shelf interaction fusion: MEMS gating, pick/put-back, touch, shrink,
fallen stock, camera-mount tamper fusion, after-hours PIR, pipeline trigger."""

from __future__ import annotations

from datetime import datetime

import pytest

from storemind.core.clock import IST, ManualClock
from storemind.core.events import EventType, make_event
from storemind.fusion.interaction import ShelfInteractionEngine, SlotInfo

SLOTS = [SlotInfo("shelf-a", "A1", "1", 218.0), SlotInfo("shelf-a", "A2", "2", 500.0)]


class Rig:
    def __init__(self, mems=True, near=True, open_hours=None, start=None):
        self.clock = ManualClock(start=start) if start else ManualClock()
        self.near = near
        self.engine = ShelfInteractionEngine(SLOTS, mems_enabled=mems, open_hours=open_hours,
                                             person_at_shelf=lambda shelf: self.near)
        self.out = []

    def at(self, t, event_type, **data):
        self.clock.set(t)
        event = make_event(ts=self.clock.now(), store="s", node="stm32-01", type=EventType(event_type), data=data)
        self.out += self.engine.on_event(event, self.clock)

    def weight(self, t, channel, grams, stable=True):
        self.at(t, "WEIGHT", node="stm32-01", slot=channel, grams=grams, stable=stable)

    def motion(self, t, kind):
        self.at(t, "SHELF_MOTION", node="m1", shelf="shelf-a", kind=kind, peak_mg=300, rms_mg=90, dur_ms=400)

    def tick(self, t):
        self.clock.set(t)
        self.out += self.engine.tick(self.clock)

    def pickups(self):
        return [(e.data["slot"], e.data["action"], e.data["units"]) for e in self.out
                if e.type is EventType.PICKUP]


def baseline(rig):
    rig.weight(0, "1", 2094)
    rig.weight(0, "2", 4350)


def test_pick_is_the_settled_weight_change_and_handling_noise_is_ignored():
    rig = Rig()
    baseline(rig)
    rig.motion(10, "TOUCH")
    for t, g in ((10.5, 2400), (11.0, 1800), (11.5, 2250)):
        rig.weight(t, "1", g, stable=False)              # hand on the shelf
    rig.weight(12.0, "1", 2300, stable=True)             # "stable" push mid-handling
    rig.weight(12.5, "1", 1700, stable=False)
    rig.motion(14, "SETTLED")
    rig.weight(14.8, "1", 1658)                          # two packs gone
    rig.weight(14.8, "2", 4351)
    assert rig.pickups() == [("A1", "pick", 2)]
    assert rig.engine.gated_readings >= 4


def test_new_weight_arriving_before_settled_is_used():
    rig = Rig()
    baseline(rig)
    rig.motion(10, "TOUCH")
    rig.weight(10.5, "1", 2500, stable=False)
    rig.weight(12.3, "1", 2312)                          # put one back, reported before SETTLED
    rig.weight(12.3, "2", 4349)
    rig.motion(12.8, "SETTLED")
    assert rig.pickups() == [("A1", "put_back", 1)]


def test_a_push_that_is_not_a_whole_number_of_packs_waits_for_the_next_reading():
    rig = Rig()
    baseline(rig)
    rig.motion(10, "TOUCH")
    rig.weight(11.0, "1", 2410)                          # +316 g push flagged stable, then SETTLED
    rig.motion(11.2, "SETTLED")
    assert rig.pickups() == []
    rig.weight(11.8, "1", 1876)                          # the real result: one pack taken
    rig.weight(11.8, "2", 4350)
    assert rig.pickups() == [("A1", "pick", 1)]


def test_touch_without_change_is_engagement_when_a_shopper_is_there():
    rig = Rig(near=True)
    baseline(rig)
    rig.motion(10, "TOUCH")
    rig.weight(10.5, "1", 2350, stable=False)            # leaning on the shelf
    rig.motion(12, "SETTLED")
    rig.tick(15)                                         # no new stable reading: timeout
    assert rig.pickups() == [("A1", "touch", None)]
    assert rig.engine.check_requests == ["shelf-a"]      # the shelf camera should look now


def test_weight_drop_with_no_touch_and_nobody_there_is_shrink():
    rig = Rig(near=False)
    baseline(rig)
    rig.weight(40, "2", 3850)
    rig.weight(50, "2", 3851)                            # confirmed by the next reading
    kinds = [e.type for e in rig.out]
    assert kinds == [EventType.SHRINK_FLAG]


def test_without_mems_a_confirmed_step_with_a_shopper_is_a_pick_but_a_single_blip_is_not():
    rig = Rig(mems=False, near=True)
    baseline(rig)
    rig.weight(20, "1", 2400)                            # blip ...
    rig.weight(20.5, "1", 2100, stable=False)            # ... cancelled by handling
    rig.weight(22, "1", 1876)
    rig.weight(23, "1", 1877)
    assert rig.pickups() == [("A1", "pick", 1)]


def test_knock_that_drops_stock_raises_fallen_stock_not_a_pick():
    rig = Rig(near=False)
    baseline(rig)
    rig.motion(30, "KNOCK")
    rig.weight(30.1, "2", 4150, stable=False)
    rig.weight(31.2, "2", 3850)
    rig.weight(31.2, "1", 2094)
    alerts = rig.engine.drain_alerts()
    assert rig.pickups() == []
    assert [a[0] for a in alerts] == ["FALLEN_STOCK:shelf-a/A2"]


def test_camera_knock_fused_with_image_tamper_is_critical_otherwise_a_bump():
    rig = Rig()
    rig.at(5, "CAMERA_MOUNT", node="m2", cam="entrance", kind="KNOCK", peak_mg=1800)
    rig.engine.note_image_tamper("entrance", 7.0)
    rig.at(8, "CAMERA_MOUNT", node="m3", cam="counter", kind="KNOCK", peak_mg=900)
    rig.at(9, "CAMERA_MOUNT", node="m4", cam="shelf", kind="TILT", peak_mg=200, tilt_deg=3.5)
    rig.tick(8.5)
    rig.tick(25)
    alerts = {key: severity.value for key, _m, severity, _e in rig.engine.drain_alerts()}
    assert alerts == {"CAMERA_MOVED:entrance": "CRITICAL", "CAMERA_BUMP:counter": "WARN",
                      "CAMERA_TILT:shelf": "WARN"}


def test_pir_motion_after_hours_is_critical_and_inside_hours_is_not():
    night = datetime(2026, 9, 24, 23, 30, tzinfo=IST)
    rig = Rig(open_hours=["09:00-22:00"], start=night)
    rig.at(0, "PRESENCE", node="stm32-01", zone="back-door", active=True)
    day = Rig(open_hours=["09:00-22:00"], start=datetime(2026, 9, 24, 11, 0, tzinfo=IST))
    day.at(0, "PRESENCE", node="stm32-01", zone="back-door", active=True)
    assert [a[0] for a in rig.engine.drain_alerts()] == ["AFTER_HOURS:back-door"]
    assert day.engine.drain_alerts() == []


def test_the_simulator_scores_m6_above_the_baselines_on_one_seed():
    from storemind.eval.eval_fusion import run_seed

    result = run_seed((11, {}))
    assert result["M6"]["pick"]["f1"] > result["weight-only"]["pick"]["f1"] > result["v1"]["pick"]["f1"]


def test_pipeline_touch_triggers_an_immediate_shelf_check(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from storemind.core.config import StoreMindConfig
    from storemind.eval.common import _NullStore
    from storemind.inference.detector import StubDetector
    from storemind.pipeline import Pipeline

    video = tmp_path / "s.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 1, (64, 48))
    for _ in range(2):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    config = StoreMindConfig(cameras=[{
        "name": "shelf-cam", "source": str(video), "role": "shelf", "shelf_period_s": 600,
        "shelves": [{"name": "shelf-a", "slots": [{"name": "A1", "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.6]]}]}]}])
    pipeline = Pipeline(config, store=_NullStore(), detector=StubDetector())
    pipeline.run(progress=False)                          # builds the shelf engine
    clock = ManualClock()
    for kind in ("TOUCH", "SETTLED"):
        pipeline.bus.publish(make_event(ts=clock.now(), store="s", node="stm32-01", type=EventType.SHELF_MOTION,
                                        data={"node": "m1", "shelf": "shelf-a", "kind": kind, "peak_mg": 300,
                                              "rms_mg": 90, "dur_ms": 400}))
    assert pipeline.cameras[0].check_now is True
    pipeline.close()
