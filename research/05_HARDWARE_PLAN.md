# 05 — Hardware plan: boards, STM32 role, sensors, cameras

## 1. Which edge board? (Pi 5 vs Jetson Nano vs Qualcomm)

| Board | AI compute | Approx. price | Good | Bad | Verdict |
|---|---|---|---|---|---|
| **Raspberry Pi 5** (we have it) | CPU only (4× Cortex-A76) | have it | Huge community, Picamera2, 2× MIPI camera ports, PCIe for AI HATs, **onboard RTC** (add battery) | No NPU; **no hardware H.264 encode/decode** (only HEVC decode); no 3.5 mm audio jack | **Main prototype node** |
| Pi 5 + **AI HAT+ 13 TOPS** (Hailo-8L) / **26 TOPS** (Hailo-8) | 13 / 26 TOPS | ~$70 / ~$110 | Plug-in NPU; YOLOv8s ≈ 128 FPS on Hailo-8L (community benchmark, batch 8, PCIe Gen 3) | Model must be compiled to Hailo HEF format | Best upgrade *if* budget allows |
| Pi 5 + **AI HAT+ 2** (Hailo-10H) | 40 TOPS (INT4), 8 GB own RAM | $130 | Also runs small LLMs/VLMs | Vision ≈ same as 26 TOPS HAT | Only if we do N9 (LLM report) |
| **Jetson Nano** (original 4 GB, from college) | 128 CUDA cores | free (college) | TensorRT; YOLOv8n ≈ 38.6 FPS network-only | **JetPack 4 is end-of-life** (4.6.6 final), Ubuntu 18.04 / old Python; one public benchmark got only **12.3 FPS end-to-end** live; and it's **NVIDIA in a Qualcomm problem statement** | Don't make it the core. Optional "2nd store" node to show multi-store sync |
| Jetson Orin Nano Super | 67 TOPS | ~₹48k+ | Very fast | Expensive, still not Qualcomm | Skip |
| **Radxa Dragon Q6A** (Qualcomm QCS6490) | 12 TOPS Hexagon NPU | ~$62 (4 GB) – $124 (16 GB) | **Qualcomm silicon**, 3 camera connectors, Ubuntu, YOLOv8 NPU examples | Import/availability in India; newer ecosystem | **Best "Qualcomm path" if we can get one** |
| RUBIK Pi 3 (QCS6490) | 12 TOPS | ~$99–179 | Qualcomm, Pi-like form factor | Availability | Alternative to Q6A |
| Qualcomm RB3 Gen 2 dev kit (QCS6490) | 12 TOPS | ~₹50k (IndiaMART listing) | Official Qualcomm kit, IM SDK + GStreamer | Costly | Ask college/mentors |
| **Arduino UNO Q** (Qualcomm QRB2210 + **STM32U585**) | Small (A53 CPU) | ~₹6.7k (Robocraze) | Linux + real-time MCU on one **Qualcomm** board, RPC "Bridge" between them | Too slow for multi-camera vision | **Shelf sensor node** / small vision node |

### Recommendation
- **Now → internal round:** Pi 5 (CPU) + active cooler + 1–2 cameras + Blue Pill sensor node. Add **Qualcomm AI Hub** latency numbers for the same models on QCS6490 (costs nothing but a Qualcomm ID sign-up).
- **Before finale:** get *one* Qualcomm board (Dragon Q6A / RUBIK Pi 3 / UNO Q) and run at least one pipeline on it live. "Prototype on Pi, production on Snapdragon/Dragonwing" is the story. Hailo HAT is the fallback if no Qualcomm board is possible.
- **Jetson Nano:** if the college gives it, use it as a second store node to demo HQ multi-store sync — never as the main AI device in the pitch.

## 2. What the STM32 does (fix the PPT claim)

The Blue Pill (STM32F103C8T6: 72 MHz Cortex-M3, 64 KB flash, 20 KB RAM) **cannot run vision**. Its job is the *real-time nervous system*:

| Task (FreeRTOS) | Rate | Output |
|---|---|---|
| HX711 load cells (1–2 trays / a grain bin) | 10 Hz, median + moving average, tare on button | `$W,<cell>,<grams>*CS` |
| VL53L0X/L1X ToF (hand-in-shelf) | 20 Hz | `$T,<zone>,<mm>*CS` |
| IR break-beam at door | interrupt | `$B,<door>,<ts_ms>*CS` (ground truth for camera counts) |
| "Restocked" push-button | interrupt, debounced | `$R,<shelf>*CS` |
| Actuators: tower light, buzzer | on command | executes `@L,<colour>` / `@Z,<pattern>` from the Pi |
| Health / heartbeat | 1 Hz | `$H,<uptime>,<errors>*CS` + IWDG watchdog |

- **Protocol:** NMEA-style text lines with an XOR checksum (easy to read in a serial monitor, robust enough), 115200 baud. Pi timestamps each line on arrival; a sync message every minute keeps MCU ticks aligned.
- **Wiring distance:** plain UART is fine for < 1–2 m. For shelves across a store use **RS-485** (MAX485 module, twisted pair, multi-drop, hundreds of metres) — optionally **Modbus RTU**, the industrial standard, so shelves look like standard industrial sensors.
- **Board caveats:** many "Blue Pills" are clones (CKS32/CS32) that fight with ST tools; USB on some clones has a wrong pull-up resistor → use a USB-TTL adapter (CP2102/CH340) or the Pi's GPIO UART. If you want on-MCU ML (e.g. weight-anomaly model with ST's STM32Cube.AI), an **STM32F411 "Black Pill"** (Cortex-M4F, 128 KB RAM) is a cheap upgrade.
- Pi GPIO UART: GPIO14 (TX, pin 8) / GPIO15 (RX, pin 10), 3.3 V logic on both sides, **common GND**. Enable the serial port in `raspi-config` (Interface → Serial: login shell *No*, hardware *Yes*).

## 3. Sensors — each one must remove an ambiguity (rule from our FreeRTOS guide)

| Sensor | Ambiguity it removes | Keep? |
|---|---|---|
| HX711 + load cell | Camera sees only the front row; loose grain bins | **Yes (core)** |
| VL53 ToF | Was the shopper actually reaching into the shelf? (pickup vs just standing) | Yes |
| IR break-beam at door | Ground truth to **measure** camera counting accuracy; backup counter | Yes (cheap, great for evaluation) |
| PIR | Coarse motion — camera already does it better | Skip |
| BH1750 light / BME280 | Context only (lighting quality affects CV confidence) | Optional |

## 4. Bill of materials (approximate prices — check before buying)

| Item | Qty | Approx. ₹ | Note |
|---|---|---|---|
| Raspberry Pi 5 | 1 | have | 8 GB preferred |
| Official active cooler | 1 | 500–700 | Sustained inference throttles without it |
| 27 W USB-C PSU | 1 | 1,000–1,500 | Underpowered Pi = random crashes |
| Pi Camera Module 3 **Wide** (120°) | 1 | 3,000–4,000 | + **Pi 5 camera cable (22-pin → 15-pin)**, bought separately |
| USB webcam 1080p | 1 | 1,500–2,500 | 2nd camera; or use existing CCTV |
| CR2032/ML2032 RTC battery for Pi 5 | 1 | 100–300 | Correct timestamps when offline |
| Blue Pill + ST-Link V2 | 1+1 | 250–450 + 300–500 | |
| HX711 + 5 kg load cell | 2 | 200–400 each | |
| VL53L0X ToF | 1–2 | 250–400 each | Use XSHUT pins to run several on one I²C bus |
| IR break-beam pair | 1 | 150–300 | |
| MAX485 module | 2 | 50–100 each | If shelves are far from the Pi |
| 12 V tower light + MOSFET/relay module | 1 | 600–1,300 | Visible "open counter" signal |
| USB speaker | 1 | 500–1,000 | Pi 5 has no 3.5 mm jack |
| Small UPS / power bank HAT | 1 | 1,000–2,500 | Power cuts are common; keeps the box alive |
| *(optional)* AI HAT+ 13 TOPS | 1 | ~7,000–10,000 | |
| *(optional)* Arduino UNO Q | 1 | ~6,745 | Qualcomm + STM32 shelf node |

