# 26 — Two people, two laptops, one repo: work split + integration contracts

Written 24 Sep 2026. Read with `CLAUDE_CODE_PROMPT_V2.md` (the build plan, M0–M11).

**In one line:** Person A builds the **"eyes and brain"** (vision, analytics, AI, Qualcomm). Person B builds the **"nervous system"** (cameras in, sensors in, dashboard out, the Pi box). They meet at **one written contract**: the event schema + the serial protocol + the MQTT topics. Each side tests against a **simulator of the other side**, so nobody waits for anybody.

Analogy: two teams building a railway from opposite ends. It only meets in the middle if both follow the same track gauge. The contract (§3) is the track gauge; it is agreed **first**, in PR-0.

---

## 1. Who does what (and why)

| | **Person A — Vinith** | **Person B — friend** |
|---|---|---|
| Laptop | Lenovo LOQ, **RTX 4050 (6 GB)**, i5 HX | ASUS Vivobook 15, i5 H, **no NVIDIA GPU** |
| Role | **Vision & Intelligence** (needs the GPU: CAVIAR re-runs, tracker bake-off, model export, training) | **Platform & Hardware integration** (CPU is enough: streams, serial, firmware, web, Linux ops) |
| Milestones (from the V2 plan) | **PR-0** contracts · **M1** counting v2 + tracker bake-off + IR-beam cross-check logic · **M3** shelf v2 (incl. BH1750 lux use) · **M4** queue v2 · **M6-fusion** (pick/put-back from MEMS + weight, tamper fusion) · **M8-models** (NCNN / LiteRT INT8 export + `bench_pi.py`) · **M9** Qualcomm AI Hub · **M10** Ask-your-store · owns `RESULTS.md` | **M2** CCTV ingest (go2rtc, MediaMTX, discover/probe, fake CCTV, watchdog, PIR wake-up) · **M5** STM32 firmware + `PROTOCOL.md` + bridge + simulator + HIL test · **M6-firmware** (MEMS task + `$M`) · **M7** dashboard panels, LED/buzzer alerts, systemd, install script, soak + chaos · **M8-deploy** (Pi 5 install, UART setup, chrony) · **CI** (GitHub Actions) · onboarding/wiring/setup docs |
| Owns these paths | `storemind/storemind/{inference,tracking,analytics,fusion,eval,train}/`, `storemind/tools/{calibrate,shelf_*,label_*,aihub_*,bench_*}.py`, `deploy/qualcomm/`, `storemind/storemind/llm/` | `storemind/storemind/{ingest,sensors,api,health,alerts,store}/`, `storemind/storemind/firmware/` (or `firmware/stm32/`), `storemind/tools/{discover,probe,fake_cctv,hil_test,mems_test}.py`, `deploy/pi5/`, `scripts/`, `.github/workflows/` |
| Needs from the other | Sensor events on the bus (from B's simulator until real hardware) | Analytics events to display (from A's engines, or the existing `--backend scripted` replay) |

**Shared files (contract files).** Change them only in a **small separate PR**, which the other person approves **before** dependent work merges:
`storemind/storemind/core/events.py`, `core/bus.py`, `core/config.py`, `configs/*.yaml` schema, `pipeline.py`, `run.py`, `requirements.txt`, `CLAUDE.md`, `docs/INTERFACES.md`, `docs/PROTOCOL.md`.

**Hardware in hand.** Whoever physically holds the **Pi 5** and the **STM32 + sensors** runs the hardware steps (HIL test, Pi benchmark). The code never assumes who that is: every hardware step is a script + checklist in `docs/HARDWARE_TODO.md`.

---

## 2. Git workflow for two Claude Codes (no collisions)

Both use Claude Code on **separate clones** (Vinith: `C:\SIH`; friend: his own clone). Two clones never collide on disk. Collisions only happen in git, and these rules prevent them:

1. **Never work on `master`.** One branch per milestone: `a/m1-counting-v2`, `b/m2-ingest-v2`, `b/m5-stm32`, …
2. **Start of every session:**
   ```
   git switch master
   git pull
   git switch <your-branch>
   git merge master
   ```
   Or `git rebase master` if the branch is not pushed yet.
3. **Small PRs** (one milestone or half of one). **The other person reviews and approves** (the repo already requires approval). This doubles as knowledge sharing: each of you learns the other half.
4. **Logs are per person**, so they never conflict:
   - `logs/WORK_LOG_A.md` and `logs/WORK_LOG_B.md` (append-only).
   - `handoff/HANDOFF_A.md` and `handoff/HANDOFF_B.md` (overwritten each session).
   - The old root `WORK_LOG.md` and `HANDOFF_FOR_CLAUDE.md` become short index files pointing to these.
