# StoreMind - measured results

Generated 2026-09-25 06:49 by `python -m storemind.eval.run_all`.

Every number on this page came from a command printed beside it. Nothing here was typed by hand. If a measurement could not be made, the row says so.

## How to read the data buckets

| bucket | meaning | what it may be used for |
|---|---|---|
| **A** | public benchmark with published ground truth | real accuracy claims |
| **B** | our own field recording, hand-labelled by the team | real accuracy claims |
| **C** | simulation with known ground truth | proving the logic is correct - **never** an accuracy claim |
| **S** | real footage, no ground truth | speed only - **never** an accuracy claim |
| **Q** | Qualcomm AI Hub hosted/proxy device | Qualcomm latency - **never** "our board" |
| **P** | published third-party figure, cited | context only - not our measurement |

This follows `research/09b_TEST_DATA_VALIDITY.md`. A simulation can only ever show that the arithmetic is right; it cannot show that the system works in a shop.

## Machine

| property | value |
|---|---|
| platform | Windows-10-10.0.26200-SP0 |
| processor | Intel64 Family 6 Model 191 Stepping 2, GenuineIntel |
| python | 3.11.8 |
| cpu_cores | 10 |
| cpu_threads | 16 |
| ram_gb | 16.9 |
| opencv | 5.0.0 |
| git_commit | 22e266f |

## Results

### Entry / exit counting on real footage - CAVIAR shopping centre

**Data bucket A** - public benchmark with published ground truth

16 clips (8 scenarios x 2 camera views), real people, real detector (YOLO11n), no ground truth was shown to the system. Credit: EC Funded CAVIAR project/IST 2001 37540 (CC BY-SA).

Ground-truth crossings are derived from the published trajectories over the same line the system uses; the line coordinates and the stability check behind them are recorded in `configs/caviar.yaml`.

**Caveat that belongs on the slide:** CAVIAR is a Portuguese mall at 384x288 with a sparse crowd. It is easier than a crowded kirana at peak hour, though the small frame makes distant people harder. It shows the pipeline works on real video. It does not show it works in our target store - only our own recordings can.

**Did not meet target: exit count accuracy 85.7% (target >= 90%).** We over-count in both directions - 33 entries against 30 and 24 exits against 21 - and the event-level precision figures below show where it comes from: extra crossings, not missed ones. The likely cause is people loitering near the line in a mall corridor, which a shop doorway sees far less of. Do not present the entry figure as 'meets target' without the exit figure beside it.

| metric | result | target |
|---|---|---|
| clips / annotated people | 16 / 111 | - |
| total video | 15.9 min | - |
| entries counted (truth) | 33 (30) | - |
| exits counted (truth) | 24 (21) | - |
| entry count accuracy | 90.0% | >= 90% |
| exit count accuracy | 85.7% | >= 90% |
| total count accuracy | 88.2% | >= 90% |
| entry event precision / recall / F1 | 0.85 / 0.93 / 0.89 | - |
| exit event precision / recall / F1 | 0.71 / 0.81 / 0.76 | - |
| mean crossing timing error | 0.33 s | - |
| people-in-frame MAE | 0.65 people | <= 1-2 |
| detection match rate (IoU >= 0.4) | 74.4% of annotated people | - |
| **ID switches** (ByteTrack) | **63** over 13,721 annotated boxes in processed frames | - |
| --- legacy pipeline on the same clips --- |  |  |
| legacy entries counted (truth) | 95 (30) | - |
| legacy exits counted (truth) | 75 (21) | - |
| legacy entry count accuracy | 0.0% | >= 90% |
| legacy entry event F1 | 0.16 | - |
| legacy track IDs created | 1398 | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_caviar
```

</details>

### Counting v2, detector and tracker bake-off - CAVIAR, cross-validated

**Data bucket A** - public benchmark with published ground truth

2-fold cross-validation over the two camera views: detector, tracker and counter settings are chosen on one view and scored on the other, so every ground-truth crossing below is scored by a setting chosen without it. All variants replay cached detections, so only the component under test changes. The protocol was revised after a first run (`tracker_bakeoff_run1.md`), and both runs are published. Full tables: `eval/results/tracker_bakeoff.md`.

**Did not meet target on held-out data:** entry accuracy 96.7%, exit accuracy 76.2% (target >= 90%). See docs/COUNTING.md for what limits it.

| metric | result | target |
|---|---|---|
| today: YOLO11n@640 + ByteTrack + v1 counter: entries (truth) / acc | 33 (30) / 90.0% | >= 90% |
| today: YOLO11n@640 + ByteTrack + v1 counter: exits (truth) / acc | 24 (21) / 85.7% | >= 90% |
| today: YOLO11n@640 + ByteTrack + v1 counter: entry / exit event F1 | 0.89 / 0.76 | - |
| **counting v2 procedure, held-out (CV)**: entries (truth) / acc | 31 (30) / 96.7% | >= 90% |
| **counting v2 procedure, held-out (CV)**: exits (truth) / acc | 16 (21) / 76.2% | >= 90% |
| **counting v2 procedure, held-out (CV)**: entry / exit event F1 | 0.82 / 0.76 | - |
| fold: tune corridor -> test front | yolo26n@640 c0.15 + bytetrack, test F1 0.61 | - |
| fold: tune front -> test corridor | yolo11n@960 c0.25 + bytetrack, test F1 0.85 | - |
| configuration tuned on all 16 clips (graded on the answers; not an accuracy claim) | yolo11s@640 c0.25 + bytetrack, `mode=gate, gate_px=8.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5` | - |
| detector that ships on the Pi 5 | YOLO11n@640 (YOLO11s: 3.75 FPS on the laptop CPU, below the 8 FPS budget; its place is the Qualcomm NPU, docs/QUALCOMM.md) | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.detcache && python -m storemind.eval.bakeoff
```

