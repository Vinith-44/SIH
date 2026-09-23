# 22 — Review of the Gemini Deep Research report (verified 24 Sep 2026)

Source: "StoreMind SIH Project Research" (Gemini Deep Research, Google Doc PDF, 14 pages).
I re-checked its key claims on the web. Verdict per item: ✅ use · ⚠️ use with care · ❌ don't use.

## 1. The most important finding (Gemini MISSED it; found while verifying)

**N1 (door-to-counter forecast) has strong prior art.**
- **Irisys patent US7778855B2 (2010), "Automatic self-optimizing queue management system":** uses **entrance counters + POS data to predict future arrivals and the required number of open checkouts ahead of time**, with dynamic learning. Irisys sells this as a product (e.g. Morrisons, 2018).
- **Xovis** (3D sensors) sells predictive wait-time / queue forecasting from entrance counts and purchase duration.
- Gemini's "closest prior art" for N1 (a pure-maths arXiv paper on tempered Erlang queues) is **not** relevant.

**What changes:** never say N1 is "first" or "novel" on its own. Say:
> "Predictive checkout staffing, proven in big-box retail with dedicated 3D/thermal sensors (Irisys, Xovis), brought to India's kiranas on **existing CCTV + a ₹15–25k edge box**, fully offline, with the door-to-counter lag **learned automatically** (cross-correlation, no re-identification) and an open Erlang-C model."

This is still strong: the value is **proven** (patent + products + a real retailer), and our contribution is **cost, hardware reuse, offline operation and self-calibration**. Cite Irisys/Xovis on the slide; judges respect teams who know the prior art.

## 2. Claims checked

| Gemini claim | Check | Verdict |
|---|---|---|
| QCS6490 NPU vs Pi 5 CPU: **8.7× FPS, 11× FPS/W, 16 °C cooler** | Verified: consult.red benchmark, **Raspberry Pi 5 CPU 3.91 FPS @ 5.56 W (0.70 FPS/W, 1423 mJ/frame, 70 °C) vs RUBIK Pi 3 QCS6490 NPU 34.22 FPS @ 4.40 W (7.78 FPS/W, 129 mJ/frame, 54 °C)**. Same RUBIK board with NPU off: 3.44 FPS, so the gain comes from the NPU | ✅ **Best "why Qualcomm" evidence for the PPT.** Cite consult.red |
| N2 prior art: one-shot/embedding planogram compliance (MDPI 2023) | Plausible; matches known literature | ✅ Reword "label-free" → "**zero-shot / one-reference-photo** slot monitoring, kirana-optimised" |
| N3 prior art: Amazon patent US9996818B1 (camera + depth + load cells for inventory) | Title matches Google Patents | ✅ Cite as prior art; our angle = low-cost STM32/HX711, loose-grain bins |
| N4 lost-sales prior art (DSStream blog) | Weak source (blog) | ⚠️ Keep our wording "real-time, on-device, from shopper dwell at empty slots" |
| N5 radar source (EFY magazine) | Weak source; idea is fine | ⚠️ Add **zone gating/filtering** for ghost targets (valid engineering point) |
| N6: SalesCode "DigiVyapar" multi-principal eB2B on ONDC | Shows reorder tools exist | ⚠️ Our angle = **triggered by physical shelf sensing**, with an offline queue |
| N7 Text-to-SQL survey | Generic | ✅ Keep; note "on-device, over events not video" |
| "Judges severely penalize CPU-only inference" | No evidence given; Gemini's inference | ❌ Don't repeat as fact. Still: showing NPU numbers (AI Hub / RUBIK) is clearly a plus |
| OOS 11.3% in South Indian kiranas | Single ResearchGate paper (CavinKare, Chennai) | ⚠️ Only as "one Chennai study found…" |
| 25% abandon if wait > 2 min; 55% > 4 min | Vendor blog (RSI Concepts, 2022), no primary survey | ⚠️ Prefer the Digimarc/Forrester figure (11% abandon; 32% leave to check out elsewhere) from `20_DEEP_RESEARCH_REPORT.md` |
| India shrinkage 3.2% "highest in world" | Scribd copy of an old (~2011) Global Retail Theft Barometer | ❌ Too old; don't use |
| 70% substitute brand, 9% switch retailer on stock-out | Vendor blog | ⚠️ Label "industry estimate" |
| DPDP: processed counts aren't personal data; **notice/signage** still wise; penalties up to ₹250 crore | Broadly consistent with DPDP Act; legal nuance on notice vs legitimate use | ⚠️ Put a **"Privacy by design + signage"** slide, but say "aligned with DPDP; to be reviewed by legal" |
| "Winning teams show live telemetry" / "synthetic demos get severe deductions" | Sources don't support (one cited source is **our own WORK_LOG.md**) | ❌ Not evidence. **But the advice is good anyway** (see below) |
| Qualcomm SIH past winners | "not found" | Honest; nothing to use |

## 3. Good suggestions from Gemini worth adopting (engineering value, not "evidence")

1. **Hardware telemetry on the dashboard:** FPS, inference ms, CPU/SoC temperature, power (W, from a USB meter or INA219), energy per frame; on Qualcomm: NPU in use.
2. **Live camera in the demo** (RTSP/IP camera or Pi camera), not only recorded clips. Keep replay as backup.
3. **QNN/AI Hub export path** for the QCS6490 NPU (already planned, M8).
4. **ROI calculator slide:** kit cost vs recovered lost sales + staff time → payback in months (label assumptions).
5. **Radar zone gating:** ignore LD2450 targets outside the counter box; simple smoothing/Kalman.
6. **Reorder drafts queue offline** in SQLite, sent when online (already our design; make it visible).
7. **DPDP notice template:** multilingual (English/Telugu/Hindi) "Anonymous analytics in use" sign for the store entrance.
8. **Interrupt-driven STM32 sensing:** HX711 DOUT-ready interrupt + FreeRTOS task notifications instead of busy polling.
9. **Small on-device LLM for "Ask your store":** try a model available in Qualcomm AI Hub's LLM catalogue for the target chip (e.g. a Llama 3.2 1B/3B variant if listed for that chipset). Check the catalogue before claiming.

## Sources
- Irisys patent US7778855B2: https://patents.google.com/patent/US7778855B2/en · related US8615420B2: https://patents.google.com/patent/US8615420B2/en · Morrisons + Irisys (2018): https://www.businesswire.com/news/home/20180104005065/en/Morrisons-Selects-Irisys-to-Streamline-Checkout-Promise
- Xovis predictive wait times: https://www.xovis.com/insights/detail/queuing-up-predictive-wait-times-in-retail-use-case
- NPU vs CPU benchmark (Pi 5 vs RUBIK Pi 3 / QCS6490): https://consult.red/insights/npus-vs-cpus-for-edge-ai-vision-less-heat-less-power-more-headroom/
- Planogram one-shot (MDPI 2023): https://www.mdpi.com/2076-3417/13/18/10145 · Amazon patent US9996818B1: https://patents.google.com/patent/US9996818B1/en
- Checkout abandonment (Digimarc/Forrester via eMarketer): https://www.emarketer.com/content/here-s-what-long-checkout-lines-mean-for-grocery-sales
