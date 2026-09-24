# Interfaces — events, MQTT topics, config, result files

**Status:** contract v2, agreed in PR-0 (research/26 §3). Code: `storemind/storemind/core/events.py`,
`core/bus.py`, `core/config.py`. Tests: `storemind/tests/test_contracts_v2.py` and
`test_interfaces_doc.py` (which validates **every JSON example on this page** against the
real pydantic models and checks its topic).

Serial lines between the STM32 and the Pi are in [`PROTOCOL.md`](PROTOCOL.md).

## 0. Change rules

- These files are shared: `core/events.py`, `core/bus.py`, `core/config.py`, the config
  schema, `pipeline.py`, `run.py`, `requirements.txt`, `CLAUDE.md`, this file, `PROTOCOL.md`.
  Change them only in a **small separate PR** that the other person approves before
  dependent work merges.
- **Additive only.** A new type or a new *optional* field is fine in a contract PR.
  Renaming or removing a field, or making an optional field required, is a breaking
  change: bump `SCHEMA_VERSION` and agree a migration first.
- Payloads reject unknown fields (`extra="forbid"`), so a typo in a producer fails loudly.

## 1. Envelope (every event)

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version of the producer. Now **2**. v1 events stay valid. |
| `id` | str | UUID hex, unique per event (idempotent sync). |
| `ts` | str | ISO-8601 with offset. In replay mode derived from the video timeline. |
| `store` | str | Store id (`config.store`). |
| `node` | str | Who published it: the Pi (`pi5-01`) for vision events, the **MCU id** (`stm32-01`) for sensor events the bridge publishes. |
| `cam` | str \| null | Camera name, or null (`_` in the topic). |
| `type` | str | One of the types below. |
| `data` | object | Type-specific payload, validated. |

## 2. Event types

### 2.1 v1 (unchanged)

| Type | Payload fields | Producer |
|---|---|---|
| `ENTRY` / `EXIT` | `line, track, direction` | A footfall |
| `ZONE_VISIT` | `zone, zone_kind, track, dwell_s` | A zones |
| `QUEUE_STATE` | `counter, queue_len, queue_len_smooth, median_wait_s?, service_rate_per_min?, in_service` | A queue |
| `SERVICE_DONE` | `counter, track, service_s, wait_s?` | A queue |
| `SLOT_STATE` | `shelf, slot, sku?, state (FULL/LOW/EMPTY/WRONG_ITEM/UNKNOWN), fill?, confidence?, reason?` | A shelf |
| `SENSOR` | `node, sensor, channel?, value, unit` | legacy generic sensor reading |
| `PICKUP` / `SHRINK_FLAG` | `shelf, slot, grams?, evidence` | A fusion |
| `LOST_SALE_RISK` | `shelf, slot, sku?, price?, dwell_s, est_value?` | A fusion |
| `FORECAST` | `lambda_hat_per_min, mu_per_min_per_counter, open_counters, recommended_counters, eta_min?, pred_wait_s?, lag_min?, basis` | A forecast |
| `ALERT` | `severity (INFO/WARN/CRITICAL), message_key, message, lang, ack, alert_id, context` | alert manager |
| `HEALTH` | `fps, cpu_percent?, cpu_temp_c?, mem_percent?, camera_ok, tamper, uptime_s?, video_bytes_stored` **+ v2:** `power_w?, mj_per_frame?, throttled?` | health monitor |

### 2.2 v2 (new, research/26 §3.1)

In a v2 sensor payload, `node` is the **physical sensor node** the reading came from: the
MCU id, or for MEMS events the MEMS node id on that MCU (`m1`, `m2`, mapped to a shelf or
camera by `sensors.mems_nodes`). Wire integers are scaled back to real units by the bridge.

