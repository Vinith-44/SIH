# M1 tracker bake-off and counter tuning (CAVIAR, bucket A)

Protocol v2 (2-fold cross-validation over the two views). Every row replays the same cached YOLO11n detections. Run 1 and the reason the protocol changed: `tracker_bakeoff_run1.md` and the docstring of `eval/bakeoff.py`.

## Headline: held-out (cross-validated) vs today's default

| | entries (truth) | exits (truth) | entry acc | exit acc | entry P/R/F1 | exit P/R/F1 |
|---|---|---|---|---|---|---|
| YOLO11n@640 + ByteTrack + v1 counter (today) | 33 (30) | 24 (21) | 90.0% | 85.7% | 0.85/0.93/0.89 | 0.71/0.81/0.76 |
| **counting v2, held-out (CV)** | 31 (30) | 16 (21) | 96.7% | 76.2% | 0.81/0.83/0.82 | 0.88/0.67/0.76 |

## Folds

| tune on | test on | chosen detector | chosen tracker | chosen counter | tune F1 | test F1 |
|---|---|---|---|---|---|---|
| corridor | front | yolo26n@640 c0.15 | bytetrack | mode=gate, gate_px=12.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5 | 0.99 | 0.61 |
| front | corridor | yolo11n@960 c0.25 | bytetrack | mode=gate, gate_px=8.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.0 | 0.90 | 0.85 |

## Detectors x trackers (descriptive, v1 counter)

