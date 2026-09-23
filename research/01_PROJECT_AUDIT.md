# 01 — Audit of what we have today

> Honest review of the code, models, notebooks, PPT and the FreeRTOS guide in `C:\SIH` (as of 23 Sep 2026).
> Goal: know exactly what's broken before the judges find it.

## 1. What's in the folder

| Item | What it actually is |
|---|---|
| `Shopper analytics` (zip) | `live_shopper_analytics.py`, config, `1.tflite`, Kaggle notebook |
| `Queue Management` (zip) | `live_queue_intelligence.py`, config, `1.tflite`, sample logs |
| `Shelf monitoring` (zip) | `live_shelf_void_webcam_grid.py`, `shelf_void_grid_int8.tflite`, Kaggle notebook |
| `1.tflite` (both folders) | **Identical file** (same MD5). Stock **EfficientDet-Lite0 COCO**, input 320×320 uint8, max 25 detections. Not trained by us. |
| `shelf_void_grid_int8.tflite` | Our own 388K-param CNN trained **from scratch**, 10×10 grid output |
| `sihfinal.pptx` | 6-slide SIH template deck |
| `StoreMind_Advanced_BluePill_FreeRTOS_Guide.pdf` | Good design doc for the STM32 sensor node (HX711, ToF, IR, FreeRTOS) |
| `the solution.txt` | Just a copy of the "Expected Solution" list from the PS, no solution yet |
| STM32 / HX711 firmware | **Not in the folder** (the PPT screenshot shows HX711 "LIVE – REAL HARDWARE", so it exists somewhere — add it) |

## 2. Big-picture problems (fix these first)

1. **Three separate demo scripts, no system.** No shared event bus, no database, no dashboard, no link between modules. The PS asks for a *platform*.
2. **No accuracy numbers anywhere.** The shelf notebook never computes precision/recall/mAP. The shopper notebook's only result is `IN=0, OUT=0` on our own test video — i.e. counting failed.
3. **The "Results" slide is a DEMO.** The dashboard screenshot literally says *"DEMO · No hardware telemetry and no pipeline artifacts found. Showing the demo dataset…"*, ToF is "REPLAY – SIMULATED", vision is "LIVE / DEMO". The heatmap image looks generated/mocked. Judges who zoom in will notice.
4. **PPT claims that the code doesn't do:**
   - "blurring every customer the millisecond they are detected" → no blur anywhere in code.
   - "STM32 handles … CV handling … along with the ML stack inference" → impossible on a Blue Pill (72 MHz, 20 KB RAM) and it contradicts our own FreeRTOS guide ("Do not attempt to run the computer-vision pipeline … on the Blue Pill"). A hardware judge will catch this immediately.
5. **Windows-laptop code.** `cv2.CAP_DSHOW`, `winsound`, `py` launcher, `cv2.imshow` window → will not run headless on the Raspberry Pi as-is.

## 3. Shopper analytics — bugs & weak points

| # | Problem | Where | Why it hurts | Fix |
|---|---|---|---|---|
| S1 | Frame is **squashed** 1920×1080 → 320×320 (no letterbox) | `PersonDetector.detect` | People get distorted & tiny → missed detections | Letterbox, or use the camera's low-res sub-stream, or a 640-input model |
| S2 | Greedy nearest-centroid tracker with a fixed **100 px** radius | `CentroidTracker` | Same threshold for 480p and 1080p; no motion model; greedy order → ID switches → double/missed counts | ByteTrack (MIT) via `supervision` |
| S3 | Line crossing uses the **box centre**, no hysteresis band | `point_side` loop | Centre jitters across the line; half-visible people have wrong centres | Use the **foot point** (bottom-centre) + a dead-band either side of the line |
| S4 | A new track has `last_side=None` | tracker | If the ID switches mid-crossing, the crossing is lost | Count on track-level trajectory (first side vs last side) |
| S5 | Heatmap in **image pixels**, decays 0.998 **per frame** | main loop | Heat depends on FPS; can't merge cameras; not a floor plan | Homography → floor-plan grid; accumulate per minute |
| S6 | Dwell timer resets when a track is lost for a few frames | promo logic | Dwell is under-counted | Keep a gap tolerance (e.g. 2 s) before closing a visit |
| S7 | Event log is **wiped on every start** (`write_text("")`) | `main()` | Lose all history → no daily/weekly reports | Append to SQLite |
| S8 | Zones are placeholder numbers (`0.08, 0.55 …`) | config | Not calibrated to any real camera | Calibration tool: click points on a snapshot |
| S9 | Capture + inference in one thread | `main()` | On RTSP, OpenCV buffers frames → latency grows to seconds | Grab thread that keeps only the latest frame |
| S10 | `tf.lite.Interpreter` is deprecated | imports | Warning already printed in notebook | `ai-edge-litert` package |

