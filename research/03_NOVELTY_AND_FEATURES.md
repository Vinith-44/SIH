# 03 — Novelty: what makes StoreMind different

> "Privacy-first + offline + edge" is **not** novelty — the PS itself asks for it, so every team will say it.
> Novelty = something the PS asks for that others solve badly, solved in a way that is new, measurable and demo-able.

## 0. What already exists (so we don't claim it as new)

| Category | Examples | Their gap (our opening) |
|---|---|---|
| Global shelf-audit / retail-execution AI | Trax, ParallelDots ShelfWatch, Infilect, Karna | Built for CPG brands; photos by field reps / cloud processing; not continuous; not kirana-priced |
| Footfall / people counting | RetailNext-type sensors, CCTV vendor analytics | Counting only; cloud dashboards; no link to shelves or queues |
| Autonomous checkout | Amazon Just Walk Out, Zippin | Very expensive, many cameras, cloud heavy |
| Hackathon repos (e.g. XLink) | YOLO + threshold | No prediction, no fusion, no evidence |

**Our wedge:** a ~₹15–25k box (Pi 5 + sensor node + alerts) that plugs into the CCTV a Tier-2/3 store *already has*, works with no internet, and connects **three signals nobody connects**: who walks in → what they look at on shelves → how long they wait to pay.

---

## 1. Novelty pillars (ranked)

Scores: Impact on judges / Novelty / Effort (L = low, M, H).

| # | Pillar | Impact | Novelty | Effort |
|---|---|---|---|---|
| N1 | Door-to-counter queue forecasting (arrival-driven, Erlang-C) | H | H | M |
| N2 | Label-free shelf & planogram monitoring (reference snapshots + occlusion gating) | H | H | M |
| N3 | Camera + weight + ToF fusion on an STM32 FreeRTOS node (incl. loose-grain bins) | H | M-H | M |
| N4 | Lost-sales ₹ metric (dwell at an empty slot) + conversion via POS | H | H | L-M |
| N5 | Staff actuation in local language (voice + tower light + phone over store Wi-Fi) | M-H | M | L |
| N6 | Bring-your-own-CCTV + task-aware frame scheduling (one Pi, many cameras) | M-H | M | L-M |
| N7 | Verifiable privacy (no pixels persisted, "privacy counter", DPDP-aligned) | M | M | L |
| N8 | Runs on Qualcomm silicon — models profiled on real devices via Qualcomm AI Hub | H (sponsor!) | M | L |
| N9 | (Stretch) On-device daily report in Telugu/Hindi/English from computed numbers | M | M | M |

Build N1, N2, N4, N5, N8 for sure; N3 because we already have HX711; N9 only if time permits.

---

## N1. Door-to-counter queue forecasting

**Problem with everyone's approach:** they look at the queue camera and alarm when the queue is *already* long. The PS says *"predict congestion **before** queues become excessive."*

**Idea:** the entrance camera is a *leading indicator*. People who enter now reach the billing counter roughly one shopping-trip later.

1. Entrance camera → arrivals per minute `E(t)`.
2. Counter cameras → per counter: queue length, **service time** (how long a customer stays at the billing spot) → service rate `μ` per counter; and arrivals to checkout `A(t)`.
3. Learn the **shopping-trip lag** `L` (typical minutes from entry to checkout) *without tracking anyone across cameras* — just by cross-correlating the two count series `E(t)` and `A(t)`. Privacy-safe: no re-identification.
4. Forecast checkout arrival rate for the next 5–15 min: `λ̂(t+k) ≈ conversion × E(t+k−L)` (plus time-of-day profile when history exists).
5. **Erlang-C (M/M/c queue)** → for c = 1…N counters compute expected wait and P(wait > target). Recommend the smallest `c` that keeps P(wait > 3 min) below e.g. 20%.
6. Output: *"Open Counter 3 in ~6 minutes (predicted wait 4.2 min → 1.1 min)."* Then **measure** whether wait actually dropped → closes the loop (ACT → MEASURE → LEARN from our own dashboard story).

Verified working snippet (tested):

