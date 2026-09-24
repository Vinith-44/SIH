# StoreMind — Build Plan v2 for Claude Code (software + hardware-integration pipelines)

Written 24 Sep 2026 from `research/23_PIPELINE_DEEP_RESEARCH.md` and `research/24_CCTV_INTEGRATION.md`.
Claude Code: read this whole file first, then work milestone by milestone. **This file is the contract.**

---

## 0. Situation (read before touching code)

**Read, in this order:**
1. `C:\SIH\CLAUDE.md`
2. `C:\SIH\HANDOFF_FOR_CLAUDE.md` and `C:\SIH\WORK_LOG.md` (history), then **`research/26_TEAM_SPLIT_AND_INTEGRATION.md` — two people work in parallel; do ONLY your person's milestones (A or B) and follow its contract and git rules**
3. `C:\SIH\storemind\storemind\eval\results\RESULTS.md` (current numbers)
4. `C:\SIH\research\23_PIPELINE_DEEP_RESEARCH.md` (§2 target pipeline, §3 problem playbook, §4 hardware, §5 Qualcomm, §6 reliability, §9 build order)
5. `C:\SIH\research\24_CCTV_INTEGRATION.md` (camera ingest design, URL cheat-sheet, onboarding)
6. `C:\SIH\StoreMind_Advanced_BluePill_FreeRTOS_Guide.pdf` (the team's STM32 pin map and task plan; follow it unless research/23 §4.2 says otherwise, and document every difference)

**Hardware the team has NOW**

| Item | Status |
|---|---|
| Windows laptop (dev) | yes |
| **Raspberry Pi 5** | yes (the edge box) |
| **STM32F103C8 "Blue Pill"** + FreeRTOS | yes |
| **Sensor set (from the hardware team's FreeRTOS guide, updated 24 Sep)** | **Load cell + HX711**, **MEMS accelerometer/IMU (replaces VL53 ToF)**, **IR break-beam**, **PIR**, **BH1750** (light), **BME280** (temp/humidity/pressure), **Buzzer + LED**, **Servo (optional)**, restock button (optional). **LD2450 radar is optional/not purchased — code it, keep it disabled by default.** Team confirms quantities in `docs/HARDWARE_INVENTORY.md`. Every sensor driver must be switchable in config so a missing sensor never breaks anything |
| **Each sensor's job (rule from the team guide: every sensor must resolve an ambiguity another modality can't)** | HX711 → *how much* left the shelf · MEMS → *when* the shelf was touched/bumped + clean weight gating + camera tamper · IR beam → *precise* entry/exit crossing, cross-checks the camera count · PIR → coarse presence: wake-up/motion gate for cameras and after-hours intrusion alert · **BH1750 → tells the shelf engine whether a change is a *lighting* change or a real *empty slot*** (selects the lighting reference, marks "too dark → UNKNOWN") · BME280 → environment panel + perishable-zone (dairy/grains) and edge-box temperature alerts · Buzzer+LED → local alerts · Servo → demo-only mock gate/indicator |
| College CCTV | **permission available**; cameras are wired (brand/DVR model not yet known). Build for analog DVR **and** IP NVR |
| Qualcomm board (RUBIK Pi 3 / QCS6490) | **NO** (too expensive). Use **Qualcomm AI Hub hosted devices** (free) + a documented, ready-to-run port path |
| ESP32-S3 shelf cameras | **pending (team confirms this evening)**. Build the software interface + simulator now; firmware only after confirmation |
| **MEMS sensor (team decision, 24 Sep)** | **MEMS accelerometer/IMU replaces the ToF sensors. There is NO microphone / UPI chime detector** (ignore research/23 §3.2 "chime" and §4.3 — superseded). Default chip **MPU6050 (GY-521)**, alternative **ADXL345**; team confirms the exact part in `docs/HARDWARE_INVENTORY.md`. Jobs: shelf **interaction (pick/touch) events**, **vibration gate for load-cell readings**, **camera-mount tamper/bump detection**, shelf **tilt/knock** alerts. Note: a MEMS accelerometer does not measure stock level (ToF did) — stock level comes from camera + load cell |

**Secrets (hard rule)**
- Qualcomm AI Hub token: the team configures it themselves with `qai-hub configure --api_token <TOKEN>` (stored in the user's home folder). Code reads it from the qai-hub client config or env `QAI_HUB_API_TOKEN`. **Never write the token into any file in the repo, log, doc or commit.**
- Camera usernames/passwords: `configs/secrets.yaml` (git-ignored) or env vars; `configs/secrets.example.yaml` committed with placeholders.
- Add a pre-commit check (or a test) that fails if a token-like string or `secrets.yaml` is staged.

**Ground rules**
- One milestone at a time, **one branch per milestone**, PR into `master` (the repo requires approval). No force-push, no history rewrite, never commit video/model/media files.
- After every milestone: run all tests → (Person A) re-run `python -m storemind.eval.run_all` and update `RESULTS.md` / (Person B) write results to `eval/results/platform/` → update your own `logs/WORK_LOG_<A|B>.md`, `handoff/HANDOFF_<A|B>.md`, and the milestone's doc in `docs/` → commit → open PR for the other person to approve.
- **Honest labels on every number:** A = public benchmark, B = our own recording, C = simulation, S = speed only, **Q = Qualcomm AI Hub hosted/proxy device**, **P = published third-party figure (cite)**. Never tune on the clips you report (CAVIAR: tune on corridor view, report on front view).
- **Never invent hardware results.** If a step needs hardware we don't have, write the code, a simulator and a hardware test script, and add the exact manual steps to `docs/HARDWARE_TODO.md`.
- Privacy: no faces, no identity, no stored video, no audio capture at all. Frames live in RAM only. Shelf reference crops are allowed (shelves, not people). Dashboard shows "video stored: 0 bytes".
- Don't modify the team originals: `sihfinal.pptx`, the three extension-less ZIPs, the FreeRTOS PDF, the `.txt` files.
- Keep the Pi 5 target in mind: Python 3.11, Raspberry Pi OS Bookworm 64-bit, no heavy deps in the core path.
- If near the usage limit: stop at a clean point, update the handoff, commit.

---

## 1. Milestones (in this order)

### M0 — Repo hygiene + documentation skeleton (short)
- `git status`, confirm clean state, create branch.
- Create `docs/` with these files (short stubs now, filled by each milestone):
  `ARCHITECTURE.md` (pipeline diagram from research/23 §2, updated), `CONFIG_REFERENCE.md`, `CCTV_ONBOARDING.md`, `PROTOCOL.md`, `WIRING.md`, `FIRMWARE.md`, `SETUP_PI5.md`, `SHELF.md`, `QUEUE.md`, `COUNTING.md`, `MEMS.md`, `EVALUATION.md`, `QUALCOMM.md`, `PRIVACY_DPDP.md`, `OPERATIONS.md` (systemd, logs, soak, chaos), `TROUBLESHOOTING.md`, `DEMO_RUNBOOK.md`, `HARDWARE_INVENTORY.md`, `HARDWARE_TODO.md`, `TEAM_GUIDE.md` (simple English, for teammates who don't code).
- `docs/README.md` = index: "if you want X, read Y".
- Secrets handling + `.gitignore` check as described above.
- Config schema validation (pydantic) for all YAML configs, with clear error messages.

### M1 — Counting v2 (fixes the exit over-count)  *(research/23 §3.1)*
- Two-line gate / zone sequence A→B, direction-vector check (strict/balanced modes), min displacement (floor coords if calibrated, else pixels), min track age, one count per track per direction, hysteresis kept.
- Per-zone detection filters (min box area, min score, class filter).
- **Tracker bake-off:** ByteTrack vs OC-SORT vs BoT-SORT (no ReID); report IDF1/ID switches/count accuracy; pick default by result.
- Staff exclusion: cashier/back-door zones + ArUco-style printed badge detection (OpenCV `aruco`) → staff tracks excluded, never identified.
- **IR break-beam cross-check:** two beams at the door (`$B`) give direction + exact crossing time; used to validate the camera counter live (agreement % on the dashboard) and as a fallback count if the entrance camera fails. Tested with the simulator now, real beams later.
- **Accept:** CAVIAR exit accuracy ≥ 90% on the held-out view; ID switches reported; tests added.

### M2 — Camera ingest v2 = the CCTV connection layer  *(research/24 §5–§9, research/23 §2)*
- **go2rtc** as the single camera gateway (one connection per camera, restream on :8554, JPEG frame API). MediaMTX for the fake-CCTV test harness. Provide install scripts for Windows (dev) and Pi 5 (ARM64).
- **Camera templates** in `configs/store.yaml`: `brand: hikvision|cpplus|dahua|tapo|onvif|generic`, `ip`, `port`, `channel`, `stream: sub|main`, `role: entrance|counter-N|shelf-X`. URL built automatically from the cheat-sheet in research/24 §3; credentials from secrets.
- **Sources supported:** RTSP (DVR/NVR/IP cam), ONVIF (auto-discover + pick lowest profile ≥ 640 px wide), HTTP snapshot (shelves), Pi Camera Module 3 (picamera2), USB webcam, video file, **ESP32-S3 MQTT JPEG** (interface + simulator only for now), HDMI capture card (V4L2).
- **Tools:**
  - `tools/discover.py`: ONVIF WS-Discovery + port probe (554, 80, 443, 8000, 37777, 2020, 5543) on a user-given subnet only.
  - `tools/probe.py`: ffprobe each URL → codec, resolution, FPS; tries brand URL variants; prints a ready-to-paste YAML block.
  - Keep/extend `tools/camera_check.py` (real FPS + lag) and `tools/calibrate.py` (lines, lanes, billing spot, shelf slots, floor points; works from a go2rtc snapshot).
- **Resilience:** RTSP over TCP, reconnect with exponential backoff, stale-frame watchdog (no frame 5 s → reconnect + health alert), latest-frame-only queues (never build lag), per-camera FPS policy by role (entrance 8–10, counter 3–5, shelf = snapshot every 1–5 min), **motion gate** before the detector (plus **PIR `$P` as a hardware wake-up**: camera zones with no PIR activity drop to 1 FPS; after-hours PIR = intrusion alert), tamper/moved/covered-lens detection, night/IR (greyscale) detection → per-camera threshold switch.
- **Time:** document chrony on the Pi as NTP server for the DVR and the STM32; log clock drift per camera.
- **Health panel:** every camera green/amber/red with FPS, lag, last frame age, reconnect count.
- **Events out:** ONVIF Profile M-style JSON over MQTT (object class, line crossing, zone events) in addition to our own schema.
- **Test harness:** `tools/fake_cctv.py` = MediaMTX + `ffmpeg -re -stream_loop -1` publishing our clips as `rtsp://localhost:8554/<cam>` so the full code path is tested without real CCTV.
- **Stream-density benchmark:** how many cameras one box sustains at target FPS (laptop now, Pi 5 in M8).
- **College onboarding pack** (`docs/CCTV_ONBOARDING.md`): the 15-minute checklist from research/24 §5, plus **"questions to ask college IT"** (DVR/NVR brand + model, analog or IP, IP address, can they create a read-only live-view user, number of channels, which channel sees entrance/counter/shelves, sub-stream settings, any free LAN port), permission-letter template and DPDP notice sign (English/Telugu/Hindi) in `docs/templates/`.
- **Accept:** 3 fake-CCTV streams + 1 local camera (webcam/Pi cam) run 1 h with no lag growth; unplug/replug a stream recovers automatically; tests for URL builder, backoff, watchdog.

### M3 — Shelf monitoring v2 (TOP PRIORITY for quality)  *(research/23 §3.3; current engine `storemind/analytics/shelf.py`)*
Current engine: occlusion gate → edge-density + colour-mass fill vs one reference → HSV+gradient histogram cosine for WRONG_ITEM → K-of-N vote. Known gaps: no lighting normalisation, SSIM computed but unused, hard-coded 0.75 and Canny thresholds, only simulated results (95.8%, bucket C), detector mode has no trained model.
Build:
- **Lighting robustness:** CLAHE on luminance before all measures; **edge/gradient SSIM** used in the decision (not just displayed); auto-Canny (median-based) or config thresholds.
- **Reference bank:** multiple references per slot (day / evening / night), auto-selected by **BH1750 lux (`$E`) when available**, else global brightness/colour-temperature of the frame; lux below a threshold → slot UNKNOWN ("too dark"), never EMPTY; a sudden lux jump suppresses shelf state changes for one cycle; auto re-reference after "Restocked"; slow drift update only when the slot is confidently FULL and unoccluded.
- **Per-slot rectification:** 4-point homography from calibration so oblique CCTV views compare fairly.
- **Glare mask:** ignore saturated specular pixels in all measures.
- **Fusion with weight:** if a slot has a load cell (STM32 `$W`), fuse camera fill + weight fill (weight wins for deep shelves; disagreement → "check shelf" with reason).
- **Shelf sources:** CCTV snapshot (go2rtc frame API / ISAPI / Dahua CGI), Pi Camera, USB cam, file, ESP32-S3 MQTT JPEG (simulated). Same engine for all.
- **Outputs:** slot states EMPTY/LOW/FULL/WRONG_ITEM/UNKNOWN(occluded, dark, camera fault) with reason + confidence; **lost-sales estimate** (shoppers dwelling at an EMPTY slot × price, labelled "estimate"); **reorder draft queue** in SQLite (offline, WhatsApp-text export; ONDC later).
- Everything configurable (thresholds, K-of-N, period) — no magic numbers in code.
- **Own test set (bucket B):** `tools/shelf_capture.py` takes timed photos of a real demo shelf (hostel/canteen/lab rack) at different times of day; `tools/shelf_label.py` lets a teammate mark each slot EMPTY/LOW/FULL/WRONG in a few clicks; `eval/eval_shelf.py` reports per-state precision/recall/F1 split by lighting.
- Stretch (only after the above passes): PatchCore anomaly per slot (anomalib) and the Kaggle SKU-110K detector notebook in `storemind/train/`.
- **Accept:** simulated suite still passes; on our own shelf set EMPTY F1 ≥ 0.85 in both day and evening lighting (if the team hasn't captured it yet, ship the tools + a synthetic lighting-change test and list the capture steps in `HARDWARE_TODO.md`).

### M4 — Queue v2  *(research/23 §3.2)*
- Membership = speed threshold + minimum dwell (≈5 s) before joining; passers-by excluded.
- Party merge (people within ~1 m who leave together = one party); report parties and people.
- Polyline lane / occupancy grid for bent queues; tail-overflow alert.
- Wait time from gap-tolerant timers **and** Little's law W = L/λ cross-check (shown side by side).
- Balk (entered queue area, left within N s) and renege (left before service) KPIs.
- Service end from: customer leaving the billing polygon (default), or radar targets leaving the counter box if the optional LD2450 is fitted (M5). Per-counter μ; Erlang-C recommendation kept.
- Optional radar fusion (only if LD2450 fitted): targets inside the counter box fused with camera queue length when the camera is occluded.
- **Accept:** synthetic + own canteen clip (if recorded): queue MAE ≤ 1, wait error ≤ 20%.

### M5 — STM32 sensor node + Pi integration  *(research/23 §4.2, §4.4; team FreeRTOS PDF)*
**Single source of truth:** `docs/PROTOCOL.md` — every message, field, unit, rate, checksum, example line, and error handling. Code on both sides must match it; a test parses every example in the doc.
- Messages MCU→Pi: `$W` weight (slot, grams), `$M` MEMS event (node, event = TOUCH|SETTLED|TILT|KNOCK|TAMPER, peak mg, RMS mg, duration ms; plus a periodic status with tilt angle), `$B` IR beam (id, edge, ms timestamp; two beams → direction), `$P` PIR (zone, ON/OFF), `$E` environment (lux, °C, %RH, hPa), `$Q` radar targets (optional), `$R` restocked button (shelf), `$H` heartbeat (uptime, free heap, stack high-water per task, error counters incl. I2C). Pi→MCU: `$L` LED pattern, `$Z` buzzer pattern, `$V` servo angle (optional), `$S` time sync, `$C` config (thresholds, tare, calibration).
- **Demo mode:** NMEA-style text + XOR checksum (readable in a serial monitor for judges). **Production mode:** COBS framing + CRC16, same message set. Mode switch by command.
- **Firmware** in `firmware/stm32/` (PlatformIO or STM32CubeIDE project + FreeRTOS), builds with `arm-none-eabi-gcc` in CI even without a board:
  - USART1 (PA9/PA10) ↔ Pi 5 header UART (GPIO14/15, pins 8/10), 115200, 3.3 V, common GND.
  - USART2 (PA2/PA3) ↔ LD2450 (optional, disabled by default) at 256000 baud; parse 30-byte frames `AA FF 03 00 … 55 CC` (no CRC → validate header/tail/length); **zone gating + N consecutive frames** on the MCU.
  - I2C1 (PB6/PB7) **MEMS IMU** (MPU6050 at 0x68/0x69 via AD0 → max 2 per bus; ADXL345 at 0x53/0x1D). More shelves → **TCA9548A I2C mux** (don't use I2C2: on the Blue Pill PB10/PB11 are shared with USART3). Wire the sensor **INT pin to an EXTI** (motion/activity interrupt) so the MCU sleeps the task until something moves. Sample 100–200 Hz on the MCU, compute peak/RMS per 100 ms window **on the MCU** and send **events, not raw samples** (UART bandwidth + 20 KB RAM). Per-node calibration (bias/orientation) at boot, stored in flash.
  - HX711: DOUT falling-edge EXTI → task notification; clock pulses inside a short critical section (PD_SCK high > 60 µs powers it down); tare + calibration command stored in flash.
  - IR beam and PIR on EXTI, timestamp in ISR, defer to the Presence task. Restock button debounced.
  - **BH1750 (0x23/0x5C) and BME280 (0x76/0x77) share I2C1 with the MEMS IMU (0x68/0x69)** — no address clash. **Protect I2C1 with a FreeRTOS mutex** (MEMS task and Environment task both use it) and add an **I2C bus-recovery routine** (STM32F1 I2C can lock BUSY after a glitch: toggle SCL 9×, re-init, count it in `$H`).
  - Buzzer + LED via GPIO (transistor for the buzzer), non-blocking patterns; servo via TIM PWM (optional). IWDG watchdog kicked by the Health task only if all tasks checked in.
  - **Task layout = the team guide's 8 tasks** (keep their names so the hardware team recognises them): HX711 task, **MEMS task (replaces ToF task)**, Presence task (PIR/IR), Environment task (BME280/BH1750, slow ~1–5 s), Fusion/State task (timestamps, local patterns: TOUCH→SETTLED→weight Δ), UART task (TX queue + RX command parser), Actuator task, Health task. **Priorities from the guide's model**, highest→lowest: Actuation → UART RX/commands → HX711 → MEMS/presence events → state processing → environment → diagnostics. Measure and document stack high-water marks; target total RAM < 16 KB of 20 KB; if it doesn't fit, note the STM32F411 Black Pill fallback.
  - Every sensor enable/disable by compile flag **and** runtime config so missing hardware is fine.
  - Host-side unit tests for all parsers/encoders (compile the C parser for the PC and test it).
- **Pi side:** `storemind/sensors/bridge.py` (pyserial, auto-reconnect, checksum/CRC validation, per-message stats, time sync, publishes to the event bus), `storemind/sensors/simulator.py` (virtual serial / TCP loopback, scripted scenarios: rush, empty shelf, restock, pick/put-back, lights off, after-hours intrusion, I2C fault), `tools/hil_test.py` (hardware-in-loop: sends commands, checks replies, 1 h framing-error test).
- **Docs:** `docs/WIRING.md` (pin table + wiring diagram image, power: 5 V/3.3 V rails, HX711 + load-cell wiring, I2C pull-ups (one set only), PIR 3.3 V output check, servo on its own 5 V supply, common ground, level notes), `docs/FIRMWARE.md` (build, flash via ST-Link/serial, tasks, RAM budget; if RAM runs out → STM32F411 Black Pill note), `docs/SETUP_PI5.md` UART section (enable header UART in `/boot/firmware/config.txt`, disable serial console, verify the device name on Pi 5 — header UART vs the separate debug UART — and use a udev symlink `/dev/storemind-mcu`).
- **Accept:** simulator drives all dashboard sensor events; 1 h simulated run with 0 framing errors; real-board run listed in `HARDWARE_TODO.md` with exact steps if no board is connected.

### M6 — MEMS accelerometer: shelf interaction, weight gating, tamper  *(replaces ToF and the old microphone idea)*
Analogy: the MEMS chip is the shelf's "sense of touch" — the camera sees, the load cell weighs, the MEMS feels when someone touches or bumps the shelf.
- **Firmware (M5 task):** per-node state machine IDLE → ACTIVE (vibration above threshold) → SETTLING → SETTLED; emits `$M` TOUCH / SETTLED with peak/RMS/duration; TILT if the shelf angle changes > N° for > T s; KNOCK for a single large spike; thresholds set by command and stored in flash.
- **Weight gating (key fusion):** HX711 readings taken while the shelf is ACTIVE are marked unstable and ignored; the weight change is computed only after SETTLED → **clean "pick / put-back" events** = (Δgrams, which slot, when). Δgrams ÷ unit weight = units taken (unit weight entered in calibration).
- **Pick events without a load cell:** TOUCH + a tracked shopper near that shelf (camera) = "interaction" (count + dwell per shelf), used for shelf-engagement heatmaps and the lost-sales estimate (touch at an EMPTY slot).
- **Shelf engine trigger:** a TOUCH → SETTLED sequence triggers an immediate shelf check of that slot (instead of waiting for the next period) → faster EMPTY/LOW detection, fewer wasted checks (task-aware scheduling).
- **Camera tamper:** a MEMS node on a camera bracket → KNOCK/TILT = "camera moved" alert + mark that camera's calibration invalid (fused with the image-based tamper check from M2).
- **Pi side:** `storemind/sensors/mems.py` — event handling, fusion rules above, per-node config (`role: shelf|camera_mount`, `slot`, thresholds).
- **Simulator:** scripted MEMS scenarios (pick, put-back, lean on shelf, trolley bump, camera knock) in `storemind/sensors/simulator.py`.
- **Evaluation (bucket B when hardware is ready):** `tools/mems_test.py` guides a teammate through 30 picks, 10 put-backs, 10 accidental bumps, 10 camera knocks and logs ground truth. **Accept:** pick/put-back detection ≥ 90% with weight gating, false TOUCH < 1 per 10 min idle, camera knock detected ≥ 9/10. Until the board is wired: simulator tests pass + steps listed in `HARDWARE_TODO.md`.
- `docs/MEMS.md`: chip choice, mounting (glue/screw the module flat on the shelf underside, axis orientation), thresholds, fusion logic diagram, how to calibrate.

### M7 — Fusion v2, dashboard, hardware panel, operations  *(research/23 §6)*
- Fusion rules updated for weight + MEMS (pick/put-back, tamper), IR-beam vs camera entry cross-check (disagreement > X% → "check entrance calibration"), PIR after-hours intrusion, BH1750 lighting context, BME280 perishable-zone alerts, optional radar; every alert carries its evidence ("shelf B3 empty: camera fill 0.12, weight −1.8 kg, lux 420 normal").
- Dashboard: camera health panel, sensor panel (last value per sensor, error counters), **hardware panel** (FPS, inference ms p50/p95, CPU temp, throttling flags, power from Pi 5 PMIC `vcgencmd pmic_read_adc` with correction *real W ≈ 1.1451 × PMIC W + 0.5879*, **mJ per frame**), privacy panel (0 bytes stored), phone-friendly layout, `storemind.local` via mDNS.
- Alerts: LED + buzzer patterns via STM32 (optional servo mock gate), optional voice (offline TTS) in Telugu/Hindi/English.
- Operations: systemd unit per service (`Restart=always`, `WatchdogSec` + sd_notify), log rotation, SQLite WAL + nightly compaction + retention, config versioning, event UUIDs (idempotent sync), `scripts/install_pi5.sh` one-command install.
- Tests: **24 h soak** script (memory/disk growth report) and **chaos** script (kill a stream, kill MQTT, unplug serial, cover lens, fill disk) — system must degrade and alert, not crash.
- Dashboard screenshots: use Playwright headless to capture them automatically into `ppt_assets/`.

### M8 — Raspberry Pi 5 deployment + real speed
- Export detector for Pi CPU: NCNN and LiteRT INT8 (and ONNX); backend chosen in config.
- Benchmark **on the Pi** in one sitting: median + p95 ms, FPS, °C, throttling, W and mJ/frame, stream density. Bucket S, device named.
- If the Pi isn't reachable from Claude Code, produce `tools/bench_pi.py` + exact steps in `HARDWARE_TODO.md`; the team runs it and pastes the JSON back.

### M9 — Qualcomm path without a board  *(research/23 §5)*
- **Detector backend abstraction** already in place → add `litert_qnn` (LiteRT + `libQnnTFLiteDelegate.so`, `backend_type: htp`) and `ort_qnn` (ONNX Runtime QNN EP) backends, selectable by config, with a CPU fallback and an "accelerator in use" check.
- **Qualcomm AI Hub (free, token already configured by the team):** `tools/aihub_profile.py` → compile + profile our detector (and AI Hub's own YOLO/detector model for comparison) on the hosted **"QCS6490 (Proxy)"** device (list devices with `qai-hub list-devices` first; also profile one other IoT target if available), run an inference job on sample frames and compare outputs with the laptop model. Save latency, memory, compute-unit split (NPU/GPU/CPU layers) to `eval/results/qualcomm_aihub.json`. **Label as bucket Q "AI Hub hosted proxy device"** — never as "our board".
- `deploy/qualcomm/`: RUBIK Pi 3 / QCS6490 port guide (same Pi Camera Module 3, same 40-pin UART to STM32), IM SDK GStreamer pipeline (`qtimlvconverter → qtimltflite|qtimlqnn → qtimlvdetection`), systemd units, gotchas (fastrpc/CDSP firmware, QAIRT runtime ↔ skel version match). Mark "prepared, not yet run on hardware".
- `docs/QUALCOMM.md` table: Pi 5 CPU (measured, S) vs QCS6490 (AI Hub, Q) vs published consult.red (P: 3.91 vs 34.22 FPS; 1,423 vs 129 mJ/frame; 70 vs 54 °C).
- Licence note in docs: Ultralytics YOLO is AGPL-3.0 — fine for the hackathon; list licence-friendly alternatives for a commercial product.

### M10 — Stretch: "Ask your store" + daily summary
- Local small LLM (llama.cpp on Pi 5; QNN path documented for QCS6490) → read-only SQL over whitelisted views → answer cites rows. Daily summary in English/Telugu/Hindi from computed numbers only.
- **Accept:** 20 test questions, 0 invented numbers.

### M11 — Final docs + demo pack
- Fill every `docs/` file; `DEMO_RUNBOOK.md` (primary: live Pi cam + fake-CCTV streams + STM32/simulator; backup: MediaMTX replay; exact commands and a 5-minute script); `TEAM_GUIDE.md` in simple English with pictures; updated architecture + CCTV diagrams; final `RESULTS.md` with buckets A/B/C/S/Q/P.

---

## 2. Definition of done (whole plan)
- One command starts everything on the laptop (fake CCTV + simulator) and on the Pi 5 (real cameras + STM32).
- Any teammate can follow `docs/` to install on a fresh Pi, connect the college DVR, wire the STM32 and run the demo, without asking anyone.
- All numbers are labelled, reproducible by one command, and nothing is tuned on the reported test set.
- Tests green; no secrets or video in git.

## 3. When blocked
Hardware missing, credentials missing, or a decision needed → don't guess. Write it in `docs/HARDWARE_TODO.md` / `HANDOFF_FOR_CLAUDE.md` under "Needs the team", then continue with the next unblocked item.
