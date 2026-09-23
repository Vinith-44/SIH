# 06 — AI models, the "no data" problem, and how to prove accuracy

## 1. Key insight: we were training the wrong thing

- **Detecting people is a solved problem.** Pretrained COCO / CrowdHuman detectors are good. Our shopper/queue failures came from *how* we used the model (squashed 320×320 input, weak tracker, box-centre counting, uncalibrated zones) — not from lack of training data.
- **What we lack is *evaluation* data** (to prove accuracy) and **shelf** data. Fix those two.

## 2. Models per module

| Module | Model (hackathon) | Why | Alternatives |
|---|---|---|---|
| Person detection | **YOLO26n / YOLO11n** exported to **NCNN** (Pi CPU) or LiteRT INT8; 416–640 input with letterbox | Fast, strong on people; NCNN is the fastest CPU format on Pi per Ultralytics' benchmarks | **Qualcomm Person-Foot-Detection** (AI Hub, BSD-3, 640×480, detects people + feet) · EfficientDet-Lite0 (what we have — weaker on small people) |
| Tracking | **ByteTrack** (via `supervision`, MIT) | No appearance model → cheap + privacy-friendly | OC-SORT, BoT-SORT (skip ReID) |
| Zones / lines / heatmap | `supervision` `LineZone`, `PolygonZone`, heatmap utilities | Saves days of code, well tested | Our own code (keep ours for foot-point + hysteresis) |
| Shelf product/gap detector | YOLO-n fine-tuned on **SKU-110K** (class "product") + **gap/void** datasets → 2 classes: `product`, `gap` | Class-agnostic → works on unseen Indian brands; counts facings | RF-DETR / D-FINE (Apache-2.0) if we want a permissive licence |
| Planogram match | Small embedding model (MobileNetV3 / EfficientNet-Lite feature vector; DINOv2-small if compute allows) + cosine similarity vs reference crop | No per-SKU training | Colour-histogram baseline (surprisingly strong for packets) |
| Auto-labelling (training-time only) | **YOLOE** / Grounding DINO / YOLO-World (open-vocabulary) to pre-label shelf images; humans correct | 3–5× faster labelling | SAM for masks |
| Forecast | Erlang-C + lag cross-correlation (no ML needed); later a small gradient-boosting model on time-of-day history | Explainable, works from day 1 | Prophet / LightGBM when weeks of data exist |

### Licensing (mention in the PPT as product maturity)
Ultralytics YOLO (v8/11/26) is **AGPL-3.0**: fine for the hackathon/open source, but a closed commercial product needs an Ultralytics enterprise licence **or** a permissively licensed detector (YOLOX, RF-DETR, D-FINE, NanoDet, EfficientDet-Lite, Qualcomm AI Hub models with BSD/Apache licences). Keep the inference backend swappable (see `04_ARCHITECTURE.md`).

## 3. Datasets

| Purpose | Dataset | Size | Notes |
|---|---|---|---|
| Shelf products (dense) | **SKU-110K** (CVPR 2019) | 11,743 images (8,219 / 588 / 2,936), ~1.7 M boxes, 1 class | Ultralytics has a ready `SKU-110K.yaml`; research use — check licence terms |
| Indian shelves | **Grocer-Help** (Scientific Reports, 2026) | 13,771 images, 349 classes, 8 stores in 5+ Indian states | Partial release (Zenodo / IEEE DataPort); full set on request — **email the authors, cite it** |
| Shelf gaps / voids | Our Kaggle `retail-shelf-void-detection` (506 imgs) + `gapDetection` datasets (we already cloned 1.5 GB) + Roboflow Universe "empty shelf" sets | few hundred–few thousand | Merge into one `gap` class, dedupe near-duplicates |
| Indian grocery items | Roboflow Universe (e.g. "Indian Grocery Object Detection", IIT Patna "Grocery_Items") | varies | Check licence per dataset |
| People (validation only) | MOT17 / MOT20 (tracking), CrowdHuman (dense), Oxford Town Centre (CCTV angle) | — | Research/non-commercial licences; use for evaluation, not redistribution |
| **Our own videos (most important)** | College canteen queue, library/lab entrance, stationery shop / a friendly local kirana | 10–20 min per scene | With permission and a visible notice; store only for evaluation, delete after |

