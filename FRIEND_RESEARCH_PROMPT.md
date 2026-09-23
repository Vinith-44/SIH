# Research prompt for a teammate's Claude account

**How to use:** open claude.ai → start a new chat → turn on **Research** (or web search) → paste everything below the line → wait for the report → download it as Markdown → save it as `research/30_FRIEND_RESEARCH.md` in the GitHub repo (via a pull request) or send it to Vinith.
If this report arrives, Vinith tells Claude Code: *"Skip M9 web research; research/30_FRIEND_RESEARCH.md covers it."* That saves his credits for coding.

---

You are a research analyst helping a student team in the **Smart India Hackathon 2026** (problem statement **SIH26179**, sponsor **Qualcomm**, category **Hardware**). We're through our college internal round; online screening is Oct–Nov 2026 and the grand finale is December 2026. Competition is intense. I need a **deep, source-backed research report**. Never invent facts: every claim needs a URL and a date; if you can't find something, write "not found".

## Our project (context)
**StoreMind:** an offline edge-AI retail platform for Indian kirana stores, supermarkets and quick-commerce dark stores. It runs on a Raspberry Pi 5 now and targets Qualcomm Dragonwing (QCS6490) via Qualcomm AI Hub. An STM32 FreeRTOS sensor node handles HX711 shelf weight, ToF, IR beam, a 24 GHz mmWave radar at billing counters, and a tower light. It uses existing CCTV (RTSP). No video is stored and there's no face recognition. Modules: shopper analytics (entries/exits, dwell, floor heatmap), shelf monitoring (out-of-stock, planogram), queue intelligence (wait/service time, forecasting, counter recommendation), sensor fusion, local dashboard, voice alerts in Telugu/Hindi.

**Our novelty claims (verify each):**
- **N1** Door-to-counter queue forecasting: entrance-camera arrival counts + entry→checkout lag (cross-correlation, no re-identification) + Erlang-C (M/M/c) → "open counter 3 in ~6 min" before the queue forms.
- **N2** Label-free shelf/planogram monitoring: per-slot "restocked" reference snapshots + occlusion gating (skip frames where a person blocks the shelf) + temporal voting + embedding similarity for wrong-item detection; new product = one photo, no retraining.
- **N3** Low-cost camera + load-cell + ToF fusion (incl. loose-grain bins sold by weight in kiranas) to detect hidden depletion and shrinkage.
- **N4** "Lost-sales ₹" metric: shopper dwell at an empty/low slot × SKU price.
- **U1** Camera-free billing-counter sensing with a cheap mmWave radar (HLK-LD2450), fused with the camera.
- **U2** Shelf-to-supplier loop: empty shelf → reorder draft → WhatsApp / ONDC DigiDukaan.
- **U3** "Ask your store": an on-device small LLM translating owner questions (English/Hindi/Telugu) into SQL over our anonymous *events* database (not video).

## Research questions

### 1. Prior art & patents (most important)
For **each** claim N1–N4, U1–U3, search Google Scholar, arXiv, IEEE Xplore, ACM, Google Patents and company websites. Give a table: claim | closest prior work (title, authors/company, year, link) | how similar (%) | what's different about ours | **safe wording for our PPT** (e.g. "novel for low-cost offline kirana deployment" vs "first"). Be blunt if it already exists.

### 2. Evidence of the problem in India (with numbers)
- Out-of-stock rates / lost sales in Indian kirana and modern trade (Nielsen, Kantar, Redseer, BCG, Bain, FICCI, RAI, academic).
- Billing-queue problems in Indian supermarkets (DMart, Reliance Smart, More…): wait-time surveys, abandonment, news.
- Retail shrinkage/theft % in India.
- CCTV penetration in small Indian shops; typical DVR brands.
- Prices of retail video-analytics / shelf-analytics subscriptions in India (Wobot, Staqu, ParallelDots, Infilect, etc.) or best available proxies.
- Quick-commerce dark stores: operational pain points (stock accuracy, picker queues, attrition).

### 3. Qualcomm's perspective
- Qualcomm's retail and edge-AI products 2025–2026 (Dragonwing, Insight platform, Intelligent Video Suite, Vusion partnership, Edge Impulse/Arduino acquisitions, AI Hub, IM SDK). What would a Qualcomm engineer judging SIH care about? (performance per watt, NPU use, INT8, scalability, security, OTA, etc.)
- Qualcomm's problem statements in **past SIH editions (2022–2025)**: what they asked for, which teams won, what those teams built (links to GitHub/news/LinkedIn posts where possible).
- Any Qualcomm student programmes / hardware loan / developer-kit access for Indian students in 2026.

### 4. Hardware price check (India, Sept 2026)
Current prices with links (Robu.in, Robocraze, Probots, Amazon.in, element14 India, Fab.to.Lab): Raspberry Pi 5 (4 GB/8 GB), Pi 5 active cooler, 27 W PSU, Camera Module 3 Wide, Pi 5 camera cable, Raspberry Pi AI HAT+ (13/26 TOPS), Arduino UNO Q, Radxa Dragon Q6A, RUBIK Pi 3, Qualcomm RB3 Gen 2, STM32 Blue Pill, STM32F411 Black Pill, ST-Link V2, HX711 + 5 kg load cell, VL53L0X, IR break-beam, HLK-LD2450 radar, MAX485, 12 V tower light, USB speaker, USB-C power meter.

### 5. What wins SIH hardware finals
Patterns from past SIH winners (hardware edition): PPT structure, demo style, what judges asked, common reasons teams lost. Cite blogs/LinkedIn/YouTube descriptions/news.

### 6. Legal & privacy
In-store analytics under India's **DPDP Act 2023 + DPDP Rules 2025**: is anonymous people counting "personal data"? Signage/notice requirements? Any guidance on CCTV analytics in retail? Cite law firms / MeitY / credible sources.

## Output format
- One Markdown report, title "30 — Friend research report", date, sections 1–6 as above, tables where possible, **every row with source URL + date + confidence (high/med/low)**.
- End with **"Top 10 changes StoreMind should make"**, ranked, each tied to evidence.
- End with a full source list.
- Keep it skimmable: short sentences, no filler.
