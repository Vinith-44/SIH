"""M8: ONNX INT8 export (Conv-only static quantization) and calibration preprocessing."""

from __future__ import annotations

import numpy as np
import pytest


def _tiny_conv_model(path):
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    rng = np.random.default_rng(0)
    weight = numpy_helper.from_array(rng.normal(0, 0.1, (4, 3, 3, 3)).astype(np.float32), "w")
    graph = helper.make_graph(
        [helper.make_node("Conv", ["images", "w"], ["conv"], pads=[1, 1, 1, 1]),
         helper.make_node("Sigmoid", ["conv"], ["output0"])],
        "tiny", [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 32, 32])],
        [helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 4, 32, 32])], [weight])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.save(model, str(path))


def test_calibration_tensors_match_the_onnx_detector_preprocessing(tmp_path):
    cv2 = pytest.importorskip("cv2")
    from storemind.inference.export import calibration_tensors

    for i in range(3):
        cv2.imwrite(str(tmp_path / f"f{i}.jpg"), np.full((48, 64, 3), 40 * i, np.uint8))
    tensors = calibration_tensors(tmp_path, imgsz=32)
    assert len(tensors) == 3 and tensors[0].shape == (1, 3, 32, 32)
    assert tensors[0].dtype == np.float32 and float(tensors[2].max()) <= 1.0
    with pytest.raises(SystemExit):
        calibration_tensors(tmp_path / "empty", 32)


def test_onnx_int8_export_quantizes_conv_and_still_runs(tmp_path):
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("onnx")
    import onnx
    import onnxruntime as ort

    from storemind.inference.export import export_onnx_int8

    fp32 = tmp_path / "tiny.onnx"
    _tiny_conv_model(fp32)
    calib = tmp_path / "calib"
    calib.mkdir()
    rng = np.random.default_rng(1)
    for i in range(4):
        cv2.imwrite(str(calib / f"c{i}.jpg"), rng.integers(0, 255, (32, 32, 3), dtype=np.uint8))
    out = export_onnx_int8(fp32, calib, imgsz=32)
    ops = {node.op_type for node in onnx.load(str(out)).graph.node}
    assert "QuantizeLinear" in ops and "DequantizeLinear" in ops
    x = np.random.default_rng(2).random((1, 3, 32, 32), dtype=np.float32)
    ref = ort.InferenceSession(str(fp32), providers=["CPUExecutionProvider"]).run(None, {"images": x})[0]
    got = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(None, {"images": x})[0]
    assert np.abs(ref - got).mean() < 0.05                 # close to FP32 after quantization