5. **`RESULTS.md` is regenerated only by Person A.** B's measured results (stream density, soak, HIL, Pi install) go to `storemind/storemind/eval/results/platform/*.json|md`, and `run_all` includes them (A adds that hook in PR-0).
6. **Never commit** videos, audio, models, `.venv`, `data/*.db`, secrets (`configs/secrets.yaml`, `.env`, the AI Hub token).
7. **Merge order when both touch `pipeline.py`:** whoever merges second rebases and re-runs all tests. Keep `pipeline.py` edits tiny, and put engines in their own modules registered with one line.

**Environment setup:**

- **Vinith:** CUDA build of PyTorch (use the exact command from pytorch.org for the pinned version). Plug the laptop in and use performance mode for benchmarks.
- **Friend:** install the **CPU-only** PyTorch wheel (`--index-url https://download.pytorch.org/whl/cpu`), which avoids a ~2 GB CUDA download. For dashboard/ingest work use `--backend scripted`, or YOLO11n@320 on CPU (fine).
- **Both:**
  1. Create the venv in `storemind/.venv`.
  2. Run `pip install -r storemind/requirements.txt`.
  3. Get test videos with `download_caviar.ps1` and `python storemind/tools/make_synthetic_video.py --out videos --scene all`. Videos are not in git.
  4. Install ffmpeg.
  5. B only: go2rtc and MediaMTX binaries.

---

## 3. The contract (PR-0: written first, merged before anything else)

### 3.1 Event schema additions (`core/events.py`, `SCHEMA_VERSION = 2`, additive only)
Existing types stay unchanged: ENTRY, EXIT, ZONE_VISIT, QUEUE_STATE, SERVICE_DONE, SLOT_STATE, SENSOR, PICKUP, SHRINK_FLAG, LOST_SALE_RISK, FORECAST, ALERT, HEALTH.

| New type | Payload | Producer → consumer |
|---|---|---|
| `SHELF_MOTION` | `node, shelf, slot?, kind: TOUCH\|SETTLED\|TILT\|KNOCK, peak_mg, rms_mg, dur_ms` | B bridge → A fusion (pick/put-back, shelf-check trigger) |
| `CAMERA_MOUNT` | `node, cam, kind: KNOCK\|TILT, peak_mg, tilt_deg?` | B bridge → A/B tamper fusion + health |
| `BEAM_CROSS` | `node, door, direction: in\|out, t_ms_mcu` | B bridge → A footfall cross-check |
| `PRESENCE` | `node, zone, active: bool` | B bridge → B ingest (FPS wake-up) + A after-hours rule |
| `ENVIRONMENT` | `node, lux?, temp_c?, rh_pct?, pressure_hpa?` | B bridge → A shelf (lux) + dashboard |
| `WEIGHT` | `node, slot, grams, stable: bool` | B bridge → A fusion (weight gated by SHELF_MOTION) |
| `NODE_HEALTH` | `node, uptime_s, free_heap, min_stack_words, i2c_err, uart_err, crc_err, reset_cause, link: up\|down` | B bridge → health panel |
| `CAMERA_HEALTH` | `cam, state: ok\|stale\|reconnecting\|tampered\|dark, fps, lag_ms, reconnects` | B ingest → health panel + A (skip analytics on bad frames) |

`HealthData` gains `power_w`, `mj_per_frame`, `throttled`. **Rule:** a producer may add optional fields; renaming or removing a field needs a contract PR.

### 3.2 MQTT topics (existing convention kept)
- Events: `storemind/{store}/{node}/{cam or _}/{TYPE}`. QoS 1 for ENTRY/EXIT/SERVICE_DONE/SLOT_STATE/ALERT/BEAM_CROSS/SHELF_MOTION; QoS 0 for states; **retained** for QUEUE_STATE, SLOT_STATE, HEALTH, NODE_HEALTH, CAMERA_HEALTH, ENVIRONMENT.
- **Liveness:** each process sets an MQTT **Last Will** on `storemind/{store}/{node}/status` = `offline` (retained) and publishes `online` on connect. The dashboard shows a dead node within the keep-alive time, for free.
- Commands to the sensor node: `storemind/{store}/{node}/cmd/{LED|BUZZER|SERVO|TARE|CAL|CONFIG}`. The bridge turns them into serial commands and publishes the `$K` ack result.
- **paho-mqtt 2.x gotcha:** `mqtt.Client()` without a callback API version fails in 2.x. Use `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)` and pin `paho-mqtt>=2,<3`. The current `core/bus.py` uses the old form, so fix it in PR-0.

