# StoreMind on Qualcomm QCS6490 (RUBIK Pi 3 / RB3 Gen 2) — port guide

**Status: prepared, NOT yet run on hardware.** We do not own a QCS6490 board. Every Qualcomm number we quote comes
from **Qualcomm AI Hub hosted devices** (bucket Q, `docs/QUALCOMM.md`). This guide is the checklist for the day we
get one. **Owner:** Vinith (Person A).

## 1. Why the port is small
- **Same camera.** RUBIK Pi 3 takes the Raspberry Pi Camera Module 3 (IMX708) on its CSI connectors.
- **Same 40-pin header.** The UART on pins 8/10 connects to the STM32 sensor node exactly as on the Pi 5
  (`docs/PROTOCOL.md`, `docs/WIRING.md`).
- **Same code.** StoreMind is pure Python. Only the detector backend changes, from `onnx`/NCNN on the Pi 5 CPU to
  `litert_qnn` or `ort_qnn` on the Hexagon NPU. Tracking, analytics, fusion, bus, API and dashboard are unchanged.

## 2. Model
Use the INT8 TFLite compiled by AI Hub, `models/yolo11n_aihub_w8a8.tflite`, produced by `tools/aihub_profile.py`
from our ONNX export with our calibration frames. It is the exact artefact AI Hub profiled on the hosted RB3 Gen 2.

## 3. Runtime: two options

**A. LiteRT + QNN TFLite delegate (simplest)**
```yaml
detector:
  backend: litert_qnn
  model: ../models/yolo11n_aihub_w8a8.tflite
  qnn_lib: /usr/lib/libQnnTFLiteDelegate.so     # from the QAIRT SDK / vendor image
  require_accelerator: true                      # refuse to start on CPU during a demo
```
Under the hood this is `load_delegate("libQnnTFLiteDelegate.so", {"backend_type": "htp"})`.

**B. ONNX Runtime + QNN execution provider**
```yaml
detector:
  backend: ort_qnn
  model: ../models/yolo11n_int8.onnx             # QDQ model; the HTP needs quantized ops
  qnn_lib: /usr/lib/libQnnHtp.so
```
This needs an `onnxruntime` build with the QNN EP (`onnxruntime-qnn` wheel, or built against QAIRT).

**Verify the NPU is really used.** On start the detector logs and reports `accelerator`, which is either
`qnn-htp (...)` or `cpu (fallback: <reason>)`. Also compare latency with the delegate on and off using
`tools/bench_pi.py` (it runs on any Linux box):
```
python tools/bench_pi.py --models litert:../models/yolo11n_aihub_w8a8.tflite litert_qnn:../models/yolo11n_aihub_w8a8.tflite
```
A real NPU run is several times faster and draws less power. If the two times are equal, the delegate is not active.
The `bench_pi` power readout uses the Pi 5 PMIC; on QCS6490 use an inline USB-C power meter instead.

## 4. Zero-copy camera → NPU (production path, IM SDK)
Qualcomm's Intelligent Multimedia SDK runs the whole vision front end in GStreamer. Our Python keeps tracking,
analytics and fusion:
```
qtiqmmfsrc camera=0 ! video/x-raw,width=1280,height=720,framerate=10/1 ! \
  qtimlvconverter ! qtimltflite delegate=external external-delegate-path=libQnnTFLiteDelegate.so \
      external-delegate-options="QNNExternalDelegate,backend_type=htp;" model=yolo11n_aihub_w8a8.tflite ! \
  qtimlvdetection threshold=25.0 results=20 module=yolov8 labels=coco.labels ! \
  appsink    # -> StoreMind tracker via a small adapter (to write when we have the board)
```
Element names are from the RUBIK Pi 3 IM SDK samples (research/23 sources). Check them against the vendor image
you install; the `module=` for YOLO11 output may differ.

## 5. System
- **OS:** the vendor's Ubuntu / Qualcomm Linux image. The QAIRT runtime libraries must match the image.
- **Services:** one systemd unit per process, as on the Pi (Ram's `deploy/pi5/` units apply unchanged apart from paths).
- **Time:** chrony as on the Pi 5. The STM32 is synced with `$S`.

## 6. Gotchas (research/23 §5)
- **fastrpc / CDSP firmware** must be loaded, or the delegate falls back to CPU silently. Our `accelerator` field
  catches this.
- **QAIRT runtime and skel versions must match.** A mismatch gives error `0x80000600` when the delegate loads.
- **QCS6490 = HTP v68.** FP16 on the HTP is not supported there, so use the INT8 (w8a8) model. The FP32 model runs on
  CPU/GPU.
- Keep the model input at 640×640 NCHW, as compiled by AI Hub. The `litert` backend handles channels-first inputs.

## 7. Checklist for the first day with a board
- [ ] Flash the vendor image; confirm `/dev/fastrpc-cdsp` and the QAIRT libraries.
- [ ] `python -c "from ai_edge_litert.interpreter import load_delegate; load_delegate('libQnnTFLiteDelegate.so', {'backend_type':'htp'})"`
- [ ] Run `tools/bench_pi.py` with `litert` vs `litert_qnn` (NPU on/off), and paste the JSON into `eval/results/pi/`.
- [ ] Run the demo config with `require_accelerator: true`.
- [ ] Measure power with an inline meter; compute mJ/frame; update `docs/QUALCOMM.md`, moving it from bucket Q to S.