</details>

### Entry / exit counting - synthetic entrance

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Perfect detections (`--backend scripted`) so this grades the line-crossing logic: hysteresis band, foot point, per-track cooldown. The clip contains two traps: a shopper who loiters *on* the line, and two people crossing shoulder to shoulder.

| metric | result | target |
|---|---|---|
| entries counted | 14 (truth 14) | - |
| exits counted | 6 (truth 6) | - |
| entry count accuracy | 100.0% | >= 90% |
| exit count accuracy | 100.0% | >= 90% |
| entry event precision / recall / F1 | 1.00 / 1.00 / 1.00 | - |
| mean crossing timing error | 0.08 s | - |
| occupancy MAE | 0.08 people | <= 1-2 |

<details><summary>commands</summary>

```bash
python -m storemind.run --config configs/demo.yaml --camera entrance --source C:\SIH\videos\entrance\synthetic_entrance.mp4 --backend scripted --model C:\SIH\videos\entrance\synthetic_entrance_detections.json --fps 25
```

</details>

### Queue length, wait and service time - synthetic counter

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Perfect detections. Grades the gap-tolerant wait timer, the 3-second minimum before service is believed to have started, and median smoothing - the three things audit items Q2, Q3 and Q6 got wrong.

| metric | result | target |
|---|---|---|
| customers detected | 7 of 7 | - |
| service event precision / recall / F1 | 1.00 / 1.00 / 1.00 | - |
| queue-length MAE | 0.06 people | <= 1 |
| wait-time MAE | 0.39 s (1.1%) | <= 20% |
| service-time MAE | 0.14 s (0.6%) | - |
| median wait (ours vs truth) | 39.9 s vs 39.5 s | - |

<details><summary>commands</summary>

```bash
python -m storemind.run --config configs/demo.yaml --camera counter-1 --source C:\SIH\videos\queue\synthetic_queue.mp4 --backend scripted --model C:\SIH\videos\queue\synthetic_queue_detections.json --fps 25
```

</details>

### Queue v1 vs v2 - simulated tracks (passers-by, parties, balks, reneges, bent lane)

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

`eval/queue_sim.py` simulates an L-shaped queue as tracker output, with exact truth; 'noisy' adds box jitter, 3% missed detections and 0.3 ID switches per person-minute. Defaults were tuned on seeds 1-10; this table is seeds 11-40. It proves the logic, not accuracy on a real queue (that needs our own canteen clip: docs/HARDWARE_TODO.md).

**Not solved:** the balk / renege split. With the tuned 3 s join time, people who stop 3-6 s and leave count as reneges, so only their sum ('walked away unserved') is usable. Tail overflow is barely exercised here (1 true sample), so it is covered by unit tests only. The test seeds were scored twice: after a unit test found a balk-timing bias, the rescore gave identical numbers.

