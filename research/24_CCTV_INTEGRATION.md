# 24 — Connecting to a store's existing CCTV (design, onboarding, security, demo)

Researched 24 Sep 2026. PPT diagram: `diagrams/cctv_integration.png`.

**In one line:** StoreMind plugs into the CCTV a shop already has. One LAN cable to the recorder, a read-only login, and the box reads a low-resolution copy of each camera's stream, like opening a live link. **Their recording is untouched, nothing is stored, and nothing goes to the internet.**

Analogy: the DVR is a TV station that already broadcasts every camera. We're one more TV in the room, tuned to the low-quality channel, watching and never recording.

---

## 1. What Indian stores actually have (so we support the right things)

| Fact | Implication for us |
|---|---|
| **IP cameras ≈ 60% of India's camera sales in 2026**; analog-HD DVRs (HDCVI/AHD/TVI) are the rest and dominate the installed base in small shops | Support **both**: IP NVRs and analog DVRs. Both expose RTSP per channel over the network |
| **CP Plus is the market leader** (≈20.8% share FY25 → ≈45.4% in Q4 FY26); Indian brands >80% of the market by Feb 2026 | **CP Plus first-class support.** Most CP Plus recorders use Dahua-style URLs |
| New **STQC / Essential Requirements (ER)** cybersecurity certification for CCTV (OWASP-based testing, trusted supply chain), enforced from April 2025 with phased enforcement into 2026. Hikvision & Dahua reportedly left India's internet-connected segment | Many existing shops still run **older Hikvision/Prama/Dahua** units, so support them too. If we ever *sell our own camera node* (e.g. ESP32 shelf cam), it would need STQC/ER certification. Our box only *reads* streams |
| Small shops often use **Wi-Fi cameras** (CP Plus Ezykam, Tapo, etc.) instead of a DVR | Support **direct camera RTSP** (e.g. Tapo `stream2`, via a "camera account") |
| Typical kirana: **2–4 cameras** on a 4-channel DVR, usually pointed at the **cash counter and entrance** (theft deterrence) | Those two views are exactly our **queue** and **footfall** cameras. Shelves often need an extra view |

## 2. Six ways to connect (ranked)

| # | Method | When | How |
|---|---|---|---|
| **A** | **RTSP sub-stream from the DVR/NVR** (default) | Any networked recorder | One URL per channel, sub-stream (e.g. 640×360), read-only user |
| **B** | **ONVIF discovery** (Profile S / T) | IP cameras/NVRs that support ONVIF | Auto-find devices, list profiles, pick the low-res profile's stream URI automatically |
| **C** | **HTTP snapshot** (JPEG) | **Shelves**: need one image every 1–5 min, not video | Hikvision `/ISAPI/Streaming/channels/<ch>01/picture`; Dahua/CP Plus `/cgi-bin/snapshot.cgi?channel=<n>`; go2rtc `/api/frame.jpeg?src=<cam>`. Almost zero load |
| **D** | **Direct camera RTSP** | Standalone Wi-Fi/IP cameras | e.g. Tapo `rtsp://user:pass@IP:554/stream2` (camera account; local network only) |
| **E** | **HDMI capture** | Old DVR with no usable network | DVR HDMI out → USB HDMI capture card → Pi (one multi-view feed; crop per camera) |
| **F** | **Add our own camera** | No CCTV covers the shelf/counter | Pi Camera Module 3 (works on Pi 5 **and** RUBIK Pi 3), or ESP32-S3 shelf node |

## 3. URL cheat-sheet (confirm on the actual device; firmware varies)

