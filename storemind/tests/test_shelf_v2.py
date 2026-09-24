"""M3 shelf v2: lighting robustness, reference bank, dark/jump handling, glare,
rectification, weight fusion, and the pipeline inputs that feed them."""

from __future__ import annotations

import numpy as np
import pytest

from storemind.analytics.shelf import (
    ShelfEngine,
    SlotSpec,
    edge_density,
    glare_mask,
    gradient_texture,
    gray_world,
)
from storemind.core.clock import ManualClock
from storemind.core.config import ShelfConfig
from storemind.core.geometry import Polygon

W, H = 320, 240
SLOT = [(0.1, 0.1), (0.5, 0.1), (0.5, 0.6), (0.1, 0.6)]


def shelf_image(fill: float = 1.0, gain: float = 1.0, tint=(1.0, 1.0, 1.0),
                hue_bgr=(40, 180, 220), noise: float = 2.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((H, W, 3), 120, dtype=np.float32)
    x1, y1, x2, y2 = int(0.1 * W), int(0.1 * H), int(0.5 * W), int(0.6 * H)
    image[y1:y2, x1:x2] = (150, 160, 165)                       # back panel
    columns, step = 5, (x2 - x1) // 5
    for c in range(int(round(columns * fill))):
        px = x1 + c * step + 2
        image[y1 + 4:y2 - 4, px:px + step - 4] = hue_bgr
        image[y1 + 4:y1 + 6, px:px + step - 4] = 250
        image[y2 - 6:y2 - 4, px:px + step - 4] = 20
    image = image * gain * np.array(tint, np.float32) + rng.normal(0, noise, image.shape)
    return np.clip(image, 0, 255).astype(np.uint8)


def engine(**overrides) -> ShelfEngine:
    defaults = {"vote_k": 2, "vote_n": 3, "low_threshold": 0.5, "empty_threshold": 0.28}
    config = ShelfConfig(name="shelf-a", **{**defaults, **overrides})
    slot = SlotSpec(polygon=Polygon("A1", SLOT, "slot").resolve(W, H), sku="Atta", price=260.0,
                    full_grams=overrides.pop("_full", None))
    return ShelfEngine([("shelf-a", [slot], config)], cam="shelf-a")


def state(e: ShelfEngine) -> str:
    return e.shelves["shelf-a"]["slots"]["A1"].state.value


def run(e: ShelfEngine, image, times=3, lux=None, clock=None):
    clock = clock or ManualClock()
    events = []
    for _ in range(times):
        if lux is not None:
            e.observe_lux(lux)
        events += e.update(image, [], clock)
        clock.advance(60)
    return events


# --------------------------------------------------------------------------- #

def test_gradient_texture_is_invariant_to_light_gain_but_canny_is_not():
    bright, dim = shelf_image(gain=1.0, noise=0), shelf_image(gain=0.45, noise=0)
    assert gradient_texture(dim) == pytest.approx(gradient_texture(bright), rel=0.15)
    assert edge_density(dim) < 0.7 * edge_density(bright)


def test_evening_light_does_not_empty_a_full_slot():
    # Single reference, no lux: the measures themselves must cope with dimmer, warmer light.
    e = engine(reference_bank=1, dark_lux=None, dark_brightness=None, lux_jump_ratio=None)
    e.capture_references(shelf_image())
    run(e, shelf_image(gain=0.5, tint=(0.8, 0.95, 1.1)))
    assert state(e) == "FULL"


def test_an_empty_slot_is_still_found_in_evening_light():
    e = engine()
    e.capture_references(shelf_image())
    run(e, shelf_image(fill=0.0, gain=0.5, tint=(0.8, 0.95, 1.1)))
    assert state(e) == "EMPTY"


def test_too_dark_says_unknown_never_empty():
    e = engine()
    e.observe_lux(400)
    e.capture_references(shelf_image())
    events = run(e, shelf_image(fill=0.0, gain=0.06), lux=3)
    assert state(e) == "UNKNOWN"
    assert [ev.data["reason"] for ev in events][0].startswith("too dark (lux 3")


def test_without_a_light_sensor_brightness_decides_dark():
    e = engine()
    e.capture_references(shelf_image())
    run(e, shelf_image(fill=0.0, gain=0.06))
    assert state(e) == "UNKNOWN"


def test_a_sudden_light_change_skips_one_cycle():
    e = engine(vote_k=1, vote_n=1)
    e.observe_lux(400)
    e.capture_references(shelf_image())
    clock = ManualClock()
    e.observe_lux(40)                          # 10x jump
    assert e.update(shelf_image(fill=0.0), [], clock) == []
    assert e.shelves["shelf-a"]["slots"]["A1"].observations == 0
    e.observe_lux(42)
    e.update(shelf_image(fill=0.0), [], clock)
    assert state(e) == "EMPTY"


def test_the_bank_learns_new_lighting_while_full_and_picks_it_by_lux():
    e = engine()
    e.observe_lux(450)
    e.capture_references(shelf_image())
    run(e, shelf_image(gain=0.6, tint=(0.8, 0.95, 1.1)), times=4, lux=140)
    runtime = e.shelves["shelf-a"]["slots"]["A1"]
    assert len(runtime.bank) == 2 and sorted(r.level for r in runtime.bank) == [140, 450]
    assert e._nearest(runtime.bank, 150).level == 140


def test_glare_pixels_are_masked_and_gray_world_ignores_the_slots():
    image = shelf_image()
    image[60:80, 60:90] = 255
    assert glare_mask(image)[70, 75] and not glare_mask(image)[200, 300]
    tinted = shelf_image(tint=(0.7, 1.0, 1.3))
    exclude = np.zeros((H, W), bool)
    exclude[int(0.1 * H):int(0.6 * H), int(0.1 * W):int(0.5 * W)] = True
    balanced = gray_world(tinted, exclude)[~exclude].reshape(-1, 3).mean(axis=0)
    assert np.ptp(balanced) < 3.0


def test_rectification_warps_a_slanted_slot_to_the_configured_size():
    slanted = Polygon("A1", [(0.1, 0.2), (0.5, 0.1), (0.55, 0.6), (0.12, 0.5)], "slot").resolve(W, H)
    crop = ShelfEngine._crop(shelf_image(), slanted, ShelfConfig(name="s", rectify_size=(96, 128)))
    assert crop.shape == (128, 96, 3)


def test_weight_fusion_wins_on_deep_shelves_and_flags_disagreement():
    config = ShelfConfig(name="shelf-a", vote_k=1, vote_n=1, empty_threshold=0.28,
                         low_threshold=0.5, disagree_fill=0.4)
    slot = SlotSpec(polygon=Polygon("A1", SLOT, "slot").resolve(W, H), deep=True)
    e = ShelfEngine([("shelf-a", [slot], config)], cam="shelf-a")
    e.observe_weight("shelf-a", "A1", 1800, stable=True)
    e.capture_references(shelf_image())                 # learns full_grams = 1800
    e.observe_weight("shelf-a", "A1", 300, stable=True)  # back rows gone, front row still full
    e.observe_weight("shelf-a", "A1", 1790, stable=False)  # handled: ignored
    events = run(e, shelf_image(), times=1)
    assert state(e) == "EMPTY"
    assert "CHECK SHELF: camera" in events[0].data["reason"]


def test_camera_fault_and_long_occlusion_are_unknown():
    from storemind.tracking.tracker import Track

    e = engine(occluded_unknown_cycles=3)
    e.capture_references(shelf_image())
    clock = ManualClock()
    blocker = Track(1, (0, 0, W, H), 0.9)
    for _ in range(3):
        e.update(shelf_image(), [blocker], clock)
    assert state(e) == "UNKNOWN"
    e2 = engine()
    e2.capture_references(shelf_image())
    e2.set_camera_ok(False)
    events = e2.update(shelf_image(), [], ManualClock())
    assert state(e2) == "UNKNOWN" and events[0].data["reason"] == "camera fault"


def test_pipeline_routes_lux_weight_and_restock_to_the_shelf(tmp_path):
    cv2 = pytest.importorskip("cv2")
    from storemind.core.config import StoreMindConfig
    from storemind.core.events import EnvironmentData, EventType, SensorData, WeightData, make_event
    from storemind.eval.common import _NullStore
    from storemind.inference.detector import StubDetector
    from storemind.pipeline import Pipeline

    video = tmp_path / "s.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 1, (W, H))
    for _ in range(3):
        writer.write(shelf_image())
    writer.release()
    config = StoreMindConfig(
        sensors={"cell_map": {"shelf-a/A1": "3"}},
        cameras=[{"name": "shelf-cam", "source": str(video), "role": "shelf", "shelf_period_s": 1,
                  "shelves": [{"name": "shelf-a", "auto_reference_s": None,
                               "slots": [{"name": "A1", "points": SLOT}]}]}])
    pipeline = Pipeline(config, store=_NullStore(), detector=StubDetector())
    pipeline.run(progress=False)
    shelf = pipeline.cameras[0].shelf
    clock = ManualClock()

    def pub(t, data):
        pipeline.bus.publish(make_event(ts=clock.now(), store="s", node="stm32-01", type=t, data=data))

    pub(EventType.ENVIRONMENT, EnvironmentData(node="stm32-01", lux=320))
    pub(EventType.WEIGHT, WeightData(node="stm32-01", slot="3", grams=950, stable=True))
    assert shelf._lux[None] == 320
    assert shelf.shelves["shelf-a"]["slots"]["A1"].weight_g == 950
    pipeline.cameras[0].pending = type("F", (), {"image": shelf_image()})()
    pub(EventType.SENSOR, SensorData(node="stm32-01", sensor="restock", channel="shelf-a",
                                     value=1, unit="press"))
    assert shelf.shelves["shelf-a"]["slots"]["A1"].bank
    pipeline.close()


def test_reorder_drafts_open_on_empty_and_close_on_restock():
    from datetime import datetime

    from storemind.analytics.reorder import ReorderQueue
    from storemind.core.events import EventType, SlotStateData, make_event

    queue = ReorderQueue()
    clock = ManualClock()

    def slot(state, name="A1", sku="Atta 5kg"):
        clock.advance(60)
        queue.on_event(make_event(ts=clock.now(), store="s", node="n", type=EventType.SLOT_STATE,
                                  data=SlotStateData(shelf="shelf-a", slot=name, sku=sku, state=state)))

    slot("LOW")
    slot("EMPTY")
    slot("EMPTY", "B1", "Soap bar")
    slot("UNKNOWN", "B1", "Soap bar")
    drafts = queue.open_drafts()
    assert [(d["sku"], d["state"]) for d in drafts] == [("Atta 5kg", "EMPTY"), ("Soap bar", "EMPTY")]
    text = queue.whatsapp_text("BVRIT canteen", now=datetime(2026, 9, 24, 18, 0))
    assert "- Atta 5kg: OUT since 09:01" in text and "draft" in text
    slot("FULL")
    assert [d["sku"] for d in queue.open_drafts()] == ["Soap bar"]