| metric | result | target |
|---|---|---|
| v1: queue MAE / party MAE | 0.38 / 0.85 | queue MAE <= 1 |
| v1: median-wait err / Little's-law W err | 2.7% / 53.1% | <= 20% |
| v1: joins counted (truth) / walked away unserved (truth) | 2598 (833) / 0 (95) | - |
| v2: queue MAE / party MAE | 0.19 / 0.15 | queue MAE <= 1 |
| v2: median-wait err / Little's-law W err | 2.8% / 20.4% | <= 20% |
| v2: joins counted (truth) / walked away unserved (truth) | 825 (833) / 87 (95) | - |
| v1 noisy: queue MAE / party MAE | 0.41 / 0.89 | queue MAE <= 1 |
| v1 noisy: median-wait err / Little's-law W err | 52.5% / 61.2% | <= 20% |
| v1 noisy: joins counted (truth) / walked away unserved (truth) | 3081 (833) / 0 (95) | - |
| v2 noisy: queue MAE / party MAE | 0.21 / 0.18 | queue MAE <= 1 |
| v2 noisy: median-wait err / Little's-law W err | 32.9% / 12.4% | <= 20% |
| v2 noisy: joins counted (truth) / walked away unserved (truth) | 969 (833) / 118 (95) | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_queue_v2 --grid   # tuning seeds only
python -m storemind.eval.eval_queue_v2
```

</details>

### Shelf slot state - synthetic shelf

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Perfect person detections, real image processing on the shelf itself (no model, no training: reference crop vs current crop). The clip includes a shopper standing in front of the shelf for 20 s to exercise the occlusion gate.

| metric | result | target |
|---|---|---|
| slot state accuracy | 95.8% (48 samples) | - |
| EMPTY precision / recall / F1 | 1.00 / 1.00 / 1.00 | F1 >= 0.85 |
| EMPTY detection delay | 5.0 s | - |
| frames skipped by the occlusion gate | 9 | - |
| false alerts while a shopper blocked the shelf | 0 | 0 |

<details><summary>commands</summary>

```bash
python -m storemind.run --config configs/demo.yaml --camera shelf-a --source C:\SIH\videos\shelf\synthetic_shelf.mp4 --backend scripted --model C:\SIH\videos\shelf\synthetic_shelf_detections.json
```

</details>

### Shelf engine under changing light - synthetic timelines

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Synthetic shelves (`tools/shelf_synth.py`) under day, evening, tube light, dim, dark and glare, with exact slot truth and a simulated BH1750. Thresholds were tuned on seeds 1-10 only; this table is seeds 11-40. It proves the lighting logic, not accuracy on a real shelf (that needs our own photos: docs/HARDWARE_TODO.md).

**Disclosures:** (1) The test seeds were scored twice. The second run came after a unit test exposed a confidence bug that stopped the reference bank learning. EMPTY F1 was 0.93 before the fix and 0.92 after. (2) The lux sensor shows no benefit here: the synthetic light is uniform, so frame brightness predicts it perfectly. A real shelf is where the BH1750 has to prove itself.

| metric | result | target |
|---|---|---|
| v1 (before M3): slot-state accuracy (lit) | 54.7% | - |
| v1 (before M3): EMPTY P / R / F1 | 0.98 / 0.59 / 0.74 | F1 >= 0.85 |
| v1 (before M3): EMPTY F1 day / evening / dim | 0.91 / 0.15 / 0.33 | >= 0.85 |
| v1 (before M3): dark slot-steps -> UNKNOWN / false EMPTY | 0 / 0 of 714 | all UNKNOWN, 0 EMPTY |
| v2 with lux: slot-state accuracy (lit) | 91.7% | - |
| v2 with lux: EMPTY P / R / F1 | 0.97 / 0.88 / 0.92 | F1 >= 0.85 |
| v2 with lux: EMPTY F1 day / evening / dim | 0.91 / 0.93 / 0.89 | >= 0.85 |
| v2 with lux: dark slot-steps -> UNKNOWN / false EMPTY | 714 / 0 of 714 | all UNKNOWN, 0 EMPTY |
| v2 without lux: slot-state accuracy (lit) | 91.7% | - |
| v2 without lux: EMPTY P / R / F1 | 0.97 / 0.89 / 0.93 | F1 >= 0.85 |
| v2 without lux: EMPTY F1 day / evening / dim | 0.91 / 0.95 / 0.89 | >= 0.85 |
| v2 without lux: dark slot-steps -> UNKNOWN / false EMPTY | 714 / 0 of 714 | all UNKNOWN, 0 EMPTY |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_shelf_lighting --grid   # tuning seeds only
python -m storemind.eval.eval_shelf_lighting
```

