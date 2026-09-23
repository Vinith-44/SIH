# WORK_LOG

Append-only. Dated entries. Every number here came from a command recorded beside it.

---

## 2026-09-23 — Session 2 (Claude Code): M0 → M3 (partial)

### M0 — Setup
- Extracted the three extension-less ZIPs into `legacy/` (originals untouched).
- Created the `storemind/` git repo, venv at `storemind/.venv` (Python 3.11),
  pinned `requirements.txt`, `.gitignore` (venv, videos, snapshots, models, `*.db`).
- Copied the two legacy TFLite models into `models/` and downloaded YOLO11n + YOLO26n.

**Inventory** (`python tools/inventory.py`):

| folder | file | resolution | fps | duration | ground truth |
|---|---|---|---|---|---|
| entrance | synthetic_entrance.mp4 | 960x540 | 25 | 120 s | generated |
| queue | synthetic_queue.mp4 | 960x540 | 25 | 180 s | generated |
| shelf | synthetic_shelf.mp4 | 960x540 | 25 | 150 s | generated |
| rush | synthetic_rush_entrance.mp4 | 640x360 | 8 | 1500 s | generated |
| rush | synthetic_rush_counter.mp4 | 640x360 | 8 | 1500 s | generated |
| other | vtest.avi | 768x576 | 10 | 79.5 s | **none — needs hand-labelling** |

| model | size | note |
|---|---|---|
| yolo11n.pt | 5.61 MB | downloaded |
| yolo26n.pt | 5.54 MB | downloaded |
| efficientdet_lite0_coco_legacy.tflite | 4.56 MB | the legacy `1.tflite`, 320x320 uint8 |
| shelf_void_grid_int8_legacy.tflite | 0.43 MB | the legacy grid model |

**`videos/` contained no real footage.** Generated synthetic clips with exact
ground truth (`tools/make_synthetic_video.py`) and downloaded `vtest.avi`
(OpenCV samples, Apache-2.0) as the only real pedestrian footage.
**Real store/canteen video is still blocking real accuracy numbers.**

### M1 — Core vision pipeline
Built `storemind/` per `research/04_ARCHITECTURE.md`: pydantic event schema,
in-process bus with the MQTT interface, replay-safe clock, threaded ingest
(file/USB/RTSP-TCP/HTTP/CSI) with per-task FPS scheduling, one `Detector`
interface over ultralytics / LiteRT / ONNX / scripted, ByteTrack, foot-point
line counting with hysteresis, gap-tolerant zone dwell, floor-plan heatmap in
person-seconds, SQLite (WAL) + rollups, alert manager, health + tamper.

### M2 — Queue + forecast
Per-counter lane/billing polygons, gap-tolerant wait timers, 3 s minimum before
service starts, median statistics, Erlang-C staffing recommendation, lag by
cross-correlation.

### M3 — Evaluation harness (partial)
`storemind/eval/` + `tools/label_ground_truth.py` + ground-truth templates.

### Measured results

All on this laptop (Intel 10-core / 16 threads, 16.9 GB, Windows 11, Python
3.11.8, OpenCV 5.0.0, commit `5ce7e4e`).

**Analytics logic, synthetic clips with perfect detections** (`--backend scripted`):

| metric | result | target |
|---|---|---|
| entry count accuracy | **100%** (14/14) | >= 90% |
| exit count accuracy | **100%** (6/6) | >= 90% |
| entry/exit event P/R/F1 | **1.00 / 1.00 / 1.00** | — |
| mean crossing timing error | **0.15 s** | — |
| occupancy MAE | **0.08 people** | <= 1–2 |
| queue length MAE | **0.00 people** (18 samples) | <= 1 |
| wait-time MAE | **0.39 s (1.1%)** | <= 20% |
| service-time MAE | **0.14 s (0.6%)** | — |
| service event P/R/F1 | **1.00 / 1.00 / 1.00** | — |
| shelf state accuracy | **95.8%** (48 samples) | — |
| shelf EMPTY P/R/F1 | **1.00 / 1.00 / 1.00**, delay 5.0 s | F1 >= 0.85 |

Commands:
```
python -m storemind.eval.eval_counting --config configs/demo.yaml --camera entrance \
  --source ../videos/entrance/synthetic_entrance.mp4 --backend scripted \
  --model ../videos/entrance/synthetic_entrance_detections.json --fps 25
python -m storemind.eval.eval_queue --config configs/demo.yaml --camera counter-1 \
  --source ../videos/queue/synthetic_queue.mp4 --backend scripted \
  --model ../videos/queue/synthetic_queue_detections.json --fps 25
python -m storemind.eval.eval_shelf --config configs/demo.yaml --camera shelf-a \
  --source ../videos/shelf/synthetic_shelf.mp4 --backend scripted \
  --model ../videos/shelf/synthetic_shelf_detections.json
```

**Door-to-counter forecast (novelty N1)** —
`python -m storemind.eval.eval_forecast --config configs/rush.yaml --backend scripted`

| metric | result |
|---|---|
| congestion onset (ground truth) | 1000 s |
| first "open another counter" warning | 480 s |
| **lead time** | **8.7 min** |
| warnings / false alarms | 8 / 0 |
| estimated lag vs true lag | **6 min / 6 min** |

**Detector benchmark**, real clip `vtest.avi` 768x576, 100 frames —
`python -m storemind.eval.benchmark --source ../videos/other/vtest.avi --frames 100`

| backend | model | imgsz | infer ms | FPS | det/frame |
|---|---|---|---|---|---|
| ultralytics | yolo11n.pt | 320 | 27.49 | 35.9 | 4.34 |
| ultralytics | yolo11n.pt | 416 | 42.78 | 23.2 | 4.44 |
| ultralytics | yolo11n.pt | 640 | 97.64 | 10.2 | 4.73 |
| ultralytics | yolo26n.pt | 640 | 88.98 | 11.2 | 4.73 |
| litert | efficientdet_lite0_coco_legacy.tflite | 320 | 23.59 | 41.7 | 4.20 |

These are **laptop** numbers. Pi 5 numbers must come from running the same
script on the Pi.

### Bugs the harness found and fixed
1. `YOLO.predict()` rebuilds a predictor every call: 105.7 ms -> **27.5 ms**
   per frame at 320 for identical detections (>=95% of boxes match at IoU 0.85).
2. The forecaster had no minimum history and recommended extra tills at t=0 from
   one minute of door counts — the same class of bug as the legacy
   "78 predicted from a queue of 1". Added a warm-up window.
3. The lag estimator was fed completed services instead of lane joins; during a
   rush the served population is exactly the one the queue holds back, which
   halved the estimated lag (3 min vs true 6 min).
4. Shelf slots stayed `UNKNOWN` until their first state *change*: state accuracy
   35.4% -> **95.8%** after announcing state on restock.
5. `trackers.ByteTrackTracker` marks tentative detections with `tracker_id = -1`
   and reuses that id; letting them through turned a 7-person queue into 14
   arrivals.

### Tests
`python -m pytest tests/ -q` -> **112 passed**.

### Commits
- `19ded61` M0–M2: core pipeline, queue intelligence and Erlang-C forecast
- `5ce7e4e` M3 (partial): evaluation harness, ground-truth tooling, detector fast path
