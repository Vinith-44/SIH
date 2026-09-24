"""M1: gate counter, detection filters, staff exclusion, IR-beam cross-check,
tracker registry and cached-detection replay."""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pytest

from storemind.analytics.footfall import FootfallCounter, GateCounter, build_counter
from storemind.analytics.staff import StaffFilter
from storemind.core.clock import ManualClock
from storemind.core.config import DetectionFilterConfig, LineConfig, StaffConfig, StoreMindConfig
from storemind.core.events import BeamCrossData, CameraHealthData, EventType, make_event
from storemind.core.geometry import Line
from storemind.fusion.beam import BeamCrossCheck
from storemind.inference.cached import CachedDetector, CachedTimeline
from storemind.inference.detector import Detection
from storemind.inference.filters import DetectionFilter
from storemind.tracking.tracker import TRACKER_CLASSES, Track, build_tracker

WIDTH, HEIGHT = 640, 480


def gate(**kwargs) -> GateCounter:
    line = Line("door", (0.0, 0.5), (1.0, 0.5), margin_px=10).resolve(WIDTH, HEIGHT)
    kwargs.setdefault("gate_px", 20.0)
    kwargs.setdefault("confirm_s", 0.0)
    return GateCounter(line, cam="entrance", **kwargs)


def track_at(track_id: int, foot_y: float, foot_x: float = 320.0) -> Track:
    return Track(track_id, (foot_x - 20, foot_y - 120, foot_x + 20, foot_y), 0.9)


def walk(counter, clock, track_id, points, step_s=0.125):
    """`points` are foot y values, or (x, y) tuples."""
    events = []
    for p in points:
        x, y = (320.0, p) if not isinstance(p, tuple) else p
        events += counter.update([track_at(track_id, y, x)], clock)
        clock.advance(step_s)
    return events


# --------------------------------------------------------------------------- #
# Gate counter
# --------------------------------------------------------------------------- #

def test_gate_counts_a_clean_walk_across_once():
    counter, clock = gate(), ManualClock()
    events = walk(counter, clock, 1, [100, 150, 200, 230, 250, 280, 330, 400])
    assert (counter.entries, counter.exits) == (1, 0)
    assert [e.type for e in events] == [EventType.ENTRY]


def test_gate_ignores_jitter_inside_the_band():
    counter, clock = gate(gate_px=30), ManualClock()
    walk(counter, clock, 1, [100, 180, 200] + [230, 250, 232, 249, 235, 252] * 5 + [240])
    assert (counter.entries, counter.exits) == (0, 0)


def test_a_track_born_at_the_line_is_too_young_to_count():
    counter, clock = gate(min_track_age_s=0.5), ManualClock()
    walk(counter, clock, 7, [226, 260, 300])            # 3 frames = 0.25 s old at the crossing
    assert counter.entries == 0
    assert counter.rejected["too_young"] == 1


def test_walking_along_the_line_is_rejected_by_the_direction_check():
    counter, clock = gate(gate_px=10, direction_mode="balanced"), ManualClock()
    # Mostly sideways (x +60 px per step) with a small drift across the line.
    path = [(20 + 60 * i, 228 + 3 * i) for i in range(10)]
    walk(counter, clock, 3, path)
    assert counter.entries == 0
    assert counter.rejected["direction"] == 1


def test_direction_off_counts_the_same_sideways_walk():
    counter, clock = gate(gate_px=10, direction_mode="off"), ManualClock()
    walk(counter, clock, 3, [(20 + 60 * i, 228 + 3 * i) for i in range(10)])
    assert counter.entries == 1


def test_one_count_per_track_per_direction_stops_a_loiterer():
    counter, clock = gate(cooldown_s=0.0), ManualClock()
    down, up = [150, 200, 300, 350], [350, 300, 200, 150]
    walk(counter, clock, 1, down + up + down + up + down)
    assert (counter.entries, counter.exits) == (1, 1)
    assert counter.rejected["repeat"] >= 2


def test_confirmation_cancels_a_step_back_and_commits_when_the_track_vanishes():
    counter, clock = gate(confirm_s=0.5), ManualClock()
    walk(counter, clock, 1, [60, 100, 150, 200, 260, 270, 200, 150])   # over and straight back
    assert counter.entries == 0 and counter.rejected["reverted"] == 1

    counter, clock = gate(confirm_s=0.5), ManualClock()
    events = walk(counter, clock, 2, [60, 100, 150, 200, 280])        # crosses, then lost
    assert events == []
    for _ in range(6):                                                 # 0.75 s with no detection
        events += counter.update([], clock)
        clock.advance(0.125)
    assert counter.entries == 1 and len(events) == 1