</details>

### Pick / put-back from MEMS touch + load cell - simulated sensor events

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

`eval/sensor_sim.py` generates the events the serial bridge will publish (TOUCH / SETTLED, unstable readings while a shelf is handled, 10% of them wrongly flagged stable, trolley knocks). The simulator encodes our assumptions about the firmware, so this checks the fusion logic against them. It is not a hardware result. Defaults were tuned on seeds 1-10; this table is seeds 11-40. Hardware acceptance (pick/put-back >= 90% on the real board) is in docs/HARDWARE_TODO.md.

| metric | result | target |
|---|---|---|
| v1: pick P / R / F1 | 0.05 / 1.00 / 0.09 | >= 0.90 |
| v1: put-back P / R / F1 | not measured yet / 0.00 / not measured yet | >= 0.90 |
| v1: false shrink flags / fallen-stock alerts (true) | 243 / 0 (15) | 0 / all |
| weight-only: pick P / R / F1 | 0.66 / 0.66 / 0.66 | >= 0.90 |
| weight-only: put-back P / R / F1 | 0.54 / 0.61 / 0.57 | >= 0.90 |
| weight-only: false shrink flags / fallen-stock alerts (true) | 0 / 0 (15) | 0 / all |
| M6: pick P / R / F1 | 0.98 / 0.98 / 0.98 | >= 0.90 |
| M6: put-back P / R / F1 | 0.90 / 0.96 / 0.93 | >= 0.90 |
| M6: false shrink flags / fallen-stock alerts (true) | 0 / 15 (15) | 0 / all |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_fusion --grid   # tuning seeds only
python -m storemind.eval.eval_fusion
```

</details>

### Ask your store - questions answered from the event database (simulated store)

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Questions in plain English -> one read-only SQL query over whitelisted views -> an answer that cites its rows. The expected answers are computed in Python from the simulated events, not with SQL. **Invented numbers** = numbers in an answer that appear in none of the cited rows (target 0). **Wrong** = a real, cited result for the wrong query (or a seconds value called minutes); the dashboard must show the query. **Set C, first run** is the held-out number: those 20 questions were written after every change they could have influenced, and are re-graded here with the stricter unit check added later. **Run 4** is the current code on the same questions, no longer held out. History: docs/ASK.md section 3. LLM latency is on the laptop GPU, not the Pi.

| metric | result | target |
|---|---|---|
| rules: set C first run (held out), correct / wrong / refused | 12 / 6 / 2 of 20 |  |
| rules: set C run 4 (current code, seen), correct / wrong / refused | 12 / 6 / 2 of 20 |  |
| rules: invented numbers, run 4 (sets A+B+C, 60 questions) | 0 | 0 |
| rules: Hindi / Telugu visitor question (4 phrasings) | 0 / 4 |  |
| llm (qwen2.5-coder:1.5b): set C first run (held out), correct / wrong / refused | 11 / 7 / 2 of 20 |  |
| llm (qwen2.5-coder:1.5b): set C run 4 (current code, seen), correct / wrong / refused | 13 / 5 / 2 of 20 |  |
| llm (qwen2.5-coder:1.5b): invented numbers, run 4 (sets A+B+C, 60 questions) | 0 | 0 |
| llm (qwen2.5-coder:1.5b): median seconds per question | 4.97 |  |
| llm (qwen2.5-coder:1.5b): Hindi / Telugu visitor question (4 phrasings) | 2 / 4 |  |
| llm (qwen2.5-coder:1.5b), then rules - deployed: set C first run (held out), correct / wrong / refused | 13 / 7 / 0 of 20 |  |
| llm (qwen2.5-coder:1.5b), then rules - deployed: set C run 4 (current code, seen), correct / wrong / refused | 15 / 5 / 0 of 20 |  |
| llm (qwen2.5-coder:1.5b), then rules - deployed: invented numbers, run 4 (sets A+B+C, 60 questions) | 0 | 0 |
| llm (qwen2.5-coder:1.5b), then rules - deployed: median seconds per question | 4.97 |  |
| llm (qwen2.5-coder:1.5b), then rules - deployed: Hindi / Telugu visitor question (4 phrasings) | 2 / 4 |  |
| llm (qwen2.5-coder:3b): set C first run (held out), correct / wrong / refused | 13 / 7 / 0 of 20 |  |
| llm (qwen2.5-coder:3b): set C run 4 (current code, seen), correct / wrong / refused | 15 / 5 / 0 of 20 |  |
| llm (qwen2.5-coder:3b): invented numbers, run 4 (sets A+B+C, 60 questions) | 0 | 0 |
| llm (qwen2.5-coder:3b): median seconds per question | 5.26 |  |
| llm (qwen2.5-coder:3b): Hindi / Telugu visitor question (4 phrasings) | 3 / 4 |  |
| llm (qwen2.5-coder:3b), then rules - deployed: set C first run (held out), correct / wrong / refused | 12 / 8 / 0 of 20 |  |
| llm (qwen2.5-coder:3b), then rules - deployed: set C run 4 (current code, seen), correct / wrong / refused | 14 / 6 / 0 of 20 |  |
| llm (qwen2.5-coder:3b), then rules - deployed: invented numbers, run 4 (sets A+B+C, 60 questions) | 0 | 0 |
| llm (qwen2.5-coder:3b), then rules - deployed: median seconds per question | 5.23 |  |
| llm (qwen2.5-coder:3b), then rules - deployed: Hindi / Telugu visitor question (4 phrasings) | 3 / 4 |  |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_ask
python -m storemind.eval.eval_ask --model qwen2.5-coder:3b --no-rules --out storemind/storemind/eval/results/ask_3b.json
```