## 5. Connecting cameras — step by step

Test script: `tools/camera_check.py` (works for CSI, USB, RTSP, HTTP phone and video files; saves a snapshot for zone calibration and prints real FPS).

### 5.1 Pi Camera Module 3 (CSI ribbon)
1. Power off. Pi 5 uses the smaller **22-pin** camera connector (CAM/DISP 0 or 1) — the cable that comes with the camera is 15-pin, so use the Pi 5 adapter cable. Contacts face the right way (check the connector latch diagram).
2. Boot and check:
   ```bash
   rpicam-hello --list-cameras        # should list imx708 (Camera Module 3)
   rpicam-still -o test.jpg           # take a photo
   rpicam-hello -t 0                  # live preview (needs a monitor)
   ```
3. Python (Picamera2 is preinstalled on Raspberry Pi OS):
   ```python
   from picamera2 import Picamera2
   cam = Picamera2(0)
   cam.configure(cam.create_video_configuration(main={"size": (1280, 720), "format": "RGB888"}))
   cam.start()
   frame = cam.capture_array()        # already BGR byte order → use directly with OpenCV
   ```
   If you use a venv, create it with `--system-site-packages` so it can see Picamera2.

### 5.2 USB webcam
```bash
v4l2-ctl --list-devices            # find /dev/videoX
```
```python
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # higher FPS than raw YUYV
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
```

### 5.3 Existing CCTV (DVR / NVR / IP camera) — the product path
1. Find the recorder's IP (router admin page, or ONVIF Device Manager on Windows).
2. Typical RTSP URLs (brand-dependent — confirm in the device's manual/settings):
   - Hikvision: `rtsp://USER:PASS@IP:554/Streaming/Channels/101` (ch1 main) · `/102` (ch1 **sub-stream**)
   - Dahua / CP Plus (many models): `rtsp://USER:PASS@IP:554/cam/realmonitor?channel=1&subtype=1` (sub-stream)
3. Test: open the URL in VLC (Media → Open Network Stream) or `ffprobe "rtsp://…"`.
4. **Use the sub-stream** (e.g. 640×360 or 704×576): enough for detection, far cheaper to decode.
5. Force RTSP over TCP (fewer broken frames on busy Wi-Fi):
   ```python
   os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
   cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
   ```
6. Always read in a **separate thread and keep only the newest frame** — otherwise OpenCV buffers old frames and our "real-time" system lags by seconds.
7. Pi 5 tip: it has a hardware **HEVC (H.265)** decoder but no H.264 hardware decoder. A low-res H.264 sub-stream is still fine in software; for many streams, set cameras to H.265 and test whether your FFmpeg/GStreamer build uses the hardware decoder.
8. Security: change default DVR passwords; put cameras on their own Wi-Fi/VLAN; never expose RTSP to the internet.

### 5.4 Phone as a camera (development only)
- Android "IP Webcam" app → `http://PHONE_IP:8080/video`
- DroidCam → `http://PHONE_IP:4747/video`
Phone and Pi on the same Wi-Fi.

### 5.5 Mounting (accuracy depends more on this than on the model)
| Camera | Placement |
|---|---|
| Entrance | 2.5–3 m high, looking down 45–90°, whole door width visible, counting line 1–2 m inside the door, perpendicular to walking direction. Steeper = fewer occlusions and fewer faces (privacy). Note: stock COCO models are weaker on pure top-down views — 45–60° is the safe choice unless we fine-tune. |
| Counter | Behind/side of the queue lane, whole lane + billing spot in view; one polygon per lane. |
| Shelf | Facing the shelf, 1.5–3 m away, slightly above; avoid tube-light glare on packets; rigid mount (any movement breaks slot calibration → our tamper check detects it). |

## 6. Power & reliability (Indian store reality)
- Power cuts: small UPS; SQLite in WAL mode (much more robust to sudden power loss); services auto-restart via systemd.
- No internet: everything local; RTC battery keeps time; sync resumes automatically.
- Heat: active cooler; monitor `vcgencmd measure_temp` in the health service.