### 3.3 Serial protocol STM32 ↔ Pi (`docs/PROTOCOL.md` = single source of truth)
**Frame (demo mode):** `$<T>,<seq>,<ms>,<fields…>*<XX>\r\n`, where:
- `seq` is a rolling 0–255 counter, so the Pi can detect lost lines.
- `ms` is MCU uptime in ms. The Pi maps it to wall-clock time using `$S` sync.
- `XX` is the XOR of the bytes between `$` and `*`, as 2 hex digits.
- Maximum 96 bytes.

**All values are integers** (scaled): newlib-nano's `printf` has no float support unless you pay flash for `_printf_float`. **Production mode:** the same fields in a binary struct, **COBS-framed + CRC16-CCITT**, switched by `$C,MODE,BIN`.

| Dir | Line | Fields |
|---|---|---|
| MCU→Pi | `$W` | slot, grams, stable(0/1) |
| MCU→Pi | `$M` | node, role(S=shelf/C=camera), event(TOUCH/SETTLED/TILT/KNOCK), peak_mg, rms_mg, dur_ms, tilt_ddeg |
| MCU→Pi | `$B` | beam_id, state(0 blocked/1 clear) (raw, for debugging) |
| MCU→Pi | `$D` | door, IN/OUT (direction from two beams, decided on the MCU for µs timing) |
| MCU→Pi | `$P` | zone, 0/1 |
| MCU→Pi | `$E` | lux, temp_c×10, rh×10, hpa×10 |
| MCU→Pi | `$R` | shelf (restock button) |
| MCU→Pi | `$Q` | (optional LD2450) n, x1,y1,v1,… |
| MCU→Pi | `$H` | uptime_s, free_heap, min_stack_words, i2c_err, uart_err, reset_cause |
| MCU→Pi | `$K` | cmd_seq, OK/ERR, code (ack for every command) |
| Pi→MCU | `$S` | epoch_ms (time sync, every 60 s) |
| Pi→MCU | `$L` / `$Z` | pattern: OFF/ON/SLOW/FAST/ALERT (LED / buzzer) |
| Pi→MCU | `$V` | servo angle (optional) |
| Pi→MCU | `$C` | key, value… (TARE,slot · CAL,slot,grams · MEMS_THR,node,mg · MODE,TXT/BIN · ENABLE,sensor,0/1) |

The Pi retries a command 3× if no `$K` arrives within 200 ms. The bridge counts bad checksums, gaps in `seq` and timeouts, and reports them in NODE_HEALTH.

### 3.4 Config contract
- One `configs/store.yaml` with sections `cameras:`, `zones:`, `shelves:`, `counters:`, `sensors:`, `mqtt:`, `alerts:`.
- A `sensors.node.enabled_sensors` list, so a missing sensor is just "not listed".
- A pydantic schema with clear errors.
- Secrets in `configs/secrets.yaml` (git-ignored), with `secrets.example.yaml` committed.

---

## 4. Integration pipeline research: what makes it industry-grade

**1. Contract-first + simulators both ways.** A tests with B's `sensors/simulator.py` and `tools/fake_cctv.py`. B tests the dashboard with A's recorded event logs (`data/*.jsonl` replay). Neither waits for real hardware. This is how NVIDIA's DeepStream samples and Intel's retail kits are tested: replay RTSP, not live cameras (research/23 §1).

**2. Golden logs.** Record 10 minutes of real serial output once (`tools/serial_record.py`) and commit the small `.log` text file. The bridge's tests replay it byte-for-byte, so real-world noise (partial lines, bad checksums) becomes a regression test.

**3. Testing ladder:**
1. Unit tests.
2. Contract tests: every example in `PROTOCOL.md` and `INTERFACES.md` is parsed by a test.
3. End-to-end smoke: `tools/e2e_smoke.py` starts fake CCTV + STM32 simulator + pipeline + API for 3 minutes and asserts events of every type reached SQLite and the WebSocket.
4. HIL with the real board.
5. 24 h soak test.
6. Chaos tests.
7. Field test at the college.

**4. CI (GitHub Actions, private repo).** On every PR:
- `ruff` + `pytest` on ubuntu (CPU torch, or skip torch-marked tests).
- A firmware build job: `apt install gcc-arm-none-eabi cmake`, then build `firmware/stm32`.
- Host-compiled C unit tests for the protocol parser.

