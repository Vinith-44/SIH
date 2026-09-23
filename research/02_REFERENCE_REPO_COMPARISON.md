# 02 — The other team's repo: how strong is it really?

Repo: https://github.com/adarsh2133/SIH-Qualcomm-RetailAI-XLink (Team XLink, same PS 26179). Cloned and read on 23 Sep 2026.

## TL;DR

It **looks** much stronger than ours because of a long README, but the code is only a bit more mature than ours, and it has **no novelty**. We can beat it clearly. Copy its engineering hygiene, beat it on novelty, hardware, accuracy evidence and business value.

## What it actually contains

| File | Reality |
|---|---|
| `entry_counter.py` | YOLO11n (Ultralytics) + **ByteTrack** + foot-point line crossing + cooldown. Threaded capture/inference, reconnect, headless mode. Input size 224. |
| `queue_monitor.py` | YOLO11n people count inside one polygon (foot point) + median smoothing. **No wait time, no service time, no prediction.** Status = NORMAL / CONGESTED / CRITICAL by threshold. |
| `shelf_monitor.py` | Loads `shelf_model.pt` **which is not in the repo**; falls back to COCO `yolo11n.pt` (which knows bottles/cups, not SKUs). `STOCK_CAMERAS = []` → shelf cameras are empty by default. |
| `camera_config.py` | Phone cameras via DroidCam-style URLs `http://192.168.1.4x:4747/video` |
| `health_monitor.py` | CPU/RAM/disk/temp, service up/down, stale status files, network check |
| `metrics_api.py` + `dashboard.py` | Small HTTP JSON API + **CustomTkinter desktop** dashboard |
| `launch_retailedge.py` | Starts all processes |
| `Train_YOLO_Models.ipynb` | The unmodified public "Train YOLO Models" Colab tutorial by EJ Technology (candy dataset example) |
| README | Describes folders (`raspberry_pi/`, `local_machine/analytics/heatmap.py`, `tests/`, SQLite…) that **do not exist** in the repo |

## Side-by-side

| Capability | Ours today | XLink | Target for us |
|---|---|---|---|
| Person detector | EfficientDet-Lite0 (TFLite, 320) | YOLO11n (PyTorch, 224) | YOLO26n/11n or Qualcomm Person-Foot-Detection, exported to NCNN/LiteRT/Hailo/QNN |
| Tracker | Home-made centroid | ByteTrack | ByteTrack |
| Entry/exit counting | Box centre, fails on test video | Foot point + cooldown | Foot point + hysteresis band + **validated accuracy %** |
| Dwell / heatmap / zones | Yes (image-space) | README only | Floor-plan heatmap, per-zone dwell |
| Queue wait & service time | Yes (buggy) | No | Yes, per counter |
| Queue **prediction** | Noisy slope | No | **Arrival-rate + Erlang-C forecast (novel)** |
| Counter recommendation | Beep | Text status | "Open counter 3 in ~6 min" + tower light + voice |
| Shelf OOS | Own overfit grid model | Missing model | Slot-level state machine + occlusion gating |
| Planogram compliance | No | No | **Reference-snapshot matching, no per-SKU training (novel)** |
| Sensor fusion (weight, ToF) | Designed + HX711 working | No | Yes — key hardware differentiator |
| MCU / real-time | STM32 + FreeRTOS design | No | Yes |
| Storage | JSONL (wiped each run) | JSON status files | SQLite + aggregates |
| Dashboard | Demo | Desktop Tkinter | Web dashboard + phone PWA over store Wi-Fi |
| Health monitoring | No | Yes | Yes + camera-tamper detection |
| Qualcomm alignment | None | "Qualcomm-compatible" (words only) | **Qualcomm AI Hub profiling on real Snapdragon/Dragonwing devices** |
| Accuracy evidence | None | None | Measured table on our own recorded videos |

## What to copy (engineering basics — cheap wins)

1. Capture thread + inference thread, keep only the latest frame, auto-reconnect.
2. ByteTrack + **foot point** for zones and lines.
3. `HEADLESS=1` mode for the Pi.
4. Health monitor (CPU, temp, stale camera).
5. Environment-variable / config-file driven cameras.

## Where they are weak (our attack surface)

- No prediction, no queueing model, no counter recommendation logic.
- No working shelf model; no planogram compliance.
- No hardware beyond a Pi + phones; nothing real-time; no sensors; no actuation.
- No evaluation numbers.
- Ultralytics YOLO is **AGPL-3.0** — fine for a hackathon, but a "product" pitch should mention a licence plan (see `06_AI_MODELS_AND_DATA.md`).

> Don't copy their code into ours verbatim (it's their work; no licence file in the repo). Re-implement the ideas — they're standard patterns.