</details>

### Door-to-counter queue forecast - simulated rush

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Two synchronised 25-minute clips on one timeline with a 6-minute shopping-trip lag built in. The forecaster only ever sees ENTRY events and the counter's own queue events - it is never told the lag. Simulation is the *right* tool here: it is the only way to know the true lag and the true congestion onset exactly.

**Prior art, checked (research/22): this idea is not new.** Irisys patent US7778855B2 (2010) predicts checkout staffing from entrance counts, and Irisys and Xovis both sell it to big-box retailers with dedicated overhead sensors. Never claim "first". The contribution is doing it on a shop's existing CCTV and a ~Rs 15-25k offline box, with the door-to-counter lag learned automatically instead of configured.

| metric | result | target |
|---|---|---|
| entries detected at the door | 80 | - |
| congestion onset (ground truth) | 1000 s | - |
| first 'open another counter' warning | 480 s | - |
| **lead time** | **8.7 min** | >= 3 min |
| warnings / false alarms | 8 / 0 | 0 false alarms |
| shopping-trip lag estimated vs true | 6 min vs 6.0 min | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_forecast --config configs/rush.yaml --entrance-camera entrance --counter-camera checkout --backend scripted
```

</details>

### Before / after - counting logic only, identical perfect detections

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Both stacks are fed the *same* perfect detections, which isolates the tracking and counting logic from the detector.

**Honest result: on this clip the two are level.** With perfect, well-separated detections the legacy centroid tracker has nothing to trip over, and its box-centre counting happens to survive the loiterer because the box *centre* never reaches the line. The legacy failure modes need a real detector and real crowding - see the CAVIAR section.

| metric | result | target |
|---|---|---|
| entries counted (truth 14) | legacy 14 / StoreMind 14 | 14 |
| exits counted (truth 6) | legacy 6 / StoreMind 6 | 6 |
| entry count accuracy | legacy 100.0% / StoreMind 100.0% | - |
| occupancy MAE | legacy 0.08 / StoreMind 0.08 | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.legacy_baseline --config configs/demo.yaml --camera entrance --source C:\SIH\videos\entrance\synthetic_entrance.mp4
```

</details>

### Before / after - whole stacks on the synthetic clip

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

The legacy stack including its own detector (EfficientDet-Lite0 squashed to 320x320). Shown for completeness only: a rendered clip is out of distribution for a COCO detector, so this measures the detector's dislike of synthetic imagery as much as the algorithm. **Do not put this row on a slide** - use the CAVIAR comparison.

| metric | result | target |
|---|---|---|
| entries counted (truth 14) | 257 | 14 |
| track IDs created for ~20 people | 1263 | - |
| entry false positives | 243 | 0 |

<details><summary>commands</summary>

```bash
python -m storemind.eval.legacy_baseline --config configs/demo.yaml --camera entrance --source C:\SIH\videos\entrance\synthetic_entrance.mp4
```

</details>