Keep it light: private repos have a monthly free-minutes quota; check Settings → Billing. Students can get GitHub Pro through the Student Developer Pack.

**5. Firmware project format.** Generate with **STM32CubeMX**: FreeRTOS (CMSIS-RTOS2), pins from `docs/WIRING.md`, **CMake** toolchain output, so the same project builds in STM32CubeIDE / VS Code and in CI. Put the protocol encoder/parser in **portable C files with no HAL calls** so they compile and are tested on the PC. PlatformIO (`board = bluepill_f103c8`) is an alternative; there are CubeMX + PlatformIO Blue Pill templates.

**6. Blue Pill gotchas to document:**
- Many boards carry **clone chips (CKS32/CS32)**. ST-Link/OpenOCD may reject the chip ID, so set `-c "set CPUTAPID 0x2ba01477"`.
- The on-board USB pull-up resistor is often the wrong value (irrelevant if you use UART).
- On the F103, **I2C can lock with BUSY stuck**: add bus recovery (9 SCL clocks, re-init) and count it.
- USART1 (PA9/PA10) doubles as the serial bootloader port.

**7. Pi 5 specifics:**
- Enable the header UART in `/boot/firmware/config.txt` and disable the serial console.
- Pi 5 has a separate debug UART connector, so check which `/dev/ttyAMA*` is the header UART, and use a udev symlink.
- Pi 5 has a built-in **RTC** (add the battery) and runs **chrony** as the NTP server for the DVR and the STM32 (`$S`).
- One systemd unit per process, with `WatchdogSec` + `sd_notify`.

**8. Serial reading on the Pi.** Use a dedicated reader thread (pyserial) with a line assembler that tolerates partial lines, a bounded queue into the bus, and auto-reopen when the USB-UART re-enumerates. Never block the vision loop on serial.

**9. Trackers for A's bake-off.** Ultralytics ships **ByteTrack and BoT-SORT** configs built in. Roboflow **`trackers`** (Apache-2.0) has modular re-implementations including **OC-SORT** and ByteTrack that work with any detector. Evaluate all on CAVIAR with the same detections cached (`data/caviar_full.json`) so only the tracker changes.

**10. Observability.** Every process logs structured JSON lines. The `/metrics` page shows per-stage latency (ingest → detect → track → analytics → bus → DB → WebSocket), so "where did the 200 ms go?" has an answer on stage.

---

## 5. Timeline suggestion (both in parallel)

| Step | Person A | Person B |
|---|---|---|
| 0 (first ~1–2 h) | **PR-0**: contract (§3), per-person logs, schema v2 + tests, paho fix, `run_all` platform hook → friend approves | Set up clone, venv (CPU torch), videos, ffmpeg, go2rtc, MediaMTX; review PR-0 |
| 1 | M1 counting v2 + tracker bake-off | M2 ingest v2 + fake CCTV + e2e smoke harness |
| 2 | M3 shelf v2 (lux, reference bank, rectification, glare, weight fusion) | M5 PROTOCOL.md + bridge + simulator, then firmware skeleton (all 8 tasks) |
| 3 | M4 queue v2 + M6-fusion (pick/put-back, tamper) | M6 MEMS firmware, M7 dashboard panels + LED/buzzer + ops |
| 4 | M8 model export + bench script, M9 AI Hub | M8 Pi 5 install + UART + chrony + soak/chaos, CI |
| 5 | M10 (stretch), final RESULTS | College CCTV onboarding + field test, docs |
| Together | Weekly: a 30-min demo on master using `DEMO_RUNBOOK.md`; fix whatever breaks first | |

## Sources
- paho-mqtt 2.0 migration (callback API version): https://eclipse.dev/paho/files/paho.mqtt.python/html/migrations.html
- Roboflow trackers (Apache-2.0, OC-SORT/ByteTrack): https://github.com/roboflow/trackers · https://trackers.roboflow.com/latest/
- PlatformIO Blue Pill board: https://docs.platformio.org/en/latest/boards/ststm32/bluepill_f103c8.html · CubeMX + PlatformIO Blue Pill template: https://github.com/Oct19/Bluepill-CubeMX-PlatformIO-Template
- GitHub Actions billing (private-repo quotas): https://docs.github.com/billing/managing-billing-for-github-actions/about-billing-for-github-actions
- Research basis: `research/23` (pipeline, hardware, reliability), `research/24` (CCTV), `CLAUDE_CODE_PROMPT_V2.md` (milestones)