## 4. Queue intelligence — bugs & weak points

| # | Problem | Evidence | Fix |
|---|---|---|---|
| Q1 | **Queue zone covers ~84% × 85% of the frame** and overlaps the service zone | config `queue_zone` | Anyone anywhere = "in queue". Draw a real lane polygon per counter |
| Q2 | **Prediction = slope between first and last point × 30 s.** At start-up the window is < 1 s, so one person appearing → slope explodes | `queue_events.jsonl`: `queue_count: 1, predicted_30s: 78.4, action: OPEN_ADDITIONAL_COUNTER` — a false alarm | Minimum window, smoothing (EMA/median), and better: arrival-rate model (see `03_NOVELTY`) |
| Q3 | Service events from ID flicker | log: `service_seconds: 0.4`, `average_completed_wait_seconds: 0.8` | Minimum dwell (e.g. 3 s) before "service started"; gap tolerance |
| Q4 | Only one counter, no λ (arrival rate) / μ (service rate) | whole design | PS asks "recommend opening additional counters" → need per-counter model |
| Q5 | Alert = laptop beep | `beep()` | Real staff actuation: tower light / voice / phone |
| Q6 | Wait time = time since track first seen in zone; resets on ID loss | tracker | Gap-tolerant timers; report median not mean |

## 5. Shelf monitoring — bugs & weak points

| # | Problem | Evidence | Fix |
|---|---|---|---|
| H1 | **Model badly over-fits.** Trained from scratch on 431 images, no pretrained backbone, no augmentation | Train loss 1.37 → 0.09, val loss best 1.19 at epoch 10 then climbs to **2.77** by epoch 18 | Transfer learning (pretrained detector), augmentation, more data (SKU-110K etc.) |
| H2 | **No accuracy metric at all** | notebook has no mAP / P / R cell | Report mAP50, precision, recall on a held-out test set |
| H3 | Only 1 box per 10×10 grid cell, no NMS | `target_for_image`: "A cell with multiple voids retains the larger one" | Use a standard detector head |
| H4 | "Void %" of the whole ROI → FULL / LOW / EMPTY | `shelf_status` | Can't say **which product** is out; no planogram compliance at all (a PS requirement) |
| H5 | No occlusion handling | — | A shopper standing in front = random alerts. Gate with the person detector |
| H6 | No temporal smoothing; alerts every 10 s | `last_beep` | State machine with K-of-N voting |
| H7 | Wide shelf squashed to 320×320 | `detect` | Tile the shelf into slots |
| H8 | `instructions.txt` mentions `shelf_void_efficientdet_lite0.tflite` + `live_shelf_void_webcam.py` which don't exist | instructions | Update docs |
| H9 | 1.5 GB `gapDetectionDatasets` cloned and zipped but never used | notebook cells 1–3 | Use it (it's more data!) |

## 6. PPT review (slide by slide)

| Slide | Issue |
|---|---|
| 2 | "Innovation" = only privacy/offline. Every team will say this. No real novelty stated. |
| 3 | Text overlaps the "Process flow" heading; diagram is a generic image, not **our** architecture; claims "blurring" |
| 4 | Claims STM32 does CV/ML inference (wrong). Challenges listed without numbers |
| 5 | "audience:" text overlaps images; Results = DEMO dashboard + mock heatmaps |
| 6 | First reference ("Lecture on BareMetal on the Edge: Greginard Shimposwki, Linux Open Source Summit 2026") **could not be verified** — remove it. The queue paper (Deshmukh et al.) is from **IJSREM**, not "Journal of Real-Time Image Processing" — fix the venue. The other 3 papers exist. |

## 7. What is genuinely good (keep it)

- Privacy intent is right: only anonymous JSON events, no frames saved.
- INT8 quantisation awareness, TFLite on CPU — correct edge mindset.
- Config-file-driven zones (just need real calibration).
- The **FreeRTOS / STM32 sensor-node guide is strong** — clear task split, "a sensor must remove an ambiguity" rule, honest scope. This is our hardware depth; build on it.
- HX711 load cell already reads real hardware.
- The SENSE → UNDERSTAND → PREDICT → DECIDE → ACT → MEASURE → LEARN loop idea on the dashboard is a good story — it just needs real data behind it.