def test_excluded_tracks_never_count_in_either_counter():
    for counter in (gate(), FootfallCounter(gate().line)):
        clock = ManualClock()
        for y in [100, 150, 200, 280, 330, 400]:
            counter.update([track_at(1, y)], clock, exclude={1})
            clock.advance(0.125)
        assert counter.entries == 0


def test_build_counter_follows_line_mode():
    line = gate().line
    single = LineConfig(a=(0, 0.5), b=(1, 0.5))
    assert isinstance(build_counter(line, single, store="s", node="n", cam="c"), FootfallCounter)
    gated = LineConfig(a=(0, 0.5), b=(1, 0.5), mode="gate", gate_px=24, direction_mode="strict")
    counter = build_counter(line, gated, store="s", node="n", cam="c")
    assert isinstance(counter, GateCounter) and counter.half_gate == 12.0


# --------------------------------------------------------------------------- #
# Detection filters and staff
# --------------------------------------------------------------------------- #

def det(x1, y1, x2, y2, conf=0.9, cls=0) -> Detection:
    return Detection((x1, y1, x2, y2), conf, cls)


def test_filters_apply_only_inside_their_polygon():
    mannequin_corner = DetectionFilterConfig(points=[(0.0, 0.0), (0.25, 0.0), (0.25, 1.0), (0.0, 1.0)],
                                             min_score=0.8)
    everywhere = DetectionFilterConfig(min_area_px=400, max_area_frac=0.5)
    keep = DetectionFilter([mannequin_corner, everywhere], WIDTH, HEIGHT)
    kept = keep([det(10, 100, 60, 300, conf=0.6),       # in the corner, low score -> drop
                 det(400, 100, 450, 300, conf=0.6),      # elsewhere, same score -> keep
                 det(400, 100, 405, 105),                # 25 px^2 -> too small everywhere
                 det(0, 0, 640, 480)])                   # whole frame -> too large
    assert [d.xyxy[0] for d in kept] == [400]
    assert keep.dropped == 3


def test_staff_zone_dwell_marks_a_track_as_staff_for_good():
    staff = StaffFilter(StaffConfig(zones=[[(0.0, 0.0), (0.3, 0.0), (0.3, 0.6), (0.0, 0.6)]],
                                    zone_dwell_s=2.0), WIDTH, HEIGHT)
    cashier, shopper = track_at(1, 200, 100), track_at(2, 200, 500)
    for t in range(0, 30, 5):                                          # 0 .. 2.5 s
        found = staff.update(None, [cashier, shopper], t / 10)
    assert found == {1}
    assert staff.update(None, [track_at(1, 400, 500)], 3.0) == {1}   # stays staff outside


def test_printed_aruco_badge_marks_the_wearer():
    cv2 = pytest.importorskip("cv2")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    marker = cv2.aruco.generateImageMarker(dictionary, 7, 60)
    image = np.full((HEIGHT, WIDTH, 3), 255, np.uint8)
    image[150:210, 300:360] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)  # on person 1's chest
    staff = StaffFilter(StaffConfig(badge=True, badge_ids=[7], badge_every_n=1), WIDTH, HEIGHT)
    wearer, other = Track(1, (280, 100, 380, 400), 0.9), Track(2, (450, 100, 550, 400), 0.9)
    assert staff.update(image, [wearer, other], 0.0) == {1}
    wrong_id = StaffFilter(StaffConfig(badge=True, badge_ids=[3], badge_every_n=1), WIDTH, HEIGHT)
    assert wrong_id.update(image, [wearer, other], 0.0) == set()


# --------------------------------------------------------------------------- #
# IR beam cross-check
# --------------------------------------------------------------------------- #

def beam(clock, t, direction="in", door="door1"):
    return make_event(ts=clock.start + timedelta(seconds=t), store="s", node="stm32-01",
                      type=EventType.BEAM_CROSS,
                      data=BeamCrossData(node="stm32-01", door=door, direction=direction,
                                         t_ms_mcu=int(t * 1000)))


def cam_cross(clock, t, direction="in", line="door"):
    return make_event(ts=clock.start + timedelta(seconds=t), store="s", node="pi5-01",
                      cam="entrance", type=EventType.ENTRY if direction == "in" else EventType.EXIT,
                      data={"line": line, "track": 1, "direction": direction})


def test_beam_and_camera_agree_within_tolerance():
    clock = ManualClock()
    check = BeamCrossCheck({"door1": "entrance"}, tolerance_s=2.0, min_samples=4)
    for i in range(10):
        check.on_event(beam(clock, i * 10.0))
        check.on_event(cam_cross(clock, i * 10.0 + 0.8))
    check.on_event(beam(clock, 200.0))                       # camera missed one
    stats = check.agreement("door1")
    assert stats["matched"] == 10 and stats["agreement"] == pytest.approx(20 / 21)
    assert check.alerts() == []


