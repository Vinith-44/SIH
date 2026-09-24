# Qualcomm path

**Owner:** Vinith (Person A) · **Milestone:** M9 · **Code:** `storemind/tools/aihub_profile.py`, `inference/detector.py`
(`litert_qnn`, `ort_qnn`) · **Port guide:** `deploy/qualcomm/README.md` · **Results:** `eval/results/qualcomm_aihub.json`

## 1. What we measured: Qualcomm AI Hub hosted devices (bucket Q, not our board)

We compiled our ONNX exports to TFLite and ran them on devices **Qualcomm hosts**. The main one is a
**Dragonwing RB3 Gen 2 Vision Kit (QCS6490)**, the chip family the problem statement targets; the QCS8550 proxy is
there for context. INT8 = AI Hub w8a8 quantization, calibrated on 20 of our frames. "Agreement" compares the
device's detections with our local ONNX FP32 model on 6 vtest frames. That is too few frames for a rate, but it is
enough to catch a broken model.

| model | precision | RB3 Gen 2 (QCS6490) latency | NPU layers | peak memory | device detections vs local FP32 | QCS8550 proxy |
|---|---|---|---|---|---|---|
| YOLO11n | FP32 | 150.9 ms | 5% | 66 MB | 29 / 29 identical | 6.0 ms |
| **YOLO11n** | **INT8** | **12.8 ms** | **100%** | **17 MB** | all 29 found, +2 extra (precision 94%) | 2.7 ms |
| YOLO26n | FP32 | 138.0 ms | 5% | 64 MB | 29 / 29 identical | 5.8 ms |
| YOLO26n | INT8 | 13.9 ms | 100% | 19 MB | all 29 found, +2 extra | 2.9 ms |
| YOLO11s | FP32 | 239.3 ms | 5% | 108 MB | 30 / 30 identical | 7.0 ms |
| **YOLO11s** | **INT8** | **11.0 ms** | **100%** | 26 MB | all 30 found, +5 extra (precision 86%) | 3.5 ms |

What it says:
- **On QCS6490, FP32 barely touches the NPU** (Hexagon v68 has no efficient FP path); the work lands on GPU and CPU
  at about 140–240 ms. **INT8 runs 100% on the NPU at 11–14 ms**, about 12× faster, with a quarter of the memory.
- **YOLO11s becomes affordable.** The Pi 5 CPU can't run it at 8 FPS (M1: 3.75 FPS on the laptop CPU). On the
  QCS6490 NPU the profile estimates 11 ms, even below YOLO11n. That estimate is AI Hub's, from a single profile job,
  and it is surprising enough that a real board must confirm it. YOLO11s had the best tracking IDF1 in the M1
  bake-off, so this is the concrete "why Qualcomm" argument: **the better model fits the budget only on the NPU.**
- **INT8 adds a few boxes** (precision 86–94% against FP32 on these frames). Before shipping INT8 on a board,
  re-run the CAVIAR counting check per format (`eval/eval_export.py`) with the AI Hub model.

## 2. The INT8 failure we hit first (and fixed)

The first AI Hub run's INT8 models also ran 100% on the NPU at about 12 ms, **but detected nothing**. The model's
single output concatenated box coordinates (0–640 px) with class scores (0–1). One INT8 scale for both turned every
score into exactly 0.0 (checked locally on the downloaded model: 1 distinct score value).
**Fix:** `tools/aihub_profile.py` cuts the final `Concat` and exposes `boxes` and `scores` as two outputs, each with
its own scale. `merge_yolo_outputs()` joins them on the host. The failed run is kept in
`eval/results/qualcomm_aihub_run1_single_output.json`. Our own ONNX INT8 export (M8) avoided this by quantizing only
the convolutions.

## 3. Comparison table (keep the buckets apart on any slide)

| | Raspberry Pi 5 CPU | QCS6490 NPU |
|---|---|---|
| **Our detector** | not measured yet (`tools/bench_pi.py`, docs/HARDWARE_TODO.md "M8") — **S** | YOLO11n INT8 12.8 ms, YOLO11s INT8 11.0 ms (AI Hub hosted RB3 Gen 2) — **Q** |
| **Published** (consult.red, their YOLO setup, not ours) | 3.91 FPS · 1,423 mJ/frame · 70 °C | 34.22 FPS · 129 mJ/frame · 54 °C — **P** |

The P row is a third-party benchmark with a different model and pipeline. It shows the trend (the NPU is about 9×
faster at about 1/11 of the energy per frame) and is not a claim about StoreMind. Source:
https://consult.red/insights/npus-vs-cpus-for-edge-ai-vision-less-heat-less-power-more-headroom/

## 4. Running on a QCS6490 board (when we have one)

- Backends `litert_qnn` (LiteRT + QNN TFLite delegate, HTP) and `ort_qnn` (ONNX Runtime + QNN EP).
- Every detector reports `accelerator`, e.g. `qnn-htp (LiteRT QNN delegate)` or `cpu (fallback: <reason>)`.
  `require_accelerator: true` refuses to start on CPU. **We never claim NPU use that did not happen.**
- Model: `models/yolo11n_aihub_w8a8.tflite` (downloaded by the tool). Full checklist: `deploy/qualcomm/README.md`.

## 5. Also recorded (from M1/M8)
- YOLO11s is too slow for the Pi 5 CPU (3.75 FPS on the laptop CPU) — its place is the NPU (above).
- YOLO26n: same size as YOLO11n; on AI Hub it behaves like YOLO11n (13.9 ms INT8).
- **LiteRT INT8 for the Pi**: the AI Hub-compiled `*_aihub_w8a8.tflite` is standard TFLite. It runs with
  `backend: litert` on any CPU, which unblocks the M8 LiteRT item without WSL. Its CPU speed on the Pi is still to be
  measured with `bench_pi.py`. Checked locally (laptop CPU, 40 vtest frames): 3.92 boxes/frame vs 3.95 for ONNX FP32, 25 ms vs 35 ms.

## 6. Licence note
Ultralytics YOLO models are AGPL-3.0: fine for the hackathon and an open demo. A closed commercial product needs an
Ultralytics Enterprise licence or a permissively licensed detector.

## Sources
- Qualcomm AI Hub: https://aihub.qualcomm.com/ (jobs linked per row in `eval/results/qualcomm_aihub.json`)
- LiteRT + QNN delegate: https://docs.qualcomm.com/doc/80-70029-15B/topic/run-a-litert-model-using-delegate.html
- QCS6490 = HTP v68: https://github.com/pytorch/executorch/issues/7356 · research/23 §5
