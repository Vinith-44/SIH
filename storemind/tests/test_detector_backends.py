"""Detector backends: letterboxing, NMS, and fast-path equivalence.

The heavy-model tests skip themselves when the weights are not present, so the
suite still runs on a fresh clone with no `models/` folder.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from storemind.core.config import DetectorConfig
from storemind.inference.detector import (
    Detection,
    LetterboxInfo,
    StubDetector,
    build_detector,
    letterbox,
    nms,
    unletterbox_xyxy,
)

MODELS = Path(__file__).resolve().parents[2] / "models"
VIDEOS = Path(__file__).resolve().parents[2] / "videos"
YOLO11 = MODELS / "yolo11n.pt"
EFFICIENTDET = MODELS / "efficientdet_lite0_coco_legacy.tflite"
REAL_CLIP = VIDEOS / "other" / "vtest.avi"


def test_letterbox_preserves_aspect_ratio():
    """Audit S1: the legacy code squashed 1920x1080 into 320x320, turning people
    into wide blobs the detector had never seen in training."""
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    padded, info = letterbox(image, 640)
    assert padded.shape == (640, 640, 3)
    assert info.scale == pytest.approx(640 / 1920)
    assert info.pad_y == pytest.approx((640 - 360) // 2)
    assert info.pad_x == 0


def test_letterbox_round_trip_maps_a_box_back():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    _, info = letterbox(image, 320)
    original = np.array([[100.0, 200.0, 300.0, 400.0]])
    in_letterbox = original.copy()
    in_letterbox[:, [0, 2]] = original[:, [0, 2]] * info.scale + info.pad_x
    in_letterbox[:, [1, 3]] = original[:, [1, 3]] * info.scale + info.pad_y
    back = unletterbox_xyxy(in_letterbox, info, 640, 480)
    assert back[0] == pytest.approx(original[0], abs=1e-3)


def test_unletterbox_clips_to_the_frame():
    info = LetterboxInfo(scale=0.5, pad_x=0.0, pad_y=0.0)
    out = unletterbox_xyxy(np.array([[-50.0, -50.0, 9999.0, 9999.0]]), info, 640, 480)
    assert out[0][0] == 0 and out[0][1] == 0
    assert out[0][2] == 639 and out[0][3] == 479


def test_nms_removes_overlapping_boxes():
    boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [500, 500, 600, 600]], dtype=float)
    scores = np.array([0.9, 0.8, 0.7])
    assert sorted(nms(boxes, scores, 0.5)) == [0, 2]


def test_nms_keeps_boxes_that_barely_overlap():
    boxes = np.array([[0, 0, 100, 100], [90, 90, 190, 190]], dtype=float)
    scores = np.array([0.9, 0.8])
    assert sorted(nms(boxes, scores, 0.5)) == [0, 1]


def test_nms_on_empty_input():
    assert nms(np.zeros((0, 4)), np.zeros((0,)), 0.5) == []


def test_stub_detector_replays_a_script():
    scripted = [[Detection((0, 0, 10, 10), 0.9, 0)], []]
    detector = StubDetector(scripted)
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    assert len(detector.detect(image)) == 1
    assert detector.detect(image) == []


def test_config_rejects_an_unknown_backend():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DetectorConfig(backend="nope")  # type: ignore[arg-type]


def test_build_detector_rejects_an_unknown_backend():
    class Bogus:
        backend = "nope"

    with pytest.raises(SystemExit):
        build_detector(Bogus())


@pytest.mark.skipif(not (YOLO11.is_file() and REAL_CLIP.is_file()),
                    reason="needs models/yolo11n.pt and videos/other/vtest.avi")
def test_fast_path_agrees_with_ultralytics_predict():
    """The fast path exists purely for speed; if it ever disagrees with the
    official API it is a bug, not an optimisation."""
    import cv2

    from storemind.inference.detector import UltralyticsDetector

    cap = cv2.VideoCapture(str(REAL_CLIP))
    frames = []
    while len(frames) < 8:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    assert frames

    fast = UltralyticsDetector(str(YOLO11), conf=0.4, iou=0.5, imgsz=320)
    assert fast.fast is True
    slow = UltralyticsDetector(str(YOLO11), conf=0.4, iou=0.5, imgsz=320)
    slow.fast = False

    def iou(a, b) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
        union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
        return inter / union if union > 0 else 0.0

    matched = expected = 0
    for frame in frames:
        a = fast.detect(frame)
        b = slow.detect(frame)
        assert abs(len(a) - len(b)) <= 1          # a borderline box may differ
        expected += len(b)
        remaining = list(a)
        for reference in b:
            best = max(remaining, key=lambda d: iou(d.xyxy, reference.xyxy), default=None)
            if best is not None and iou(best.xyxy, reference.xyxy) >= 0.85:
                matched += 1
                remaining.remove(best)
    assert expected > 0
    assert matched / expected >= 0.95


@pytest.mark.skipif(not (EFFICIENTDET.is_file() and REAL_CLIP.is_file()),
                    reason="needs the legacy EfficientDet TFLite and vtest.avi")
def test_litert_backend_finds_people_in_a_real_clip():
    """The legacy EfficientDet-Lite0 must keep working through the new interface:
    that is what lets us show a like-for-like old-versus-new comparison."""
    import cv2

    try:
        from storemind.inference.detector import LiteRTDetector
    except Exception:  # pragma: no cover
        pytest.skip("LiteRT unavailable")

    try:
        detector = LiteRTDetector(str(EFFICIENTDET), conf=0.35)
    except SystemExit:
        pytest.skip("no TFLite runtime installed")

    assert detector.postprocess == "tflite_detection"
    assert (detector.in_h, detector.in_w) == (320, 320)

    cap = cv2.VideoCapture(str(REAL_CLIP))
    counts = []
    for _ in range(10):
        ok, frame = cap.read()
        if not ok:
            break
        counts.append(len(detector.detect(frame)))
    cap.release()
    assert counts and sum(counts) / len(counts) >= 1.0


@pytest.mark.skipif(not (VIDEOS / "entrance" / "synthetic_entrance_detections.json").is_file(),
                    reason="run tools/make_synthetic_video.py first")
def test_scripted_detector_replays_by_frame_index():
    from storemind.inference.scripted import ScriptedDetector

    detector = ScriptedDetector(VIDEOS / "entrance" / "synthetic_entrance_detections.json")
    image = np.zeros((540, 960, 3), dtype=np.uint8)
    detector.seek(500)
    first = detector.detect(image)
    detector.seek(500)
    again = detector.detect(image)
    assert [d.xyxy for d in first] == [d.xyxy for d in again]
