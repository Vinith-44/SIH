# StoreMind — handoff (2026-09-23)

SIH 2026 · PS 26179 · Qualcomm · Team TechGladiators. Read `CLAUDE.md` and
`research/` first. This file is the current state; `WORK_LOG.md` has the numbers.

## What exists

`C:\SIH\storemind` — one git repo, venv at `storemind/.venv` (Python 3.11),
112 passing tests, 2 commits. The three legacy scripts are replaced by one
event-driven pipeline.

| Milestone | State |
|---|---|
| M0 setup, ZIPs extracted to `legacy/`, inventory | **done** |
| M1 ingest / detector backends / ByteTrack / footfall / zones / heatmap | **done** |
| M2 queue engine + Erlang-C forecast | **done** |
| M3 evaluation harness | **most of it** — see "half-done" |
| M4 `tools/calibrate.py` | **not started** |
| M5 storage + alerts + health | **done**; FastAPI + dashboard **not started** |
| M6 shelf engine v1 (label-free) | **done**; Kaggle training notebook not started |
| M7 fusion rules | **done**; serial bridge, STM32 simulator, firmware **not started** |
| M8–M11 Qualcomm, research 11–15, PPT assets, Pi deploy | **not started** |

## How to run it

```powershell
cd C:\SIH\storemind
.\.venv\Scripts\python -m pytest tests\ -q                       # 112 pass

# full three-camera replay into SQLite
.\.venv\Scripts\python -m storemind.run --config configs\demo.yaml --db data\demo.db

# the same, with a blurred preview window
.\.venv\Scripts\python -m storemind.run --config configs\demo.yaml --camera entrance --show

# the door-to-counter forecast demo (the novelty slide)
.\.venv\Scripts\python -m storemind.eval.eval_forecast --config configs\rush.yaml --backend scripted

# detector speed on real footage
.\.venv\Scripts\python -m storemind.eval.benchmark --source ..\videos\other\vtest.avi --frames 100
```

Regenerate the test clips (they are gitignored): `python tools/make_synthetic_video.py --out ../videos --scene all` and `--scene rush`.

## Measured results (all commands in `WORK_LOG.md`)

Logic on synthetic clips with perfect detections: entry/exit **100%** (14 in /
6 out), occupancy MAE **0.08**, queue-length MAE **0.00**, wait MAE **0.39 s
(1.1%)**, service MAE **0.14 s**, shelf state accuracy **95.8%**, shelf EMPTY
F1 **1.00** at 5 s delay. Forecast: warned **8.7 min** before congestion, **0**
false alarms, lag estimated **6 min** vs true **6 min**. Detector on real
footage (laptop): YOLO11n@320 **27.5 ms/frame**, 35.9 FPS.

**Say this honestly on slides:** these grade the *analytics logic* with a
perfect detector, not end-to-end accuracy on real footage. Only the benchmark
numbers come from real video, and they measure speed, not accuracy.

## Half-done / known gaps

- **M3**: `legacy_baseline.py` (old-vs-new table for the PPT) and `run_all.py`
  (writes `storemind/eval/results/RESULTS.md`) are **not written**. The
  individual eval scripts work and print everything needed; `RESULTS.md` does
  not exist yet.
- No real-footage accuracy anywhere. `vtest.avi` has no ground truth.
- The shelf `detector` method (SKU-110K) is stubbed behind a config flag; only
  the reference/label-free path is implemented and tested.
- `FusionEngine.tick()` is a no-op drain; fusion rules only fire on events.

## Exact next step

1. Write `storemind/eval/legacy_baseline.py`: re-implement the legacy algorithm
   faithfully (EfficientDet `1.tflite` squashed to 320x320 with **no**
   letterbox, the greedy 100 px centroid tracker with `max_misses = 12`,
   **box-centre** line crossing with 2 s cooldown, parameters exactly from
   `legacy/Shopper_analytics/Shopper analytics/shopper_analytics_config.json`)
   and run it against the new pipeline on `videos/entrance/synthetic_entrance.mp4`.
   Everything needed was already read: the legacy `CentroidTracker`, `point_side`
   and the config values. That before/after table is the strongest PPT asset.
2. Then `storemind/eval/run_all.py` -> `storemind/eval/results/RESULTS.md`
   (table + exact commands + machine specs; `eval/common.machine_specs()` exists).
3. Then M4 `tools/calibrate.py`, then M5 FastAPI + offline dashboard.

## Decisions made (deviations from the research pack)

- Package layout is flattened: `storemind/analytics/…` rather than
  `storemind/edge/analytics/…`. Everything runs on the edge node, so the `edge/`
  level discriminated nothing.
- All analytics take a `Clock`. Replay uses the **video** timeline, never the
  wall clock — otherwise replaying at 27x would make every wait time wrong and
  every "measured" number fiction.
- Preferred `trackers.ByteTrackTracker` over `supervision.ByteTrack`, which is
  deprecated and removed in supervision 0.31.
- The dashboard will be vanilla JS with inline SVG, no vendored library — the
  strictest reading of the no-CDN rule.
- Synthetic clips grade logic only, and every report says so.

## Blocked — needs the team

1. **Real videos** (entrance, queue, shelf) with hand-counted ground truth.
   Use `tools/label_ground_truth.py` and
   `videos/GROUND_TRUTH_TEMPLATES/README.md`. Nothing else unblocks real
   accuracy numbers.
2. **Qualcomm ID + AI Hub API token** for M8 (`qai-hub configure`).
3. **Raspberry Pi 5** to re-run `eval/benchmark.py` — every FPS number we have
   is a laptop number.
4. **STM32 firmware** is not in `C:\SIH`; the PPT claims HX711 runs on real
   hardware. Please add the existing sketch/project.
5. Qualcomm **Person-Foot-Detection** TFLite in `models/` if we want the
   sponsor-aligned detector comparison.
