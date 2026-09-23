# 04 — StoreMind system architecture

Diagram for the PPT: `diagrams/storemind_architecture.png` (source: `diagrams/storemind_architecture.html`).

## 1. Plain-language picture first

Think of the store as a body:
- **Eyes** = cameras (existing CCTV or Pi cameras).
- **Nerves / reflexes** = STM32 sensor node: feels weight on shelves, a hand near a shelf, someone crossing the door beam; flashes a light or buzzes instantly.
- **Brain** = the edge box (Raspberry Pi 5 now; Qualcomm board later): turns video into *events* ("person entered", "slot A3 empty", "counter 2 wait 4 min").
- **Memory** = a small local database of events and hourly totals — never video.
- **Mouth** = alerts: speaker voice, tower light, staff phones.
- **Head office** (optional) = when internet is available, only small summaries go up, so a chain owner can see all stores.

## 2. Layered architecture

```
┌──────────────────────────── LAYER 0: SENSE ─────────────────────────────┐
│ Entrance cam   Counter cams    Shelf cams          STM32 sensor node(s)  │
│ (CCTV RTSP /   (CCTV RTSP /    (Pi Cam 3 Wide /    HX711 load cells,     │
│  Pi Cam CSI)    USB)            USB, 1 frame/30s)  ToF, IR beam, button  │
└──────┬───────────────┬───────────────┬──────────────────┬───────────────┘
       │ RTSP/CSI      │               │                  │ UART / RS-485
┌──────▼───────────────▼───────────────▼──────────────────▼──── LAYER 1: EDGE NODE (Pi 5) ──┐
│ Ingest: 1 grab-thread per camera, keep latest frame, reconnect, per-task FPS scheduler     │
│ Inference service: shared detector(s), backend = LiteRT | NCNN | Hailo | Qualcomm QNN      │
│ Tracking: ByteTrack per camera (motion only, no appearance features)                        │
│ Analytics engines:                                                                          │
│   Footfall (line + hysteresis) │ Zones & dwell │ Floor heatmap │ Queue (per counter)       │
│   Shelf slot state machine (occlusion gate + voting) │ Planogram match                     │
│ Fusion & prediction: camera ⊕ weight ⊕ ToF, lost-sales, Erlang-C counter forecast          │
│ Event bus: Mosquitto MQTT (local)      Storage: SQLite (events + 1-min aggregates)         │
│ API: FastAPI (REST + WebSocket)        Alert manager (dedupe, cooldown, escalate)          │
│ Health: watchdog, camera-tamper/moved, temps, FPS, stale-node (MQTT Last-Will)            │
└──────┬───────────────────────────────┬─────────────────────────────┬──────────────────────┘
       │ HTTP/WebSocket (store Wi-Fi)  │ UART cmd / speaker          │ MQTT bridge / HTTPS
┌──────▼──────────┐           ┌────────▼──────────┐          ┌───────▼────────────────────────┐
│ LAYER 2: USE    │           │ LAYER 2: ACT      │          │ LAYER 3: HQ (optional, online) │
│ Web dashboard,  │           │ Tower light,      │          │ Store-and-forward sync of      │
│ staff phone PWA,│           │ buzzer, voice in  │          │ aggregates only; multi-store   │
│ daily/weekly    │           │ Telugu/Hindi/Eng  │          │ dashboard; POS/ERP adapters;   │
│ reports         │           │                   │          │ config & model updates (OTA)   │
└─────────────────┘           └───────────────────┘          └────────────────────────────────┘
```

## 3. Services on the edge node (one process each, managed by systemd)

| Service | Job | Tech |
|---|---|---|
| `ingest` | Read cameras, keep only latest frame, reconnect, FPS schedule | OpenCV/FFmpeg, Picamera2, GStreamer (optional) |
| `vision` | Detect → track → run analytics engines → publish events | LiteRT / NCNN / HailoRT / QNN, `supervision` (MIT) for zones/lines |
| `sensor-bridge` | UART/RS-485 frames from STM32 → MQTT; MQTT commands → STM32 | pyserial |
| `fusion` | Combine camera + sensor events; queue forecast; lost-sales | Python, numpy |
| `store` | Subscribe to all events → SQLite; roll up 1-min / 1-h aggregates | sqlite3 (WAL mode) |
| `api` | REST + WebSocket for dashboard; calibration tool (draw zones/slots) | FastAPI |
| `alerts` | Rules, dedupe, voice, tower light, phone push, (online) Telegram | Piper TTS / pre-recorded audio |
| `sync` | When online, push aggregates to HQ; retry forever | MQTT bridge (QoS 1) or HTTPS batch |
| `health` | CPU/temp/FPS/camera-tamper; Last-Will "offline" per node | psutil |

Start as **one Python process with threads** for the hackathon if time is short — keep the same module boundaries so it can be split later.

## 4. Event model (the contract between all modules)

Every module publishes small JSON events. Nothing else crosses module boundaries.

```json
{"v":1,"ts":"2026-09-23T18:04:11.320+05:30","store":"bvrit-demo","node":"pi5-01",
 "cam":"counter-2","type":"QUEUE_STATE",
 "data":{"counter":2,"queue_len":4,"median_wait_s":142,"service_rate_per_min":0.71}}
```