## 4. Building our own shelf dataset in 3 days (the "no data" fix)

1. **Build a demo shelf** (a small rack with real products: biscuits, Maggi, soaps, a rice/dal box on a load cell). This is also our live-demo prop.
2. Shoot 300–500 photos with phones: different times, lights on/off, partial stock, empty slots, wrong product in slot, a hand in front. Add 100–200 photos from a real store (with permission).
3. **Pre-label automatically** with the SKU-110K-trained model (products) + YOLOE text prompt "empty shelf space" (gaps) → correct in CVAT / Label Studio / Roboflow.
4. Augment: brightness/contrast, motion blur, perspective, **synthetic occlusion** (paste person silhouettes), mosaic.
5. Split by **shelf/scene**, not by random image (otherwise near-duplicate photos leak into test and inflate accuracy).

## 5. Training recipe (shelf detector)

```bash
# Kaggle / Colab GPU
pip install ultralytics
yolo detect train data=SKU-110K.yaml model=yolo11n.pt imgsz=640 epochs=50 batch=16      # stage 1: product
yolo detect train data=shelf_ours.yaml model=runs/detect/train/weights/best.pt \
     imgsz=640 epochs=80 patience=20 close_mosaic=10                                       # stage 2: product+gap
yolo detect val   model=runs/detect/train2/weights/best.pt data=shelf_ours.yaml split=test
yolo export model=runs/detect/train2/weights/best.pt format=ncnn   # Pi CPU
yolo export model=runs/detect/train2/weights/best.pt format=tflite int8=True data=shelf_ours.yaml
```
Then: compile + profile the ONNX/TFLite on a QCS6490 device in **Qualcomm AI Hub** and record the latency.

Qualcomm AI Hub sketch (sign in with a Qualcomm ID, `pip install qai-hub`, `qai-hub configure --api_token …`):
```python
import qai_hub as hub
compile_job = hub.submit_compile_job(model="best.onnx",
        device=hub.Device("<pick a QCS6490 / RB3 Gen 2 device from hub.get_devices()>"),
        options="--target_runtime tflite")
profile_job = hub.submit_profile_job(model=compile_job.get_target_model(), device=compile_job.device)
```

## 6. Evaluation plan (this is what wins the "feasibility" score)

Record → manually annotate a small ground truth → run our pipeline in **replay mode** → compare.

| Module | Ground truth (how) | Metric | Target |
|---|---|---|---|
| Entry/exit | Manual tally while watching the video (or IR break-beam) | Count accuracy = 1 − \|pred − gt\| / gt | ≥ 90% |
| Occupancy | Entries − exits vs manual snapshot | MAE (people) | ≤ 1–2 |
| Queue length | Manual count every 10 s | MAE (people) | ≤ 1 |
| Wait time | Stopwatch for 20 customers | MAE (s), median error | ≤ 20% |
| Forecast | Compare "open counter" recommendations vs when the queue actually exceeded the threshold | Lead time (min), precision/recall of alerts | ≥ 3 min lead |
| Shelf state | Labelled slot states on test photos/video | Precision / recall / F1 for EMPTY and LOW | F1 ≥ 0.85 |
| Detector | Test split | mAP50, mAP50-95 | report, don't hide |
| System | On the Pi 5 | FPS per camera, end-to-end alert latency, CPU temp, RAM | live on dashboard |

Always show **our measured numbers**, even if modest. "92% entry-count accuracy on 214 real crossings in our college canteen" beats any unmeasured claim.