def test_disagreement_raises_check_calibration_alert():
    clock = ManualClock()
    check = BeamCrossCheck({"door1": "entrance"}, min_samples=5, alert_below=0.8)
    for i in range(10):
        check.on_event(beam(clock, i * 10.0))
        check.on_event(cam_cross(clock, i * 10.0 + 0.5, direction="out" if i % 2 else "in"))
    (key, message, evidence), = check.alerts()
    assert key == "BEAM_MISMATCH:door1" and "check entrance calibration" in message
    assert evidence["agreement"] == pytest.approx(0.5)


def test_beam_takes_over_counting_while_the_camera_is_down():
    clock = ManualClock()
    check = BeamCrossCheck({"door1": "entrance"})
    assert check.on_event(beam(clock, 1.0)) == []
    check.on_event(make_event(ts=clock.now(), store="s", node="pi5-01", cam="entrance",
                              type=EventType.CAMERA_HEALTH,
                              data=CameraHealthData(cam="entrance", state="stale", fps=0.0)))
    out = check.on_event(beam(clock, 2.0, direction="out"))
    assert len(out) == 1 and out[0].type is EventType.EXIT
    assert out[0].data["line"] == "beam:door1" and out[0].cam == "entrance"
    assert check.on_event(out[0]) == []                       # our own output is not re-counted


# --------------------------------------------------------------------------- #
# Trackers and cached replay
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kind", sorted(TRACKER_CLASSES))
def test_every_bakeoff_tracker_follows_one_walking_person(kind):
    config = StoreMindConfig().tracker.model_copy(update={"type": kind, "frame_rate": 8})
    tracker = build_tracker(config)
    ids = set()
    for step in range(20):
        y = 100 + 10 * step
        tracks = tracker.update([Detection((300, y, 340, y + 120), 0.9, 0)])
        ids |= {t.track_id for t in tracks}
    assert len(ids) == 1, (kind, ids)


def test_cached_detector_and_timeline_replay_scores_and_times(tmp_path):
    cache = {"model": "m.pt", "imgsz": 640, "conf_floor": 0.05, "size": [384, 288],
             "times": {"0": 0.0, "3": 0.12}, "frames": {"0": [[1, 2, 3, 4, 0.9, 0]],
                                                        "3": [[1, 2, 3, 4, 0.1, 0]]}}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cache), encoding="utf-8")
    detector = CachedDetector(path, conf=0.25)
    timeline = CachedTimeline(detector)
    frames = [timeline.read(), timeline.read()]
    assert timeline.read() is None
    assert [(f.index, f.video_s, f.size) for f in frames] == [(0, 0.0, (384, 288)),
                                                              (3, 0.12, (384, 288))]
    detector.seek(0)
    assert detector.detect(frames[0].image)[0].conf == 0.9
    detector.seek(3)
    assert detector.detect(frames[1].image) == []            # 0.1 < 0.25
    detector.seek(1)
    with pytest.raises(KeyError):
        detector.detect(frames[0].image)
    with pytest.raises(ValueError):
        CachedDetector(path, conf=0.01)


# --------------------------------------------------------------------------- #
# Pipeline wiring: beam cross-check, filters and staff reach the engines
# --------------------------------------------------------------------------- #

def _tiny_video(path, frames=4):
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 8, (64, 48))
    for _ in range(frames):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    return path


def test_pipeline_wires_the_beam_cross_check(tmp_path):
    from storemind.eval.common import _NullStore
    from storemind.inference.detector import StubDetector
    from storemind.pipeline import Pipeline

    config = StoreMindConfig(cameras=[{
        "name": "entrance", "source": str(_tiny_video(tmp_path / "e.avi")), "role": "entrance",
        "line": {"a": [0, 0.5], "b": [1, 0.5], "mode": "gate", "beam_door": "door1"}}])
    pipeline = Pipeline(config, store=_NullStore(), detector=StubDetector())
    assert pipeline.beam_check is not None and pipeline.beam_check.doors["door1"].cam == "entrance"
    clock, seen = ManualClock(), []
    pipeline.bus.subscribe_types([EventType.ENTRY, EventType.EXIT], seen.append)

    pipeline.bus.publish(beam(clock, 1.0))
    pipeline.bus.publish(cam_cross(clock, 1.5))
    assert pipeline.beam_check.agreement("door1")["matched"] == 1

    pipeline.bus.publish(make_event(ts=clock.now(), store="s", node="pi5-01", cam="entrance",
                                    type=EventType.CAMERA_HEALTH,
                                    data=CameraHealthData(cam="entrance", state="dark", fps=0.0)))
    pipeline.bus.publish(beam(clock, 5.0))
    assert [e.data["line"] for e in seen][-1] == "beam:door1"
    pipeline.close()
