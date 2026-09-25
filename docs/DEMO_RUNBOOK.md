# Demo runbook

**Owner:** A + B · **Research:** research/24 §9 · **Numbers:** `storemind/storemind/eval/results/RESULTS.md`

Three ways to run the demo, most impressive first. Always have the next one ready: if the primary fails on stage,
switch without apologising.

| | what the judges see | depends on | state |
|---|---|---|---|
| **Primary** | live Pi Camera 3 + fake-CCTV streams + STM32 node (or its simulator), dashboard on a laptop | Pi 5, STM32, venue network | **Ram fills in** (section 2) |
| **Backup** | same code path, recorded clips served as RTSP "CCTV" by MediaMTX | laptop only | **Ram fills in** (section 3) |
| **Laptop replay** | three synthetic cameras replayed with saved detections, dashboard, daily summary, Ask your store | laptop only, no network | **works today** (section 4, run on 2026-09-25) |

## 1. The day before

- [ ] `git pull` on the demo laptop; `cd storemind; .venv/Scripts/python -m pytest -q` all green.
- [ ] Section 4 rehearsed end to end with a timer.
- [ ] `../videos/` present on the laptop (the clips are not in git).
- [ ] Ollama running with `qwen2.5-coder:1.5b` (`ollama list`), or decide to show only the daily summary.
- [ ] Telugu and Hindi summary checked by a native speaker (docs/ASK.md §4).
- [ ] Copy of RESULTS.md open in a tab, and the numbers in section 6 on a card.
- [ ] **Ram:** Pi 5, STM32, cables, power, camera mounts; checklist in section 2.

## 2. Primary demo: live Pi + fake CCTV + STM32 (Ram fills in)

> **Ram:** exact commands to start go2rtc/MediaMTX, the serial bridge (or the STM32 simulator), and
> `storemind.run` on the Pi; which config file; how the laptop opens the dashboard; what to check in the first
> 60 seconds. Link docs/SETUP_PI5.md and docs/WIRING.md.

What to point at once it is running (Vinith presents this part):
- walk through the door → the entry counter goes up; walk back → the exit counter;
- stand in the counter lane for 3 s → queue length 1; walk past quickly → no change (dwell membership, M4);
- take a pack off the load-cell shelf → PICKUP with units; put it back → put-back (M6);
- cover the shelf camera or turn the light off → the slot says UNKNOWN "too dark", never EMPTY (M3);
- nudge a camera → CRITICAL "camera moved" alert and counting pauses (tamper detection).

## 3. Backup: recorded clips as fake CCTV (Ram fills in)

> **Ram:** `tools/fake_cctv.py` (MediaMTX + ffmpeg) serves clips as `rtsp://127.0.0.1:8554/cam1...`; give the
> exact command, the config that points the cameras at those URLs, and how long it takes to start.

## 4. Laptop replay (works today, no network)

All commands from `C:\SIH\storemind`. Tested on 2026-09-25.

```bash
# 1. Replay the three synthetic cameras at real speed, with the dashboard.
#    --start-time makes the clock read "evening", so charts and the summary look like a shop at rush hour.
.venv/Scripts/python -m storemind.run --config configs/demo.yaml --backend scripted --realtime --api \
    --start-time 2026-03-15T18:00:00+05:30 --db data/demo_stage.db
#    open http://localhost:8000/
#    the clips are 120 s (entrance), 180 s (counter) and 150 s (shelf); stop with Ctrl+C when they end

# 2. Daily summary in three languages from what was just recorded (no model involved).
.venv/Scripts/python -m storemind.llm.summary --db data/demo_stage.db --day 2026-03-15

# 3. Ask your store (needs Ollama; without it the keyword rules answer).
.venv/Scripts/python -m storemind.llm.ask --db data/demo_stage.db --today 2026-03-15 "How many people came in today?"
```

