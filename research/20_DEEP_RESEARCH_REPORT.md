# 20 — Deep research report: making StoreMind Qualcomm-worthy and industry-grade

Researched 23 Sep 2026 from ~50 web sources (links at the end of each section). Written for students with no time: plain language first, details after.

---

## 0. The 12-line summary

1. **Qualcomm's own direction = our direction.** In 2025–26 Qualcomm bought Arduino, Edge Impulse, Foundries.io, Augentix and Focus.AI, launched "AI-native store" work with VusionGroup (Feb 2026), an AI camera platform with on-camera natural-language video search (Mar 2026), and a $150M India edge-AI fund (Feb 2026). **Pitch StoreMind as "the AI-native store for India's 13 million kiranas, built the Qualcomm way."**
2. **The money problem is huge:** retailers lose **$1.73 trillion/year (6.5% of sales)** to inventory distortion (out-of-stocks + overstocks); **Asia-Pacific alone $642B** (IHL, Sep 2025).
3. **Nobody serves the kirana with edge AI.** Indian video-analytics players (Wobot, Staqu) target chains/enterprise, run on NVIDIA/x86 or cloud subscriptions, and some use face recognition. Big-box AI stores (Amazon, Vusion) are far too expensive for a kirana.
4. **Amazon's Just Walk Out fuses cameras + shelf weight sensors** with a multimodal model. That validates our camera + load-cell design; we do it for about 1/100th of the cost.
5. **New cheap hardware novelty:** a **₹999 24 GHz mmWave radar (HLK-LD2450)** tracks up to 3 people at 6 m over UART. Put one per billing counter: queue/service sensing **with no camera at all** (privacy, darkness, occlusion). It plugs straight into the STM32. Strong for the Hardware category.
6. **New India-specific novelty:** close the loop from **empty shelf → reorder**. The government's **ONDC DigiDukaan** (June 2026, *launched in Hyderabad*, 10,000+ retailers, HUL/ITC/Nestlé…) is digitising 1.4 crore kiranas' B2B buying. StoreMind can be the "eyes" that trigger it.
7. **New Qualcomm-aligned novelty:** "**Ask your store**" in plain language, running on-device. Qualcomm does this over *video*; we do it over our *anonymous events database* (a small LLM writes SQL). Cheaper, faster and privacy-safe.
8. **Second market:** India has **5,625 quick-commerce dark stores** (July 2026). Same engines apply: shelf availability, picker queues. That's a B2B market for the business slide.
9. **Model upgrades:** YOLO26n (Jan 2026, no NMS step, CPU-friendly, AGPL licence). RF-DETR Nano (48.4 COCO mAP, Apache-2.0) is better on accelerators. Qualcomm's Person-Foot-Detection runs ≈5.5 ms on QCS6490. Edge VLMs (Moondream2 1.8B) run on a Pi 5 but slowly → use them *occasionally*, never per frame.
10. **Qualcomm judges care about performance per watt.** Measure **energy per inference (mJ/frame)** on the Pi with a ₹300 USB power meter and compare with AI Hub latency. Almost no student team does this.
11. **SIH scoring** (from an SIH 2026 institute circular): novelty, complexity, clarity, feasibility, practicability, sustainability, scale of impact, user experience, future potential; **ideas with business potential score higher**; max 6 slides; prototype preferred for hardware. Grand finale **December 2026**; online screening Oct–Nov.
12. **Free boosters:** GitHub Student Pack (free GitHub Pro → protected private repo, Copilot, $100 Azure), Google AI Plus free for Indian students (Gemini Deep Research + NotebookLM → turn these docs into audio summaries), Qualcomm AI Hub (free), Edge Impulse free plan, Kaggle GPUs, Wokwi (simulate the Blue Pill online). See `21_FREE_TOOLS_AND_CLOUD.md`.

---

## 1. What Qualcomm wants (so our pitch speaks their language)

**Analogy:** if the sponsor is a car company, don't pitch a bicycle. Show a car built from their engine.

