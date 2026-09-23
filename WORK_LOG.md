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

---

## 2026-09-24 — Session 3 (Claude Code): M3 finished, CAVIAR (bucket A), M4, M5

### Repository note
The git repo had moved to `C:\SIH` and was tracking the 16 CAVIAR clips, `vtest.avi`,
the synthetic clips and four model binaries (~200 MB). CLAUDE.md and
`videos/README.md` both forbid committing video, so these were untracked with
`git rm --cached` (nothing deleted from disk) and `.gitignore` extended. The blobs
remain in the history of the initial commit; removing them needs a history rewrite,
which is the team's call before this is pushed anywhere.

### M3 — finished
- `eval/legacy_baseline.py` re-implements the legacy algorithm parameter for
  parameter from `shopper_analytics_config.json`. Two deviations, both favouring
  the legacy: it gets the same calibrated counting line as the new pipeline, and
  its cooldown uses video time.
- `eval/run_all.py` → `eval/results/RESULTS.md`, every row labelled with its data
  bucket, machine specs, and the exact command per section.

### CAVIAR — the first real accuracy numbers (bucket A)
16 clips (8 scenarios × 2 views), 15.9 min, 111 annotated people, real YOLO11n at
8 FPS / 640 input. Ground truth derived from the published CVML trajectories.

| metric | StoreMind | legacy | target |
|---|---|---|---|
| entries counted (truth 30) | **33** | 95 | — |
| exits counted (truth 21) | **24** | 75 | — |
| entry count accuracy | **90.0%** | 0.0% | ≥ 90% |
| exit count accuracy | **85.7%** | — | ≥ 90% (**missed**) |
| entry event P / R / F1 | **0.85 / 0.93 / 0.89** | F1 0.16 | — |
| exit event P / R / F1 | 0.71 / 0.81 / 0.76 | — | — |
| mean crossing timing error | **0.33 s** | — | — |
| people-in-frame MAE | **0.65 people** | — | ≤ 1–2 |
| detection match rate (IoU ≥ 0.4) | 74.4% | — | — |
| ID switches | **63** over 13,721 boxes | 1,398 track IDs for 111 people | — |

Command: `python -m storemind.eval.eval_caviar --json data/caviar_full.json`

**We over-count in both directions and miss the exit target.** Event precision
(0.85 entry, 0.71 exit) shows the cause is extra crossings, not missed ones —
consistent with people loitering near a mall-corridor line. This is stated in
RESULTS.md rather than left for a reader to notice.

Counting lines were placed on evidence, not guesswork: ground-truth trajectories
were overlaid on a busy frame (`tools/caviar_preview.py`) and the crossing count
checked for stability against the annotation-jitter margin. Corridor y = 0.75
gives 33 GT crossings, unchanged at 4 px and 8 px margins; front y = 0.45 across
the shop doorway gives 18, and those can be cross-checked against CAVIAR's own
activity labels (10 "shop enter", 7 "shop exit" episodes). Both recorded in
`configs/caviar.yaml`.

Detector input size was chosen on measured recall against the annotation, not by
guessing: imgsz 320 → 0.728, 448 → 0.756, **640 → 0.814**, with negligible false
positives at every setting.

### Two measurement bugs found and fixed
1. **Detection match rate counted frames the pipeline never processed.** The
   entrance camera runs at 8 FPS on 25 FPS footage, so two frames in three were
   scored as misses: 18.6% reported versus **74.4%** actual. Accuracy figures
   were unaffected. The field is now `gt_boxes_in_processed_frames`.
2. **Speed numbers are not comparable across runs.** The same benchmark command
   on the same clip measured 27.5 ms and 159 ms per frame on different days —
   laptop thermal/power state, a ~5× swing. RESULTS.md now says so and all speed
   rows are generated in one run.

### Detector speed (bucket S — no ground truth, speed only)
`vtest.avi`, 768×576, 100 frames, quiet machine:

| backend | input | ms/frame | FPS | det/frame |
|---|---|---|---|---|
| ultralytics yolo11n.pt | 320 | 27.9 | 35.4 | 4.34 |
| ultralytics yolo11n.pt | 416 | 44.0 | 22.6 | 4.44 |
| ultralytics yolo11n.pt | 640 | 101.1 | 9.9 | 4.73 |
| ultralytics yolo26n.pt | 640 | 92.1 | 10.8 | 4.73 |
| litert efficientdet_lite0 (legacy) | 320 | 19.3 | 50.9 | 4.20 |

### M5 — API and dashboard
FastAPI REST + WebSocket, one offline HTML/CSS/JS page, no CDN, inline SVG charts,
polling fallback. Verified live: acknowledge persists; "Mark restocked" captured
4 slot references and flipped slot A1 from EMPTY to FULL. Endpoints all return
200 (`/`, `/api/state`, `/api/events`, `/api/series`, `/api/health`,
`/api/heatmap.png`, static assets).

### M4 — calibration tool
`tools/calibrate.py`: click the counting line, zones, lanes, billing spots, shelf
slots (with SKU and price) and the floor-plan quad; writes YAML that validates
against the config schema and merges into an existing file. Covered by tests.

### PPT assets (`C:\SIH\ppt_assets`)
`caviar_before_after.png`, `caviar_tracking.png`, `forecast_timeline.png` (all
generated from evaluation JSON only, palette validated with the dataviz
validator), plus `vtest_overlay.mp4` and `entrance_overlay.mp4` with stills.

### Prior-art correction (research/22)
`research/22_GEMINI_RESEARCH_REVIEW.md` establishes that door-to-counter
predictive staffing is **not** novel — Irisys patent US7778855B2 (2010) and
products from Irisys and Xovis. All "novelty N1" wording in the code, RESULTS.md
and the forecast chart was rewritten: the contribution is existing CCTV, offline
operation, ~₹15–25k, and a lag learned automatically rather than configured.

### Tests
`python -m pytest tests/ -q` → **124 passed**.
