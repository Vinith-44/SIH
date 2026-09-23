# 08 — Roadmap, task split, demo script

Dates aren't fixed yet — plan in phases. **Rule:** a feature only goes on a slide once it runs on the Pi with a measured number.

## Phase 0 — Before the internal round (≈ 1 week)

Goal: an honest, strong PPT + a short working demo video.

| # | Task | Owner (suggested) | Done when |
|---|---|---|---|
| 0.1 | Record 3 test videos (canteen queue, an entrance, a shelf) with permission; hand-count ground truth | Everyone, 1 afternoon | CSVs of true counts/waits |
| 0.2 | Replace detector + tracker: YOLO11n/26n (NCNN) + ByteTrack + foot-point + hysteresis; letterbox input | CV lead | Entry count accuracy measured on video 1 |
| 0.3 | Fix queue: real lane polygon, min window + smoothing, gap-tolerant wait timer, min service dwell | CV #2 | No false alerts on video 2; wait MAE measured |
| 0.4 | Erlang-C forecast + counter recommendation (code in `03_NOVELTY`) fed by entrance counts | Algo/backend | Forecast shown on replay |
| 0.5 | Shelf: occlusion gate + slot grid + temporal voting on top of the current model (quick win) | CV #3 | Stable slot states on video 3 |
| 0.6 | Sign up to Qualcomm AI Hub; compile + profile person model on a QCS6490 device | Anyone | Latency screenshot for the slide |
| 0.7 | Rebuild PPT per `09_PPT_GUIDE.md` with our architecture diagram and measured numbers | PPT owner | Reviewed by whole team |

## Phase 1 — Unified system (≈ 2–3 weeks)

- Repo layout from `04_ARCHITECTURE.md`; one config file; replay mode for every engine.
- MQTT event bus + SQLite + FastAPI + web dashboard (live tiles, shelf grid, queue forecast, alerts, footfall charts, floor heatmap).
- Calibration tool: click to draw lines/zones/slots on a snapshot.
- STM32 FreeRTOS node: HX711 + ToF + IR beam + button + tower light; sensor-bridge to MQTT.
- Alerts: voice (Telugu/Hindi/English clips or offline TTS), tower light, phone PWA.
- Health: FPS, temperature, camera tamper, node offline (MQTT Last Will).

## Phase 2 — Novelty depth (≈ 2 weeks)

- Shelf v2: SKU-110K + gap detector; planogram embedding match; "restocked" reference snapshots.
- Fusion: pickup / hidden depletion / shrink flags; **lost-sales ₹** metric; POS CSV → conversion rate.
- Floor-plan heatmap via homography; daily/weekly report (PDF/CSV).
- Run one pipeline live on a Qualcomm board (Dragon Q6A / RUBIK Pi 3 / UNO Q) or Hailo HAT.
- HQ: second node (Jetson Nano or laptop) syncing aggregates → multi-store view.

## Phase 3 — Finale polish

- Full evaluation table; failure-case analysis (low light, crowd, glare).
- Robustness: power-cut test, unplug-internet test, camera-cover test.
- Cost sheet per store tier; privacy one-pager (DPDP alignment).
- (Stretch) on-device daily report narrated by a small LLM.

## Suggested team split (6 people)

| Role | Owns |
|---|---|
| CV lead | Person detection/tracking, footfall, zones, heatmap |
| CV #2 | Queue engine, wait/service time, evaluation scripts |
| CV #3 / data | Shelf dataset, training, planogram match |
| Embedded | STM32 FreeRTOS node, sensors, tower light, RS-485/UART protocol |
| Backend / algo | MQTT, SQLite, FastAPI, fusion, Erlang-C forecast, sync |
| Frontend / pitch | Dashboard, reports, PPT, demo video, Q&A prep |

## Live demo script (≈ 4 minutes)

1. **Door:** a teammate walks in past the entrance camera → entry count +1 on dashboard; IR beam agrees.
2. **Forecast:** start the replay of a busy entrance video → forecast rises → "Open Counter 2 in ~5 min" + amber tower light + voice line in Telugu — *before* the queue camera shows a long queue.
3. **Shelf:** take all packets out of one slot → within ~1 min slot turns EMPTY, replenishment alert on phone; stand in front of the shelf → no false alert (occlusion gate).
4. **Hidden depletion:** remove items from the back of the load-cell tray → camera still "FULL", weight drops → fusion flags hidden depletion.
5. **Lost sale:** stand at the empty slot 10 s and leave → `LOST_SALE_RISK ₹` appears.
6. **Offline:** unplug the internet → everything keeps running; plug back → HQ view catches up.
7. **Privacy counter:** "Video stored: 0 bytes."
