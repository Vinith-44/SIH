# Config reference

**Owner:** A + B (shared schema) · **Source of truth:** `storemind/storemind/core/config.py` ·
**Examples:** `storemind/configs/demo.yaml`, `vtest.yaml`, `caviar.yaml`, `rush.yaml`

Every key a StoreMind YAML file can hold, with its type, default and meaning.
`tests/test_config_reference.py` fails if a key exists in `config.py` but not on this page, so the page cannot
silently fall behind the code. Changing a key is a contract change: a small separate PR, approved by the other
person (CLAUDE.md).

How to read it:
- `cameras[].line.a` means "the `a` key of the `line` block of each item in the `cameras` list".
- **required** keys have no default: the config does not load without them.
- An unknown key is an error, not ignored: every block rejects keys it doesn't know.
- A bad value names the exact key, e.g.
  `invalid config configs/demo.yaml: cameras.0.fps: Input should be a valid number`.
- Points are `[x, y]`. Values ≤ 1 are fractions of the frame width/height; larger values are pixels
  (`core/geometry.py` resolves them once the frame size is known).
- **Not read yet** marks a key that exists in the schema but that no code on master reads. Setting it changes
  nothing today. (All such keys are Ram's serial-bridge settings, which arrive with the bridge.)
- Retired keys: `forecast.horizon_min` (never read; the forecast's look-ahead is the learned door-to-counter lag).
  Old configs that still contain it load with a warning.

Secrets never go in these files.
- Camera and MQTT passwords come from `configs/secrets.yaml` (git-ignored; shape in
  `configs/secrets.example.yaml`) or from the environment: `STOREMIND_MQTT_USERNAME` / `_PASSWORD`,
  `STOREMIND_CAM_<NAME>_USERNAME` / `_PASSWORD`.
- The Qualcomm AI Hub token only ever comes from `qai-hub configure` or `QAI_HUB_API_TOKEN`.

## 1. Store identity

| key | type | default | meaning |
|---|---|---|---|
| `store` | str | `demo-store` | store id; in every event and MQTT topic |
| `node` | str | `pi5-01` | this box's id; in every event it emits |

## 2. Cameras (`cameras[]`)

| key | type | default | meaning |
|---|---|---|---|
| `cameras` | list | `[]` | one item per camera |
| `cameras[].name` | str | **required** | camera id; used in events (`cam`), secrets and `--camera` |
| `cameras[].source` | str | **required** | file path, RTSP URL or camera index (ingest/sources.py) |
| `cameras[].role` | entrance · counter · shelf · zone · generic | `generic` | what the camera is for: `shelf` cameras take one frame every `shelf_period_s`; `entrance` cameras get the floor heatmap |
| `cameras[].fps` | float | `8.0` | frames per second to process (not the camera's own FPS); also the tracker's frame rate |
| `cameras[].infer_size` | int or null | `null` | detector input size for this camera; `null` = `detector.imgsz`. A different size gives the camera its own detector (one per size, shared). A fixed-shape exported model (ONNX static, TFLite) must have been exported at that size, or the pipeline refuses to start. An injected detector (tests, cached evaluation) is never replaced |
| `cameras[].rotate` | 0 · 90 · 180 · 270 | `0` | rotate frames first (camera mounted sideways) |
| `cameras[].reference_frame` | str or null | `null` | image of the normal view; when set, tamper detection is on (camera moved or blocked → counting pauses and a CRITICAL alert fires) |
| `cameras[].shelf_period_s` | float | `30.0` | shelf cameras: one frame every N seconds |

### 2.1 Door line (`cameras[].line`, footfall; docs/COUNTING.md)

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].line` | block or null | `null` | a door line on this camera turns footfall counting on |
| `cameras[].line.name` | str | `door` | line id in ENTRY/EXIT events |
| `cameras[].line.a` | point | **required** | one end of the line |
| `cameras[].line.b` | point | **required** | the other end |
| `cameras[].line.margin_px` | float | `12.0` | `single` mode: a foot point must be this far past the line to change side (hysteresis) |
| `cameras[].line.entry_direction` | pos · neg | `pos` | which crossing direction counts as entering |
| `cameras[].line.cooldown_s` | float | `3.0` | the same track cannot count again within this time |
| `cameras[].line.mode` | single · gate | `single` | `single` = the v1 one-line counter; `gate` = the M1 band counter (`demo.yaml` uses it) |
| `cameras[].line.gate_px` | float | `10.0` | `gate`: width of the band centred on the line (about 3% of frame height) |
| `cameras[].line.min_track_age_s` | float | `0.0` | a track younger than this cannot count |
| `cameras[].line.min_displacement_px` | float | `0.0` | minimum net movement across the line within `direction_window_s` |
| `cameras[].line.direction_mode` | off · balanced · strict | `off` | how closely the movement must point across the line (angle limits: `DIRECTION_COS` in analytics/footfall.py) |
| `cameras[].line.direction_window_s` | float | `1.0` | window for the displacement and direction checks |
| `cameras[].line.confirm_s` | float | `0.5` | stay on the far side this long before the crossing counts |
| `cameras[].line.beam_door` | str or null | `null` | IR break-beam door watching this line; turns on the camera-vs-beam cross-check (fusion/beam.py) |

### 2.2 Zones (`cameras[].zones[]`, dwell and promo; analytics/zones.py)

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].zones` | list | `[]` | named floor areas |
| `cameras[].zones[].name` | str | **required** | zone id in ZONE_VISIT events |
| `cameras[].zones[].points` | list of points | **required** | polygon |
| `cameras[].zones[].kind` | zone · promo · shelf_front | `zone` | `promo` = a promotion display (also measured by analytics/promo.py, docs/PROMO.md); `shelf_front` = in front of a slot (drives lost-sale risk) |
| `cameras[].zones[].min_dwell_s` | float | `3.0` | shorter visits are not reported |
| `cameras[].zones[].shelf` | str or null | `null` | shelf this zone faces (LOST_SALE_RISK in fusion) |
| `cameras[].zones[].slot` | str or null | `null` | slot this zone faces |

Promo-only keys (`kind: promo`; any other kind with one of these set fails to load). The owner marks the promotion; the system measures it (docs/PROMO.md). For a promo zone, `shelf` + `slot` (both or neither) are the linked slot whose load-cell picks count as "took the item".

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].zones[].promo_name` | str or null | `null` | name shown on the dashboard, e.g. "Diwali offer"; null = the zone name |
| `cameras[].zones[].sku` | str, list of str, or null | `null` | product(s) on offer |
| `cameras[].zones[].offer_text` | str or null | `null` | e.g. "Buy 2 get 1 free" |
| `cameras[].zones[].price` | float or null | `null` | offer price, rupees |
| `cameras[].zones[].start_date` | date or null | `null` | first active day, inclusive (YYYY-MM-DD); null = no start limit |
| `cameras[].zones[].end_date` | date or null | `null` | last active day, inclusive; must not be before `start_date` |
| `cameras[].zones[].approach_band` | float 0-1 or null | `null` (= 0.08) | how close to the zone, in frame heights, counts as walking past it |
| `cameras[].zones[].report_every_s` | float > 0 or null | `null` (= 300) | length of each PROMO_STATE window, seconds |

### 2.3 Billing counters (`cameras[].counters[]`, queue; docs/QUEUE.md)

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].counters` | list | `[]` | one item per billing counter |
| `cameras[].counters[].name` | str | **required** | counter id in QUEUE_STATE / SERVICE_DONE |
| `cameras[].counters[].lane` | list of points | `[]` | queue lane polygon (this or `lane_polyline` is required) |
| `cameras[].counters[].billing` | list of points | **required** | where the customer stands while being billed |
| `cameras[].counters[].min_service_s` | float | `3.0` | must stand in `billing` this long before service counts as started (ID flicker made 0.4 s "services") |
| `cameras[].counters[].gap_tolerance_s` | float | `2.0` | wait and service timers survive detection gaps this long |
| `cameras[].counters[].open` | bool | `true` | counter is staffed; the forecast uses the number of open counters |
| `cameras[].counters[].congestion_len` | int | `5` | smoothed queue length that means "congested now" |
| `cameras[].counters[].membership` | polygon · dwell | `polygon` | `polygon` = v1 (anyone in the lane is queuing); `dwell` = M4 (must stay and slow down, which excludes passers-by) |
| `cameras[].counters[].join_dwell_s` | float | `3.0` | `dwell`: in the lane this long, and slow, before joining |
| `cameras[].counters[].max_join_speed` | float | `0.10` | frame heights per second; faster = walking past |
| `cameras[].counters[].speed_window_s` | float | `2.0` | window over which speed is measured |
| `cameras[].counters[].lane_polyline` | list of points | `[]` | bent queue: centre line, billing end first |
| `cameras[].counters[].lane_width` | float | `0.12` | fraction of frame height around the polyline |
| `cameras[].counters[].tail_zone` | list of points | `[]` | where the queue spills out of the lane (tail overflow flag) |
| `cameras[].counters[].party_dist` | float | `0.06` | fraction of frame height: people closer than this are one party ... |
| `cameras[].counters[].party_join_window_s` | float | `4.0` | ... if they joined within this time of each other |
| `cameras[].counters[].balk_min_s` | float | `2.0` | stopped in the lane at least this long, then left without joining = a balk |
| `cameras[].counters[].littles_window_s` | float | `600.0` | averaging window for the Little's-law wait cross-check |

### 2.4 Shelves (`cameras[].shelves[]`, docs/SHELF.md)

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].shelves` | list | `[]` | shelves this camera watches |
| `cameras[].shelves[].name` | str | **required** | shelf id |
| `cameras[].shelves[].slots` | list | `[]` | slots on the shelf |
| `cameras[].shelves[].slots[].name` | str | **required** | slot id |
| `cameras[].shelves[].slots[].points` | list of points | **required** | slot polygon (4 points can be rectified) |
| `cameras[].shelves[].slots[].sku` | str or null | `null` | product name in events and alerts |
| `cameras[].shelves[].slots[].price` | float or null | `null` | rupees; used for the lost-sale estimate |
| `cameras[].shelves[].slots[].reference_facings` | int or null | `null` | facings visible when fully stocked; used for the fill ratio |
| `cameras[].shelves[].slots[].full_grams` | float or null | `null` | load-cell weight when full; `null` = learnt at the "Restocked" press |
| `cameras[].shelves[].slots[].deep` | bool | `false` | deep shelf: the camera sees only the front row, so weight wins |
| `cameras[].shelves[].slots[].unit_grams` | float or null | `null` | weight of one pack (M6: picks and put-backs in units) |
| `cameras[].shelves[].low_threshold` | float | `0.5` | fill below this = LOW (v1: 0.4) |
| `cameras[].shelves[].empty_threshold` | float | `0.28` | fill below this = EMPTY (v1: 0.15); tuned on synthetic seeds 1-10 |
| `cameras[].shelves[].vote_k` | int | `3` | a state change needs K of the last N readings ... |
| `cameras[].shelves[].vote_n` | int | `5` | ... out of N |
| `cameras[].shelves[].occlusion_iou` | float | `0.05` | skip the reading when a person box overlaps the shelf this much |
| `cameras[].shelves[].auto_reference_s` | float or null | `0.0` | replays: take the "restocked" reference at this video time; live shelves: `null`, and the reference comes from the Restocked button |
| `cameras[].shelves[].reference_dir` | str or null | `null` | where reference crops are kept between runs |
| `cameras[].shelves[].white_balance` | bool | `true` | gray-world white balance (v1: false) |
| `cameras[].shelves[].clahe` | bool | `true` | CLAHE on luminance before measuring (v1: false) |
| `cameras[].shelves[].texture` | gradient · canny | `gradient` | texture measure: gain-normalised gradients (v1: canny) |
| `cameras[].shelves[].canny` | auto · fixed | `auto` | `canny` texture: median-based or fixed thresholds |
| `cameras[].shelves[].canny_low` | int | `60` | fixed Canny low threshold |
| `cameras[].shelves[].canny_high` | int | `160` | fixed Canny high threshold |
| `cameras[].shelves[].use_ssim` | bool | `true` | gradient SSIM joins the fill estimate (v1: false) |
| `cameras[].shelves[].ssim_weight` | float | `0.3` | weight of SSIM in the fill estimate |
| `cameras[].shelves[].glare_mask` | bool | `true` | ignore specular pixels (v1: false) |
| `cameras[].shelves[].glare_v` | int | `245` | glare = HSV value above this ... |
| `cameras[].shelves[].glare_s` | int | `40` | ... and saturation below this |
| `cameras[].shelves[].reference_bank` | int | `4` | references kept per slot, one per lighting (v1: 1) |
| `cameras[].shelves[].bank_lux_ratio` | float | `1.8` | lux within this ratio = "same lighting" |
| `cameras[].shelves[].dark_lux` | float or null | `15.0` | BH1750 lux below this → UNKNOWN "too dark", never EMPTY |
| `cameras[].shelves[].dark_brightness` | float or null | `0.12` | the same rule from frame brightness when there is no light sensor |
| `cameras[].shelves[].lux_jump_ratio` | float or null | `2.5` | a sudden lux change this large skips one cycle |
| `cameras[].shelves[].drift_alpha` | float | `0.05` | slow reference update while confidently FULL (v1: 0) |
| `cameras[].shelves[].rectify` | bool | `true` | warp 4-point slots to a rectangle (v1: false) |
| `cameras[].shelves[].rectify_size` | [w, h] | `[96, 128]` | size after warping |
| `cameras[].shelves[].occluded_unknown_cycles` | int | `10` | occluded this many cycles in a row → UNKNOWN |
| `cameras[].shelves[].lux_node` | str or null | `null` | ENVIRONMENT node whose lux applies to this shelf; `null` = any |
| `cameras[].shelves[].weight_mode` | camera · fuse | `fuse` | `fuse` = combine the camera fill with the load cell when a reading exists |
| `cameras[].shelves[].disagree_fill` | float | `0.4` | a camera vs weight fill gap this large raises "check shelf" |

### 2.5 Floor plan, detection filters, staff

| key | type | default | meaning |
|---|---|---|---|
| `cameras[].floor_plan` | block or null | `null` | image → floor mapping for the heatmap |
| `cameras[].floor_plan.image_points` | 4 points | **required** | four points in the image ... |
| `cameras[].floor_plan.plan_points` | 4 points | **required** | ... and the same four on the floor plan (metres) |
| `cameras[].floor_plan.plan_width` | float | `10.0` | floor plan width, metres |
| `cameras[].floor_plan.plan_height` | float | `10.0` | floor plan height, metres |
| `cameras[].floor_plan.cell_size` | float | `0.5` | metres per heatmap cell |
| `cameras[].filters` | list | `[]` | per-area detection filters applied before tracking (inference/filters.py) |
| `cameras[].filters[].points` | list of points | `[]` | area the filter applies to; empty = whole frame |
| `cameras[].filters[].min_score` | float or null | `null` | drop detections below this confidence |
| `cameras[].filters[].min_area_px` | float | `0.0` | drop boxes smaller than this |
| `cameras[].filters[].max_area_frac` | float | `1.0` | drop boxes larger than this fraction of the frame |
| `cameras[].filters[].classes` | list of int or null | `null` | keep only these class ids |
| `cameras[].staff` | block or null | `null` | staff exclusion; never identifies anyone (analytics/staff.py) |
| `cameras[].staff.zones` | list of polygons | `[]` | staff-only areas (cashier, back door) |
| `cameras[].staff.zone_dwell_s` | float | `2.0` | this long inside a staff zone = staff |
| `cameras[].staff.badge` | bool | `false` | look for printed ArUco badges |
| `cameras[].staff.aruco_dict` | str | `DICT_4X4_50` | OpenCV ArUco dictionary of the badges |
| `cameras[].staff.badge_ids` | list of int | `[]` | badge marker ids; empty = any id |
| `cameras[].staff.badge_every_n` | int | `2` | look for badges every N processed frames |

## 3. Detector and tracker (docs/MODELS.md, docs/QUALCOMM.md, docs/COUNTING.md)

| key | type | default | meaning |
|---|---|---|---|
| `detector` | block | | person detector |
| `detector.backend` | ultralytics · litert · onnx · litert_qnn · ort_qnn · scripted · stub | `ultralytics` | runtime; `litert_qnn` / `ort_qnn` = Qualcomm NPU (M9); `scripted` replays saved detections. `run.py --backend` offers the same list |
| `detector.model` | str | `yolo11n.pt` | weights file (.pt, .onnx, .tflite or an NCNN folder) |
| `detector.conf` | float | `0.35` | detection confidence threshold |
| `detector.iou` | float | `0.5` | NMS IoU threshold |
| `detector.person_class` | int | `0` | class id of "person" |
| `detector.num_threads` | int | `4` | CPU threads for LiteRT / ONNX Runtime |
| `detector.imgsz` | int | `640` | input size, for every camera |
| `detector.qnn_lib` | str or null | `null` | `libQnnTFLiteDelegate.so` (litert_qnn) or `libQnnHtp.so` / `QnnHtp.dll` (ort_qnn) |
| `detector.require_accelerator` | bool | `false` | `true`: refuse to start if the NPU cannot be loaded; `false`: fall back to CPU and say so in `accelerator` |
| `tracker` | block | | multi-object tracker |
| `tracker.type` | bytetrack · ocsort · botsort · sort · simple | `bytetrack` | the M1 bake-off shipped ByteTrack; BoT-SORT runs without ReID |
| `tracker.track_activation_threshold` | float | `0.25` | detection confidence needed to start a track |
| `tracker.lost_track_buffer` | int | `30` | frames a lost track is kept for re-matching |
| `tracker.minimum_matching_threshold` | float | `0.8` | matching threshold |
| `tracker.frame_rate` | int | `8` | replaced per camera by `cameras[].fps` |
| `tracker.high_conf_det_threshold` | float or null | `null` | ByteTrack high-confidence split; `null` = library default |
| `tracker.minimum_consecutive_frames` | int | `1` | frames before a new track is reported |

## 4. Forecast, shelf engine, alerts, storage, API

| key | type | default | meaning |
|---|---|---|---|
| `forecast` | block | | "open another counter" forecast (fusion/forecast.py) |
| `forecast.target_wait_min` | float | `3.0` | wait the store wants to stay under |
| `forecast.max_prob_over_target` | float | `0.2` | recommend enough counters that P(wait > target) stays below this |
| `forecast.max_counters` | int | `8` | never recommend more than this |
| `forecast.min_lag_min` | int | `1` | shortest door → counter delay searched for (cross-correlation) |
| `forecast.max_lag_min` | int | `40` | longest delay searched for |
| `forecast.conversion` | float | `0.6` | share of people entering who reach a billing counter |
| `forecast.default_service_s` | float | `90.0` | service time used when no measured service rate exists yet |
| `forecast.period_s` | float | `60.0` | how often a FORECAST event is published |
| `forecast.warmup_min` | int | `3` | minutes of data before the first forecast |
| `shelf` | block | | shelf engine choice |
| `shelf.method` | reference · detector · hybrid | `reference` | `reference` = compare with the restocked reference (the evaluated method); `detector` = count product boxes (fill = boxes / `reference_facings`); `hybrid` = the mean of both, confidence capped by their agreement. `detector` and `hybrid` need `detector_model`; **no product detector has been trained or evaluated yet** |
| `shelf.detector_model` | str or null | `null` | product detector (class 0 = product) for `detector` / `hybrid`; runs on the `detector.backend`; required for those methods |
| `shelf.embed_threshold` | float | `0.75` | wrong-item check: embedding similarity below this = WRONG_ITEM |
| `alerts` | block | | alert manager (alerts/manager.py, Ram) |
| `alerts.cooldown_s` | float | `120.0` | the same alert does not fire twice within this time |
| `alerts.escalate_after_s` | float | `300.0` | an unacknowledged alert escalates after this |
| `alerts.voice_enabled` | bool | `false` | spoken alerts |
| `alerts.voice_lang` | str | `en` | voice clip language: `en`, `hi` or `te` |
| `alerts.console` | bool | `true` | print alerts to the console |
| `alerts.sound` | bool | `false` | beep on alert |
| `alerts.open_hours` | list of "HH:MM-HH:MM" | `[]` | opening hours; PIR motion outside them = after-hours alert; empty = rule off |
| `storage` | block | | event database (store/db.py, Ram) |
| `storage.db_path` | str | `data/storemind.db` | SQLite file |
| `storage.retention_days` | int | `30` | raw events older than this are deleted; per-minute aggregates are kept |
| `api` | block | | dashboard server (api/server.py, Ram) |
| `api.host` | str | `0.0.0.0` | listen address |
| `api.port` | int | `8000` | listen port |

## 5. Sensors and MQTT (Ram's side: docs/PROTOCOL.md, docs/INTERFACES.md)

Most of these keys are for the serial bridge, which is Ram's M5 work and not on master yet. The keys marked
**not read yet** are read by nothing on master today.

| key | type | default | meaning |
|---|---|---|---|
| `sensors` | block | | STM32 sensor node |
| `sensors.enabled` | bool | `false` | a sensor node is attached |
| `sensors.port` | str | `COM5` | serial port (`/dev/ttyAMA0` on the Pi) |
| `sensors.baud` | int | `115200` | **not read yet** (serial bridge) |
| `sensors.tcp` | str or null | `null` | `host:port` of the sensor simulator, instead of a serial port |
| `sensors.cell_map` | map | `{}` | slot → load-cell channel, for fusion |
| `sensors.node` | block | | the STM32 node |
| `sensors.node.id` | str | `stm32-01` | node id |
| `sensors.node.enabled_sensors` | list | hx711, mems, ir_beam, pir, bh1750, bme280, buzzer, led, restock_button | fitted sensors (servo and ld2450 are off by default); **not read yet** (serial bridge) |
| `sensors.node.protocol_mode` | txt · bin | `txt` | **not read yet**: text frames (demo) or COBS + CRC16 |
| `sensors.node.time_sync_s` | float | `60.0` | **not read yet**: `$S` time-sync period |
| `sensors.node.cmd_timeout_ms` | int | `200` | **not read yet**: retry a command if no `$K` arrives in time |
| `sensors.node.cmd_retries` | int | `3` | **not read yet** |
| `sensors.mems_nodes` | list | `[]` | **not read yet**: where each MEMS accelerometer is mounted |
| `sensors.mems_nodes[].id` | str | **required** | MEMS node id |
| `sensors.mems_nodes[].role` | shelf · camera_mount | **required** | under a shelf, or on a camera bracket |
| `sensors.mems_nodes[].shelf` | str or null | `null` | required for `shelf` |
| `sensors.mems_nodes[].slot` | str or null | `null` | slot above the sensor |
| `sensors.mems_nodes[].cam` | str or null | `null` | required for `camera_mount` |
| `mqtt` | block | | MQTT bus (core/bus.py) |
| `mqtt.enabled` | bool | `false` | `false` = in-process bus only (laptop demo) |
| `mqtt.host` | str | `127.0.0.1` | broker host |
| `mqtt.port` | int | `1883` | broker port |
| `mqtt.username` | str or null | `null` | leave `null`: filled from secrets |
| `mqtt.password` | str or null | `null` | leave `null`: filled from secrets |
| `mqtt.listen` | bool | `false` | also receive events from other processes (serial bridge, ingest) |
