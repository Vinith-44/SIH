# Dashboard: platform panels and the tower light

**Owner:** Ram (Person B) · **Status:** M7a · Code: `storemind/storemind/api/{server,panels}.py`,
`api/static/`, `alerts/tower.py`, `health/monitor.py`.

The dashboard is served by the pipeline (`python -m storemind.run ... --api`, port 8000). Offline:
no CDN, no fonts, inline SVG. M7a adds five panels, all built from events on the bus, so they look the
same whether the bridge and camera ingest run in the pipeline process (laptop) or as separate services
over MQTT (Pi).

| Panel | Shows | From |
|---|---|---|
| **Cameras** | one line per camera: green / amber / red, state, FPS, lag, reconnects | `CAMERA_HEALTH` (M2 ingest); falls back to the pipeline's own FPS and tamper check |
| **Sensor node** | link up/down, uptime, free heap, smallest stack, reset cause, crc / uart / i2c errors; last weight per load cell, lux + climate, PIR per zone, door in/out from the IR beams, last MEMS event per shelf and camera bracket; buttons to test the LED / buzzer | `NODE_HEALTH`, `WEIGHT`, `ENVIRONMENT`, `PRESENCE`, `BEAM_CROSS`, `SHELF_MOTION`, `CAMERA_MOUNT` |
| **Hardware** | detector, FPS, inference ms p50/p95, CPU °C, throttled now, power (Pi 5 PMIC, corrected), mJ per frame | `HEALTH` |
| **Privacy** | video bytes stored (0), frames on disk (0), no face recognition, no re-ID, random track ids, no audio, retention days | configuration facts |
| **Reorder list** | open EMPTY / LOW slots, oldest first, and a "Copy as WhatsApp text" button | Vinith's `ReorderQueue` (M3) |

Alerts raised by other processes (for example the bridge's `SENSOR_LINK:<node>` when the node falls
silent for 30 s) are added to the alert list.

API: `GET /api/platform`, `GET /api/reorder`, `POST /api/node/{LED|BUZZER|SERVO|TARE|CAL}?pattern=&angle=&slot=&grams=`
(needs the bridge in the same process; on the Pi use MQTT `cmd/*`).

**Power.** On a Pi 5 the health monitor sums V×I over the PMIC rails (`vcgencmd pmic_read_adc`) and
applies the published correction *real W ≈ 1.1451 × PMIC W + 0.5879* (research/23 §4.5; it misses
USB/HAT loads). mJ per frame = power ÷ frames processed per second across all cameras. On the laptop
these show "not measured".

## Tower light and buzzer (`alerts/tower.py`)

The LED shows the **worst alert still open**; acknowledging the last one turns it off. Urgent alerts
also sound the buzzer, which the node switches off on its own after 5 s.

| Alert key | LED | Buzzer |
|---|---|---|
| `AFTER_HOURS`, `SHELF_TILT`, `CAMERA_MOVED`, `CAMERA_TAMPER`, `SLOT_EMPTY` | ALERT | ALERT (`SLOT_EMPTY`: FAST) |
| `QUEUE_OVERFLOW`, `FALLEN_STOCK` | FAST | FAST |
| `QUEUE_CONGESTED`, `CAMERA_TILT` | FAST | — |
| `SLOT_LOW`, `WRONG_ITEM`, `HIDDEN_DEPLETION`, `SHRINK`, `QUEUE_FORECAST`, `CAMERA_BUMP`, `SENSOR_LINK` | SLOW | — |
| anything else | by severity: CRITICAL → ALERT + buzzer, WARN → SLOW, INFO → ON | |

In the laptop demo the pipeline's `TowerLightSink` drives the bridge directly. On the Pi the bridge is
its own service: it hears ALERT events over MQTT and applies the same policy; acknowledging on the
dashboard re-publishes the alert with `ack: true`, which turns the LED off there too. New voice lines
(English TTS; Hindi / Telugu clips not recorded yet) exist for the same keys.

## Checked (bucket C, laptop + simulator)

Pipeline in live mode on the three synthetic clips + the STM32 simulator over TCP + dashboard, opened
in a browser: every panel filled, no console errors; the "LED alert" button reached the simulated node
(`$K OK`); Vinith's `SHELF_TILT` alert set the node's LED and buzzer to ALERT; bridge counters stayed at
0 crc / 0 framing / 0 lost. Tests: `storemind/tests/test_platform_panels.py`.

## Ask your store + daily summary (M10 wiring)

Vinith's `storemind/llm` (docs/ASK.md) is served by the dashboard:

| Endpoint | Returns |
|---|---|
| `GET /api/ask?q=...` (`&today=YYYY-MM-DD` optional) | `text, sql, columns, rows, backend, answered, model_query, notes, elapsed_ms, backends` |
| `GET /api/summary?day=YYYY-MM-DD&lang=en\|te\|hi` | `{day, lang, text}` |

- Each request uses a **read-only** connection (`open_readonly`: TEMP views + authorizer), one per worker
  thread, beside the pipeline's writer (WAL). The endpoints are plain `def`, so FastAPI runs them in its
  thread pool: a slow model never blocks the dashboard's event loop or WebSocket.
- The backend list (the local model through Ollama if it has the model, then the keyword rules) is probed
  once and re-probed every 5 minutes, not per question.
- The panel follows ASK.md's UI rules: the query and its rows are always shown under the answer; a query
  written by the model carries "check it asks what you meant"; fallback notes are shown.
- Settings by environment (`/etc/storemind/storemind.env`, no config contract change):
  `STOREMIND_LLM_MODEL` (default `qwen2.5-coder:1.5b`, `off` = rules only), `STOREMIND_LLM_HOST`
  (default `http://localhost:11434`).
- On the Pi: `sudo ./scripts/install_pi5.sh --with-llm` installs Ollama and pulls the model;
  `python scripts/ask_latency.py --label pi5_qwen1.5b` times every question of Vinith's set through the API
  and writes `eval/results/platform/ask_latency_<label>.json` (bucket S; HARDWARE_TODO "M10" step 5). **Pi latency: not measured yet.**

The hardware panel also shows the detector's **accelerator** (M9: `qnn-htp (...)` on a Qualcomm board,
`cpu (fallback: ...)` when the QNN delegate did not load), so a silent CPU fallback is visible.
