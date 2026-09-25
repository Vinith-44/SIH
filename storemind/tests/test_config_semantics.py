"""Config keys do what docs/CONFIG_REFERENCE.md says (M11 follow-ups):
`cameras[].infer_size`, the retired `forecast.horizon_min`, `shelf.method`
detector / hybrid with `shelf.detector_model`, and the CLI backend list."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from storemind.analytics.shelf import ShelfEngine, SlotSpec, combine_fills
from storemind.core.clock import ManualClock
from storemind.core.config import ForecastConfig, ShelfConfig, StoreMindConfig, load_config
from storemind.core.geometry import Polygon
from storemind.inference.detector import Detection, Detector, StubDetector


class FakeDetector(Detector):
    def __init__(self, model: str = "", imgsz: int = 640, fixed: int | None = None, boxes: int = 0) -> None:
        self.model, self.input_size, self.boxes = model, fixed or imgsz, boxes

    def detect(self, image):
        return [Detection((0, 0, 1, 1), 0.9, 0) for _ in range(self.boxes)]


def _tiny_video(path):
    cv2 = pytest.importorskip("cv2")
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 8, (64, 48))
    for _ in range(2):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    return str(path)


@pytest.fixture()
def built(monkeypatch):
    """Replace build_detector in the pipeline; record what was asked for."""
    calls = []

    def fake_build(config, fixed=None):
        calls.append(config)
        return FakeDetector(config.model, config.imgsz, fixed)

    monkeypatch.setattr("storemind.pipeline.build_detector", fake_build)
    return calls


def _pipeline(tmp_path, cameras, **config):
    from storemind.eval.common import _NullStore
    from storemind.pipeline import Pipeline

    for i, cam in enumerate(cameras):
        cam.setdefault("name", f"cam{i}")
        cam.setdefault("source", _tiny_video(tmp_path / f"{i}.avi"))
    return Pipeline(StoreMindConfig(cameras=cameras, **config), store=_NullStore())


# --- infer_size ----------------------------------------------------------------- #

def test_infer_size_default_shares_the_one_detector(tmp_path, built):
    pipeline = _pipeline(tmp_path, [{}, {"infer_size": 640}])
    assert [c.detector for c in pipeline.cameras] == [None, None]
    assert [c.imgsz for c in built] == [640]
    pipeline.close()


def test_infer_size_gives_one_detector_per_size(tmp_path, built):
    pipeline = _pipeline(tmp_path, [{"infer_size": 416}, {"infer_size": 416}, {}])
    a, b, c = (cam.detector for cam in pipeline.cameras)
    assert a is b and a.input_size == 416 and c is None
    assert sorted(cfg.imgsz for cfg in built) == [416, 640]
    pipeline.close()


def test_fixed_shape_model_refuses_another_size(tmp_path, monkeypatch):
    monkeypatch.setattr("storemind.pipeline.build_detector",
                        lambda config: FakeDetector(config.model, config.imgsz, fixed=640))
    with pytest.raises(SystemExit, match="fixed 640 px input"):
        _pipeline(tmp_path, [{"infer_size": 416}])


def test_injected_detector_is_never_replaced(tmp_path, built):
    from storemind.eval.common import _NullStore
    from storemind.pipeline import Pipeline

    config = StoreMindConfig(cameras=[{"name": "c", "source": _tiny_video(tmp_path / "c.avi"), "infer_size": 416}])
    pipeline = Pipeline(config, store=_NullStore(), detector=StubDetector())
    assert pipeline.cameras[0].detector is None and built == []
    pipeline.close()


# --- forecast.horizon_min --------------------------------------------------------- #

def test_retired_horizon_min_is_dropped_not_fatal(tmp_path, caplog):
    assert "horizon_min" not in ForecastConfig.model_fields
    assert ForecastConfig(horizon_min=10, max_counters=4).max_counters == 4
    saved = tmp_path / "old.yaml"
    saved.write_text("forecast:\n  horizon_min: 10\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert load_config(saved).forecast.max_counters == 8
    assert "horizon_min is retired" in caplog.text


# --- shelf.method ------------------------------------------------------------------ #

def test_detector_methods_need_a_model():
    for method in ("detector", "hybrid"):
        with pytest.raises(ValidationError, match="detector_model"):
            StoreMindConfig(shelf={"method": method})
        assert StoreMindConfig(shelf={"method": method, "detector_model": "p.onnx"}).shelf.method == method
    with pytest.raises(ValueError, match="needs a product detector"):
        ShelfEngine([], method="hybrid")


def test_pipeline_hands_the_product_detector_to_the_shelf_engine(tmp_path, built):
    shelf_cam = {"role": "shelf", "shelves": [{"name": "s", "slots": [{"name": "A1", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}]}
    pipeline = _pipeline(tmp_path, [shelf_cam], shelf={"method": "hybrid", "detector_model": "products.onnx"})
    product = [cfg for cfg in built if cfg.model == "products.onnx"]
    assert len(product) == 1 and product[0].person_class == 0
    camera = pipeline.cameras[0]
    camera.resolve_geometry(64, 48)
    assert camera.shelf.method == "hybrid" and camera.shelf.detector is pipeline.shelf_detector
    pipeline.close()


W, H = 320, 240
SLOT = [(0.1, 0.1), (0.5, 0.1), (0.5, 0.6), (0.1, 0.6)]


def _full_shelf() -> np.ndarray:
    image = np.full((H, W, 3), 120, np.uint8)
    x1, y1, x2, y2 = int(0.1 * W), int(0.1 * H), int(0.5 * W), int(0.6 * H)
    step = (x2 - x1) // 5
    for c in range(5):
        image[y1 + 4:y2 - 4, x1 + c * step + 2:x1 + (c + 1) * step - 2] = (40, 180, 220)
        image[y1 + 4:y1 + 6, x1 + c * step + 2:x1 + (c + 1) * step - 2] = 250
    return image


def _state(method: str, products: int) -> str:
    config = ShelfConfig(name="s", vote_k=2, vote_n=3, dark_lux=None, dark_brightness=None, lux_jump_ratio=None)
    slot = SlotSpec(polygon=Polygon("A1", SLOT, "slot").resolve(W, H), sku="Atta", reference_facings=5)
    engine = ShelfEngine([("s", [slot], config)], method=method,
                         detector=FakeDetector(boxes=products) if method != "reference" else None)
    engine.capture_references(_full_shelf())
    clock = ManualClock()
    for _ in range(3):
        engine.update(_full_shelf(), [], clock)
        clock.advance(60)
    return engine.shelves["s"]["slots"]["A1"].state.value


def test_methods_disagree_the_way_they_should():
    # The camera sees a full slot; the (fake) product detector counts 0 of 5 packs.
    assert _state("reference", 0) == "FULL"
    assert _state("detector", 0) == "EMPTY"
    assert _state("hybrid", 0) == "LOW"        # the mean of ~1.0 and 0.0
    assert _state("hybrid", 5) == "FULL"


def test_combine_fills_caps_confidence_by_agreement():
    assert combine_fills(1.0, 0.9, 0.0, 0.9) == (0.5, 0.0)
    fill, confidence = combine_fills(0.8, 0.9, 0.6, 0.7)
    assert fill == pytest.approx(0.7) and confidence == pytest.approx(0.7)


# --- CLI ------------------------------------------------------------------------- #

def test_cli_offers_every_backend_the_config_accepts():
    from storemind.run import build_parser

    for backend in ("litert_qnn", "ort_qnn"):
        assert build_parser().parse_args(["--backend", backend]).backend == backend
