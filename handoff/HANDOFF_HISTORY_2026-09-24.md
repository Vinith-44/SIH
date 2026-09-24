# StoreMind — handoff (2026-09-24)

SIH 2026 · PS 26179 · Qualcomm · Team TechGladiators. Read `CLAUDE.md` and
`research/` first. `WORK_LOG.md` has every number and the command that produced
it; `storemind/eval/results/RESULTS.md` is the generated results page.

## What exists

Git repo at `C:\SIH`, venv at `storemind/.venv` (Python 3.11), **124 tests pass**.

| Milestone | State |
|---|---|
| M0 setup, ZIPs → `legacy/`, inventory | **done** |
| M1 ingest / detectors / ByteTrack / footfall / zones / heatmap | **done** |
| M2 queue engine + Erlang-C forecast | **done** |
| M3 evaluation harness + before/after + RESULTS.md | **done** |
| M4 `tools/calibrate.py` | **done** (GUI untested headlessly; config output is tested) |
| M5 storage, alerts, health, FastAPI + offline dashboard | **done** |
| M6 shelf engine v1 (label-free) | **done**; Kaggle SKU-110K notebook **not started** |
| M7 fusion rules | **done**; serial bridge, STM32 simulator, firmware **not started** |
| M8 Qualcomm AI Hub | **not started** (needs a token) |
| M9 prior art | **skipped as instructed** — but see the correction below |
| M10 PPT assets | charts + overlay clips **done**; dashboard screenshots **blocked** |
| M11 Pi deployment | **not started** |

## How to run it

```powershell
cd C:\SIH\storemind
.\.venv\Scripts\python -m pytest tests\ -q                      # 124 pass

# live dashboard over a replayed store (open http://localhost:8000)
.\.venv\Scripts\python -m storemind.run --config configs\demo.yaml --backend scripted `
    --realtime --api --db data\dash.db

# real footage, real detector, with an annotated window
.\.venv\Scripts\python -m storemind.run --config configs\caviar.yaml --camera corridor --show

# regenerate every result page and chart
.\.venv\Scripts\python -m storemind.eval.run_all         # reuses data\caviar_full.json
.\.venv\Scripts\python tools\make_ppt_assets.py

# re-measure CAVIAR from scratch (~1 hour of inference)
del data\caviar_full.json
.\.venv\Scripts\python -m storemind.eval.eval_caviar --json data\caviar_full.json
```

Test clips are gitignored; regenerate with
`python tools/make_synthetic_video.py --out ../videos --scene all` and `--scene rush`.

## Measured results

**Real footage (bucket A — CAVIAR, 16 clips, 111 people, 15.9 min):**
entry count accuracy **90.0%**, exit **85.7%**, entry event F1 **0.89**,
crossing timing error **0.33 s**, people-in-frame MAE **0.65**, **63** ID
switches over 13,721 boxes. Legacy pipeline on the same clips: **95 entries
against 30**, F1 0.16, 1,398 track IDs for 111 people.

**Logic on simulation (bucket C):** counting 100%, queue-length MAE 0.00, wait
MAE 0.39 s, shelf state accuracy 95.8%, shelf EMPTY F1 1.00. Forecast warned
**8.7 min** before congestion with **0** false alarms and recovered the 6-minute
lag exactly.

**Speed (bucket S, no ground truth):** YOLO11n @320 **27.9 ms/frame** on this
laptop.

### Say these things honestly
- **We miss the exit-count target (85.7% vs ≥90%) and over-count in both
  directions.** Precision figures show the cause is extra crossings, not missed
  ones. RESULTS.md states this; do not quote the 90.0% entry figure alone.
- CAVIAR is a Portuguese mall at 384×288 with a sparse crowd — **easier** than a
  crowded kirana. It proves the pipeline works on real video, not that it works
  in our target store.
- Bucket C numbers grade logic with a perfect detector. They are not accuracy.
- Speed varies ~5× with laptop thermal state; compare rows within one run only.
- The debug overlay blurs **detected** people. An undetected person is not
  blurred — the real privacy guarantee is that frames are never written at all.

### Prior-art correction (important for the deck)
`research/22` establishes that door-to-counter predictive staffing is **not
novel** — Irisys patent US7778855B2 (2010), and Irisys and Xovis sell it. All
"novelty N1" wording in the code and results has been rewritten. The honest
claim: the same proven idea on a shop's **existing CCTV**, a ~₹15–25k **offline**
box, with the lag **learned automatically** instead of configured.

## Exact next step

1. **M7 hardware bridge** — `storemind/sensor_bridge.py` (NMEA-style `$W/$T/$B/$R/$H`
   with XOR checksum, per `research/05` §2), `tools/stm32_simulator.py` so it
   demos without hardware, then the FreeRTOS firmware skeleton. The fusion rules
   that consume these events already exist and are tested; only the transport is
   missing, so this is the highest-value remaining work.
2. **M11 Pi deployment** (`deploy/pi/`) and re-run `eval/benchmark.py` on the Pi —
   every speed number we have is a laptop number.
3. **M8 Qualcomm** once a token exists.

## Blocked — needs the team

1. **Our own recordings (bucket B) — still the biggest gap.** Queue wait time and
   shelf stock level have *no* real-world accuracy number and no public dataset
   covers them. Use `tools/label_ground_truth.py` and
   `videos/GROUND_TRUTH_TEMPLATES/README.md`.
2. **Dashboard screenshots for the deck** — the API and page were verified
   working end to end, but this session had no browser available. Run the
   dashboard command above and screenshot into `C:\SIH\ppt_assets\`.
3. **Qualcomm ID + AI Hub API token** (`qai-hub configure`).
4. **Raspberry Pi 5** for real edge numbers.
5. **STM32 firmware** is still not in `C:\SIH`; the PPT claims HX711 on real
   hardware. Please add the existing sketch.
6. **Git history** still contains ~200 MB of video blobs from the initial commit.
   Decide whether to rewrite history before pushing anywhere.
