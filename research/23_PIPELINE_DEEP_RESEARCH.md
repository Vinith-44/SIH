# 23 — Deep research before implementation: software + hardware pipelines

Researched 24 Sep 2026 (≈45 sources). Goal: know **every way the system can break in a real store** and what the big players (NVIDIA Metropolis, Intel, Frigate, Qualcomm, Xovis, Focal Systems) already learned, **before** writing more code. Read §0, then use §3–§5 as the implementation checklist.

---

## 0. The 15 decisions this research leads to

1. **Copy the proven industry pattern:** *ingest → (motion gate) → detect → track → analytics → event broker → store → KPIs by SQL*. NVIDIA's DeepStream retail sample and Intel's retail kits have exactly this shape. Our code already matches it, so we're on the right track.
2. **Put a restreamer in front of every camera** (go2rtc or MediaMTX). DVRs often allow only a few connections; Frigate's rule is *"one connection to the camera; everything else reads the restream."*
3. **Use a motion gate before the detector** (Frigate): shelf and quiet cameras cost almost nothing when nothing moves.
4. **Fix the exit over-count with a *two-line gate / zone sequence*** (count only A→B→exit with a minimum displacement and minimum track age), the approach used by DeepStream `nvdsanalytics` direction modes and Lumeo "chained lines + minimum dwell". Keep hysteresis.
5. **Evaluate OC-SORT and BoT-SORT (no ReID) against ByteTrack.** On MOT17 (tuned), IDF1: ByteTrack 72.7, OC-SORT 76.5, BoT-SORT 78.7. Our CAVIAR ID switches (63) should drop.
6. **Queue = membership problem, not a polygon.** Filter passers-by by **speed + minimum dwell**, merge **companions into one party**, use a **polyline lane / occupancy grid** for bent queues, and cross-check wait time with **Little's law (W = L / λ)**. It survives ID switches.
7. **Staff exclusion:** exclude the **cashier zone** and give staff a **printed pattern badge (ArUco-style)**. That's the same idea Xovis sells (uniform pattern tag, identifies *staff*, never *individuals*).
8. **The billing "service end" is invisible to cameras, so listen for it.** 94% of small merchants use UPI, and soundboxes announce every payment. A **MEMS microphone + a tiny on-device classifier** can detect "payment received" chimes, giving **transactions, conversion rate and service time with no POS integration**. It never records audio. **India-specific and novel.** (This gives the team's "MEMS sensor" a real job.)
9. **Shelves: copy Focal Systems' cadence, not video.** Focal scans shelves **hourly** with battery Wi-Fi shelf cameras (~400 per 30k sq ft Walmart Canada store). We: **every 1–5 min**, occlusion-gated, plus optional **ESP32-S3 shelf-edge camera nodes** for slots CCTV can't see.
10. **Make the shelf engine lighting-robust:** compare **edges/gradients (SSIM on CLAHE-normalised images)**, keep **per-lighting references** (day/night), and auto-re-reference after "restocked".
11. **The Qualcomm path is concrete now:**
    - LiteRT + **`libQnnTFLiteDelegate.so` (`backend_type: htp`)** in Python on QCS6490.
    - Or ONNX Runtime QNN EP.
    - Or Ultralytics `format="qnn"` (HTP arch "68"; QCS6490 is HTP v68, but the doc doesn't list QCS6490 by name, so verify).
    - Or IM SDK GStreamer (`qtimlvconverter → qtimltflite/qtimlqnn → qtimlvdetection`).
    - **Prove the NPU is really used** (the `accel_verify` idea from the dragon_q6a_yolo suite).
12. **The RUBIK Pi 3 (QCS6490) takes the *same* Raspberry Pi Camera Module 3 and has a Pi-compatible 40-pin header** (UART on pins 8/10). Our Pi 5 camera + STM32 wiring moves to Qualcomm silicon unchanged. Put that on a slide.
13. **Energy per frame without buying a meter:** Pi 5 PMIC via `vcgencmd pmic_read_adc`. It misses USB/HAT power, so apply the published correction *real W ≈ 1.1451 × PMIC W + 0.5879*. The Qualcomm benchmark to beat: **Pi 5 CPU 1,423 mJ/frame vs QCS6490 NPU 129 mJ/frame**.
14. **"Ask your store" is feasible on QCS6490:** Qwen3.5-0.8B runs on its NPU at **~7.4 tokens/s decode, 82 tokens/s prefill** (ORT + QNN EP). A 40-token SQL answer takes about 6 s. Pi 5 CPU with llama.cpp is the fallback.
15. **Test like Intel:** replay recorded clips as **fake CCTV over RTSP (MediaMTX + ffmpeg loop)**. Report **"stream density"**: how many cameras one box sustains at target FPS. Add a **24 h soak test** and **chaos tests** (unplug camera, kill Wi-Fi, cut power).

---

## 1. What the big players do (and what we copy)

| System | Architecture | Copy this | Skip this |
|---|---|---|---|
| **NVIDIA DeepStream retail sample** | PeopleNet detector → basket classifier → NvDCF tracker → custom message converter → **Kafka** → **ksqlDB** SQL streams → Django API → dashboard, all in Docker | Event-driven decoupling; **KPIs as SQL over events** (we do SQLite views); secondary classifier idea (basket / no basket = conversion proxy) | Kafka/ksqlDB (too heavy for a Pi) → MQTT + SQLite |
| **DeepStream `nvdsanalytics`** | ROI filter, overcrowding, **direction detection**, **line crossing = line + direction vector with strict/balanced/loose modes**, "extended" line | Direction-vector check + mode on our line counter; overcrowding alert per ROI | — |
| **NVIDIA Metropolis 3.2 / VSS 3.2** (Jul 2026) | Vision-AI *agents*: natural-language search, summarise and alert over live video; multi-camera tracking; synthetic data (Cosmos) | "Ask your store" over **events**; daily summary | Running VLMs over video on a Pi |
| **Intel retail kits** (loss-prevention, self-checkout) | GStreamer + DL Streamer + OpenVINO; **MediaMTX replays .mp4 as RTSP**; configurable **inference interval**; **"stream density"** benchmark; **"LVLM invoked by agent only when CV is uncertain"** | All four ideas | — |
| **Frigate NVR** | **go2rtc restream**, low-res *detect* stream, **motion first → detect only in motion regions**, multiprocess detectors, MQTT events, zones with filters | Restream, motion gate, per-object filters (min area, min score, stationary handling) | Recording video (privacy) |
| **Qualcomm IM SDK** (RB3 Gen 2 / RUBIK Pi 3) | GStreamer: `v4l2src/qtiqmmfsrc → qtimlvconverter → qtimltflite \| qtimlqnn \| qtimlsnpe → qtimlvdetection → qtivcomposer → waylandsink \| v4l2h264enc → qtirtspbin` | Name it as our production path: DeepStream-equivalent on Qualcomm | It has no tracker/analytics plugin, so our Python tracking and analytics stay |
| **Xovis / Irisys** | Dedicated overhead 3D/thermal sensors; queue prediction; **staff-exclusion pattern badge** | Staff badge idea; prior-art citation for N1 | Proprietary sensors |
| **Focal Systems** | Battery Wi-Fi **shelf cameras**, **hourly** scans, discards images containing people, ~400 cams/store | Scan cadence; privacy (drop frames with people, which our occlusion gate already does) | 400 cameras (cost) |

**Mapping to sell to Qualcomm judges:** *"We built the DeepStream-style pipeline, but on Qualcomm: IM SDK for decode/pre-process/NPU inference, our tracker + analytics + fusion on top, MQTT like Frigate, SQL KPIs like NVIDIA's retail sample."*

---

## 2. Target software pipeline (v2)

```
CCTV/IP cams ─RTSP─► go2rtc/MediaMTX (1 conn per camera, restream on :8554)
Pi Cam 3 (CSI) ────► │
ESP32-S3 shelf-cam ─MQTT JPEG every 1–5 min─┐
                     ▼                      │
            Ingest (latest-frame, per-task FPS) ─► Motion gate (frame diff on low-res)
                     ▼                                   │ no motion → skip
            Detector backend:  Pi 5: NCNN/LiteRT CPU │ Hailo │ QCS6490: LiteRT+QNN(htp) / ORT-QNN / IM SDK
                     ▼
            Tracker: ByteTrack (default) │ OC-SORT │ BoT-SORT-noReID (chosen by CAVIAR IDF1)
                     ▼
   ┌──────── Analytics ───────────────────────────────────────────────┐
   │ Footfall: 2-line gate + direction + min track age + hysteresis   │
   │ Queue: membership (speed+dwell), party merge, polyline lane,     │
   │        wait (timers) + Little's-law cross-check                   │
   │ Shelf: occlusion gate → CLAHE+edge SSIM vs per-lighting reference │
   │        → K-of-N vote → slot state; optional PatchCore anomaly     │
   │ Staff filter: cashier zone + pattern-badge detector               │
   └──────────────────────────────────────────────────────────────────┘
                     ▼                                   ▲
   Fusion ◄── MQTT ◄── sensor-bridge ◄── STM32 (HX711, ToF, IR beam, LD2450 radar, button)
     │                                  ◄── audio node (UPI chime detector)
     ▼
   Events → SQLite (WAL) → SQL views (KPIs) → FastAPI/WebSocket → dashboard + phone
                                           → alerts (tower light, voice) → HQ sync (optional)
```

---

## 3. Problem-by-problem playbook (what WILL go wrong in a real store)

### 3.1 Entry/exit counting
| Problem | Why | Fix (priority) |
|---|---|---|
| Over-count (our CAVIAR exits 85.7%) | People loiter/turn back near the line; ID switch creates a "new" person on the other side | **P0:** two-line gate or zone sequence (A→B), **direction vector check** (nvdsanalytics "strict"), **min displacement** (e.g. ≥ 0.5 m in floor coords), **min track age** (≥ 0.5 s), one count per track per direction. Tune on corridor clips, test on front clips (no overfitting) |
| ID switches in crowds | Occlusion; ByteTrack is motion-only | **P1:** try OC-SORT / BoT-SORT (no ReID) via `trackers`; raise detector input (recall 0.73 @320 → 0.81 @640 on CAVIAR); keep ≥ 8 FPS |
| Groups / children hidden behind adults | Body boxes merge | **P2:** head-detection model (CrowdHuman-trained YOLO head detectors exist) for the entrance camera; reconcile head + body counts (Lumeo does this) |
| Staff walking in and out all day | Counted as customers | **P1:** cashier/back-door exclusion zones + **pattern badge** (print an ArUco-style tag; detect with OpenCV `aruco`) → staff events excluded, never identified |
| Trolleys, bags, reflections | False detections | **P1:** class filter + min box size + min score per zone (Frigate-style filters) |
| Night / low light | Detector recall drops | **P2:** IR-capable CCTV; per-hour confidence threshold; report accuracy separately for day/night |

### 3.2 Queue
| Problem | Why | Fix |
|---|---|---|
| Passers-by counted as queue | Polygon only | **P0:** speed threshold (walking-through velocity) + **minimum dwell (e.g. 5 s)** before joining (Zone24x7 uses a velocity threshold) |
| Companions = 2–3 "customers" | Family/friends queue together | **P1:** party clustering: people who stay within ~1 m and leave together = 1 party; report *parties* and *people* |
| Curved/bent queues, overflow into aisles | Straight polygon misses tail | **P1:** polyline lane + occupancy grid; "tail zone" overflow alert |
| Occlusion at peak (queue hides itself) | Camera angle | **P0:** high/overhead mounting; **radar LD2450 at counter** (3 targets) fused with camera; **Little's law** W = L/λ as a cross-check that doesn't need tracks |
| Service end not visible | Payment is invisible | **P1 (novel):** **UPI soundbox chime detector** (MEMS mic) → transaction event; or POS CSV; or "customer leaves billing polygon" |
| Mid-queue joins, leaving and re-joining | Real behaviour | **P2:** gap-tolerant timers (done) + rejoin window |
| Balking (sees queue, leaves) / reneging (leaves queue) | Lost revenue | **P1:** new KPIs: entered-queue-zone-but-left < N s = balk; left queue before service = renege (Zone24x7 reports 50% balk reduction as a headline metric) |
| Alert flapping | Threshold noise | **P0:** hysteresis + persistence (done partly; keep) |
| Clocks differ across cameras/sensors | No time sync | **P1:** Pi runs **chrony as local NTP server** for cameras; RTC battery; STM32 synced from Pi |
| Multiple counters | Assignment ambiguity | **P1:** one lane polygon + billing spot per counter; per-counter μ |

### 3.3 Shelf
| Problem | Why | Fix |
|---|---|---|
| Lighting changes (sun, tube lights, night) | Raw pixel compare breaks | **P0:** CLAHE + **edge/gradient SSIM**; per-lighting reference set; auto re-reference after restock |
| Glare on glossy packs | Specular highlights | **P2:** camera angle; polarising filter; mask glare pixels |
| Shopper in front | Occlusion | **Done** (occlusion gate); also drop frames with people for privacy (Focal does the same) |
| Deep shelves (back empty) | Camera sees front only | **Done in design:** load cell fusion |
| Far/small slots on CCTV | Resolution | **P2:** **ESP32-S3 shelf-edge camera nodes** (JPEG every 1–5 min over MQTT, deep sleep between); per-slot homography rectification |
| New/changed products | Planogram drift | "Restocked" button re-captures reference (done); WRONG_ITEM via embeddings |
| "Something's off" without labels | Unknown failure types | **P3:** PatchCore anomaly (anomalib) per slot trained on "good" images only |
| Camera bumped | Calibration invalid | **Done:** tamper/moved detection |

### 3.4 Detection & compute
| Problem | Fix |
|---|---|
| 5× speed swings on the laptop (thermal) | Benchmark only on the target device, one sitting; report median + p95; **stream density** |
| Pi 5 CPU too slow for many cameras | Motion gate + per-task FPS + INT8; then Qualcomm NPU (8.7× FPS in the Pi 5 vs RUBIK Pi 3 benchmark) |
| "NPU used" claims that aren't true | Verify accelerator use (kernel accounting, like `accel_verify.py`); show latency with delegate on/off |

---

## 4. Hardware integration pipeline

### 4.1 Cameras
- **Existing CCTV:** RTSP sub-stream → go2rtc/MediaMTX restream → our ingest. Read-only DVR user. Written permission.
- **Pi Camera Module 3 (IMX708):** works on Pi 5 *and* **RUBIK Pi 3 (QCS6490)**, which lists IMX708/IMX477/IMX219 support on 2× 4-lane CSI. Same camera, zero change.
- **ESP32-S3 shelf-edge node (optional, novel hardware):** OV2640/OV5640, captures JPEG every 1–5 min, MQTT to the Pi, deep-sleeps between shots (ESP32-S3 camera designs run for months on batteries at low capture rates).
- **Decode:** Pi 5 has HEVC hardware decode only (H.264 in software). On QCS6490 use the IM SDK/v4l2 hardware decoders. Always use sub-streams.

### 4.2 STM32 Blue Pill sensor node: resource map (fits the F103C8)
| Peripheral | Connects to | Notes |
|---|---|---|
| USART1 (PA9/PA10) | Pi 5 / RUBIK Pi UART (pins 8/10) | 115200; 3.3 V both sides; common GND |
| USART2 (PA2/PA3) | **LD2450 radar** | **256000 baud** (BRR error ≈ 0.3% at 36 MHz APB1, fine); 30-byte frames `AA FF 03 00` + 3×8-byte targets + `55 CC`; **no CRC**, so validate header/tail/length |
| USART3 (PB10/PB11) | MAX485 (optional RS-485 to more shelves) | Modbus RTU optional |
| I2C1 (PB6/PB7) | VL53L0X/L1X ×N | Same address 0x29 → XSHUT pins to assign addresses at boot |
| GPIO ×2 per HX711 | Load cells | Bit-banged: **PD_SCK high > 60 µs powers the HX711 down**, so do the 24–27 clock pulses inside a short critical section; use **DOUT falling-edge EXTI → task notification** instead of polling (Gemini's "interrupt-driven" advice) |
| EXTI | IR break-beam | Timestamp in ISR, defer to a task |
| GPIO → MOSFET/relay | 12 V tower light, buzzer | Flyback diode; separate 12 V supply |
| IWDG | Watchdog | Health task kicks it |

FreeRTOS on 20 KB RAM is feasible for ~6 small tasks (HX711, ToF, radar parse, IR, UART-TX/RX, actuator/health). Measure stack high-water marks. If RAM runs out, use an **STM32F411 Black Pill** (128 KB RAM, FPU).

**Radar zone gating on the MCU:** keep only targets inside the counter rectangle (x/y bounds) and require N consecutive frames. Kills ghost/multipath targets (metal racks reflect 24 GHz).

### 4.3 MEMS sensor: give it one clear job
- ❌ MEMS accelerometer "shelf touch": weak signal, easily confused. Drop it.
- ✅ **MEMS microphone = UPI soundbox "payment received" detector.** STM32F103C8 has **no I2S** (only high-density F103 parts do), so put the mic on an **ESP32-S3** (I2S, tinyML via Edge Impulse keyword spotting) or a **USB mic on the Pi**. Emit only `$A,<counter>,PAYMENT*CS` events. **No audio stored or transmitted.**
- Evidence it matters: **94% of small merchants use UPI** (DFS study, Feb 2026). Soundboxes helped bring **65–70 million merchants** onto digital payments (NPCI–BCG). Paytm alone has **1.5 crore** subscription merchants with device-led growth. NPCI is building a **unified soundbox** (May 2026).
- Caveats: several soundboxes (different phrases) → train on each brand's chime/phrase; background music → a confidence threshold; place the mic near the soundbox.

### 4.4 Protocol MCU ↔ edge
- **Demo mode:** human-readable NMEA-style lines with XOR checksum (easy to show judges in a serial monitor).
- **Production mode:** **COBS framing + CRC16** binary packets (standard robust UART framing; zero-byte delimiters, resync after noise). Same message set.
- Messages: `W` weight, `T` ToF, `B` beam, `Q` radar targets, `A` audio event, `R` restocked button, `H` heartbeat; commands `L` light, `Z` buzzer, `S` time-sync.

### 4.5 Power & energy measurement
- Pi 5: `vcgencmd pmic_read_adc`, sum V×I over rails, apply *real ≈ 1.1451×PMIC + 0.5879 W* (misses USB/HAT loads). Or an INA219 on the STM32 for the whole box.
- Report **mJ per frame** and **mJ per "insight"** (per counted entry / per shelf check), next to the published QCS6490 numbers.

### 4.6 PCB (team's "PCB condensation")
- v1 = carrier board: Blue Pill socket + JST headers (HX711 ×2, ToF ×2, LD2450, IR beam, MAX485, mic node, tower-light MOSFET), 12 V → 5 V/3.3 V buck, TVS/ESD on external lines, SWD header, test points. Design in **KiCad**; show the schematic in the deck even if the board arrives late.

---

## 5. Qualcomm deployment pipeline (concrete)

1. **Profile first (free):** Qualcomm AI Hub compile + profile on a QCS6490 device → latency table.
2. **Run on the NPU, pick one:**
   - LiteRT + QNN delegate:
     ```python
     delegate = tflite.load_delegate("libQnnTFLiteDelegate.so", {"backend_type": "htp"})
     interpreter = tflite.Interpreter(model_path=MODEL, experimental_delegates=[delegate])
     ```
   - ONNX Runtime + QNN EP (the route the Qwen3.5-0.8B-on-Q6A project uses).
   - Ultralytics `model.export(format="qnn", name="68")` → a self-contained ONNX with a QNN context binary (INT8 weights / 16-bit activations). QCS6490 = HTP v68; not named in the Ultralytics doc, so verify on the device.
   - IM SDK GStreamer for zero-copy camera → NPU → overlay.
3. **Verify the NPU really runs it** (per-process accelerator accounting; the delegate on/off A/B).
4. **Measure:** FPS, ms, W, mJ/frame, °C. Compare with Pi 5 CPU (published: 3.91 vs 34.22 FPS; 1,423 vs 129 mJ/frame; 70 vs 54 °C).
5. **Gotchas:** fastrpc/CDSP firmware must be loaded; QAIRT runtime ↔ skel versions must match (mismatch → error `0x80000600`). Use the vendor's Ubuntu image.

---

## 6. Reliability & operations (what makes it "rock solid")

- **systemd** unit per service, `Restart=always`, `WatchdogSec=` with `sd_notify` heartbeats; boot-time auto-start; log rotation.
- **SQLite WAL** + nightly compaction; retention policy; config versioned in git; every event carries a UUID (idempotent sync).
- **Time:** chrony on the Pi (NTP server for cameras + STM32 sync); RTC battery.
- **OTA:** `git pull` + service restart for the hackathon. Production: containers + a fleet OTA tool; Foundries.io (Qualcomm-owned) is the Qualcomm-aligned story.
- **Security:** cameras on their own VLAN/SSID, read-only DVR user, no port forwarding, TLS for any uplink.
- **Testing ladder:**
  1. unit tests (have 124);
  2. **RTSP replay** of recorded clips via MediaMTX + `ffmpeg -re -stream_loop -1` (fake CCTV);
  3. **stream density** test;
  4. **24 h soak** (memory leaks, disk growth);
  5. **chaos**: unplug a camera, kill Wi-Fi, pull power, cover a lens: the system must degrade and alert, not crash;
  6. **field test** in a real shop (bucket B).

---

## 7. Edge GenAI: where it actually helps

- **"Ask your store" over events** (not video): Qwen-class 0.5–1.5B model → read-only SQL on whitelisted views → answer cites the rows. QCS6490 NPU ≈ 7.4 tok/s decode (Qwen3.5-0.8B); Pi 5 CPU via llama.cpp as fallback.
- **VLM only when CV is uncertain** (Intel's rule): e.g. confirm a WRONG_ITEM shelf flag once, or describe an anomaly. Never per frame, never on people.
- **Daily summary** in Telugu/Hindi/English from computed numbers only.

---

## 8. Don'ts (cost us marks or reliability)

- No face recognition, demographics, emotion or "behaviour scoring". Say "anonymous footfall, dwell, queues".
- No per-frame VLM. No Jetson as the main device in a Qualcomm PS.
- Don't tune on the same clips you report (split corridor/front).
- Don't claim N1 as novel (Irisys/Xovis prior art).
- Don't record audio or video, ever. Events only.

---

## 9. Implementation order after team confirmation (with acceptance tests)

| # | Work | Accept when |
|---|---|---|
| 1 | Counting v2: two-line gate + direction + min displacement/age; tracker bake-off (ByteTrack / OC-SORT / BoT-SORT) | CAVIAR exit accuracy ≥ 90% on the *held-out* view; ID switches reported |
| 2 | Ingest v2: go2rtc/MediaMTX restream, motion gate, RTSP replay test harness, stream-density benchmark | 3 fake-CCTV streams + 1 Pi cam run 1 h without lag growth |
| 3 | Queue v2: membership (speed+dwell), party merge, polyline lane, Little's-law cross-check, balk/renege KPIs, staff zone | Synthetic + own canteen clip: queue MAE ≤ 1, wait error ≤ 20% |
| 4 | Sensor node: bridge + simulator + FreeRTOS firmware (HX711 EXTI, ToF, IR, **LD2450 gated**, tower light), NMEA demo + COBS/CRC mode | Simulator and a real board both drive dashboard events; 1 h no framing errors |
| 5 | UPI chime detector (ESP32-S3 or Pi USB mic) → `$A` events → transactions, conversion, service time | ≥ 90% chime detection on 50 recorded chimes; 0 false triggers on 10 min of shop noise |
| 6 | Shelf v2: CLAHE+edge SSIM, per-lighting references; optional ESP32-S3 shelf-edge node | Day/night test on our demo shelf: EMPTY F1 ≥ 0.85 |
| 7 | Hardware panel + energy (PMIC formula), systemd + watchdog, 24 h soak, chaos tests | Soak passes; chaos tests degrade gracefully |
| 8 | Qualcomm: AI Hub profile → run on QCS6490 NPU (if board) with verified acceleration | FPS/W/mJ table vs Pi 5 |
| 9 | Ask-your-store (local LLM → SQL), then reorder drafts, then the Kaggle shelf notebook | 20 test questions, 0 invented numbers |

## 10. Decisions needed from the team

1. **Buy/borrow a Qualcomm board?** RUBIK Pi 3 or Radxa Dragon Q6A (QCS6490). Same Pi camera and header. This is the single biggest "impress Qualcomm" step.
2. **MEMS sensor = UPI chime microphone?** (recommended; needs an ESP32-S3 or a USB mic)
3. **ESP32-S3 shelf-edge cameras**: in or out of scope?
4. **PCB v1** in KiCad now (schematic for the deck), fabricate later?
5. **Field test location** (canteen / a friendly kirana) and permission letter.

---

## Sources
Industry stacks: [DeepStream retail analytics](https://github.com/NVIDIA-AI-IOT/deepstream-retail-analytics) · [nvdsanalytics](https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvdsanalytics.html) · [DeepStream occupancy analytics](https://github.com/NVIDIA-AI-IOT/deepstream-occupancy-analytics) · [Metropolis 3.2 / DeepStream 9.1 (Jul 2026)](https://quasa.io/media/nvidia-metropolis-3-2-and-deepstream-9-1-enable-6x-faster-vision-ai-agent-development) · [NVIDIA VSS blueprint](https://developer.nvidia.com/blog/advance-video-analytics-ai-agents-using-the-nvidia-ai-blueprint-for-video-search-and-summarization/) · [Intel loss-prevention kit](https://github.com/intel-retail/loss-prevention) · [Intel automated self-checkout](https://github.com/intel-retail/automated-self-checkout) · [Frigate](https://github.com/blakeblackshear/frigate) · [Frigate restream/go2rtc](https://docs.frigate.video/configuration/restream/) · [MediaMTX publish](https://mediamtx.org/docs/usage/publish)
Qualcomm: [RUBIK Pi 3 IM SDK samples](https://www.thundercomm.com/rubik-pi-3/en/docs/rubik-pi-3-user-manual/1.0.0-u/Application%20Development%20and%20Execution%20Guide/IMSDK/Customize-Sample/) · [RUBIK Pi 3 hardware](https://developer.ridgerun.com/wiki/index.php/Rubik_Pi_3/Hardware_Overview) · [LiteRT on NPU (Qualcomm docs)](https://docs.qualcomm.com/doc/80-70029-15B/topic/run-a-litert-model-using-delegate.html) · [Ultralytics QNN export](https://docs.ultralytics.com/integrations/qnn/) · [dragon_q6a_yolo benchmark suite](https://github.com/ZephyrSai/dragon_q6a_yolo) · [Qwen3.5-0.8B on QCS6490 NPU](https://huggingface.co/PrismPhi/Qwen3.5-0.8B-Radxa-Dragon-Q6A-QCS6490-QNN-NPU) · [QCS6490 = HTP v68 (executorch issue)](https://github.com/pytorch/executorch/issues/7356) · [Pi 5 vs RUBIK Pi 3 NPU benchmark](https://consult.red/insights/npus-vs-cpus-for-edge-ai-vision-less-heat-less-power-more-headroom/)
Queue & counting: [Zone24x7 queue white paper](https://zone24x7.com/wp-content/uploads/2022/01/white-paper-queue-detection-white-paperd-2.pdf) · [CamThink queue analytics pitfalls](https://www.camthink.ai/blog/retail-queue-wait-time-analytics/) · [Roboflow queue tutorial](https://blog.roboflow.com/monitor-retail-queues/) · [Lumeo people counting](https://www.lumeo.com/solutions/people-counting) · [Little's law (Little & Graves)](https://web.eng.ucsd.edu/~massimo/ECE158A/Handouts_files/Little.pdf) · [Tracker comparison (Roboflow trackers)](https://trackers.roboflow.com/latest/trackers/comparison/) · [CrowdHuman head detector](https://github.com/Owen718/Head-Detection-Yolov8) · [Xovis staff exclusion](https://www.xovis.com/technology/sensor/ai-extensions/staff-exclusion) · [Milesight staff exclusion](https://www.milesight.com/iot/innovation/ai-people-counting/staff-exclusion)
Shelf: [Focal Systems shelf cameras](https://focal.systems/shelf-cameras/) · [Focal at Walmart Canada](https://www.vision-systems.com/non-factory/article/14283989/vision-system-at-walmart-canada-tracks-inventory-on-store-shelves) · [anomalib](https://github.com/open-edge-platform/anomalib) · [ESP32-S3 low-power camera](https://www.hackster.io/camthink2/ultra-low-power-esp32-s3-event-triggered-vision-ai-camera-ac4b2d)
Hardware: [LD2450 protocol (Rust crate docs)](https://docs.rs/ld2450/latest/ld2450/) · [LD2450 protocol PDF](https://make.net.za/wp-content/datasheets/HLK%20LD2450%20Serial%20Communication%20Protocol%20v1.03.pdf) · [HX711 library/timing](https://github.com/bogde/HX711) · [COBS framing article](https://www.embeddedrelated.com/showarticle/113.php) · [Pi 5 power via PMIC](https://github.com/jfikar/RPi5-power) · [ST AN5027 PDM mics](https://www.st.com/resource/en/application_note/an5027-interfacing-pdm-digital-microphones-using-stm32-mcus-and-mpus-stmicroelectronics.pdf) · [Edge Impulse KWS on XIAO ESP32-S3](https://wiki.seeedstudio.com/xiao_esp32s3_keyword_spotting/)
UPI/soundbox: [Small merchants & UPI (Business Standard, Aug 2026)](https://www.business-standard.com/finance/news/small-merchants-built-upi-s-reach-here-s-what-government-data-shows-126081700057_1.html) · [NPCI unified soundbox (Inc42, May 2026)](https://inc42.com/buzz/npci-to-roll-out-unified-soundbox-infrastructure-for-merchants-report/)
