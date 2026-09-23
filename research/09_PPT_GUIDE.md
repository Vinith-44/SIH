# 09 — PPT rebuild guide (SIH 6-slide template)

Keep the SIH template. Fix overlaps (slide 3 "Process flow", slide 5 "audience:"). One idea per slide, big numbers, our own diagram.

## Slide 1 — Title
Unchanged (PS ID 26179, title, Hardware category, Team TechGladiators). Optional subtitle: **"StoreMind — turns a store's existing CCTV into an offline retail co-pilot."**

## Slide 2 — Proposed solution / Idea
- **One line:** An edge box (Raspberry Pi 5 → Qualcomm Dragonwing) + STM32 sensor node that links *who walks in*, *what's on the shelf* and *how long they wait* — fully offline, zero video stored.
- **How it addresses the PS:** 3 engines (Shopper, Shelf, Queue) + fusion + dashboard/alerts — map to all 7 PS sections in one small table.
- **Innovation (replace current text) — 4 bullets:**
  1. *Predicts* billing congestion from the entrance camera (door-to-counter forecast + queueing model) and recommends how many counters to open.
  2. Label-free shelf & planogram monitoring: reference snapshots + occlusion-aware voting — new SKU = one photo, no retraining.
  3. Camera + load-cell + ToF fusion (STM32 FreeRTOS) → catches hidden depletion, loose-grain bins, shrinkage.
  4. Every insight in ₹: lost-sales risk, conversion (with POS), staff alerts in Telugu/Hindi.

## Slide 3 — Technical approach
- Put `diagrams/storemind_architecture.png` here (full width).
- Small tech-stack strip: Python · YOLO/NCNN/LiteRT · ByteTrack · MQTT · SQLite · FastAPI · FreeRTOS on STM32 · Qualcomm AI Hub.
- **Remove** "blurring every customer" (not true). Say instead: *"Frames never leave RAM; only anonymous events are stored."*

## Slide 4 — Feasibility & viability
- **Measured** numbers table (from `06` evaluation): entry-count accuracy, queue MAE, shelf F1, FPS on Pi 5, latency on QCS6490 via AI Hub.
- Cost per tier: Kirana kit ≈ ₹15–25k one-time incl. Pi 5, sensors, tower light, UPS (uses existing CCTV; Pi prices have risen in 2026 — recheck) vs cloud video analytics subscriptions.
- **Challenges → mitigations** (table): occlusion → gating + voting; low data → reference-based shelf + auto-labelling; compute → task-aware FPS + INT8 + NPU; no internet → store-and-forward; power cuts → UPS + RTC + WAL.
- **Fix:** STM32 does real-time sensing/actuation, **not** CV/ML inference.

## Slide 5 — Impact & benefits
- Keep the three audience icons (kirana owners, shoppers, staff) — fix the overlapping text.
- Replace the DEMO/mock screenshots with **real** dashboard screenshots from our replay runs.
- Economic: fewer stock-outs (₹ lost-sales metric), right staffing (counter forecast); Social: privacy (DPDP-aligned), local-language alerts, works in Tier-2/3; Environmental: >1,000× less data than streaming video to cloud, low-power edge.

## Slide 6 — Research & references
Keep verified ones, fix venues, remove the unverifiable lecture. Suggested list:
1. Redmon et al., "You Only Look Once," CVPR 2016.
2. Goldman et al., "Precise Detection in Densely Packed Scenes" (SKU-110K), CVPR 2019.
3. Zhang et al., "ByteTrack: Multi-Object Tracking by Associating Every Detection Box," ECCV 2022.
4. Kumar, Patel & Astya, "Smart Shelf Monitoring Using YOLO," IEEE SCEECS 2025.
5. More & Deshpande, "Computer Vision (AI) Based Retailer Shelves Monitoring System," ICECCME 2025.
6. Deshmukh et al., "Real-Time Queue Detection and Management System Using YOLO Object Detection," IJSREM, 2024.
7. Grocer-Help Indian retail dataset paper, *Scientific Reports*, 2026.
8. Erlang-C / M/M/c queueing (any standard OR textbook, e.g. Gross & Harris, *Fundamentals of Queueing Theory*).
9. Qualcomm AI Hub documentation; Raspberry Pi AI HAT+ documentation.
10. Digital Personal Data Protection Act 2023 & DPDP Rules 2025 (MeitY).

(Double-check author lists/years on Google Scholar before submitting.)

## Likely judge questions — prepared answers

| Question | Answer |
|---|---|
| "How is this different from YOLO + counting that every team does?" | We *forecast* congestion from the door, give ₹ lost-sales, do label-free planogram checks, and fuse weight sensors. Show the measured lead time. |
| "Why STM32 if you have a Pi?" | Deterministic real-time sensing & actuation (µs interrupts, watchdog, FreeRTOS tasks), survives Linux crashes, cheap per shelf; RS-485 scales across the store. Vision stays on the edge box. |
| "Why not Jetson?" | Cost, power, and our target is Qualcomm silicon — we profiled on QCS6490 via AI Hub; Pi 5 is the dev/entry tier. |
| "How accurate?" | Show the table. Name the failure cases honestly. |
| "Privacy? Consent?" | No frames stored, no face recognition, no re-ID, session-random IDs, notice signage, DPDP-aligned. |
| "Where's your data?" | Own recorded videos + SKU-110K + gap datasets + Indian Grocer-Help; auto-labelling pipeline; reference-based shelf needs almost no labels. |
| "Will it scale to a chain?" | Same box per store, MQTT/HTTPS aggregates to HQ, config/model OTA; hardware tiers. |
| "What does it cost the store?" | One-time kit ≈ ₹15–25k with existing CCTV; no mandatory cloud fees. |
| "What if the internet/power goes?" | Show the unplug test; UPS + RTC + SQLite WAL. |