| type | emitted by | key fields |
|---|---|---|
| `ENTRY` / `EXIT` | footfall | `line`, `track` (session-random) |
| `ZONE_VISIT` | zones | `zone`, `dwell_s` |
| `QUEUE_STATE` | queue (every 5 s) | `counter`, `queue_len`, `median_wait_s` |
| `SERVICE_DONE` | queue | `counter`, `service_s` |
| `SLOT_STATE` | shelf (on change) | `shelf`, `slot`, `sku`, `state` FULL/LOW/EMPTY/WRONG_ITEM, `fill`, `confidence` |
| `SENSOR` | STM32 bridge | `node`, `sensor`, `value`, `unit` |
| `PICKUP` / `SHRINK_FLAG` | fusion | `slot`, `grams`, `evidence` |
| `LOST_SALE_RISK` | fusion | `slot`, `sku`, `price`, `dwell_s` |
| `FORECAST` | fusion (every 1 min) | `lambda_hat`, `recommended_counters`, `eta_min`, `pred_wait_s` |
| `ALERT` | alerts | `severity`, `message_key`, `lang`, `ack` |
| `HEALTH` | health | `fps`, `cpu_temp`, `camera_ok`, `tamper` |

MQTT topics: `storemind/{store}/{node}/{cam|sensor}/{type}` — e.g. `storemind/bvrit-demo/pi5-01/counter-2/QUEUE_STATE`.
Use **retained** messages for "current state" topics and **Last Will** for `…/HEALTH` (broker announces a node went offline automatically).

## 5. Storage schema (SQLite)

```sql
CREATE TABLE events(id INTEGER PRIMARY KEY, ts TEXT, store TEXT, node TEXT, cam TEXT,
                    type TEXT, data JSON);
CREATE INDEX ev_ts ON events(ts); CREATE INDEX ev_type ON events(type, ts);
CREATE TABLE agg_minute(ts_min TEXT, store TEXT, metric TEXT, key TEXT, value REAL,
                        PRIMARY KEY(ts_min, store, metric, key));
CREATE TABLE config(key TEXT PRIMARY KEY, value JSON);   -- zones, slots, planogram, thresholds
CREATE TABLE sync_outbox(id INTEGER PRIMARY KEY, payload JSON, sent INTEGER DEFAULT 0);
```
Retention: raw events 30 days, aggregates forever (tiny). Daily/weekly reports are SQL over `agg_minute`.

## 6. Compute budget on one Raspberry Pi 5 (CPU only)

Published reference: YOLO26n exported to NCNN runs ≈ 67 ms/image on a Pi 5 at 640 input (Ultralytics docs). At 320–416 input it's roughly 2–4× faster.

| Camera | FPS we need | Why | Approx. load |
|---|---|---|---|
| Entrance | 8–10 | Walking people must be tracked across the line | ~50–60% of one inference slot |
| Counter ×2 | 3–5 each | Queues move slowly | ~30–40% |
| Shelf ×N | 1 frame / 30–60 s | Shelves change slowly | negligible |

→ A single Pi 5 can demo 1 entrance + 2 counters + a few shelves on CPU. With a Hailo-8L (13 TOPS) HAT, community benchmarks show YOLOv8s at ~128 FPS (batch 8, PCIe Gen 3) → 4–8 full streams. **Measure and report our own numbers** — don't quote these as ours.

## 7. Key algorithms (short)

- **Line counting:** foot point = bottom-centre of box; count when a track's foot moves from side A (beyond `+margin`) to side B (beyond `−margin`); one count per track per direction per N seconds.
- **Wait time:** per track, time in queue polygon with 2 s gap tolerance; service starts when foot enters the counter polygon for ≥ 3 s; report median.
- **Floor heatmap:** 4-point homography (camera → floor plan) from the calibration tool; accumulate foot points per 1-min into a coarse grid (e.g. 50 cm cells).
- **Shelf slot state:** gated frames only → facings ratio + embedding similarity → K-of-N vote → publish on change.
- **Camera tamper / moved:** compare current frame with the calibration reference (ORB feature matches / SSIM); if it drops sharply → `HEALTH tamper=true` and pause counting (so zones don't silently go wrong).
- **Forecast:** see N1 in `03_NOVELTY_AND_FEATURES.md`.

## 8. Proposed code layout (new repo)

```
storemind/
  configs/store.yaml            # cameras, zones, slots, thresholds (made by calibration tool)
  edge/ingest/                  # camera readers (rtsp, csi, usb, file-replay)
  edge/inference/               # backends: litert.py, ncnn.py, hailo.py, qnn.py (same interface)
  edge/analytics/               # footfall.py, zones.py, heatmap.py, queue.py, shelf.py
  edge/fusion/                  # fusion.py, forecast.py (Erlang-C), lost_sales.py
  edge/bus/                     # mqtt helpers, event schema (pydantic)
  edge/store/                   # sqlite writer, aggregator, reports
  edge/api/                     # FastAPI + static dashboard
  edge/alerts/                  # rules, tts, tower light commands
  firmware/stm32/               # FreeRTOS sensor node (HX711, ToF, IR, actuators)
  tools/                        # camera_check.py, calibrate.py, replay_video.py, eval_counting.py
  eval/                         # ground-truth CSVs, evaluation notebooks, results tables
  docs/                         # these research files
```

**Replay mode is essential:** every engine must run on a recorded video file exactly as on a live camera. That's how we evaluate accuracy, debug without the store, and give a reliable demo even if the venue Wi-Fi fails.
