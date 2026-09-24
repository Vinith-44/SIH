"""Export the detector for edge CPUs (M8): ONNX, ONNX INT8, NCNN, LiteRT INT8.

The Pi 5 has no accelerator we use yet, so the detector has to be as cheap as
possible on four ARM cores.  Formats, and which backend runs them:

| Format | File | Backend in config | Notes |
|---|---|---|---|
| ONNX FP32 | `yolo11n.onnx` | `onnx` | the reference export; also what Qualcomm AI Hub compiles (M9) |
| ONNX INT8 | `yolo11n_int8.onnx` | `onnx` | ONNX Runtime static QDQ quantization, **Conv only** - YOLO's head mixes box pixels (0-640) and scores (0-1) in one tensor, and one INT8 scale for both crushes the scores |
| NCNN | `yolo11n_ncnn_model/` | `ultralytics` (model = the folder) | Tencent's ARM-optimised runtime; Ultralytics' recommended Pi format |
| LiteRT INT8 | `yolo11n_saved_model/*_int8.tflite` | `litert` | full-integer TFLite; also the input to the Qualcomm QNN delegate path |

Calibration images come from `--calib` (a folder of JPEGs).  They should look
like the deployment: people, CCTV angles, indoor light.

LiteRT export needs TensorFlow, which is kept out of the runtime venv: it runs in
a separate venv (`storemind/.venv-export`, git-ignored) given by `--export-python`.

    python -m storemind.inference.export --models ../models/yolo11n.pt ../models/yolo26n.pt \
        --calib <folder of jpgs> --formats onnx onnx_int8 ncnn tflite_int8 \
        --export-python .venv-export/Scripts/python.exe
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .detector import letterbox

FORMATS = ("onnx", "onnx_int8", "ncnn", "tflite_int8")


def calibration_tensors(folder: Path, imgsz: int, limit: int = 200) -> list[np.ndarray]:
    """The exact preprocessing `OnnxDetector` uses: letterbox, RGB, CHW, /255."""
    tensors = []
    for path in sorted(folder.glob("*.jpg"))[:limit]:
        image = cv2.imread(str(path))
        if image is None:
            continue
        padded, _ = letterbox(image, imgsz)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensors.append(np.transpose(rgb, (2, 0, 1))[None, ...])
    if not tensors:
        raise SystemExit(f"no calibration images in {folder}")
    return tensors


def export_ultralytics(weights: Path, fmt: str, imgsz: int) -> Path:
    from ultralytics import YOLO

    return Path(YOLO(str(weights)).export(format=fmt, imgsz=imgsz, device="cpu", verbose=False))


def export_onnx_int8(fp32: Path, calib: Path, imgsz: int) -> Path:
    from onnxruntime.quantization import (
        CalibrationDataReader,
        QuantFormat,
        QuantType,
        quantize_static,
    )
    from onnxruntime.quantization.shape_inference import quant_pre_process

    import onnxruntime as ort

    input_name = ort.InferenceSession(str(fp32), providers=["CPUExecutionProvider"]).get_inputs()[0].name
    tensors = calibration_tensors(calib, imgsz)

    class Reader(CalibrationDataReader):
        def __init__(self) -> None:
            self._it = iter(tensors)

        def get_next(self):
            tensor = next(self._it, None)
            return None if tensor is None else {input_name: tensor}

    prepared = fp32.with_name(fp32.stem + "_prep.onnx")
    quant_pre_process(str(fp32), str(prepared), skip_symbolic_shape=True)
    out = fp32.with_name(fp32.stem + "_int8.onnx")
    quantize_static(str(prepared), str(out), Reader(), quant_format=QuantFormat.QDQ,
                    per_channel=True, activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8,
                    op_types_to_quantize=["Conv"])
    prepared.unlink(missing_ok=True)
    return out


def export_tflite_int8(weights: Path, calib: Path, imgsz: int, export_python: str) -> Path:
    """Runs Ultralytics' TFLite INT8 export in the separate TensorFlow venv."""
    data_yaml = calib.parent / "calib.yaml"
    data_yaml.write_text(f"path: {calib.parent.resolve().as_posix()}\ntrain: {calib.name}\n"
                         f"val: {calib.name}\nnames:\n  0: person\n", encoding="utf-8")
    code = ("from ultralytics import YOLO; import sys; "
            f"print(YOLO(r'{weights}').export(format='tflite', int8=True, data=r'{data_yaml}', "
            f"imgsz={imgsz}, device='cpu', verbose=False))")
    result = subprocess.run([export_python, "-c", code], capture_output=True, text=True, cwd=weights.parent)
    if result.returncode != 0:
        raise RuntimeError(f"TFLite export failed:\n{result.stderr[-2000:]}")
    folder = weights.parent / f"{weights.stem}_saved_model"
    candidates = sorted(folder.glob("*int8*.tflite")) or sorted(folder.glob("*full_integer*.tflite"))
    if not candidates:
        raise RuntimeError(f"no INT8 .tflite in {folder}: {result.stdout[-500:]}")
    return candidates[0]


def export(weights: Path, formats, calib: Path | None, imgsz: int, export_python: str | None) -> dict:
    out: dict[str, str] = {}
    for fmt in formats:
        started = time.perf_counter()
        if fmt == "onnx":
            path = export_ultralytics(weights, "onnx", imgsz)
        elif fmt == "ncnn":
            path = export_ultralytics(weights, "ncnn", imgsz)
        elif fmt == "onnx_int8":
            fp32 = weights.with_suffix(".onnx")
            if not fp32.is_file():
                fp32 = export_ultralytics(weights, "onnx", imgsz)
            path = export_onnx_int8(fp32, calib, imgsz)
        elif fmt == "tflite_int8":
            if not export_python:
                raise SystemExit("tflite_int8 needs --export-python (the TensorFlow venv)")
            path = export_tflite_int8(weights, calib, imgsz, export_python)
        else:
            raise SystemExit(f"unknown format {fmt}")
        out[fmt] = str(path)
        print(f"{weights.name} -> {fmt}: {path}  ({time.perf_counter() - started:.0f} s)", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--formats", nargs="+", default=list(FORMATS), choices=FORMATS)
    parser.add_argument("--calib", default=None, help="folder of JPEG calibration frames (INT8 formats)")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--export-python", default=None, help="python of the TensorFlow venv")
    parser.add_argument("--manifest", default="../models/exports.json")
    args = parser.parse_args(argv)
    calib = Path(args.calib) if args.calib else None
    if any(f.endswith("int8") for f in args.formats) and calib is None:
        raise SystemExit("INT8 formats need --calib")
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    for weights in args.models:
        manifest.setdefault(Path(weights).stem, {}).update(
            export(Path(weights).resolve(), args.formats, calib, args.imgsz, args.export_python))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