| Type | Payload (`?` = optional) | From serial | Producer → consumer |
|---|---|---|---|
| `SHELF_MOTION` | `node, shelf, slot?, kind: TOUCH\|SETTLED\|TILT\|KNOCK, peak_mg, rms_mg, dur_ms` | `$M` role S | B bridge → A fusion (pick/put-back, shelf-check trigger) |
| `CAMERA_MOUNT` | `node, cam, kind: KNOCK\|TILT, peak_mg, tilt_deg?` | `$M` role C | B bridge → A/B tamper fusion + health |
| `BEAM_CROSS` | `node, door, direction: in\|out, t_ms_mcu` | `$D` | B bridge → A footfall cross-check |
| `PRESENCE` | `node, zone, active` | `$P` | B bridge → B ingest (FPS wake-up) + A after-hours rule |
| `ENVIRONMENT` | `node, lux?, temp_c?, rh_pct?, pressure_hpa?` | `$E` | B bridge → A shelf (lux) + dashboard |
| `WEIGHT` | `node, slot, grams, stable` | `$W` | B bridge → A fusion (weight gated by SHELF_MOTION) |
| `NODE_HEALTH` | `node, uptime_s, free_heap, min_stack_words, i2c_err, uart_err, crc_err, reset_cause, link: up\|down` | `$H` + bridge counters | B bridge → health panel |
| `CAMERA_HEALTH` | `cam, state: ok\|stale\|reconnecting\|tampered\|dark, fps, lag_ms?, reconnects` | — | B ingest → health panel + A (skip analytics on bad frames) |

Direction on the bus is lower-case (`in`/`out`, like `ENTRY`/`EXIT`); on the wire it is `IN`/`OUT`.

### 2.3 Examples (validated by the test)

Topic: `storemind/bvrit-demo/stm32-01/_/SHELF_MOTION`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "SHELF_MOTION", "data": {"node": "m1", "shelf": "shelf-a", "slot": "A1", "kind": "TOUCH", "peak_mg": 412.0, "rms_mg": 138.0, "dur_ms": 640}}
```

Topic: `storemind/bvrit-demo/stm32-01/entrance/CAMERA_MOUNT`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": "entrance", "type": "CAMERA_MOUNT", "data": {"node": "m2", "cam": "entrance", "kind": "KNOCK", "peak_mg": 1850.0, "tilt_deg": null}}
```

Topic: `storemind/bvrit-demo/stm32-01/_/BEAM_CROSS`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "BEAM_CROSS", "data": {"node": "stm32-01", "door": "door1", "direction": "in", "t_ms_mcu": 610140}}
```

Topic: `storemind/bvrit-demo/stm32-01/_/PRESENCE`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "PRESENCE", "data": {"node": "stm32-01", "zone": "aisle1", "active": true}}
```

Topic: `storemind/bvrit-demo/stm32-01/_/ENVIRONMENT`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "ENVIRONMENT", "data": {"node": "stm32-01", "lux": 420.0, "temp_c": 28.4, "rh_pct": 61.5, "pressure_hpa": 1009.3}}
```

Topic: `storemind/bvrit-demo/stm32-01/_/WEIGHT`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "WEIGHT", "data": {"node": "stm32-01", "slot": "A1", "grams": 1622.0, "stable": true}}
```

Topic: `storemind/bvrit-demo/stm32-01/_/NODE_HEALTH`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "stm32-01", "cam": null, "type": "NODE_HEALTH", "data": {"node": "stm32-01", "uptime_s": 720, "free_heap": 6144, "min_stack_words": 38, "i2c_err": 0, "uart_err": 0, "crc_err": 2, "reset_cause": "POR", "link": "up"}}
```

Topic: `storemind/bvrit-demo/pi5-01/entrance/CAMERA_HEALTH`

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "pi5-01", "cam": "entrance", "type": "CAMERA_HEALTH", "data": {"cam": "entrance", "state": "ok", "fps": 9.8, "lag_ms": 120.0, "reconnects": 0}}
```

Topic: `storemind/bvrit-demo/pi5-01/_/HEALTH` (illustrative values, not a measurement)