| Brand | Live stream (RTSP) | Snapshot (HTTP) | Notes |
|---|---|---|---|
| **Hikvision / Prama** | `rtsp://USER:PASS@IP:554/Streaming/Channels/102` (ch1 sub) · camera *n* → `n01` main, `n02` sub | `http://IP/ISAPI/Streaming/channels/101/picture` | Digest auth; older firmware: `/h264/ch1/sub/av_stream` |
| **CP Plus (most recorders) / Dahua** | `rtsp://USER:PASS@IP:554/cam/realmonitor?channel=1&subtype=1` (subtype 0 = main, 1 = sub) | `http://IP/cgi-bin/snapshot.cgi?channel=1` | Some CP Plus models use port **5543** or `/live/channel0`; check the model |
| **Tapo (TP-Link)** | `rtsp://USER:PASS@IP:554/stream1` (HQ) · `/stream2` (low) | via go2rtc frame API | Create "Camera Account" in the app; ONVIF on port **2020** |
| **Generic ONVIF** | Discovered via ONVIF `GetProfiles` → `GetStreamUri` | ONVIF `GetSnapshotUri` | Works across brands that implement Profile S/T |

Useful ports when scanning a shop network: 554 (RTSP), 80/443 (web/ISAPI), 8000 (Hikvision SDK), 37777 (Dahua SDK), 2020 (Tapo ONVIF), 5543 (some CP Plus).

## 4. Network topologies

1. **Same LAN (simplest):** Pi on the store router/switch next to the DVR.
2. **Private link (most secure, recommended to pitch):** a **USB-Ethernet adapter** on the Pi, cable **directly into the DVR's LAN port** (or a small switch shared only by DVR + Pi). The cameras and our box never touch the store's internet. "Air-gapped analytics."
3. **Wi-Fi cameras:** the Pi joins the shop Wi-Fi, or runs its own hotspot for the cameras.

For phone dashboards: the Pi's second interface (Wi-Fi) serves the dashboard on the shop Wi-Fi via `storemind.local`.

## 5. 15-minute onboarding (installer checklist)