| Qualcomm move (date) | What it means for us |
|---|---|
| **IE-IoT expansion complete** (CES, Jan 2026). Acquired **Arduino, Edge Impulse, Foundries.io, Augentix** (smart-camera imaging chips), **Focus.AI**. New Dragonwing Q-8750 (77 TOPS, 12 cameras, LLMs up to 11B) and Q-7790 (24 TOPS, smart cameras) | Use their toolchain on slides: **AI Hub** (optimise), **Edge Impulse** (train/deploy tinyML), **Arduino UNO Q** (sensor node), **Foundries.io-style OTA** (fleet updates). "Prototype on Pi → deploy on Dragonwing" |
| **Qualcomm Insight / AI camera platform** (Mar 2026): on-device perception, semantic understanding and **natural-language video queries "without sending raw video to the cloud"**. Mentions retail loss prevention and business intelligence | Our "Ask your store" (U3) is the kirana-priced version, and more private (queries events, not video) |
| **Dragonwing Intelligent Video Suite** (Apr 2025): air-gapped on-prem genAI video management, natural-language search ("show me people wearing white helmet last week"), works with **legacy non-AI cameras**; verticals include retail | Validates our "bring-your-own-CCTV + offline" design |
| **Vusion + Qualcomm "AI-Native Store"** (25 Feb 2026): on-shelf availability, shelf condition monitoring, BLE shelf-edge sensors, ESLs, associate support ("60–90 min saved per shift"). Qualcomm VP Retail: *"AI at the edge will transform everyday environments and retail is leading."* | **Use this quote on slide 2.** We are the AI-native store for India's small retailers |
| **Retail AI vision** (Edge AI & Vision Alliance, Jan 2025): digital shelves, RFID, anonymous path analytics (FastSensor), loss-prevention CV (Toshiba ELERA), genAI assistants for staff | Our feature list maps directly onto Qualcomm's retail narrative |
| **Dragonwing Q-6690** (2025): first enterprise mobile processor with **integrated UHF RFID** | Future scope: RFID tags on high-value SKUs → exact counts (mention only) |
| **$150M Strategic AI Venture Fund for India** (18 Feb 2026), emphasis on edge AI; Qualcomm "Design in India" programme exists | "Post-SIH path: Qualcomm Design in India / Ventures" on the future-scope line |
| **Qualcomm IM SDK** (GStreamer plugins for camera → NPU inference → overlay) on RB3 Gen 2 / RUBIK Pi | Name it in the deployment plan: our pipeline maps 1:1 onto IM SDK GStreamer stages |

