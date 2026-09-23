"""Calibration output and the CAVIAR ground-truth parser.

The calibration GUI cannot be driven headlessly, but the part that matters - the
config it produces - can be, and that is what would silently poison every zone
number in the system if it drifted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from storemind.core.config import StoreMindConfig      # noqa: E402
from storemind.eval.caviar import (                    # noqa: E402
    GtBox,
    GtClip,
    crossing_sensitivity,
    gt_crossings,
    parse_cvml,
    signed_distance,
)

CAVIAR_DIR = REPO.parent / "videos" / "entrance" / "caviar"


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #

def build_calibrator():
    import calibrate

    frame = np.zeros((288, 384, 3), dtype=np.uint8)
    return calibrate, calibrate.Calibrator(frame, "entrance")


def test_calibration_output_validates_against_the_config_schema():
    calibrate, cal = build_calibrator()
    Shape = calibrate.Shape
    cal.shapes = [
        Shape("line", [(0.0, 0.5), (1.0, 0.5)], "door",
              extra={"entry_direction": "pos", "margin_px": 10.0}),
        Shape("zone", [(0.1, 0.1), (0.4, 0.1), (0.4, 0.5), (0.1, 0.5)], "promo",
              extra={"kind": "promo", "min_dwell_s": 4.0}),
        Shape("lane", [(0.3, 0.5), (0.7, 0.5), (0.7, 1.0), (0.3, 1.0)], "counter-1"),
        Shape("billing", [(0.35, 0.2), (0.65, 0.2), (0.65, 0.45), (0.35, 0.45)], "counter-1"),
        Shape("slot", [(0.1, 0.2), (0.3, 0.2), (0.3, 0.5), (0.1, 0.5)], "A1",
              sku="Atta 5kg", price=260.0, extra={"shelf": "shelf-a"}),
    ]
    camera = cal.to_config("0", "entrance", 8.0, None)
    config = StoreMindConfig(cameras=[camera])
    cam = config.cameras[0]
    assert cam.line is not None and cam.line.entry_direction == "pos"
    assert [z.name for z in cam.zones] == ["promo"]
    assert [c.name for c in cam.counters] == ["counter-1"]
    assert cam.shelves[0].slots[0].sku == "Atta 5kg"
    assert cam.shelves[0].slots[0].price == 260.0


def test_counter_without_a_billing_spot_is_skipped_not_half_written():
    """A half-drawn counter would otherwise produce a config that crashes the
    pipeline at start-up, long after the person who drew it has walked away."""
    calibrate, cal = build_calibrator()
    cal.shapes = [calibrate.Shape("lane", [(0.3, 0.5), (0.7, 0.5), (0.7, 1.0)], "counter-9")]
    camera = cal.to_config("0", "counter", 5.0, None)
    assert "counters" not in camera
    StoreMindConfig(cameras=[camera])


def test_normalised_coordinates_are_resolution_independent():
    calibrate, cal = build_calibrator()
    assert cal.normalise((192, 144)) == (0.5, 0.5)
    assert cal.denormalise((0.5, 0.5)) == (192, 144)


def test_slots_group_under_their_shelf():
    calibrate, cal = build_calibrator()
    Shape = calibrate.Shape
    cal.shapes = [
        Shape("slot", [(0.1, 0.2), (0.3, 0.2), (0.3, 0.5)], "A1", extra={"shelf": "shelf-a"}),
        Shape("slot", [(0.4, 0.2), (0.6, 0.2), (0.6, 0.5)], "A2", extra={"shelf": "shelf-a"}),
        Shape("slot", [(0.1, 0.6), (0.3, 0.6), (0.3, 0.9)], "B1", extra={"shelf": "shelf-b"}),
    ]
    camera = cal.to_config("0", "shelf", 1.0, None)
    shelves = {s["name"]: s for s in camera["shelves"]}
    assert sorted(shelves) == ["shelf-a", "shelf-b"]
    assert len(shelves["shelf-a"]["slots"]) == 2
    assert camera["role"] == "shelf"


# --------------------------------------------------------------------------- #
# CAVIAR parser
# --------------------------------------------------------------------------- #

def synthetic_clip() -> GtClip:
    """A track walking straight down through y = 100, then back up."""
    boxes = []
    for frame in range(40):
        y = 40 + frame * 4          # foot point goes 40 -> 196
        boxes.append(GtBox(frame=frame, track=1, xc=50, yc=y - 10, w=20, h=20,
                           context="walking"))
    for frame in range(40, 80):
        y = 196 - (frame - 40) * 4
        boxes.append(GtBox(frame=frame, track=1, xc=50, yc=y - 10, w=20, h=20,
                           context="shop enter" if frame < 60 else "walking"))
    return GtClip(name="synthetic", xml_path=Path("none"), frames=80, boxes=boxes)


def test_signed_distance_sign_matches_the_side():
    a, b = (0.0, 100.0), (200.0, 100.0)
    assert signed_distance((50.0, 150.0), a, b) > 0     # below
    assert signed_distance((50.0, 50.0), a, b) < 0      # above


def test_gt_crossings_counts_one_each_way():
    clip = synthetic_clip()
    crossings = gt_crossings(clip, (0.0, 100.0), (200.0, 100.0), "pos")
    directions = [d for _t, _track, d in crossings]
    assert directions == ["in", "out"]


def test_gt_crossings_ignore_jitter_inside_the_margin():
    """A person hovering on the line must not manufacture crossings - if the
    ground truth did that, our counter would be scored against noise."""
    boxes = []
    for frame in range(60):
        wobble = 2.0 if frame % 2 else -2.0
        boxes.append(GtBox(frame=frame, track=1, xc=50, yc=100 + wobble - 10, w=20, h=20))
    clip = GtClip(name="jitter", xml_path=Path("none"), frames=60, boxes=boxes)
    assert gt_crossings(clip, (0.0, 100.0), (200.0, 100.0), "pos", gt_margin=8.0) == []


def test_crossing_sensitivity_reports_every_margin():
    clip = synthetic_clip()
    sensitivity = crossing_sensitivity(clip, (0.0, 100.0), (200.0, 100.0), "pos")
    assert set(sensitivity) == {2.0, 4.0, 8.0, 16.0}
    assert all(v["in"] == 1 and v["out"] == 1 for v in sensitivity.values())


def test_context_episodes_collapse_runs_into_one_event():
    clip = synthetic_clip()
    episodes = clip.context_episodes("shop enter")
    assert len(episodes) == 1          # 20 consecutive frames, one event
    track, start, end = episodes[0]
    assert track == 1 and end > start


def test_people_per_frame_covers_every_frame():
    clip = synthetic_clip()
    series = clip.people_per_frame()
    assert len(series) == clip.frames
    assert all(count == 1 for _t, count in series)


def test_foot_point_is_the_bottom_of_the_box():
    box = GtBox(frame=0, track=1, xc=50, yc=100, w=20, h=40)
    assert box.foot == (50, 120)
    assert box.xyxy == (40, 80, 60, 120)


@pytest.mark.skipif(not (CAVIAR_DIR / "cose1gt.xml").is_file(),
                    reason="CAVIAR ground truth not present")
def test_parses_a_real_caviar_file():
    clip = parse_cvml(CAVIAR_DIR / "cose1gt.xml")
    assert clip.name == "OneStopEnter1cor"
    assert clip.frames == 1500
    assert len(clip.by_track()) >= 5
    assert clip.context_episodes("shop enter")
    # Every box must be inside the 384x288 frame.
    for box in clip.boxes[:500]:
        assert -5 <= box.xc <= 389 and -5 <= box.yc <= 293