- `--backend scripted` replays saved detections, so no model or GPU is needed and the result is identical every
  time. To show the real detector on real people instead, replay a CAVIAR clip with the default backend:
  `--config configs/caviar.yaml --camera corridor --realtime --show` (a real mall clip, YOLO11n on the laptop).
  People are blurred in the window.
- Delete `data/demo_stage.db` before a new rehearsal, or the summary counts both runs.
- The dashboard's privacy line reads "Video stored: 0 bytes". Point at it.

## 5. The 5-minute script

| time | show | say |
|---|---|---|
| 0:00-0:30 | dashboard, live | "Kirana stores already have CCTV. StoreMind plugs into it, or into a low-cost Pi camera, and turns it into footfall, queue and shelf alerts, fully offline." |
| 0:30-1:30 | entrance counter moving | "People are counted at the door with a detector and tracker. On the public CAVIAR benchmark we reach 96.7% on entries but only 76% on exits, below our 90% target; we show that number rather than hide it. An IR beam on the STM32 cross-checks the camera and takes over if the camera fails." |
| 1:30-2:30 | queue panel + forecast | "A queue is people who stay, not everyone who walks through the lane. Our dwell rule counted 825 joins against 833 true in simulation, where the old rule counted 2,598. The forecast learns the door-to-counter delay and warns before the queue forms." (bucket C) |
| 2:30-3:30 | shelf panel; STM32 load cell | "Shelves are compared with a restocked reference under the current light, and a load cell under the shelf confirms picks: simulated picks F1 0.98. In the dark we say 'unknown', never 'empty'." (bucket C) |
| 3:30-4:15 | daily summary in Telugu, Ask your store | "The owner gets a daily summary in Telugu, Hindi or English. They can also ask a question; a small model on the box writes a database query, and every number comes from the data with the query shown. In 60 test questions it invented no numbers." |
| 4:15-5:00 | privacy line, Qualcomm slide | "No video is stored: that counter is 0. No faces, no re-identification. On a Qualcomm QCS6490 NPU our detector runs in 12.8 ms per frame in INT8 on AI Hub's hosted board, about 12× faster than FP32." (bucket Q) |

Rules while speaking: say the bucket for every number ("on the public benchmark", "in simulation", "on
Qualcomm's hosted board"). Never say "accuracy" for a C number.

## 6. Numbers to have on a card (from RESULTS.md; re-copy after the last regeneration)

| number | bucket | source |
|---|---|---|
| CAVIAR held-out entries 96.7%, exits 76.2% (target 90%) | A | RESULTS "Counting v2 ... cross-validated" |
| queue joins 825 vs 833 true (v1: 2,598) | C | RESULTS "Queue v1 vs v2" |
| shelf EMPTY F1 0.92 (v1 0.74); dark → UNKNOWN every time | C | RESULTS "Shelf engine under changing light" |
| picks F1 0.98, units 99.7% | C | RESULTS "Pick / put-back" |
| Ask your store: 0 invented numbers; 13/20 correct on unseen questions | C | RESULTS "Ask your store" |
| YOLO11n INT8 12.8 ms, 100% NPU on RB3 Gen 2 (QCS6490) | Q | RESULTS "Detector on Qualcomm silicon" |
| video bytes stored: 0 | - | dashboard |

## 7. When something fails on stage

| symptom | do this |
|---|---|
| dashboard page does not load | check the terminal for `dashboard: http://localhost:8000/`; if port 8000 is taken, add `--port 8001` |
| a camera shows no people | the replay clip ended (they are 2-3 minutes long): restart section 4 step 1 |
| Ask your store is slow or says "cannot answer" | show the daily summary instead: it needs no model |
| live camera or network dies (primary) | switch to section 3, then section 4; same dashboard, same story |
| the summary counts double | a previous rehearsal is in the database: use a new `--db` file |
| **Ram:** STM32 / bridge failures | Ram fills in (docs/TROUBLESHOOTING.md) |
