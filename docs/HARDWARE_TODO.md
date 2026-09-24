# Hardware to-do

**Owner:** A + B · **Filled in:** ongoing · **Status:** stub (created in PR-0).

## What goes here

Every step that needs hardware we don't have at hand, as an exact checklist a teammate can run (script + expected output), so nothing is claimed that wasn't measured.

## M1 - IR break-beam cross-check at the entrance (Person A logic, Person B hardware)

The logic is done and unit-tested (`storemind/storemind/fusion/beam.py`,
`tests/test_counting_v2.py`); it needs the real beams to produce a bucket-B number.

1. Mount two IR beams across the door, 10-20 cm apart, at hip height (so a bag or a
   child's head does not break only one). Wire per `docs/WIRING.md`; firmware sends `$D`.
2. In the store config, set `line.beam_door: door1` on the entrance camera's line
   (the id the firmware uses in `$D`).
3. Run the pipeline with the sensor bridge (or `sensors.tcp` to the simulator first).
4. Walk 30 crossings by hand (15 in, 15 out, incl. 5 pairs side by side and 3 with a
   trolley/bag), noting each in a paper tally.
5. Record: beam count, camera count, the dashboard agreement %, and the hand tally.
   Put the three counts in `logs/WORK_LOG_A.md` with the date - that is the first
   bucket-B footfall number.
6. Cover the camera lens for 10 crossings: counts must continue from the beam
   (events with `line: beam:door1`), and "camera ... disagree" must not fire while
   the camera is down.

## M8 - detector speed on the Pi 5 (decides YOLO11n vs YOLO26n; confirms YOLO11s is out)

Laptop CPU estimates (bucket S, not Pi numbers): YOLO11n@640 10.0 FPS, YOLO26n@640 10.8 FPS,
YOLO11s@640 3.75 FPS. On the Pi, run `tools/bench_pi.py` (M8) for all three at 640 and 416,
PyTorch and NCNN, and paste the JSON into `logs/WORK_LOG_A.md`. Ship the fastest model that holds
8 FPS on the entrance camera.

## M3 - shelf photos for the real acceptance test (bucket B)

M3 accepts on **EMPTY F1 >= 0.85 in day and evening light on our own shelf**. Nothing is measured until this is done.

1. Pick a real rack (hostel store, canteen, lab) with 4-8 products side by side. Mount a camera (webcam/Pi cam/
   phone on RTSP) so it sees the rack with **no people in frame**.
2. Draw the slots: `python tools/calibrate.py` on a snapshot, save to `configs/myshelf.yaml` (camera name
   `shelf-cam`). Type SKU names.
3. Start capturing, ideally with the STM32 + BH1750 connected for lux:
   `python tools/shelf_capture.py --source 0 --out ../videos/shelf_real/day1 --every 120 --serial COM5`
4. Leave it from morning to night, **including evening light and lights off**. Every 20-30 min change something:
   remove all packets of one product (EMPTY), leave one (LOW), swap in a different product (WRONG_ITEM), then
   restock everything (all FULL).
5. Label: `python tools/shelf_label.py --photos ../videos/shelf_real/day1 --config configs/myshelf.yaml --camera shelf-cam`
   (~5 s per photo; mark lighting 1-4).
6. Score: `python -m storemind.eval.eval_shelf_photos --photos ../videos/shelf_real/day1 --config configs/myshelf.yaml --camera shelf-cam`
   and paste the two lines (v1, v2) into `logs/WORK_LOG_A.md`. Do not tune on this day; capture a second day to tune.

## M4 - canteen queue clip (bucket B, the real queue acceptance test)

M4 accepts on **queue MAE <= 1 and wait error <= 20% on our own clip**. Nothing is measured until this is done.

1. Get written permission (canteen manager) and put up the DPDP notice. Frames are processed in RAM, but this
   clip is a recording, so keep it on the laptop only (`videos/` is git-ignored) and delete it after labelling.
2. Record 15-20 min of a busy billing counter from above/behind (sub-stream is fine: 640x360, 8-10 FPS),
   including a rush. Save as `../videos/queue/canteen_01.mp4`.
3. Calibrate: `python tools/calibrate.py` -> lane (or `lane_polyline` if the queue bends) + billing spot, with
   `membership: dwell`.
4. Label with `python tools/label_ground_truth.py` (queue length every 10 s; for 20+ customers: joined,
   service start, service end). Mark families as parties in a note.
5. Score: `python -m storemind.eval.eval_queue --config configs/canteen.yaml --camera counter-1
   --source ../videos/queue/canteen_01.mp4`. Run it with `membership: polygon` and with `membership: dwell`,
   and paste both into `logs/WORK_LOG_A.md`. Do not tune on this clip; record a second one to tune.

## M6 - MEMS + load cell on the real shelf (Vinith's fusion, Ram's board)

Acceptance (CLAUDE_CODE_PROMPT_V2 M6): picks and put-backs detected >= 90% with weight gating; fewer than 1 false
TOUCH per 10 min idle; at least 9 of 10 camera knocks detected. Needs Ram's firmware (`$M`, `$W`) and bridge.

1. MEMS node glued flat under the shelf; load cells under 2 slots (`sensors.cell_map`). Enter the pack weight
   of each slot as `slots[].unit_grams`.
2. Run the pipeline with the bridge and a camera on the shelf front (so `person_at_shelf` works).
3. Follow `tools/mems_test.py` (Ram, M6): 30 picks (1-2 packs), 10 put-backs, 10 touches without taking anything,
   10 accidental bumps, 10 knocks on the camera bracket, and 10 min with nobody near. Keep a paper tally.
4. Compare the tally with the `PICKUP` / `SHRINK_FLAG` events and alerts in the dashboard log; paste the counts
   into `logs/WORK_LOG_A.md`. Do not retune `noise_g` or `settle_timeout_s` on this run; run it a second time to tune.

## M8 - detector speed and energy on the Raspberry Pi 5 (bucket S; decides the shipped detector)

Needs: the Pi set up per `docs/SETUP_PI5.md` (Ram, M8-deploy), the official 27 W supply, active cooler, nothing else running.

1. Copy the exported models to the Pi's `models/` folder: `yolo11n.onnx`, `yolo11n_int8.onnx`, `yolo11n_ncnn_model/`,
   and the same three for yolo26n. The files are git-ignored: copy them with scp or a USB stick.
2. `pip install ncnn` in the Pi venv (for the NCNN backend).
3. From `storemind/`, run:
   `python tools/bench_pi.py --models ultralytics:../models/yolo11n_ncnn_model onnx:../models/yolo11n_int8.onnx onnx:../models/yolo11n.onnx ultralytics:../models/yolo26n_ncnn_model onnx:../models/yolo26n_int8.onnx onnx:../models/yolo26n.onnx --frames 200`
   Then repeat with `--imgsz 416` (entrance sub-streams are small; 416 may be enough).
4. Commit the JSON files it writes to `storemind/storemind/eval/results/pi/`. RESULTS.md picks them up.
5. Vinith then ships the fastest variant that holds **>= 8 FPS** with the least CAVIAR accuracy loss
   (eval/results/model_export.md) in the Pi config.
