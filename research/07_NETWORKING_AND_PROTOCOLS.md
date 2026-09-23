# 07 — Networking & protocols (what we need to know and say)

Plain-language version: **video stays inside the store; only small messages move.** Every link below is chosen so the store keeps working with no internet.

## 1. Map of links

```
CCTV/IP cam ──RTSP (TCP) / ONVIF──┐
Pi Camera ─────MIPI CSI-2─────────┤
USB webcam ────USB (UVC)──────────┤
                                  ▼
                           Edge node (Pi 5)
STM32 node ──UART / RS-485 (Modbus RTU)──▶ sensor-bridge ──▶ MQTT (Mosquitto, local)
Staff phones ◀──HTTP + WebSocket over store Wi-Fi (mDNS: storemind.local)──┘
HQ server ◀──MQTT bridge (TLS, QoS 1, store-and-forward) or HTTPS batch── only when online
```

## 2. Protocol cheat-sheet (for the viva)

| Link | Protocol | Why this one | Things to say |
|---|---|---|---|
| Camera → edge | **RTSP** (control) + **RTP** (video), H.264/H.265 | Every CCTV/NVR speaks it | Use **TCP** transport on Wi-Fi; use the **sub-stream**; read latest frame only |
| Camera discovery | **ONVIF** (SOAP over HTTP, WS-Discovery) | Vendor-neutral way to find cameras and their stream URLs | Lets the installer auto-discover the store's cameras |
| Pi camera | **MIPI CSI-2** | Direct, low latency, no compression | Pi 5 has two 4-lane camera/display connectors |
| MCU → edge (short) | **UART** 115200 8N1, text lines + checksum | Simple, debuggable | 3.3 V logic, common ground |
| MCU → edge (store-wide) | **RS-485** (differential, multi-drop), optionally **Modbus RTU** | Noise-immune over long cables, many shelves on one pair | Industrial standard → retailer's existing systems can read it |
| Sensors on the MCU board | **I²C** (ToF, BME280), GPIO (HX711 clock/data, IR beam via EXTI interrupt) | | VL53L0X default address 0x29 → use XSHUT pins for several |
| Inside the edge node | **MQTT** (Mosquitto) pub/sub | Decouples modules; retained "current state"; **Last Will** = automatic "node offline" message | QoS 0 for high-rate states, QoS 1 for alerts/events that must not be lost |
| Dashboard / phones | HTTP REST + **WebSocket** (FastAPI) | Live updates without refresh; works on any phone browser (PWA) | **mDNS** name `storemind.local`; if the store has no router, the Pi can be the Wi-Fi access point (hostapd / NetworkManager hotspot) |
| Edge → HQ | **MQTT bridge over TLS** (QoS 1, persistent session) or HTTPS batch upload | Store-and-forward: queue locally while offline, flush when online | Only aggregates (KB/hour), never video |
| POS / ERP | CSV export watcher, REST webhook, or DB read-only view | Most small POS/billing apps can export sales | Gives conversion rate and ₹ for lost-sales |
| Time | NTP when online, Pi 5 **RTC** (battery) when offline; MCU synced from Pi | Analytics are useless with wrong timestamps | |

## 3. Bandwidth argument (put numbers on the slide)

- One 1080p H.264 CCTV stream ≈ 2–4 Mbps ≈ **~20–40 GB/day** if sent to a cloud.
- StoreMind events: ~200 bytes each; a busy store might produce ~20,000 events/day ≈ **~4 MB/day**, and only hourly aggregates (≈ tens of KB/day) need to leave the store.
- That's a **>1,000× reduction** — and nothing leaves the store at all when offline.

(Rough figures — recompute with our measured event rates for the final deck.)

## 4. Offline-first rules

1. Every service works with the internet unplugged (test this in the demo: pull the Ethernet cable live).
2. Writes go to SQLite first; `sync_outbox` table holds anything meant for HQ; retries with backoff.
3. Idempotent uploads (each event has a UUID) → no duplicates after reconnect.
4. HQ never controls real-time decisions; it only receives summaries and sends config/model updates.

## 5. Security basics (judges ask)

- Cameras on a separate Wi-Fi/VLAN; change default DVR passwords; no port-forwarding of RTSP.
- MQTT with username/password locally; TLS for anything leaving the store.
- Dashboard login for owner; staff view limited.
- Signed model/config updates (hash check) before applying.
- No video persisted → nothing sensitive to leak.

## 6. Handy commands

```bash
# discover what's on the network (camera IPs)
sudo nmap -sn 192.168.1.0/24
# test an RTSP stream
ffprobe -rtsp_transport tcp "rtsp://USER:PASS@IP:554/Streaming/Channels/102"
# local MQTT broker
sudo apt install mosquitto mosquitto-clients
mosquitto_sub -t 'storemind/#' -v            # watch every event live
mosquitto_pub -t 'storemind/test' -m 'hello'
# read the STM32 serial lines
python3 -m serial.tools.miniterm /dev/ttyAMA0 115200
```
