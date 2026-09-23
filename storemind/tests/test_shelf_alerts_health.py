"""Shelf state machine, alert manager, tamper detector, FPS scheduler."""

from __future__ import annotations

import numpy as np
import pytest

from storemind.alerts.manager import AlertManager, Sink
from storemind.analytics.shelf import Embedder, ShelfEngine, SlotSpec, cosine, edge_density, ssim
from storemind.core.clock import ManualClock
from storemind.core.config import ShelfConfig
from storemind.core.events import AlertData, Severity
from storemind.core.geometry import Polygon
from storemind.health.monitor import TamperDetector
from storemind.ingest.sources import FpsScheduler
from storemind.tracking.tracker import Track

W, H = 320, 240
SLOT = [(0.1, 0.1), (0.5, 0.1), (0.5, 0.6), (0.1, 0.6)]


def shelf_image(fill: float = 1.0, hue: int = 40) -> np.ndarray:
    """A slot region drawn with `fill` fraction of packets on a flat back panel."""
    image = np.full((H, W, 3), 55, dtype=np.uint8)
    x1, y1 = int(0.1 * W), int(0.1 * H)
    x2, y2 = int(0.5 * W), int(0.6 * H)
    image[y1:y2, x1:x2] = 50                      # back panel
    columns = 5
    packets = int(round(columns * fill))
    step = (x2 - x1) // columns
    for c in range(packets):
        px = x1 + c * step + 2
        image[y1 + 4:y2 - 4, px:px + step - 4] = (hue, 180, 220)
        image[y1 + 4:y1 + 6, px:px + step - 4] = 250      # a printed edge
        image[y2 - 6:y2 - 4, px:px + step - 4] = 20
    return image


def make_engine(**overrides) -> ShelfEngine:
    defaults = {"vote_k": 2, "vote_n": 3, "low_threshold": 0.5, "empty_threshold": 0.2}
    config = ShelfConfig(name="shelf-a", **{**defaults, **overrides})
    slot = SlotSpec(polygon=Polygon("A1", SLOT, "slot").resolve(W, H),
                    sku="Atta 5kg", price=260.0)
    return ShelfEngine([("shelf-a", [slot], config)], cam="shelf-a")


def test_edge_density_falls_as_the_slot_empties():
    full = edge_density(shelf_image(1.0))
    half = edge_density(shelf_image(0.4))
    empty = edge_density(shelf_image(0.0))
    assert full > half > empty


def test_ssim_is_one_for_an_identical_crop():
    image = shelf_image(1.0)
    assert ssim(image, image) == pytest.approx(1.0, abs=1e-6)


def test_engine_says_nothing_before_a_reference_exists():
    engine, clock = make_engine(), ManualClock()
    assert engine.update(shelf_image(0.0), [], clock) == []


def test_emptying_a_slot_produces_one_empty_event():
    engine, clock = make_engine(), ManualClock()
    engine.capture_references(shelf_image(1.0))
    events = []
    for _ in range(4):
        events += engine.update(shelf_image(0.0), [], clock)
        clock.advance(30.0)
    states = [e for e in events if e.type.value == "SLOT_STATE"]
    assert len(states) == 1
    assert states[0].data["state"] == "EMPTY"
    assert states[0].data["sku"] == "Atta 5kg"


def test_partially_emptied_slot_reads_low():
    engine, clock = make_engine(), ManualClock()
    engine.capture_references(shelf_image(1.0))
    events = []
    for _ in range(4):
        events += engine.update(shelf_image(0.4), [], clock)
        clock.advance(30.0)
    states = [e for e in events if e.type.value == "SLOT_STATE"]
    assert states and states[-1].data["state"] == "LOW"


def test_a_full_slot_after_restocking_produces_no_alert_churn():
    engine, clock = make_engine(), ManualClock()
    engine.capture_references(shelf_image(1.0))
    events = []
    for _ in range(6):
        events += engine.update(shelf_image(1.0), [], clock)
        clock.advance(30.0)
    assert events == []


def test_occlusion_gate_skips_frames_with_a_person_in_front():
    """Audit H5: a shopper standing at the shelf used to trigger random alerts."""
    engine, clock = make_engine(occlusion_iou=0.05), ManualClock()
    engine.capture_references(shelf_image(1.0))
    blocker = Track(1, (0.05 * W, 0.05 * H, 0.6 * W, 0.9 * H), 0.9)
    events = []
    for _ in range(6):
        events += engine.update(shelf_image(0.0), [blocker], clock)
        clock.advance(30.0)
    assert events == []
    runtime = engine.shelves["shelf-a"]["slots"]["A1"]
    assert runtime.occluded_frames == 6
    assert runtime.observations == 0


def test_voting_suppresses_a_single_bad_frame():
    """Audit H6: the legacy code alerted every 10 s with no temporal smoothing."""
    engine, clock = make_engine(vote_k=3, vote_n=5), ManualClock()
    engine.capture_references(shelf_image(1.0))
    events = []
    for image in (shelf_image(1.0), shelf_image(0.0), shelf_image(1.0), shelf_image(1.0)):
        events += engine.update(image, [], clock)
        clock.advance(30.0)
    assert events == []


def test_restocking_resets_the_reference_and_the_state():
    engine, clock = make_engine(), ManualClock()
    engine.capture_references(shelf_image(1.0))
    for _ in range(4):
        engine.update(shelf_image(0.0), [], clock)
        clock.advance(30.0)
    assert engine.shelves["shelf-a"]["slots"]["A1"].state.value == "EMPTY"
    engine.capture_references(shelf_image(1.0))
    assert engine.shelves["shelf-a"]["slots"]["A1"].state.value == "FULL"


