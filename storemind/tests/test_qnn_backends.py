"""M9: QNN backends fall back to CPU honestly (no Qualcomm hardware here)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from storemind.core.config import DetectorConfig
from storemind.inference import detector as det

LEGACY_TFLITE = Path(__file__).resolve().parents[2] / "models" / "efficientdet_lite0_coco_legacy.tflite"


def test_litert_qnn_without_the_delegate_runs_on_cpu_and_says_so(monkeypatch):
    if not LEGACY_TFLITE.is_file():
        pytest.skip("legacy TFLite model not present")

    def no_delegate(lib):
        raise OSError(f"{lib}: cannot open shared object file")

    monkeypatch.setattr(det, "_load_qnn_delegate", no_delegate)
    d = det.build_detector(DetectorConfig(backend="litert_qnn", model=str(LEGACY_TFLITE)))
    assert d.accelerator.startswith("cpu (fallback: QNN delegate not loaded")
    d.detect(np.zeros((240, 320, 3), np.uint8))                    # still works
    with pytest.raises(SystemExit):
        det.build_detector(DetectorConfig(backend="litert_qnn", model=str(LEGACY_TFLITE),
                                          require_accelerator=True))


def test_ort_qnn_on_a_build_without_the_qnn_ep_falls_back(tmp_path):
    ort = pytest.importorskip("onnxruntime")
    if "QNNExecutionProvider" in ort.get_available_providers():
        pytest.skip("this onnxruntime has the QNN EP")
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    weight = numpy_helper.from_array(np.full((4, 3, 3, 3), 0.01, np.float32), "w")
    graph = helper.make_graph([helper.make_node("Conv", ["images", "w"], ["output0"], pads=[1, 1, 1, 1])], "t",
                              [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 32, 32])],
                              [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 4, 32, 32])],
                              [weight])
    model = tmp_path / "m.onnx"
    proto = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    proto.ir_version = 8
    onnx.save(proto, str(model))
    d = det.OnnxDetector(str(model), imgsz=32, use_qnn=True)
    assert d.accelerator.startswith("cpu (fallback: onnxruntime build has no QNNExecutionProvider")
    with pytest.raises(SystemExit):
        det.OnnxDetector(str(model), imgsz=32, use_qnn=True, require_accelerator=True)
    assert det.OnnxDetector(str(model), imgsz=32).accelerator == "cpu"


def test_merge_yolo_outputs_restores_the_single_tensor_either_order():
    boxes = np.arange(4 * 6, dtype=np.float32).reshape(1, 4, 6)
    scores = np.full((1, 80, 6), 0.5, np.float32)
    whole = np.concatenate([boxes, scores], axis=1)
    assert np.array_equal(det.merge_yolo_outputs([boxes, scores]), whole)
    assert np.array_equal(det.merge_yolo_outputs([scores, boxes]), whole)     # order from a runtime may differ
    assert det.merge_yolo_outputs([whole]) is whole


def test_aihub_clean_onnx_splits_the_head_so_int8_keeps_its_scores(tmp_path):
    onnx = pytest.importorskip("onnx")
    ort = pytest.importorskip("onnxruntime")
    import importlib.util
    import sys

    from onnx import TensorProto, helper

    tool = Path(__file__).resolve().parents[1] / "tools" / "aihub_profile.py"
    spec = importlib.util.spec_from_file_location("aihub_profile", tool)
    aihub = importlib.util.module_from_spec(spec)
    sys.modules["aihub_profile"] = aihub
    spec.loader.exec_module(aihub)

    # a stand-in YOLO head: Concat(Mul(boxes), Sigmoid(scores)) on axis 1
    nodes = [helper.make_node("Mul", ["b", "k"], ["box"]), helper.make_node("Sigmoid", ["s"], ["score"]),
             helper.make_node("Concat", ["box", "score"], ["output0"], axis=1)]
    graph = helper.make_graph(
        nodes, "head",
        [helper.make_tensor_value_info("b", TensorProto.FLOAT, [1, 4, 6]),
         helper.make_tensor_value_info("s", TensorProto.FLOAT, [1, 80, 6])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84, 6])],
        [helper.make_tensor("k", TensorProto.FLOAT, [1], [640.0])],
        value_info=[helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84, 6])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    path = tmp_path / "head.onnx"
    onnx.save(model, str(path))

    split = aihub.clean_onnx(path)
    outs = [o.name for o in onnx.load(str(split)).graph.output]
    assert outs == ["boxes", "scores"]
    feed = {"b": np.random.default_rng(0).random((1, 4, 6), dtype=np.float32),
            "s": np.random.default_rng(1).random((1, 80, 6), dtype=np.float32)}
    whole = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(None, feed)[0]
    parts = ort.InferenceSession(str(split), providers=["CPUExecutionProvider"]).run(None, feed)
    assert np.array_equal(det.merge_yolo_outputs(parts), whole)
