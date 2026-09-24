# M1 tracker bake-off and counter tuning (CAVIAR, bucket A)

Same cached YOLO11n detections for every row. Tuned on the **corridor** view; the **front** view is held out and never used for a choice.

## Stage 1: trackers (v1 counter)

| tracker | view | IDF1 | IDSW | MOTA | entries (truth) | exits (truth) | entry F1 | exit F1 |
|---|---|---|---|---|---|---|---|---|
| bytetrack | corridor | 0.75 | 24 | 0.73 | 23 (20) | 16 (13) | 0.88 | 0.83 |
| bytetrack | front | 0.47 | 22 | 0.11 | 10 (10) | 8 (8) | 0.90 | 0.62 |
| ocsort | corridor | 0.75 | 28 | 0.69 | 21 (20) | 13 (13) | 0.93 | 0.92 |
| ocsort | front | 0.52 | 17 | 0.40 | 7 (10) | 5 (8) | 0.82 | 0.77 |
| botsort | corridor | 0.76 | 32 | 0.73 | 22 (20) | 16 (13) | 0.90 | 0.83 |
| botsort | front | 0.50 | 27 | 0.43 | 7 (10) | 5 (8) | 0.82 | 0.77 |
| sort | corridor | 0.76 | 37 | 0.73 | 23 (20) | 16 (13) | 0.88 | 0.83 |
| sort | front | 0.43 | 39 | -0.17 | 11 (10) | 12 (8) | 0.76 | 0.60 |

**Chosen tracker (highest corridor IDF1): sort**

## Stage 2: counter grid on the corridor view (72 settings, top 10)

| setting | event F1 (mean) | total count acc |
|---|---|---|
| gate_px=8.0, min_track_age_s=1.0, direction_mode=off, confirm_s=0.5 | 0.95 | 97.0% |
| gate_px=8.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=8.0, min_track_age_s=0.5, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=0.0, direction_mode=balanced, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=0.5, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=0.5, direction_mode=balanced, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=1.0, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=12.0, min_track_age_s=1.0, direction_mode=balanced, confirm_s=0.5 | 0.94 | 100.0% |
| gate_px=16.0, min_track_age_s=0.0, direction_mode=off, confirm_s=0.5 | 0.94 | 100.0% |

**Chosen:** `{'mode': 'gate', 'gate_px': 8.0, 'min_track_age_s': 1.0, 'direction_mode': 'off', 'confirm_s': 0.5}`

## Stage 3: held-out comparison (same tracker)

| counter | view | entries (truth) | exits (truth) | entry acc | exit acc | entry P/R/F1 | exit P/R/F1 | rejected candidates |
|---|---|---|---|---|---|---|---|---|
| v1 | corridor | 23 (20) | 16 (13) | 85.0% | 76.9% | 0.83/0.95/0.88 | 0.75/0.92/0.83 | - |
| v1 | front (held out) | 11 (10) | 12 (8) | 90.0% | 50.0% | 0.73/0.80/0.76 | 0.50/0.75/0.60 | - |
| chosen | corridor | 19 (20) | 13 (13) | 95.0% | 100.0% | 1.00/0.95/0.97 | 0.92/0.92/0.92 | {'too_young': 1, 'direction': 0, 'displacement': 0, 'repeat': 0, 'cooldown': 0, 'reverted': 5} |
| chosen | front (held out) | 7 (10) | 9 (8) | 70.0% | 87.5% | 1.00/0.70/0.82 | 0.67/0.75/0.71 | {'too_young': 0, 'direction': 0, 'displacement': 0, 'repeat': 2, 'cooldown': 0, 'reverted': 2} |

Command: `python -m storemind.eval.detcache && python -m storemind.eval.bakeoff`
