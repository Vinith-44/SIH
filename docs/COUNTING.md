# Entry / exit counting

**Owner:** Person A · **Milestone:** M1 (counting v2) · **Code:** `storemind/storemind/analytics/footfall.py`,
`tracking/tracker.py`, `inference/filters.py`, `analytics/staff.py`, `fusion/beam.py` ·
**Evaluation:** `storemind/storemind/eval/bakeoff.py` → `eval/results/tracker_bakeoff.md`

## 1. What it does

A camera looks over the door and a line is drawn across it (`tools/calibrate.py`). The pipeline
follows each tracked person's **foot point** (bottom-centre of the box). Crossing the line one way is
an `ENTRY`; crossing the other way is an `EXIT`.

Two counters exist, chosen per camera by `line.mode`:

| | `single` (v1) | `gate` (v2, used in `demo.yaml`) |
|---|---|---|
| Band | ± `margin_px` hysteresis around the line | a gate `gate_px` wide = two parallel lines A and B; a crossing must go from beyond A to beyond B |
| Checks | per-track cooldown | cooldown + **one count per track per direction** + optional minimum track age, direction check (`off`/`balanced`/`strict`), minimum displacement |
| Confirmation | none | `confirm_s`: the person must stay on the far side (or walk out of view there) before the count is committed; stepping straight back cancels it |
| Explains itself | no | `counter.rejected` tallies every refused crossing by reason |

**Per-zone detection filters** (`cameras[].filters`) run before tracking. They drop detections
that are reliably wrong in one place: a mannequin, a poster, a reflection. Each filter sets a
minimum score, a min/max box area and allowed classes, and applies only inside its polygon.

**Staff exclusion** (`cameras[].staff`) marks a track as staff when it stays in a staff zone
(cashier side, stock-room door) for `zone_dwell_s`, or when it wears a printed **ArUco badge**.
Staff tracks are excluded from footfall, zones, the heat map and the queue. The shelf occlusion
gate still sees them. Only one boolean is kept per session-random track id, and the badge id is
dropped after the check. The system identifies "staff", never *which* staff.

**Trackers** (`tracker.type`) come from the Apache-2.0 `trackers` package: ByteTrack (default),
OC-SORT, BoT-SORT (no ReID, no camera-motion compensation) and SORT. The config schema rejects
trackers that re-identify people by appearance (privacy rule).

## 2. IR break-beam cross-check (M1 logic, hardware in M5)

Two IR beams across the door give exact crossings (`$D` → `BEAM_CROSS`). Set `line.beam_door` on
the entrance line and the pipeline:

- matches each beam crossing to a camera crossing in the same direction within 2 s, and reports
  **agreement = 2 × matched / (beam + camera crossings)** per door;
- alerts "entrance camera and door beam disagree … check entrance calibration" when agreement falls
  below 80% over the last 30 crossings (after at least 10 crossings);
- **takes over counting** while the camera is unhealthy (`CAMERA_HEALTH` not `ok`, or the image
  tamper detector fires). It publishes `ENTRY`/`EXIT` with `line: "beam:<door>"` and `track: -1`, so
  footfall and the queue forecast keep running.

The evaluation below shows that camera geometry matters more than any global setting. **The beam
is therefore the per-site tuning signal**:

1. Set up the camera.
2. Walk 30 crossings.
3. Adjust `gate_px` and the line placement until agreement is high.

Steps: `docs/HARDWARE_TODO.md`.

## 3. Evaluation (CAVIAR, bucket A): read this before quoting a number

**Protocol** (`eval/bakeoff.py` docstring):
- The detector runs once per clip on the GPU and its boxes are cached (`eval/detcache.py`). Every
  variant replays the same boxes, so only the component under test changes.
- **2-fold cross-validation over the two camera views.** Detector × tracker × counter settings
  (8 × 4 × 73 = 2,336 combinations) are chosen on one view and scored on the other. Every one of
  the 51 ground-truth crossings is scored by a setting chosen without it.

| | entries (truth 30) | exits (truth 21) | entry acc | exit acc | entry F1 | exit F1 |
|---|---|---|---|---|---|---|
| Before M1: YOLO11n@640 + ByteTrack + v1 counter | 33 | 24 | 90.0% | 85.7% | 0.89 | 0.76 |
| **Counting v2 procedure, held out (CV)** | 31 | 16 | 96.7% | **76.2%** | 0.82 | 0.76 |

**Findings, stated plainly:**

1. **The M1 target (exit accuracy ≥ 90% on held-out data) is not met.** Tuned v2 turns the old
   over-count into an under-count. Exit precision rises (0.71 → 0.88) but recall falls
   (0.81 → 0.67), and held-out F1 does not improve.
