# StoreMind - measured results

Generated 2026-09-24 13:25 by `python -m storemind.eval.run_all`.

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
| git_commit | c454662 |

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
| shipped configuration (tuned on all clips) | yolo11s@640 c0.25 + bytetrack, `mode=gate, gate_px=8.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5` | - |

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
| queue-length MAE | 0.00 people | <= 1 |
| wait-time MAE | 0.39 s (1.1%) | <= 20% |
| service-time MAE | 0.14 s (0.6%) | - |
| median wait (ours vs truth) | 39.9 s vs 39.5 s | - |

<details><summary>commands</summary>

```bash
python -m storemind.run --config configs/demo.yaml --camera counter-1 --source C:\SIH\videos\queue\synthetic_queue.mp4 --backend scripted --model C:\SIH\videos\queue\synthetic_queue_detections.json --fps 25
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
| ultralytics yolo11n.pt @320 (CPU) | 31.93 ms/frame &middot; 30.89 FPS &middot; 4.34 detections/frame | - |
| ultralytics yolo11n.pt @416 (CPU) | 52.37 ms/frame &middot; 18.94 FPS &middot; 4.44 detections/frame | - |
| ultralytics yolo11n.pt @640 (CPU) | 106.81 ms/frame &middot; 9.33 FPS &middot; 4.73 detections/frame | - |
| ultralytics yolo26n.pt @640 (CPU) | 144.29 ms/frame &middot; 6.9 FPS &middot; 4.73 detections/frame | - |
| litert efficientdet_lite0_coco_legacy.tflite @320 (CPU) | 96.37 ms/frame &middot; 10.22 FPS &middot; 4.2 detections/frame | - |

<details><summary>commands</summary>

```bash
STOREMIND_DEVICE=cpu python -m storemind.eval.benchmark --source ../videos/other/vtest.avi --frames 100
```

</details>

## Still missing

* **Bucket B is empty.** We have no recording from a real shop or canteen yet, so queue wait time and shelf stock level have no real-world accuracy number. No public dataset covers either (see `research/09b`), which is exactly why our own footage matters and why the reference-based shelf method exists.
* Raspberry Pi 5 numbers: every speed figure here is from a laptop.
* Qualcomm AI Hub latency: needs a Qualcomm ID and API token.