```json
{"v": 2, "id": "3f2a9c0e5b7d4e1f8a6b2c9d0e1f2a3b", "ts": "2026-09-24T18:04:11.250000+05:30", "store": "bvrit-demo", "node": "pi5-01", "cam": null, "type": "HEALTH", "data": {"fps": {"entrance": 9.8}, "cpu_percent": null, "cpu_temp_c": 61.2, "mem_percent": null, "camera_ok": {}, "tamper": {}, "uptime_s": null, "video_bytes_stored": 0, "power_w": 6.8, "mj_per_frame": 693.9, "throttled": false}}
```

## 3. MQTT

Default broker: Mosquitto on the Pi, `127.0.0.1:1883`, credentials from `configs/secrets.yaml`.
Client: **paho-mqtt 2.x** (`paho-mqtt>=2,<3`), always built as
`mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)` — the 1.x form `mqtt.Client()` fails in 2.x.

### 3.1 Topics

| Topic | Payload | QoS | Retained |
|---|---|---|---|
| `storemind/{store}/{node}/{cam or _}/{TYPE}` | event JSON (§1) | see §3.2 | see §3.2 |
| `storemind/{store}/{node}/status` | `online` / `offline` | 1 | **yes** |
| `storemind/{store}/{node}/cmd/{LED\|BUZZER\|SERVO\|TARE\|CAL\|CONFIG}` | command JSON (§3.4) | 1 | no |
| `storemind/{store}/{node}/ack` | `{"cmd", "cmd_seq", "status": "OK"\|"ERR"\|"TIMEOUT", "code"}` | 1 | no |

### 3.2 QoS and retain per event type (`core/bus.py`: `QOS1_TYPES`, `RETAINED_TYPES`)

- **QoS 1** (must not be lost): `ENTRY, EXIT, SERVICE_DONE, SLOT_STATE, ALERT, BEAM_CROSS, SHELF_MOTION`.
- **QoS 0** (states, the next message replaces them): everything else.
- **Retained** (a dashboard that connects late still gets the current value):
  `QUEUE_STATE, SLOT_STATE, HEALTH, NODE_HEALTH, CAMERA_HEALTH, ENVIRONMENT`.

### 3.3 Liveness (Last Will)

Every process sets a retained Last Will `offline` on its `status` topic before connecting,
publishes retained `online` on every (re)connect, and publishes `offline` itself on a clean
shutdown (the broker does not send the Will after a normal disconnect). The dashboard shows a
dead node within 1.5 × keep-alive (keep-alive 30 s).

`MqttBus(listen=True)` (`mqtt.listen: true`) also subscribes to `storemind/{store}/#` and
delivers events from other processes to local subscribers; the bus skips the broker's echo
of its own events by id.

### 3.4 Commands to a sensor node

The bridge turns each command into a serial line (PROTOCOL.md §3), waits for `$K`, retries
(3 × 200 ms), then publishes the result on `…/ack`.

| Topic suffix | JSON payload | Serial |
|---|---|---|
| `cmd/LED` | `{"pattern": "OFF"\|"ON"\|"SLOW"\|"FAST"\|"ALERT"}` | `$L` |
| `cmd/BUZZER` | `{"pattern": …}` | `$Z` |
| `cmd/SERVO` | `{"angle": 0-180}` | `$V` |
| `cmd/TARE` | `{"slot": "A1"}` | `$C,TARE,slot` |
| `cmd/CAL` | `{"slot": "A1", "grams": 500}` | `$C,CAL,slot,grams` |
| `cmd/CONFIG` | `{"key": "MEMS_THR"\|"MODE"\|"ENABLE", "args": [...]}` | `$C,key,args…` |

## 4. Config contract (`core/config.py`)

- One YAML per deployment (`storemind/configs/*.yaml`). Loading validates with pydantic and
  fails with the exact key, e.g. `sensors.node.enabled_sensors.1: Input should be 'hx711', …`.
- Zones, counters and shelves stay **per camera** (as in v1), because their geometry is
  drawn on that camera's image. A single `configs/store.yaml` for a real site is added by
  Person B in M2 with camera templates.
