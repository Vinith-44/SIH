# Prompt for Claude Code — paste everything below the line

---

You are the lead engineer for **StoreMind**, our Smart India Hackathon 2026 project (PS 26179, sponsor Qualcomm, category Hardware). We are through the college internal round. Competition is extremely strong. The work you do in this session goes straight into our demo and PPT, so it must be real, tested and honest.

Working folder: `C:\SIH` (this folder). First read `CLAUDE.md` (standing rules), then `research/00_START_HERE.md` and every file in `research/` (01–09 + RESEARCH_LOG). They contain the audit of our old code, the XLink comparison, the novelty plan, the architecture, hardware plan, models/data plan, protocols, roadmap and PPT guide. **Build what they describe.** Where you disagree with the research, write why in `research/10_ENGINEERING_NOTES.md` and do the better thing.

Test videos from the team will be in `C:\SIH\videos\` (`entrance\`, `queue\`, `shelf\`, `other\`), possibly with ground-truth CSVs next to them. Optional model files may be in `C:\SIH\models\`. If a folder is empty, generate test inputs yourself (a synthetic walking-dots video for logic tests; download a small public pedestrian/retail sample video only if its licence allows) and log that real videos are still needed.

## Work in this order. After EACH milestone: run tests, git commit, append to `WORK_LOG.md`, and update `HANDOFF_FOR_CLAUDE.md`.

### M0 — Setup (short)
- Extract the three extension-less ZIPs (`Queue Management`, `Shelf monitoring`, `Shopper analytics`) into `legacy/` (don't touch originals).
- Create `storemind/` git repo with the layout from `research/04_ARCHITECTURE.md` §8, a venv, `requirements.txt` (pin versions), `.gitignore` (venv, videos, snapshots, models, *.db).
- Inventory `videos/` and `models/` (resolution, FPS, duration) into `WORK_LOG.md`.

### M1 — Core vision pipeline (replay-first)
- `ingest`: threaded latest-frame reader for file / USB index / RTSP (TCP) / HTTP phone / `csi:N` (Picamera2, only imported on Pi); auto-reconnect; per-task FPS scheduler.
- `inference`: one `Detector` interface; backends: `ultralytics` (YOLO11n or YOLO26n, letterboxed, person class), LiteRT TFLite (so the legacy EfficientDet and Qualcomm Person-Foot-Detection TFLite can plug in if present in `models/`), ONNXRuntime.
- `tracking`: ByteTrack via `supervision`.
- `footfall`: foot-point line counting with a hysteresis band + per-track cooldown; entries/exits/occupancy.
- `zones`: dwell per zone with 2 s gap tolerance; promo dwell events.
- `heatmap`: floor-plan heatmap via 4-point homography (fallback: image-space), accumulated per minute, FPS-independent.
- CLI: `python -m storemind.run --config configs/demo.yaml --source videos/entrance/x.mp4 [--show|--headless]`. Overlay video preview only with `--show`.

### M2 — Queue intelligence + forecast (our #1 novelty)
- Per-counter lane polygon + billing polygon; wait timer with gap tolerance; service starts after ≥3 s in billing polygon; service time; median wait; μ per counter; λ (arrivals to checkout).
- Smoothed queue length (no more "78 predicted from 1").
- `forecast.py`: Erlang-C (M/M/c) counter recommendation + entrance→checkout lag via cross-correlation (see code in `research/03_NOVELTY_AND_FEATURES.md` N1) + short-horizon λ̂ from entrance counts. Emits `FORECAST` events: recommended counters, ETA, predicted wait.
- pytest for timers, Erlang-C (known values), lag estimator (synthetic).

### M3 — Evaluation harness (so every slide number is real)
- Ground-truth CSV templates in `videos/GROUND_TRUTH_TEMPLATES/` (entries/exits with timestamps; queue length every 10 s; waits for N customers; shelf slot states).
- `storemind/eval/`: scripts for count accuracy, occupancy MAE, queue-length MAE, wait-time MAE, forecast lead time, shelf P/R/F1, and a `benchmark.py` for FPS/latency/CPU per backend and input size.
- Run on every video that exists; write `storemind/eval/results/RESULTS.md` (table + exact commands + machine specs). Compare old legacy pipeline vs new pipeline on the same video — that before/after table is gold for the PPT.
- Build a tiny labelling helper if needed (OpenCV window: press keys to mark entry/exit or type queue counts while the video plays) so the team can make ground truth fast.

### M4 — Calibration tool
- `tools/calibrate.py`: open a snapshot or first frame, click to draw counting line, zones, queue lanes, billing spots, shelf slots (+ type SKU name & price), floor-plan points → writes `configs/*.yaml`. Also saves the reference frame for camera-tamper detection.

### M5 — Storage, API, dashboard, alerts
- SQLite (WAL): `events`, `agg_minute`, `config`, `sync_outbox` (schema in 04_ARCHITECTURE §5); hourly/daily rollups; daily + weekly report (CSV + a simple PDF or HTML).
- FastAPI: REST + WebSocket live feed. Dashboard: one offline web page (no CDN — vendor any JS lib locally), mobile-friendly: live KPI tiles (footfall, occupancy, queue per counter, forecast, shelf status grid), alerts list with acknowledge, footfall-by-hour chart, zone dwell, heatmap image, health (FPS, CPU temp, camera OK/tamper), and a **"Video stored: 0 bytes"** privacy counter.
- Alert manager: dedupe, cooldown, escalation; outputs: dashboard, console, sound. Voice: English offline TTS (pyttsx3) + a `voice_clips/` folder with slots for pre-recorded Telugu/Hindi clips (list the exact sentences we must record).
- Health service + camera tamper/moved detection vs reference frame.

### M6 — Shelf engine (novelty #2)
- Slot state machine: occlusion gate (skip frames where a person overlaps the shelf) → per-slot fill estimate → K-of-N voting → `SLOT_STATE` events (FULL/LOW/EMPTY/WRONG_ITEM).
- Fill estimate v1 (works today, no training): compare slot vs "restocked" reference crop (SSIM/edge density/colour histogram) + legacy void model as a hint. v2: product/gap detector trained on SKU-110K + gap datasets — write the Kaggle training notebook `storemind/train/shelf_detector_kaggle.ipynb` (don't train locally), with proper val/test split by scene and mAP/P/R reporting, INT8 TFLite + ONNX export.
- Planogram check: embedding similarity (small CNN feature extractor) of slot vs reference → WRONG_ITEM.
- "Restocked" action from dashboard button (and later STM32 button) captures new references.

### M7 — Sensor fusion + STM32 bridge
- `sensor_bridge.py`: serial reader for the NMEA-style line protocol in `research/05_HARDWARE_PLAN.md` §2 (`$W,…*CS`, `$T`, `$B`, `$R`, `$H`), checksum validation, commands `@L`, `@Z` for tower light/buzzer.
- `tools/stm32_simulator.py`: fake sensor node (virtual serial or TCP) so everything is demoable without hardware.
- Fusion rules: confirmed OOS, hidden depletion (camera FULL + weight dropping), pickup (ToF + weight), shrink flag, `LOST_SALE_RISK` with ₹ (dwell at EMPTY/LOW slot × price).
- Write the STM32 firmware skeleton in `storemind/firmware/stm32/` (STM32CubeIDE/HAL + FreeRTOS tasks: HX711, VL53L0X, IR EXTI, button, actuators, heartbeat, IWDG) matching the protocol — clearly marked as untested until flashed.

### M8 — Qualcomm path
- `tools/qai_hub_profile.py`: export our person/shelf models to ONNX, compile + profile on a QCS6490 device via `qai_hub` (Qualcomm AI Hub is free; token via `qai-hub configure`). If no token is configured, finish the script, log "needs team: Qualcomm ID + API token" and continue.
- Benchmark Qualcomm Person-Foot-Detection (if the TFLite is in `models/`) vs YOLO on our videos (accuracy + CPU speed).

### M9 — Close the research gaps (use web search; cite every source)
- `research/11_PRIOR_ART.md`: search Google Scholar / arXiv / IEEE / Google Patents for our novelty claims — arrival-driven queue forecasting from entrance counts, Erlang-C checkout staffing with CV, reference-based/few-shot planogram compliance, camera+load-cell shelf fusion, lost-sales from dwell. For each: closest prior work, what's different about ours, and the safe wording for the PPT ("novel for low-cost offline kirana deployment" vs "first ever"). Be honest if it already exists.
- `research/12_MARKET_AND_BUSINESS.md`: Indian retail size, number of kirana/small stores, cost of stock-outs and queue abandonment (credible sources), CCTV penetration in Indian shops, competitor prices (cloud video analytics, shelf-audit SaaS), our cost per tier, business model (one-time kit + optional subscription), TAM/SAM/SOM with stated assumptions.
- `research/13_FIELD_VALIDATION_KIT.md`: 10-question interview script (English + Telugu/Hindi hints) for 3–5 shop owners, a one-page observation sheet, consent/notice text for recording, and how to turn answers into one PPT slide.
- `research/14_JUDGING_CHECKLIST.md`: typical SIH evaluation criteria (novelty, feasibility, technical depth, impact, scalability, presentation, hardware integration) mapped to our evidence; list gaps.

### M10 — PPT assets (don't edit `sihfinal.pptx`)
- `ppt_assets/`: dashboard screenshots from replay runs, charts from `RESULTS.md`, before/after table, forecast timeline plot, updated architecture diagram if the build changed it.
- `research/15_PPT_CONTENT_READY.md`: final text per slide (SIH 6-slide template) using only measured numbers + cited facts.

### M11 — Raspberry Pi 5 deployment
- `deploy/pi/setup_pi.sh`, systemd units, `README_PI.md` (camera tests, NCNN export, headless run, autostart, UART setup for STM32, RTC battery note).

## Definition of done for this session
- `python -m storemind.run` works end-to-end on at least one video in replay, events land in SQLite, dashboard shows them live.
- Tests pass. `RESULTS.md` exists with real numbers (or clearly "not measured yet").
- `HANDOFF_FOR_CLAUDE.md` in `C:\SIH` contains: what was built (by milestone), how to run it (copy-paste commands), measured results, known bugs, decisions made, what's blocked and needs the team (videos, ground truth, Qualcomm token, hardware), and the top 5 next steps. Keep it under ~2 pages — it will be pasted into another Claude chat.

If you run low on time or context, stop at a clean milestone, commit, and write the handoff. Quality over quantity: a smaller system that really works and is measured beats a big one that doesn't. Start with M0 now.
