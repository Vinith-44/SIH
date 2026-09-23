# StoreMind (SIH 2026 · PS 26179 · Qualcomm) — research pack

Team TechGladiators, BVRIT. Prepared 23 Sep 2026. Everyone on the team (and any AI assistant you share this folder with) should read this file first.

## The 60-second summary

1. **Our current code works as a demo but can't win yet.** Three separate laptop scripts, no accuracy numbers, a queue predictor that raises false alarms (it predicted 78 people from a queue of 1), a shelf model that over-fits (validation loss rose from 1.19 to 2.77), and a PPT with claims the code doesn't do (blurring, STM32 running CV) and a DEMO dashboard as "Results". → `01_PROJECT_AUDIT.md`
2. **The XLink repo that scared us is not far ahead.** YOLO + ByteTrack + threshold, no prediction, no shelf model in the repo, no sensors, no numbers. Copy their engineering hygiene; beat them on novelty. → `02_REFERENCE_REPO_COMPARISON.md`
3. **Our novelty (build these):** → `03_NOVELTY_AND_FEATURES.md`
   - **Door-to-counter queue forecast:** entrance counts + Erlang-C queueing model → "open counter 3 in ~6 min" *before* the queue forms.
   - **Label-free shelf & planogram monitoring:** reference snapshots + occlusion gate + voting → solves our "no data" problem.
   - **Camera + weight + ToF fusion on the STM32 FreeRTOS node:** hidden depletion, loose-grain bins, shrinkage.
   - **₹ lost-sales metric + local-language staff alerts** (voice, tower light, phone).
   - **Qualcomm alignment:** profile our models on real Qualcomm chips via Qualcomm AI Hub; plan a Qualcomm board.
4. **Hardware:** Raspberry Pi 5 is the main node. Jetson Nano (old) only as an optional 2nd node — its software is end-of-life and it's NVIDIA in a Qualcomm PS. STM32 = real-time sensing/actuation, **never** vision. → `05_HARDWARE_PLAN.md`
5. **Data:** we don't need to train a people detector — we need **evaluation videos** with hand-counted ground truth, plus SKU-110K/gap datasets and our own demo shelf. → `06_AI_MODELS_AND_DATA.md`

## Files

| File | What's inside |
|---|---|
| `01_PROJECT_AUDIT.md` | Every bug/weakness in our code, models, notebooks and PPT, with fixes |
| `02_REFERENCE_REPO_COMPARISON.md` | XLink repo teardown and side-by-side |
| `03_NOVELTY_AND_FEATURES.md` | Market landscape, 9 novelty pillars ranked, tested Erlang-C code, PS mapping |
| `04_ARCHITECTURE.md` | Layered architecture, services, event schema, MQTT topics, SQLite schema, compute budget, code layout |
| `05_HARDWARE_PLAN.md` | Board comparison (Pi 5 / Hailo / Jetson / Qualcomm), STM32 role, sensors, BOM, **camera connection guide** |
| `06_AI_MODELS_AND_DATA.md` | Models per module, licences, datasets, 3-day shelf-dataset plan, training + AI Hub commands, evaluation plan |
| `07_NETWORKING_AND_PROTOCOLS.md` | RTSP/ONVIF/MQTT/UART/RS-485/Modbus/WebSocket/mDNS, bandwidth math, offline-first rules, security |
| `08_ROADMAP_AND_TASKS.md` | Phase plan, 6-person task split, 4-minute live demo script |
| `09_PPT_GUIDE.md` | Slide-by-slide rewrite, corrected references, judge Q&A |
| `diagrams/storemind_architecture.png` | Architecture diagram for slide 3 (SVG/HTML source alongside) |
| `tools/camera_check.py` | Test any camera (Pi CSI, USB, CCTV RTSP, phone, video file): real FPS, lag, calibration snapshot |
| `RESEARCH_LOG.md` | What was checked, how, and the sources |

## Do these first (this week)

1. Record 3 short test videos (entrance, queue, shelf) and hand-count them.
2. Swap in YOLO + ByteTrack + foot-point counting; fix the queue predictor; measure accuracy.
3. Sign up for Qualcomm AI Hub and profile the person model on a QCS6490 device.
4. Rebuild the PPT with the new architecture diagram, novelty bullets and **measured** numbers.

## Rules for the team (and for any AI helping us)

- Nothing goes on a slide unless it runs and has a measured number.
- STM32 never does vision; the edge box never does µs-level real-time I/O.
- No video or face data is ever written to disk.
- Every module talks through events (see `04_ARCHITECTURE.md` §4) — no hidden coupling.
- Every engine must run in replay mode on a recorded video.
