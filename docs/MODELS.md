# Models and exports (M8)

**Owner:** Vinith (Person A) · **Code:** `storemind/storemind/inference/export.py`, `tools/bench_pi.py`,
`eval/eval_export.py` · **Results:** `eval/results/model_export.md`, `eval/results/pi/*.json`

## 1. Which model, in which format, on which device

| Format | File (in `models/`, git-ignored) | Config | Status |
|---|---|---|---|
| PyTorch FP32 | `yolo11n.pt`, `yolo26n.pt` | `backend: ultralytics`, `model: ../models/yolo11n.pt` | reference; laptop GPU for evaluation |
| ONNX FP32 | `yolo11n.onnx` | `backend: onnx` | exported; also the input to Qualcomm AI Hub (M9) |
| ONNX INT8 | `yolo11n_int8.onnx` | `backend: onnx` | exported (ONNX Runtime static QDQ, **Conv layers only**) |
| NCNN | `yolo11n_ncnn_model/` | `backend: ultralytics`, `model: ../models/yolo11n_ncnn_model` | exported; Ultralytics' recommended Pi CPU format |
| LiteRT INT8 | `yolo11n_saved_model/*_int8.tflite` | `backend: litert` | **blocked on this laptop**: Ultralytics' TFLite export only runs on Linux x86 or macOS. Routes: WSL, Colab, or Qualcomm AI Hub compiling the ONNX model to TFLite (M9) |

Why only the Conv layers are quantized in ONNX INT8: YOLO's output head concatenates box coordinates (0–640 px) and
class scores (0–1) into one tensor. A single INT8 scale for both crushes the scores, so the head stays FP32.

Calibration: 198 frames from CAVIAR, OpenCV `vtest.avi` and the synthetic scenes. People, CCTV angles and indoor
light. Frames used for calibration are excluded from the fidelity check.

## 2. Results so far

**Laptop CPU and fidelity (bucket S).** Full table in `eval/results/model_export.md`.
- INT8 keeps 97.6% (YOLO11n) and 99.6% (YOLO26n) of the FP32 model's detections.
- On this x86 laptop INT8 is **not** faster than ONNX FP32 (~30 ms vs ~30 ms), and NCNN is slower (52 / 138 ms).
  NCNN targets ARM, and ONNX Runtime's INT8 speed depends on CPU instructions. **Laptop rankings may flip on the
  Pi**, so none of these are Pi numbers.

**Counting on CAVIAR per format (bucket A).** All 16 clips, run through the shipped counter. Those settings were
tuned on these clips, so compare formats with each other; the accuracy claim is the cross-validated one in
COUNTING.md.

| model | format | entries (truth 30) | exits (truth 21) | entry / exit F1 |
|---|---|---|---|---|
| YOLO11n | PyTorch = ONNX FP32 = NCNN | 28 | 21 | 0.93 / 0.86 |
| YOLO11n | ONNX INT8 | 28 | 19 | 0.93 / 0.90 |
| YOLO26n | PyTorch = ONNX FP32 = NCNN | 27 | 16 | 0.88 / 0.81 |
| YOLO26n | ONNX INT8 | 25 | 14 | 0.91 / 0.80 |

- **FP32 exports are lossless**: ONNX and NCNN give exactly the PyTorch counts.
- **INT8 moves a few crossings**: 2 exits for YOLO11n; 2 entries and 2 exits for YOLO26n. Use it only if the Pi
  needs the speed.
- With the shipped counter settings, YOLO26n counts worse than YOLO11n here, even though its tracking IDF1 was
  better in the M1 bake-off. The counter settings were never tuned with YOLO26n.

**Raspberry Pi 5 (bucket S): not measured yet.** Run `tools/bench_pi.py` on the Pi (steps in
`docs/HARDWARE_TODO.md` "M8"). The JSON it writes feeds RESULTS.md directly. The ship rule: **the most accurate
model/format that holds 8 FPS on the entrance camera.** From the laptop CPU, YOLO11s (3.75 FPS) is already out.

## 3. Reproduce

```
cd storemind
.venv/Scripts/python -m storemind.inference.export --models ../models/yolo11n.pt ../models/yolo26n.pt \
    --formats onnx onnx_int8 ncnn --calib <folder of calibration jpgs>
# LiteRT INT8 (Linux x86 / macOS only, TensorFlow venv):
python -m storemind.inference.export --models ../models/yolo11n.pt --formats tflite_int8 \
    --calib <folder> --export-python .venv-export/bin/python
.venv/Scripts/python -m storemind.eval.eval_export --caviar --calib <folder>
```

## 4. Licence note
Ultralytics YOLO11/YOLO26 weights and code are **AGPL-3.0**. That is fine for the hackathon and an open-source demo.
A closed commercial product needs an Ultralytics Enterprise licence, or a permissively licensed detector
(e.g. an Apache-2.0 model). Listed in `docs/QUALCOMM.md` for M9.

## Sources
- Ultralytics export formats, and NCNN as the recommended Raspberry Pi format:
  https://docs.ultralytics.com/integrations/ncnn/ · https://docs.ultralytics.com/guides/raspberry-pi/
- ONNX Runtime static quantization (QDQ): https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html
- Pi 5 PMIC power reading and correction: https://github.com/jfikar/RPi5-power (research/23 §4.5)