### Detector speed - real pedestrian footage

**Data bucket S** - no ground truth - speed measurement only, never an accuracy claim

OpenCV's `vtest.avi` sample (Apache-2.0), 768x576, real people in a plaza. It has **no ground truth**, so this measures speed only, never accuracy - `det/frame` is shown so a backend that is fast because it finds nothing is obvious, not as a correctness score.

These are laptop numbers and the Pi 5 must be measured on the Pi. They also move with the laptop's thermal and power state: the same command on the same clip measured 27 ms and 159 ms per frame on different days of this work. Compare rows within one run, never across runs.

**CPU only** (`STOREMIND_DEVICE=cpu`): the laptop's GPU is used for evaluation runs, but a GPU number says nothing about a Pi 5 or a QCS6490, so it is not shown here.

| metric | result | target |
|---|---|---|
| ultralytics yolo11n.pt @320 (CPU) | 33.38 ms/frame &middot; 29.56 FPS &middot; 4.34 detections/frame | - |
| ultralytics yolo11n.pt @416 (CPU) | 50.29 ms/frame &middot; 19.72 FPS &middot; 4.44 detections/frame | - |
| ultralytics yolo11n.pt @640 (CPU) | 116.08 ms/frame &middot; 8.58 FPS &middot; 4.73 detections/frame | - |
| ultralytics yolo26n.pt @640 (CPU) | 106.28 ms/frame &middot; 9.37 FPS &middot; 4.73 detections/frame | - |
| litert efficientdet_lite0_coco_legacy.tflite @320 (CPU) | 21.38 ms/frame &middot; 45.83 FPS &middot; 4.2 detections/frame | - |

<details><summary>commands</summary>

```bash
STOREMIND_DEVICE=cpu python -m storemind.eval.benchmark --source ../videos/other/vtest.avi --frames 100
```

</details>

### Exported detectors - fidelity to FP32 and laptop-CPU speed

**Data bucket S** - no ground truth - speed measurement only, never an accuracy claim

Each export's person boxes vs the FP32 PyTorch model's on vtest.avi (frames used for INT8 calibration skipped). This measures agreement with the reference model, not accuracy, and laptop CPU speed (4 threads), not Pi speed. Full table: `eval/results/model_export.md`.

