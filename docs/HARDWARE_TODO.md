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