2. **Settings do not transfer between the two views.** Each fold's winner scores about 0.9–0.99 on
   the view it was tuned on and 0.61–0.85 on the other. Both folds agree on only four choices:
   ByteTrack, the gate counter, direction check off and no minimum age. Those are now the defaults,
   plus a 0.5 s confirmation.
3. **The detector matters more than the tracker.**
   - **YOLO26n**, which is the same size as YOLO11n, raises front-view IDF1 from 0.47 to 0.62–0.65.
     That makes it a free candidate for the Pi, but M8 must confirm the speed.
   - YOLO11s raises corridor IDF1 to 0.80 but costs about 3× the compute. **It does not ship on
     the Pi.** CPU-only on this laptop (i5-HX, `STOREMIND_DEVICE=cpu`, vtest.avi, 100 frames,
     640 px) it runs at **3.75 FPS** (266 ms/frame), against 10.0 FPS for YOLO11n and 10.8 FPS
     for YOLO26n. A Pi 5 CPU is slower than this laptop, so YOLO11s cannot reach the 8 FPS
     entrance budget there. This is an estimate: M8 measures on the Pi. YOLO11s is recorded as
     the **Qualcomm NPU option** (M9: profile it on the QCS6490 proxy in AI Hub).
     **Shipped detector: YOLO11n@640** (unchanged).
   - The four trackers are within 0.02 IDF1 of each other on the corridor view.
4. **The front view is tiny** (10 entries, 8 exits), so one crossing moves its exit accuracy by
   12.5 points. CAVIAR is 384×288 footage of a Portuguese mall, not a kirana. Only our own
   recordings (bucket B) can say how the system does in a kirana.
5. **Protocol history.** A first run (`tracker_bakeoff_run1.md`) used a rule fixed in advance:
   "tracker by corridor IDF1, report the front". It picked SORT on a 0.002 IDF1 margin. The
   protocol was changed to cross-validation *after* run 1 had been seen. Both runs are published.

The configuration tuned on all 16 clips (YOLO11s@640 + ByteTrack + gate 8 px + 0.5 s confirm)
scores 28/30 entries and 21/21 exits on those same clips. **That in-sample result is not an
accuracy claim.** It is the number you get by grading on the answers. It also needs YOLO11s, which
the Pi cannot run fast enough (finding 3).

**What ships:** YOLO11n@640 + ByteTrack + gate counter with the defaults above. No further
tuning on CAVIAR: the cross-validation shows it would fit the two views, not stores.

## 4. Configuration

```yaml
tracker:
  type: bytetrack            # bytetrack | ocsort | botsort | sort | simple
cameras:
  - name: entrance
    line:
      a: [0.02, 0.50]
      b: [0.98, 0.50]
      entry_direction: pos
      mode: gate             # single = v1
      gate_px: 16            # ~3% of frame height
      confirm_s: 0.5
      direction_mode: off    # off | balanced | strict
      min_track_age_s: 0.0
      beam_door: door1       # optional: IR beam cross-check
    filters:
      - points: [[0.0, 0.0], [0.2, 0.0], [0.2, 1.0], [0.0, 1.0]]   # mannequin corner
        min_score: 0.6
    staff:
      zones: [[[0.7, 0.0], [1.0, 0.0], [1.0, 0.4], [0.7, 0.4]]]     # cashier side
      badge: true
      badge_ids: [7, 8, 9]
```

Print badges with `cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 7, 400)`,
at least 6 cm wide, on a lanyard card. Whether the camera detects a badge depends on its
resolution and distance, so check on site.

## 5. Reproduce

```
cd storemind
.venv/Scripts/python -m storemind.eval.detcache                                   # YOLO11n@640, ~15 min on the RTX 4050
.venv/Scripts/python -m storemind.eval.detcache --model ../models/yolo26n.pt --imgsz 640
.venv/Scripts/python -m storemind.eval.detcache --model ../models/yolo11n.pt --imgsz 960
.venv/Scripts/python -m storemind.eval.detcache --model ../models/yolo11s.pt --imgsz 640
.venv/Scripts/python -m storemind.eval.bakeoff                                    # ~45 min
```

## Sources
- research/23 §3.1 (counting problems and fixes).
- DeepStream `nvdsanalytics` direction modes:
  https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvdsanalytics.html
- Trackers: https://github.com/roboflow/trackers (Apache-2.0). Metric implementations come from
  `trackers.eval` (TrackEval-compatible CLEAR / Identity).
- CAVIAR: EC Funded CAVIAR project / IST 2001 37540, https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/
- Staff badge idea: Xovis staff exclusion, https://www.xovis.com/technology/sensor/ai-extensions/staff-exclusion