Sources: [IE-IoT expansion](https://www.edge-ai-vision.com/2026/01/qualcomms-ie%E2%80%91iot-expansion-is-complete-edge-ai-unleashed-for-developers-enterprises-oems/) · [AI camera platform](https://www.edge-ai-vision.com/2026/03/from-hardware-to-intelligence-the-qualcomm-ai-camera-platform-for-scalable-security-solutions/) · [Intelligent Video Suite](https://www.edge-ai-vision.com/2025/04/qualcomm-dragonwing-intelligent-video-suite-modernizes-video-management-with-generative-ai-at-its-core/) · [Vusion + Qualcomm AI-native store](https://www.vusion.com/newsroom/vusion-qualcomm-unveil-their-ai-native-store-vision/) · [Qualcomm retail AI](https://www.edge-ai-vision.com/2025/01/how-qualcomm-is-catalyzing-retails-ai-revolution/) · [Q-6690 RFID](https://www.rfidjournal.com/news/qualcomm-launches-mobile-processor-with-integrated-rfid-capabilities/223998/) · [$150M India fund](https://yourstory.com/2026/02/qualcomm-funding-investment-150-million-dollars-indian-tech-ai-startups) · [Design in India](https://www.qualcomm.com/company/locations/india/design-in-india-program) · [IM SDK GStreamer (Edge Impulse docs)](https://docs.edgeimpulse.com/hardware/deployments/run-qualcomm-im-sdk-gstreamer) · [Q-2390 (Sep 2026)](https://www.edge-ai-vision.com/2026/09/qualcomm-introduces-dragonwing-q-2390-and-iq-2390-processors-expanding-access-to-intelligent-connected-devices/)

---

## 2. Industry & market (numbers for the impact/business slide)

| Fact | Number | Source | Caveat |
|---|---|---|---|
| Kirana / neighbourhood stores | **~13 million** | Invest India | Undated blog; widely quoted |
| Kirana transactions below ₹200 | 95% | Invest India | Older data |
| Kiranas run by a single person | 98% | Invest India | → our alerts must be simple, voice/light-based |
| Kiranas tech-enabled (2018) | 3% | Invest India | Old, but shows the gap |
| Global inventory distortion | **$1.73 T/yr = 6.5% of retail sales** | IHL Group, Sep 2025 | Global figure |
| Asia-Pacific share | **$642 B (37%)** | IHL Group | |
| Computer-vision adoption growth (retail, projected) | +8,143% | IHL Group | Projection; shows momentum |
| Shoppers who'd abandon a purchase if the line is too long | ~11% (and 32% leave to check out elsewhere) | Digimarc/Forrester survey 2018 via eMarketer | US grocery, old; use as "indicative" |
| Quick-commerce dark stores in India | **5,625** in 408 cities (Blinkit 1,955 · Zepto 1,088 · Instamart 1,038 · Flipkart Minutes 880 · BigBasket 664); **Hyderabad 406** | QuickCommerceMap, July 2026 | Third-party mapping |
| ONDC DigiDukaan | Target **1.4 crore kiranas**; **10,000+ retailers + 35 brands onboarded in Hyderabad**; HUL, ITC, Coca-Cola, Nestlé, Marico… | DPIIT/ONDC roundtable 12 Jun 2026 | Integration API not public yet |

**Business model to present:** one-time kit (₹15–25k) + optional ₹299–499/month cloud sync & reports for chains. Other revenue lines: FMCG brands pay for shelf-availability insights (the same data Trax/ParallelDots sell to brands), and B2B dark-store licences. Label all prices "proposed".

Sources: [Invest India](https://www.investindia.gov.in/team-india-blogs/modernization-kirana-stores-india) · [IHL 2025](https://www.ihlservices.com/news/analyst-corner/2025/09/retail-inventory-crisis-persists-despite-172-billion-in-improvements/) · [eMarketer checkout lines](https://www.emarketer.com/content/here-s-what-long-checkout-lines-mean-for-grocery-sales) · [Dark store map 2026](https://quickcommercemap.com/reports/india-quick-commerce-map-2026) · [DigiDukaan](https://swarajyamag.com/business/dpiit-ondc-push-digidukaan-to-digitise-indias-14-crore-kirana-stores)

---

## 3. Competitors: where the gap is

| Player | What they do | Hardware / deployment | Gap we exploit |
|---|---|---|---|
| **Wobot.ai** (India) | Video intelligence on existing CCTV: compliance checklists, alerts, multi-site dashboards | Subscription, "contact for pricing" | Built for chains/QSR operations; not kirana-priced; no shelf-weight fusion or queue forecast |
| **Staqu JARVIS** (India) | 100+ video AI modules incl. facial recognition, ANPR; queue management | Edge appliances on **NVIDIA Jetson / x86 + NVIDIA GPUs**, 4–128 cameras | Enterprise/police price class; uses face recognition (privacy risk under DPDP); NVIDIA, not Qualcomm |
| **Vusion EdgeSense + Qualcomm** | ESLs, shelf-edge sensors, on-shelf availability, BLE | Big-box retailers (e.g. DM drugstores, Walmart Central America) | Far too costly for kiranas; needs ESL rollout |
| **Amazon Just Walk Out** | Cameras + shelf **weight sensors**, multimodal transformer model | Dense sensor installs | Validates fusion; cost is extreme |
| **Trax / ParallelDots / Infilect** | Shelf audits for CPG brands | Photos by reps, cloud | Not continuous; not in-store operations |

**Positioning sentence:** *"JARVIS and Wobot sell video AI to enterprises; Vusion and Amazon build AI-native stores for big-box chains. StoreMind brings the AI-native store to India's kiranas and dark stores: on Qualcomm-class edge hardware, with no face recognition and no cloud dependency."*

Sources: [Wobot review](https://www.revavenues.ai/blogs/wobot-ai-review-2026-ai-video-analytics-for-operations-compliance) · [Staqu JARVIS](https://www.staqu.com/what-is-jarvis/) · [Amazon JWO multimodal](https://www.aboutamazon.com/news/retail/amazon-just-walk-out-improves-accuracy) · [Vusion](https://www.vusion.com/)

---

## 4. Technology upgrades (state of the art, Sept 2026)

### 4.1 Detectors
| Model | Key numbers | Licence | Use it for |
|---|---|---|---|
| **YOLO26n** (Ultralytics, Jan 2026) | COCO mAP 40.9; 38.9 ms CPU ONNX; native NMS-free end-to-end; up to 43% faster on CPU than YOLO11n (their claim) | AGPL-3.0 / Enterprise | Pi 5 CPU person detection now |
| **RF-DETR Nano** (Roboflow, ICLR 2026) | COCO mAP 48.4, 2.3 ms on T4 GPU; "matches or beats lightweight YOLO on accelerators" | **Apache-2.0** | NPU/GPU targets; permissive licence story for a product |
| **Qualcomm Person-Foot-Detection** | ≈5.5 ms on QCS6490 (TFLite w8a8), 2.5M params, gives foot location | BSD-3 | Queue/entrance on Qualcomm hardware |
| **YOLOE** (open-vocabulary) | Text-prompted detection | AGPL | Auto-labelling shelf data (training time only) |

### 4.2 Vision-language models on the edge: reality check
LearnOpenCV tested edge VLMs: **Moondream2 (1.8B, <2 GB RAM) ran on all devices incl. Pi 5**. Qwen2.5-VL 3B needs ~5 GB RAM, took 145+ s for captioning on a throttled Pi 5, and Pis hit 90 °C without cooling.
**Rule:** never run a VLM per frame. Use it (a) once every few minutes to *confirm* a WRONG_ITEM planogram flag, and (b) never on people. Put language intelligence over **events** instead (U3).

### 4.3 Qualcomm deployment stack to name in the PPT
AI Hub (compile/profile, free) → LiteRT/QNN INT8 → IM SDK GStreamer pipeline on RB3 Gen 2 / RUBIK Pi / Dragon Q6A → Edge Impulse for the MCU/tinyML side → Arduino UNO Q as a Qualcomm+STM32 sensor node.

Sources: [YOLO26](https://docs.ultralytics.com/models/yolo26/) · [RF-DETR vs alternatives](https://blog.roboflow.com/rf-detr-vs-alternatives/) · [RF-DETR repo](https://github.com/roboflow/rf-detr) · [Person-Foot-Detection](https://huggingface.co/qualcomm/Person-Foot-Detection) · [VLM on edge](https://learnopencv.com/vlm-on-edge-devices/) · [Moondream2 on Pi](https://pristren.com/blog/moondream2-edge-vision-model/)

---

## 5. New novelty upgrades (on top of N1–N9 in `03_NOVELTY_AND_FEATURES.md`)

Ranked by win-probability × feasibility.

### U1. Camera-free billing-counter sensing with a ₹999 mmWave radar ⭐ (Hardware category win)
- **HLK-LD2450**, 24 GHz: tracks **3 moving targets**, X/Y + speed, 6 m range, ±60° azimuth, **UART 256000 baud**, 5 V, **₹999 incl. GST** (Probots India).
- Mount one per counter → knows *someone is at the billing spot* (service time) and *1–3 people waiting behind*, **no camera, no image, works in darkness**. TI's mmWave literature highlights exactly this: privacy (radio, not images) and robustness to lighting.
- STM32 reads it over UART → sends `$Q,<counter>,<n>,<x1,y1…>*CS` → fusion with the queue camera. Camera blocked? Radar keeps working. Camera count ≠ radar count → confidence drops (the same "sensor disagreement" story as our FreeRTOS guide).
- Pitch line: *"Privacy tiers: kirana owners who don't want cameras at the counter get radar-only queue intelligence."*
Sources: [Probots LD2450](https://probots.co.in/hlk-ld2450-24ghz-mmwave-tracking-radar-sensor.html) · [Hi-Link LD2450](https://www.hlktech.net/index.php?id=1157) · [TI mmWave people counting](https://www.ti.com/lit/pdf/sszt725) · [mmWave counting paper 2026](https://www.mdpi.com/1424-8220/26/4/1289)

### U2. Shelf-to-supplier loop (India-specific) ⭐
EMPTY/LOW slot persists → StoreMind drafts a reorder line (SKU, qty from weight/facings history, preferred distributor) → owner approves on the phone → sent via WhatsApp to the distributor when online, or exported for **ONDC DigiDukaan** ordering. DigiDukaan launched in **Hyderabad** with 10,000+ retailers: pitch it as the Telangana pilot path. Be honest: no public DigiDukaan API yet → prototype with CSV/WhatsApp, "API integration when available".

### U3. "Ask your store" on-device (Qualcomm-aligned) ⭐
Owner asks by voice or text: *"Kal atta kitni der khali tha?"* / *"Which hour had the longest queue this week?"* → a small local LLM (Qwen2.5-1.5B via llama.cpp on the Pi 5, or on an AI HAT+ 2 / Qualcomm NPU) turns it into **SQL over our events table**; the answer comes only from the DB (no hallucinated numbers). Same capability Qualcomm markets for video, but over events → private, cheap and fast.

### U4. Dark-store mode (second market)
Same engines: slot availability on dense racks, picker queue at packing stations, rider hand-off queue. 5,625 dark stores, 15–30% monthly attrition → alerts must be self-explanatory. Business slide: "Kirana (B2C-ish) + dark stores (B2B)".

### U5. Energy per inference (Qualcomm's favourite metric)
Measure with a USB-C power meter: idle W, running W, FPS → **mJ per frame** for Pi 5 CPU (and Hailo/Qualcomm if available). Put next to AI Hub latency. Few student teams do this.

### U6. Fleet learning (future scope only)
Stores share **only model updates/metrics**, never video → the shelf model improves across stores (federated-style), with OTA via a Foundries.io-like pipeline. Mention, don't build now.

### U7. RFID tier (future scope)
Q-6690-class handhelds read RFID tags for exact stock of high-value SKUs; StoreMind fuses them with camera/weight. Mention only.

---

## 6. Traps to avoid (these lose marks)

- Face recognition or "customer identification" of any kind (DPDP risk; judges will ask).
- A VLM/LLM on every video frame (too slow on a Pi; looks naive to Qualcomm engineers).
- Jetson as the main device in a Qualcomm PS.
- Claiming "first in the world". Use "novel for low-cost, offline, kirana deployment" unless prior-art search proves otherwise.
- Mock dashboards or unlabelled numbers (see `09b_TEST_DATA_VALIDITY.md`).
- Too many half-built features. Five that work beat fifteen that don't.

---

## 7. Updated architecture (delta to `04_ARCHITECTURE.md`)

- **SENSE:** + mmWave radar per counter (UART → STM32).
- **Fusion:** + camera⊕radar queue fusion with a disagreement-confidence score.
- **USE:** + "Ask your store" (LLM → SQL over events, local); + reorder drafts (WhatsApp / DigiDukaan export).
- **Metrics:** + energy per inference, + AI Hub latency on QCS6490.
- **Deployment story:** Pi 5 (dev) → Qualcomm Dragonwing via AI Hub + IM SDK (prod) → Arduino UNO Q / STM32 sensor nodes → OTA fleet.

---

## 8. SIH scoring map (from an SIH 2026 institute circular)

| Criterion | Our evidence |
|---|---|
| Novelty | Door-to-counter forecast, label-free planogram, camera+weight+radar fusion, Ask-your-store over events, shelf-to-supplier loop |
| Complexity | Edge CV + queueing theory + RTOS firmware + sensor fusion + on-device LLM |
| Clarity / format | 6 slides, one diagram, measured table |
| Feasibility / practicability | Runs on a ₹10k-class Pi; kit ₹15–25k; uses existing CCTV |
| Sustainability | >1,000× less data than cloud video; low-power edge; energy/frame measured |
| Scale of impact | 13M kiranas + 5,625 dark stores; DigiDukaan tie-in |
| User experience | Voice alerts in Telugu/Hindi, tower light, phone PWA, Ask-your-store |
| Future potential | Qualcomm Dragonwing deployment, RFID tier, fleet learning, Design-in-India path |
| **Business potential (scores higher)** | Kit + subscription + brand insights + dark-store licences |

Timeline: internal hackathons Sept 2026 → online screening of idea PPTs Oct–Nov 2026 → 36-hour grand finale **Dec 2026**.
Sources: [SIH 2026 circular (DTU)](https://www.dtu.ac.in/Web/upload/events/aug/file0807.pdf) · [SIH 2026 timeline](https://blogs.reskilll.com/smart-india-hackathon-2026-launched-timeline-registration-how-to-participate/)

---

## 9. What to tell Claude Code next (after its current run)

```
Read research/20_DEEP_RESEARCH_REPORT.md. Add to the roadmap (don't break existing work):
1. U1 mmWave: sensor-bridge support for HLK-LD2450 (UART 256000, 3 targets) via the STM32 protocol line $Q,..., a simulator for it, and camera+radar queue fusion with a disagreement-confidence score.
2. U3 Ask-your-store: local LLM (llama.cpp, Qwen2.5-1.5B-Instruct GGUF) -> SQL over the events DB with a strict read-only schema whitelist; answers cite the SQL rows; English/Hindi/Telugu input. Add to dashboard.
3. U2 reorder drafts: when a slot is EMPTY/LOW beyond a threshold, create a reorder suggestion (sku, qty, distributor) with approve/reject; export CSV + WhatsApp-ready text.
4. U5 energy: tools/energy_benchmark.py that takes manual power-meter readings + measured FPS -> mJ/frame table in RESULTS.md.
Log everything in WORK_LOG.md and HANDOFF_FOR_CLAUDE.md.
```