1. **Permission first**: written consent from the owner (template in §8). Put up the DPDP notice sign.
2. **Create a read-only user** on the DVR/NVR (live view only; no playback, no config).
3. **Discover**: ONVIF scan (ONVIF Device Manager / our `tools/discover.py`), or `nmap -p 554,80,8000,37777,2020,5543 192.168.1.0/24`.
4. **Probe each channel**: `ffprobe -rtsp_transport tcp "<url>"` → codec, resolution, FPS. Our `camera_check.py` also measures real FPS and lag.
5. **Tune the sub-stream only** (with the owner's OK; main recording untouched): H.264 or H.265, **640×360 – 1280×720**, **8–10 FPS** for entrance/queue, **I-frame interval ≈ 2× FPS**, CBR ~512 kbps–1 Mbps.
6. **Add to the restreamer** (go2rtc): one connection per camera; everything else reads `rtsp://127.0.0.1:8554/<cam>`.
7. **Assign roles**: entrance / counter-N / shelf-X → the FPS policy follows (entrance 8–10, counter 3–5, shelf = snapshot every 1–5 min).
8. **Calibrate**: take a snapshot → `tools/calibrate.py` → draw lines, lanes, billing spot, shelf slots, floor points.
9. **Validate**: count 20–30 people by hand vs the dashboard; fix line placement if off.
10. **Go live**: the health panel must show every camera green.

## 6. Software design for the ingest layer (what to build)

- **go2rtc as the single camera gateway** (small single binary, ARM64): RTSP/ONVIF/HTTP sources, restream on :8554, JPEG frame API. Avoids the recorder's connection limit (NVRs refuse extra clients once the limit is hit, and small DVRs allow far fewer than big NVRs).
- **Camera templates** in `configs/store.yaml`: `brand: hikvision|cpplus|dahua|tapo|onvif|generic`, `ip`, `channel`, `stream: sub`, `role`. The URL is built automatically; credentials are stored encrypted, never in git.
- **Auto sub-stream:** use ONVIF `GetProfiles` and pick the lowest resolution ≥ 640 px wide.
- **Resilience:** RTSP over TCP; reconnect with exponential backoff; **stale-frame watchdog** (no new frame for 5 s → reconnect + health alert); **tamper/moved** detection; per-camera FPS and lag on the health panel.
- **Time:** point the DVR's NTP at the Pi (chrony server) so camera clocks and our events agree. Use arrival time in the pipeline; log drift.
- **Night mode:** detect IR/greyscale frames → per-camera confidence threshold switch.
- **Decode cost:** sub-streams only. Pi 5 decodes HEVC in hardware (H.264 in software, fine at 640×360). QCS6490 uses hardware decoders via GStreamer.
- **Bandwidth math:** 4 sub-streams × ~0.5–1 Mbps ≈ 2–4 Mbps on the LAN. Trivial. Nothing leaves the store.

## 7. Interoperability: speak the industry's language

- **Publish our events in an ONVIF Profile M-style JSON over MQTT** (object class, counts, line-crossing, zone events). Profile M standardises analytics metadata/events and optionally uses MQTT + JSON. Existing VMS / security software could consume StoreMind events.
- Budget brands (Hikvision/Dahua) remain largely proprietary for analytics metadata, which is why we read **video** (RTSP) rather than relying on camera-side analytics.

## 8. Security, privacy and legal

- **Permission letter** (1 page): the store owner allows read-only live-view access to named cameras for anonymous analytics; no recording; no internet upload; data stays on the device; can be withdrawn anytime; contact person.
- **DPDP notice sign** at the entrance (English / Telugu / Hindi): "Anonymous footfall and queue analytics in use. No faces, no recordings."
- Read-only DVR account; strong unique password; **no port forwarding**; private link or VLAN; the Pi firewall allows only the dashboard port on the shop LAN.
- **Never change recording settings.** Only the sub-stream, only with consent.
- Credentials encrypted at rest; rotate after the pilot.

## 9. SIH demo setup (reliable on stage)

- **Primary:** one **RTSP/ONVIF-capable Wi-Fi camera** (e.g. CP Plus Ezykam or Tapo; check the listing says RTSP/ONVIF and BIS/STQC) + a **Pi Camera Module 3**, both into StoreMind live.
- **"Plug into any DVR" proof:** borrow a 4-channel DVR (college security/IT) → show the 15-minute onboarding live or in a 60-second video.
- **Backup:** **MediaMTX replaying our recorded clips as fake CCTV** (`ffmpeg -re -stream_loop -1 -i clip.mp4 -c copy -f rtsp rtsp://localhost:8554/entrance`). Same code path, no venue Wi-Fi risk.

## 10. Copy-paste slide text: "Works with the CCTV stores already have"

> **Plug-in, not rip-and-replace.** One cable to the shop's existing DVR/NVR, one read-only login, and StoreMind reads each camera's low-resolution stream: CP Plus, Hikvision/Prama, Dahua and any ONVIF camera.
> **15-minute onboarding:** discover → probe → calibrate → validate.
> **Private by design:** original recording untouched · frames never leave RAM · no internet needed · private cable to the DVR.
> **Scales:** one restream gateway per store, 2–8 cameras per box; shelves via snapshots every few minutes.

## Sources
- India CCTV market (CP Plus share, IP ≈60%, post-STQC shift): https://cofacto.substack.com/p/who-will-capture-indias-cctv-and
- STQC / ER CCTV rules: https://www.asmag.com/showpost/34950.aspx · https://www.matrixcomsec.com/stqc-certification-er-compliance-for-cctv-cameras/
- Hikvision URLs: https://www.visioforge.com/help/docs/dotnet/camera-brands/hikvision/ · NVR stream limit: https://supportusa.hikvision.com/support/solutions/articles/17000135582-how-to-see-the-number-of-streams-from-an-nvr-error-maximum-number-of-streams
- CP Plus URLs (crowdsourced): https://www.ispyconnect.com/camera/cp-plus · Dahua/CP Plus analog DVR RTSP + snapshot: https://monitoreal.com/documentation-center/manual-set-up-analogue-dahua/
- Tapo RTSP/ONVIF: https://www.tapo.com/us/faq/34/
- ONVIF profiles: https://www.onvif.org/blog/2021/08/04/do-you-know-your-onvif-profiles/ · Profile M: https://www.onvif.org/profiles/profile-m/ · https://www.forasoft.com/blog/article/onvif-profile-m
- go2rtc: https://github.com/AlexxIT/go2rtc · Frigate restream: https://docs.frigate.video/configuration/restream/ · MediaMTX: https://mediamtx.org/docs/usage/publish