| detector | tracker | view | IDF1 | IDSW | MOTA | entry F1 | exit F1 |
|---|---|---|---|---|---|---|---|
| yolo11n@640 c0.25 | bytetrack | corridor | 0.749 | 24 | 0.73 | 0.88 | 0.83 |
| yolo11n@640 c0.25 | bytetrack | front | 0.468 | 22 | 0.11 | 0.90 | 0.62 |
| yolo11n@640 c0.25 | ocsort | corridor | 0.754 | 28 | 0.69 | 0.93 | 0.92 |
| yolo11n@640 c0.25 | ocsort | front | 0.524 | 17 | 0.40 | 0.82 | 0.77 |
| yolo11n@640 c0.25 | botsort | corridor | 0.763 | 32 | 0.73 | 0.90 | 0.83 |
| yolo11n@640 c0.25 | botsort | front | 0.498 | 27 | 0.43 | 0.82 | 0.77 |
| yolo11n@640 c0.25 | sort | corridor | 0.765 | 37 | 0.73 | 0.88 | 0.83 |
| yolo11n@640 c0.25 | sort | front | 0.429 | 39 | -0.17 | 0.76 | 0.60 |
| yolo11n@640 c0.15 | bytetrack | corridor | 0.771 | 24 | 0.73 | 0.88 | 0.83 |
| yolo11n@640 c0.15 | bytetrack | front | 0.459 | 25 | -0.05 | 0.67 | 0.67 |
| yolo11n@640 c0.15 | ocsort | corridor | 0.754 | 28 | 0.69 | 0.93 | 0.92 |
| yolo11n@640 c0.15 | ocsort | front | 0.524 | 17 | 0.40 | 0.82 | 0.77 |
| yolo11n@640 c0.15 | botsort | corridor | 0.743 | 31 | 0.73 | 0.90 | 0.83 |
| yolo11n@640 c0.15 | botsort | front | 0.523 | 27 | 0.39 | 0.82 | 0.77 |
| yolo11n@640 c0.15 | sort | corridor | 0.724 | 53 | 0.71 | 0.88 | 0.83 |
| yolo11n@640 c0.15 | sort | front | 0.374 | 42 | -0.67 | 0.72 | 0.67 |
| yolo26n@640 c0.25 | bytetrack | corridor | 0.792 | 25 | 0.72 | 0.90 | 0.96 |
| yolo26n@640 c0.25 | bytetrack | front | 0.620 | 14 | 0.50 | 0.82 | 0.40 |
| yolo26n@640 c0.25 | ocsort | corridor | 0.756 | 23 | 0.68 | 0.93 | 0.92 |
| yolo26n@640 c0.25 | ocsort | front | 0.454 | 21 | 0.37 | 0.75 | 0.22 |
| yolo26n@640 c0.25 | botsort | corridor | 0.736 | 37 | 0.71 | 0.93 | 0.92 |
| yolo26n@640 c0.25 | botsort | front | 0.518 | 23 | 0.46 | 0.82 | 0.40 |
| yolo26n@640 c0.25 | sort | corridor | 0.770 | 41 | 0.71 | 0.90 | 0.96 |
| yolo26n@640 c0.25 | sort | front | 0.603 | 28 | 0.46 | 0.82 | 0.67 |
| yolo26n@640 c0.15 | bytetrack | corridor | 0.772 | 28 | 0.70 | 0.90 | 0.93 |
| yolo26n@640 c0.15 | bytetrack | front | 0.645 | 16 | 0.52 | 0.89 | 0.40 |
| yolo26n@640 c0.15 | ocsort | corridor | 0.756 | 23 | 0.68 | 0.93 | 0.92 |
| yolo26n@640 c0.15 | ocsort | front | 0.454 | 21 | 0.37 | 0.75 | 0.22 |
| yolo26n@640 c0.15 | botsort | corridor | 0.739 | 37 | 0.71 | 0.93 | 0.92 |
| yolo26n@640 c0.15 | botsort | front | 0.543 | 24 | 0.47 | 0.82 | 0.40 |
| yolo26n@640 c0.15 | sort | corridor | 0.731 | 46 | 0.70 | 0.90 | 0.93 |
| yolo26n@640 c0.15 | sort | front | 0.557 | 35 | 0.27 | 0.89 | 0.71 |
| yolo11n@960 c0.25 | bytetrack | corridor | 0.761 | 26 | 0.72 | 0.83 | 0.83 |
| yolo11n@960 c0.25 | bytetrack | front | 0.515 | 19 | 0.37 | 0.95 | 0.77 |
| yolo11n@960 c0.25 | ocsort | corridor | 0.735 | 32 | 0.68 | 0.93 | 0.92 |
| yolo11n@960 c0.25 | ocsort | front | 0.359 | 21 | 0.30 | 0.57 | 0.67 |
| yolo11n@960 c0.25 | botsort | corridor | 0.755 | 42 | 0.72 | 0.93 | 0.88 |
| yolo11n@960 c0.25 | botsort | front | 0.447 | 37 | 0.36 | 0.82 | 0.77 |
| yolo11n@960 c0.25 | sort | corridor | 0.757 | 35 | 0.72 | 0.83 | 0.80 |
| yolo11n@960 c0.25 | sort | front | 0.452 | 31 | 0.12 | 0.89 | 0.75 |
| yolo11n@960 c0.15 | bytetrack | corridor | 0.779 | 27 | 0.72 | 0.84 | 0.86 |
| yolo11n@960 c0.15 | bytetrack | front | 0.548 | 14 | 0.29 | 0.90 | 0.71 |
| yolo11n@960 c0.15 | ocsort | corridor | 0.735 | 32 | 0.68 | 0.93 | 0.92 |
| yolo11n@960 c0.15 | ocsort | front | 0.359 | 21 | 0.30 | 0.57 | 0.67 |
| yolo11n@960 c0.15 | botsort | corridor | 0.746 | 41 | 0.72 | 0.90 | 0.85 |
| yolo11n@960 c0.15 | botsort | front | 0.488 | 34 | 0.38 | 0.82 | 0.77 |
| yolo11n@960 c0.15 | sort | corridor | 0.728 | 56 | 0.70 | 0.84 | 0.89 |
| yolo11n@960 c0.15 | sort | front | 0.425 | 28 | -0.25 | 0.73 | 0.53 |
| yolo11s@640 c0.25 | bytetrack | corridor | 0.774 | 27 | 0.72 | 0.95 | 0.90 |
| yolo11s@640 c0.25 | bytetrack | front | 0.549 | 21 | 0.17 | 0.67 | 0.78 |
| yolo11s@640 c0.25 | ocsort | corridor | 0.795 | 26 | 0.71 | 0.93 | 0.89 |
| yolo11s@640 c0.25 | ocsort | front | 0.514 | 16 | 0.34 | 0.82 | 0.86 |
| yolo11s@640 c0.25 | botsort | corridor | 0.776 | 36 | 0.72 | 0.93 | 0.89 |
| yolo11s@640 c0.25 | botsort | front | 0.527 | 28 | 0.32 | 0.70 | 0.71 |
| yolo11s@640 c0.25 | sort | corridor | 0.768 | 35 | 0.72 | 0.95 | 0.90 |
| yolo11s@640 c0.25 | sort | front | 0.472 | 38 | -0.18 | 0.64 | 0.74 |
| yolo11s@640 c0.15 | bytetrack | corridor | 0.801 | 22 | 0.72 | 0.95 | 0.86 |
| yolo11s@640 c0.15 | bytetrack | front | 0.533 | 23 | 0.09 | 0.73 | 0.78 |
| yolo11s@640 c0.15 | ocsort | corridor | 0.795 | 26 | 0.71 | 0.93 | 0.89 |
| yolo11s@640 c0.15 | ocsort | front | 0.514 | 16 | 0.34 | 0.82 | 0.86 |
| yolo11s@640 c0.15 | botsort | corridor | 0.774 | 37 | 0.72 | 0.93 | 0.89 |
| yolo11s@640 c0.15 | botsort | front | 0.524 | 28 | 0.28 | 0.70 | 0.71 |
| yolo11s@640 c0.15 | sort | corridor | 0.768 | 41 | 0.71 | 0.95 | 0.86 |
| yolo11s@640 c0.15 | sort | front | 0.404 | 36 | -0.66 | 0.64 | 0.70 |

## What ships

**yolo11s@640 c0.25 + bytetrack**, `mode=gate, gate_px=8.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5` - chosen the same way on all 16 clips.

| | entries (truth) | exits (truth) | entry acc | exit acc | entry P/R/F1 | exit P/R/F1 |
|---|---|---|---|---|---|---|
| shipped, **in-sample** (not an accuracy claim) | 28 (30) | 21 (21) | 93.3% | 100.0% | 0.93/0.87/0.90 | 0.95/0.95/0.95 |

Grid: 2336 (tracker, counter) pairs. Command: `python -m storemind.eval.detcache && python -m storemind.eval.bakeoff`