- **Secrets** never go in these files: `configs/secrets.yaml` (git-ignored; template
  `configs/secrets.example.yaml`) or env vars `STOREMIND_MQTT_USERNAME/PASSWORD`,
  `STOREMIND_CAM_<NAME>_USERNAME/PASSWORD`. The Qualcomm AI Hub token is only ever in
  `qai-hub configure` or `QAI_HUB_API_TOKEN`. `tests/test_no_secrets.py` fails if a
  secrets file or token-like string is tracked or staged.

`sensors:` section (v2 additions in bold):

```yaml
sensors:
  enabled: false          # master switch
  port: COM5              # /dev/storemind-mcu on the Pi
  baud: 115200
  tcp: null               # "host:port" of the simulator
  cell_map: {shelf-a/A1: "1"}
  node:                   # v2
    id: stm32-01
    enabled_sensors: [hx711, mems, ir_beam, pir, bh1750, bme280, buzzer, led, restock_button]
    protocol_mode: txt    # txt | bin
    time_sync_s: 60
    cmd_timeout_ms: 200
    cmd_retries: 3
  mems_nodes:             # v2: where each accelerometer is mounted
    - {id: m1, role: shelf, shelf: shelf-a, slot: A1}
    - {id: m2, role: camera_mount, cam: entrance}
```

Allowed sensor names: `hx711, mems, ir_beam, pir, bh1750, bme280, buzzer, led, servo,
restock_button, ld2450`. **A sensor that is not listed does not exist** — code checks
`config.sensors.has("bh1750")` and must work without it. `servo` and `ld2450` are off by default.

`mqtt:` gains `listen: false`.

**M1 additions (contract PR `a/m1-contract`, all optional, defaults = old behaviour):**

| Key | Default | Meaning |
|---|---|---|
| `tracker.type` | `bytetrack` | `bytetrack` \| `ocsort` \| `botsort` (no ReID, no CMC) \| `sort` \| `simple`. ReID trackers are deliberately not allowed (privacy). |
| `tracker.high_conf_det_threshold` | library default | score split for two-stage association |
| `tracker.minimum_consecutive_frames` | `1` | frames before a track is confirmed |
| `cameras[].line.mode` | `single` | `single` = v1 counter; `gate` = counting v2 (docs/COUNTING.md) |
| `cameras[].line.gate_px`, `min_track_age_s`, `min_displacement_px`, `direction_mode` (`off`/`balanced`/`strict`), `direction_window_s`, `confirm_s` | 16, 0.5, 0, balanced, 1.0, 0 | gate counter parameters (pixels of the processed frame) |
| `cameras[].line.beam_door` | null | IR-beam door id (`BEAM_CROSS.door`) watching this line → cross-check + fallback |
| `cameras[].filters[]` | `[]` | `{points?, min_score?, min_area_px, max_area_frac, classes?}` per-zone detection filter |
| `cameras[].staff` | null | `{zones, zone_dwell_s, badge, aruco_dict, badge_ids, badge_every_n}` staff exclusion |

## 5. Result files (Person B → `RESULTS.md`)

Person A owns `RESULTS.md`. Person B writes measured platform results to
`storemind/storemind/eval/results/platform/`; `python -m storemind.eval.run_all` includes
each file as its own section. A file without a valid bucket is shown as "did not run".

JSON:

```json
{"title": "Stream density - Vivobook 15", "bucket": "S", "device": "ASUS Vivobook 15, i5 H",
 "note": "3 fake-CCTV streams + 1 webcam, 1 h.",
 "rows": [{"metric": "streams sustained at 8 FPS", "value": "4", "target": ">= 3"}],
 "commands": ["python tools/fake_cctv.py ...", "python tools/stream_density.py ..."]}
```

Markdown: a line `<!-- bucket: B -->`, then `# Title`, then free text.

Buckets: **A** public benchmark · **B** our own recording · **C** simulation (logic only) ·
**S** speed only · **Q** Qualcomm AI Hub hosted/proxy device · **P** published third-party figure (cite).
