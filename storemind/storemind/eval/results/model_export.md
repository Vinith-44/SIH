# Exported detectors (M8)

## Fidelity to the FP32 PyTorch model and laptop-CPU speed (bucket S)

vtest.avi, 100 frames not used for calibration, conf 0.25. Recall/precision are **against the FP32 model's boxes**, not against people. Speeds are this laptop's CPU (4 threads) - not a Pi.

| model | format | size MB | recall vs FP32 | precision vs FP32 | CPU ms median / p95 |
|---|---|---|---|---|---|
| yolo11n | pytorch fp32 | 5.61 | 100.0% | 100.0% | 99.7 / 535.5 |
| yolo11n | onnx fp32 | 10.74 | 100.0% | 100.0% | 29.8 / 34.0 |
| yolo11n | onnx int8 | 3.21 | 97.6% | 100.0% | 32.9 / 35.8 |
| yolo11n | ncnn fp32 | 10.66 | 100.0% | 100.0% | 52.3 / 58.1 |
| yolo26n | pytorch fp32 | 5.54 | 100.0% | 100.0% | 41.1 / 43.8 |
| yolo26n | onnx fp32 | 9.93 | 100.0% | 100.0% | 24.3 / 25.4 |
| yolo26n | onnx int8 | 3.06 | 99.6% | 99.0% | 29.7 / 31.6 |
| yolo26n | ncnn fp32 | 9.85 | 100.0% | 100.0% | 138.3 / 153.4 |

## Counting on CAVIAR with each format (bucket A, 16 clips, shipped counter, in-sample settings)

| model | format | entries (30) | exits (21) | entry acc | exit acc | entry F1 | exit F1 |
|---|---|---|---|---|---|---|---|
| yolo11n | pytorch fp32 | 28 | 21 | 93.3% | 100.0% | 0.93 | 0.86 |
| yolo11n | onnx fp32 | 28 | 21 | 93.3% | 100.0% | 0.93 | 0.86 |
| yolo11n | onnx int8 | 28 | 19 | 93.3% | 90.5% | 0.93 | 0.90 |
| yolo11n | ncnn fp32 | 28 | 21 | 93.3% | 100.0% | 0.93 | 0.86 |
| yolo26n | pytorch fp32 | 27 | 16 | 90.0% | 76.2% | 0.88 | 0.81 |
| yolo26n | onnx fp32 | 27 | 16 | 90.0% | 76.2% | 0.88 | 0.81 |
| yolo26n | onnx int8 | 25 | 14 | 83.3% | 66.7% | 0.91 | 0.80 |
| yolo26n | ncnn fp32 | 27 | 16 | 90.0% | 76.2% | 0.88 | 0.81 |

Compare rows *within* a model: the question is how much each export changes the result, not the absolute accuracy (that is the cross-validated number in docs/COUNTING.md).

Command: `python -m storemind.eval.eval_export --caviar`