```python
import math, numpy as np

def erlang_c(lam, mu, c):            # P(arriving customer must wait), rates per minute
    a = lam / mu
    if a >= c: return 1.0            # unstable -> queue keeps growing
    top = (a**c / math.factorial(c)) * (c / (c - a))
    return top / (sum(a**k / math.factorial(k) for k in range(c)) + top)

def recommend_counters(lam, mu, target_wait_min=3.0, max_prob=0.2, c_max=8):
    for c in range(1, c_max + 1):
        if lam / mu >= c: continue
        pw = erlang_c(lam, mu, c)
        p_long = pw * math.exp(-(c * mu - lam) * target_wait_min)   # P(wait > target)
        if p_long <= max_prob:
            return c, pw / (c * mu - lam), p_long                      # counters, mean wait, risk
    return c_max, None, None

def best_lag(entries, checkout, max_lag=40):   # minutes, no re-ID needed
    x = (entries - entries.mean()) / entries.std(); y = (checkout - checkout.mean()) / checkout.std()
    return int(np.argmax([np.mean(x[:len(x)-L] * y[L:]) for L in range(max_lag)]))
```

Example: 2.4 shoppers/min reaching billing, 1.5 min per bill → recommends **5** counters for P(wait>3 min) ≤ 20%. On synthetic data the lag estimator recovered the true 12-min lag exactly.

**Why judges like it:** it's real operations research (used in call-centre staffing), cheap to compute on a Pi, explainable, and directly answers two PS bullets. Mention Erlang-C's assumptions (Poisson arrivals, exponential service) honestly and that we validate against measured waits.

**Demo:** replay a recorded entrance video → watch the forecast rise → counter recommendation fires *before* the queue camera shows a long queue.

---

## N2. Label-free shelf & planogram monitoring

**Problem:** training a per-SKU detector needs thousands of labelled images per product — we don't have them (and a real store changes products weekly). Our current model over-fits on 431 images.

**Idea:** don't recognise products by *name*; recognise *change against a known-good reference*.

1. **Setup once (2 minutes per shelf):** in the dashboard, draw slots on a shelf snapshot and type the SKU name/price for each slot (this *is* the planogram).
2. **"Restocked" snapshot:** when staff finish restocking they press a button (dashboard or a physical STM32 button) → store a reference crop per slot.
3. **Every 30–60 s** (shelves change slowly):
   - **Occlusion gate:** skip the frame if the person detector sees anyone overlapping the shelf.
   - Per slot, a generic product/gap detector (trained on **SKU-110K** + gap datasets — class-agnostic "product" and "gap") counts front **facings** → `fill = facings_now / facings_reference` → FULL / LOW / EMPTY.
   - Per slot, an image-embedding similarity (e.g. MobileNetV3 / DINOv2-small features, cosine) vs the reference crop → if the slot is full but looks *different* → **"wrong product in slot"** = planogram violation.
   - **Temporal voting:** change state only after K of the last N gated frames agree → no flicker alerts.
4. New product? Take one reference snapshot. No retraining.

**Why it's novel for this PS:** few-shot / reference-based planogram compliance that works with little data and on a Pi; directly solves our "no data" problem; gives *which slot* and *which product* (not just "void 23%").

---

## N3. Multimodal shelf sensing (why the STM32 exists)

Cameras see only the **front** facing; a shelf can look full while the back is empty, and shoppers occlude it. Weight doesn't lie.

- STM32 (FreeRTOS) node reads **HX711 load cells** under 1–2 high-value trays, **ToF** (VL53L0X/VL53L1X) for hand-in-shelf interaction, **IR break-beam** at the door (ground truth for camera counting accuracy!).
- **India-specific:** loose staples (rice, atta, dal, sugar) sit in **bins sold by weight** in kirana stores. A camera can't read bin level reliably; a load cell can → "Toor dal bin at 18% — refill".
- Fusion rules (edge node, not MCU): camera EMPTY + weight low → confirmed OOS (high confidence); camera FULL + weight dropping → **hidden depletion** (back of shelf empty); weight drop with no shopper nearby → **shrinkage/anomaly flag**; ToF interaction + weight drop → **pickup event** (a real conversion signal at the shelf).
- The MCU does real-time sampling, filtering, debouncing, timestamping, actuation (tower light / buzzer) — **not** vision.

---

## N4. Rupee-denominated insights (business value)

- **Lost-sales events:** a shopper dwells ≥ 8 s at a slot that is EMPTY/LOW → `POTENTIAL_LOST_SALE {slot, sku, price}`. Daily: *"Atta 5 kg was empty 2h 10m during peak; 14 shoppers checked it → est. ₹4,900 at risk."*
- **Conversion indicator** (PS asks for it): bills from POS ÷ entries from camera, per hour. POS integration = read the POS export (CSV) or a simple webhook/REST adapter; many small POS/billing apps can export sales reports.
- **Promo effectiveness:** promo-zone dwell × pickups (ToF/weight) vs normal days.
- **Staff efficiency:** average service time per counter, idle vs busy minutes, "minutes above target wait".

---

## N5. Alerts staff can actually act on