def test_embedder_separates_different_products():
    embedder = Embedder()
    reference = embedder(shelf_image(1.0, hue=40))
    same = embedder(shelf_image(1.0, hue=40))
    different = embedder(shelf_image(1.0, hue=170))
    assert cosine(reference, same) == pytest.approx(1.0, abs=1e-5)
    assert cosine(reference, different) < cosine(reference, same)


def test_slot_price_lookup():
    engine = make_engine()
    assert engine.slot_price("shelf-a", "A1") == ("Atta 5kg", 260.0)
    assert engine.slot_price("nope", "A1") == (None, None)


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #

class CollectSink(Sink):
    def __init__(self) -> None:
        self.seen: list[AlertData] = []

    def emit(self, alert: AlertData) -> None:
        self.seen.append(alert)


def test_alert_dedupe_within_cooldown():
    manager = AlertManager(cooldown_s=120.0)
    sink = CollectSink()
    manager.add_sink(sink)
    clock = ManualClock()
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    clock.advance(30.0)
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    assert len(sink.seen) == 1


def test_alert_fires_again_after_cooldown():
    manager = AlertManager(cooldown_s=60.0)
    sink = CollectSink()
    manager.add_sink(sink)
    clock = ManualClock()
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    clock.advance(120.0)
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    assert len(sink.seen) == 2


def test_different_keys_are_independent():
    manager = AlertManager(cooldown_s=120.0)
    sink = CollectSink()
    manager.add_sink(sink)
    clock = ManualClock()
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    manager.raise_alert("SLOT_EMPTY:a:A2", "empty", Severity.CRITICAL, clock)
    assert len(sink.seen) == 2


def test_unacknowledged_alert_escalates_once():
    manager = AlertManager(cooldown_s=10.0, escalate_after_s=300.0)
    sink = CollectSink()
    manager.add_sink(sink)
    clock = ManualClock()
    manager.raise_alert("QUEUE_CONGESTED:c1", "long queue", Severity.WARN, clock)
    clock.advance(200.0)
    assert manager.tick(clock) == []
    clock.advance(200.0)
    escalations = manager.tick(clock)
    assert len(escalations) == 1
    assert escalations[0].data["severity"] == "CRITICAL"
    assert manager.tick(clock) == []          # only once


def test_acknowledged_alert_does_not_escalate():
    manager = AlertManager(escalate_after_s=100.0)
    clock = ManualClock()
    events = manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, clock)
    assert manager.acknowledge(events[0].data["alert_id"]) is True
    clock.advance(500.0)
    assert manager.tick(clock) == []
    assert manager.open_alerts() == []


def test_a_broken_sink_does_not_stop_the_alert():
    class Broken(Sink):
        def emit(self, alert: AlertData) -> None:
            raise RuntimeError("no speaker")

    manager = AlertManager()
    good = CollectSink()
    manager.add_sink(Broken())
    manager.add_sink(good)
    manager.raise_alert("SLOT_EMPTY:a:A1", "empty", Severity.CRITICAL, ManualClock())
    assert len(good.seen) == 1


# --------------------------------------------------------------------------- #
# Tamper + scheduler
# --------------------------------------------------------------------------- #

def scene(seed: int = 0, shift: int = 0, dark: bool = False) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    if shift:
        image = np.roll(image, shift, axis=1)
    if dark:
        image[:] = 12
    return image


def test_tamper_detector_is_quiet_on_the_same_view():
    reference = scene(1)
    detector = TamperDetector(reference, confirm_frames=2)
    for _ in range(5):
        noisy = np.clip(reference.astype(int) + np.random.randint(-6, 6, reference.shape), 0, 255)
        assert detector.update(noisy.astype(np.uint8)) is False


def test_tamper_detector_catches_a_covered_lens():
    detector = TamperDetector(scene(1), confirm_frames=2)
    assert detector.update(scene(dark=True)) is False    # needs confirmation
    assert detector.update(scene(dark=True)) is True


def test_tamper_detector_catches_a_moved_camera():
    reference = scene(2)
    detector = TamperDetector(reference, confirm_frames=2)
    moved = scene(2, shift=90)
    detector.update(moved)
    assert detector.update(moved) is True


def test_tamper_detector_recovers_when_the_view_returns():
    reference = scene(3)
    detector = TamperDetector(reference, confirm_frames=1)
    assert detector.update(scene(dark=True)) is True
    assert detector.update(reference) is False


def test_fps_scheduler_accepts_at_the_target_rate():
    scheduler = FpsScheduler(5.0)
    accepted = [t / 25.0 for t in range(250) if scheduler.should_process(t / 25.0)]
    assert 48 <= len(accepted) <= 52           # ~5 per second over 10 s


def test_fps_scheduler_for_a_shelf_camera():
    scheduler = FpsScheduler(1 / 30.0)          # one frame every 30 s
    accepted = [t for t in range(300) if scheduler.should_process(float(t))]
    assert accepted == [0, 30, 60, 90, 120, 150, 180, 210, 240, 270]


def test_fps_scheduler_zero_means_every_frame():
    scheduler = FpsScheduler(0.0)
    assert all(scheduler.should_process(t / 25.0) for t in range(50))
