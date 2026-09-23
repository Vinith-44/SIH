# StoreMind - measured results

Generated 2026-09-23 23:43 by `python -m storemind.eval.run_all`.

Every number on this page came from a command printed beside it. Nothing here was typed by hand. If a measurement could not be made, the row says so.

## How to read the data buckets

| bucket | meaning | what it may be used for |
|---|---|---|
| **A** | public benchmark with published ground truth | real accuracy claims |
| **B** | our own field recording, hand-labelled by the team | real accuracy claims |
| **C** | simulation with known ground truth | proving the logic is correct - **never** an accuracy claim |

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
| git_commit | 52470da |

## Results

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
| mean crossing timing error | 0.15 s | - |
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

### Door-to-counter queue forecast (novelty N1) - simulated rush

**Data bucket C** - simulation - logic validation only, NOT an accuracy measurement

Two synchronised 25-minute clips on one timeline with a 6-minute shopping-trip lag built in. The forecaster only ever sees ENTRY events and the counter's own queue events - it is never told the lag. Simulation is the *right* tool here: it is the only way to know the true lag and the true congestion onset exactly.

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

**Data bucket A** - public benchmark with published ground truth

OpenCV's `vtest.avi` sample (Apache-2.0), 768x576, real people in a plaza. It has **no ground truth**, so this measures speed only, never accuracy. Laptop numbers; the Pi 5 must be measured on the Pi.

| metric | result | target |
|---|---|---|
| backend / model / input | infer ms | FPS | det/frame |  |
| ultralytics yolo11n.pt @320 | 151.52 ms | 6.53 FPS | 4.34 det/frame | - |
| ultralytics yolo11n.pt @416 | 235.6 ms | 4.22 FPS | 4.44 det/frame | - |
| ultralytics yolo11n.pt @640 | 479.08 ms | 2.08 FPS | 4.73 det/frame | - |
| ultralytics yolo26n.pt @640 | 421.17 ms | 2.37 FPS | 4.73 det/frame | - |
| litert efficientdet_lite0_coco_legacy.tflite @320 | 19.9 ms | 49.28 FPS | 4.2 det/frame | - |

<details><summary>commands</summary>

```bash
python -m storemind.eval.benchmark --source ../videos/other/vtest.avi --frames 100
```

</details>

## Still missing

* **Bucket B is empty.** We have no recording from a real shop or canteen yet, so queue wait time and shelf stock level have no real-world accuracy number. No public dataset covers either (see `research/09b`), which is exactly why our own footage matters and why the reference-based shelf method exists.
* Raspberry Pi 5 numbers: every speed figure here is from a laptop.
* Qualcomm AI Hub latency: needs a Qualcomm ID and API token.