| metric | result | target |
|---|---|---|
| yolo11n pytorch fp32 (5.61 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 99.7 ms median | - |
| yolo11n onnx fp32 (10.74 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 29.8 ms median | - |
| yolo11n onnx int8 (3.21 MB) | recall/precision vs FP32 97.6% / 100.0% &middot; 32.9 ms median | - |
| yolo11n ncnn fp32 (10.66 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 52.3 ms median | - |
| yolo26n pytorch fp32 (5.54 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 41.1 ms median | - |
| yolo26n onnx fp32 (9.93 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 24.3 ms median | - |
| yolo26n onnx int8 (3.06 MB) | recall/precision vs FP32 99.6% / 99.0% &middot; 29.7 ms median | - |
| yolo26n ncnn fp32 (9.85 MB) | recall/precision vs FP32 100.0% / 100.0% &middot; 138.3 ms median | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_export --caviar
```

</details>

### Exported detectors - counting on CAVIAR per format

**Data bucket A** - public benchmark with published ground truth

All 16 CAVIAR clips through the shipped counter (ByteTrack + gate) with each export. The settings are the shipped ones, which were tuned on these clips, so compare formats with each other. The accuracy claim is the cross-validated one above.

| metric | result | target |
|---|---|---|
| yolo11n pytorch fp32: entries / exits (truth 30 / 21) | 28 / 21 &middot; F1 0.93 / 0.86 | - |
| yolo11n onnx fp32: entries / exits (truth 30 / 21) | 28 / 21 &middot; F1 0.93 / 0.86 | - |
| yolo11n onnx int8: entries / exits (truth 30 / 21) | 28 / 19 &middot; F1 0.93 / 0.90 | - |
| yolo11n ncnn fp32: entries / exits (truth 30 / 21) | 28 / 21 &middot; F1 0.93 / 0.86 | - |
| yolo26n pytorch fp32: entries / exits (truth 30 / 21) | 27 / 16 &middot; F1 0.88 / 0.81 | - |
| yolo26n onnx fp32: entries / exits (truth 30 / 21) | 27 / 16 &middot; F1 0.88 / 0.81 | - |
| yolo26n onnx int8: entries / exits (truth 30 / 21) | 25 / 14 &middot; F1 0.91 / 0.80 | - |
| yolo26n ncnn fp32: entries / exits (truth 30 / 21) | 27 / 16 &middot; F1 0.88 / 0.81 | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.eval_export --caviar
```

</details>

### Detector on Qualcomm silicon - AI Hub hosted devices

**Data bucket Q** - Qualcomm AI Hub hosted/proxy device - not our own board

Compiled to TFLite and profiled by Qualcomm AI Hub on devices it hosts (Dragonwing RB3 Gen 2 Vision Kit, QCS8550 (Proxy)). **These are not our boards.** INT8 = AI Hub w8a8 quantization with our calibration frames. 'NPU layers' is the share of network layers the profile placed on the Hexagon NPU. Output agreement compares the device's detections with our local ONNX FP32 model on vtest frames. Job links are in `eval/results/qualcomm_aihub.json`.

| metric | result | target |
|---|---|---|
| yolo11n fp32 on Dragonwing RB3 Gen 2 Vision Kit | 150.893 ms &middot; 66.1 MB peak &middot; NPU layers 5.3% &middot; boxes vs local FP32: recall 100.0%, precision 100.0% | - |
| yolo11n fp32 on QCS8550 (Proxy) | 5.966 ms &middot; 100.7 MB peak &middot; NPU layers 100.0% | - |
| yolo11n w8a8 on Dragonwing RB3 Gen 2 Vision Kit | 12.792 ms &middot; 16.7 MB peak &middot; NPU layers 100.0% &middot; boxes vs local FP32: recall 100.0%, precision 93.5% | - |
| yolo11n w8a8 on QCS8550 (Proxy) | 2.695 ms &middot; 118.9 MB peak &middot; NPU layers 100.0% | - |
| yolo26n fp32 on Dragonwing RB3 Gen 2 Vision Kit | 138.049 ms &middot; 64.2 MB peak &middot; NPU layers 5.4% &middot; boxes vs local FP32: recall 100.0%, precision 100.0% | - |
| yolo26n fp32 on QCS8550 (Proxy) | 5.775 ms &middot; 125.3 MB peak &middot; NPU layers 100.0% | - |
| yolo26n w8a8 on Dragonwing RB3 Gen 2 Vision Kit | 13.883 ms &middot; 18.9 MB peak &middot; NPU layers 100.0% &middot; boxes vs local FP32: recall 100.0%, precision 93.5% | - |
| yolo26n w8a8 on QCS8550 (Proxy) | 2.904 ms &middot; 115.8 MB peak &middot; NPU layers 100.0% | - |
| yolo11s fp32 on Dragonwing RB3 Gen 2 Vision Kit | 239.254 ms &middot; 107.9 MB peak &middot; NPU layers 5.3% &middot; boxes vs local FP32: recall 100.0%, precision 100.0% | - |
| yolo11s fp32 on QCS8550 (Proxy) | 7.033 ms &middot; 116.7 MB peak &middot; NPU layers 100.0% | - |
| yolo11s w8a8 on Dragonwing RB3 Gen 2 Vision Kit | 10.986 ms &middot; 26.2 MB peak &middot; NPU layers 100.0% &middot; boxes vs local FP32: recall 100.0%, precision 85.7% | - |
| yolo11s w8a8 on QCS8550 (Proxy) | 3.53 ms &middot; 147.7 MB peak &middot; NPU layers 100.0% | - |

<details><summary>commands</summary>

```bash
python tools/aihub_profile.py --models ../models/yolo11n.onnx ../models/yolo26n.onnx ../models/yolo11s.onnx --devices Dragonwing RB3 Gen 2 Vision Kit QCS8550 (Proxy) --calib C:/Users/VANJAN~1/AppData/Local/Temp/claude/C--Users-VANJANGI-VINITH/ae698255-07c8-4a12-afc5-065bc75017dc/scratchpad/calib/images/val
```

</details>

## Still missing

* **Bucket B is empty.** We have no recording from a real shop or canteen yet, so queue wait time and shelf stock level have no real-world accuracy number. No public dataset covers either (see `research/09b`), which is exactly why our own footage matters and why the reference-based shelf method exists.
* Raspberry Pi 5 numbers: every speed figure here is from a laptop.
* Qualcomm: only AI Hub hosted devices (bucket Q); no Qualcomm board of our own yet.