- **Voice** on a small speaker, offline TTS or pre-recorded clips, in **Telugu / Hindi / English**: *"Counter 2 kholiye"*, *"Aisle 3 — Maggi refill"*.
- **Tower light / buzzer** at the billing area driven by the STM32 (green = OK, amber = predicted congestion, red = open counter now).
- **Phone PWA** over the store's local Wi-Fi (no internet needed); when internet is available, also Telegram/WhatsApp to the owner.
- Alert manager: de-duplication, cooldown, escalation (if not acknowledged in 5 min → owner).

---

## N6. Bring-your-own-CCTV + task-aware scheduling

- Most Indian stores already have a DVR/NVR (Hikvision, CP Plus, Dahua…). They expose **RTSP** (and usually **ONVIF**). We read their **sub-stream** (e.g. 640×360) → zero camera cost, low decode load.
- **Task-aware frame scheduling:** entrance 10–15 FPS, queue 5 FPS, shelves **1 frame every 30–60 s**. Shelves don't change in milliseconds — this is how one Raspberry Pi 5 can serve 1 entrance + 2 counters + many shelf cameras. Most projects run every camera at full FPS and run out of compute.
- Hardware tiers (see `05_HARDWARE_PLAN.md`): Kirana (Pi 5, CPU) → Supermarket (Pi 5 + Hailo HAT or Qualcomm QCS6490 board) → Chain (many stores → central dashboard).

---

## N7. Privacy you can verify

- Frames live only in RAM; only events (≈200-byte JSON) are stored. A dashboard **"privacy counter"** shows *bytes of video stored: 0*.
- Motion-only tracking (ByteTrack) — no face recognition, no appearance embeddings, no cross-camera re-ID (N1 uses count cross-correlation instead).
- Track IDs are random per session; aggregates only after 24 h.
- Recommend **ceiling/high-angle mounting** → faces barely visible by design.
- Debug preview (only when an engineer opens it) is blurred.
- Aligned with India's **DPDP Act 2023** and DPDP Rules 2025 (notified Nov 2025; core obligations phase in by May 2027): data minimisation, purpose limitation, notice signage, security safeguards.

---

## N8. Qualcomm alignment (the sponsor is judging)

- Compile and **profile our exact models on real Qualcomm devices** through **Qualcomm AI Hub** (cloud-hosted Snapdragon / Dragonwing devices, Python `qai_hub` client). Put the measured latency on the slide: *"Our person model: X ms on QCS6490 NPU."*
- Consider Qualcomm's own **Person-Foot-Detection** model from AI Hub (BSD-3 licence, detects people *and localises feet*, TFLite download available) — feet are exactly what line-crossing and queue zones need.
- Hardware abstraction layer: same pipeline on Pi 5 CPU (LiteRT/NCNN), Hailo, or Qualcomm NPU (LiteRT + QNN delegate / QNN). "Prototype on Pi, deploy on Snapdragon."
- Worth knowing: **Arduino UNO Q** = Qualcomm Dragonwing QRB2210 (Linux) + STM32U585 (real-time MCU) on one board (~₹6.7k in India) — literally our "edge computer + STM32" split on one Qualcomm board. Great candidate for the shelf-sensor node.

---

## N9. (Stretch) Talk-to-your-store daily report

A small local LLM (e.g. Qwen2.5-1.5B via llama.cpp on the Pi 5 CPU, or on the AI HAT+ 2) turns the **already computed** numbers into a short daily summary in the owner's language. Guardrail: the LLM only rephrases numbers we pass in — it never computes them. Only do this after N1–N5 work.

---

## Features mapped to the PS (checklist for the PPT)

| PS requirement | StoreMind feature |
|---|---|
| Count entries/exits | Foot-point line counting + IR break-beam validation |
| Footfall by time/day/zone | SQLite aggregates per hour/day/zone |
| Dwell near products/promos | Zone visits with gap tolerance |
| Heatmaps | Floor-plan heatmap via homography |
| Low / out-of-stock | Slot state machine (N2) + weight (N3) |
| Planogram compliance | Reference-embedding match (N2) |
| Replenishment alerts | Alert manager (N5) |
| Real-time availability | Live shelf grid on dashboard |
| Queue length, wait, service time | Per-counter queue engine |
| Predict congestion | N1 forecast |
| Recommend counters | Erlang-C recommendation (N1) |
| On-device, offline, low bandwidth | Edge node + store-and-forward sync |
| Anonymous, no PII | N7 |
| Dashboard, reports, KPIs | Web dashboard + daily/weekly PDF/CSV |
| POS/ERP integration, multi-store | Adapters + MQTT/HTTPS uplink to HQ |
