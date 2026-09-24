# Shelf monitoring

**Owner:** Person A · **Milestone:** M3 (shelf v2) · **Code:** `storemind/storemind/analytics/shelf.py`,
`analytics/reorder.py` · **Tools:** `storemind/tools/shelf_synth.py`, `shelf_capture.py`, `shelf_label.py` ·
**Evaluation:** `eval/eval_shelf_lighting.py` (synthetic, bucket C), `eval/eval_shelf_photos.py` (our photos, bucket B)

## 1. How it works (no training, no product model)

1. Draw the slots once on a snapshot (`tools/calibrate.py`) and type SKU and price. That drawing is the planogram.
2. After restocking, press **Restocked** on the dashboard or the STM32 button (`$R` → `SENSOR {sensor: restock}`).
   The engine stores a reference picture of each slot.
3. Every `shelf_period_s` it looks again. A person in front of a slot → skip that slot (occlusion gate). Otherwise it
   compares the slot with its reference:
   - **fill**: packets have texture and colour; an empty slot shows a flat back panel;
   - **appearance**: full, but it doesn't look like the reference → WRONG_ITEM.
4. **K-of-N voting**: the state changes only when K of the last N looks agree.

States: `FULL`, `LOW`, `EMPTY`, `WRONG_ITEM`, `UNKNOWN`. Every state has a `reason`, e.g.
`fill 0.12 <= empty 0.28; lux 140.00` or `too dark (lux 3.00 < 15.0)`.

## 2. What v2 added (and why)

| Problem in a real store | v2 answer | Config (v1 value) |
|---|---|---|
| Evening sun is warm and tube lights are cool, so the tint looks like "a different product" | gray-world white balance, estimated on the wall and shelf boards only (not the slots, which change when stock moves) | `white_balance` (false) |
| Less light → fewer Canny edges → a full slot "empties" | texture = gradient strength ÷ local brightness, which doesn't depend on light level | `texture: gradient` (`canny`) |
| Low contrast in dim light | CLAHE on luminance | `clahe` (false) |
| Glossy packs reflect light | glare mask: white, colourless pixels are ignored by every measure | `glare_mask` (false) |
| Structure similarity was computed but never used | gradient SSIM joins the fill estimate (30%) | `use_ssim` (false) |
| One reference can't match every lighting | **reference bank**: up to 4 references per slot, one per lighting, picked by **BH1750 lux** (else frame brightness). New lighting is learnt while the slot is confidently FULL; slow drift is followed | `reference_bank` (1), `drift_alpha` (0) |
| Lights off → "everything EMPTY" alerts | **too dark → UNKNOWN**, never EMPTY | `dark_lux` 15 lux / `dark_brightness` 0.12 (null) |
| Camera exposure still adapting after lights switch on | a sudden light change (>2.5×) skips one cycle | `lux_jump_ratio` (null) |
| Oblique CCTV view of a slot | 4-point slots are warped to a rectangle | `rectify` (false) |
| Deep shelf: the camera sees only the front row | **load-cell fusion**: weight fill = grams ÷ full grams (learnt at Restocked). Deep slot → weight wins; otherwise the average. A difference > 0.4 adds "CHECK SHELF: camera X vs weight Y" | `weight_mode: fuse`, `slots[].deep`, `full_grams` |
| Camera failed or someone is always in front | `CAMERA_HEALTH` not ok → UNKNOWN "camera fault"; 10 occluded looks in a row → UNKNOWN | `occluded_unknown_cycles` |

Lux comes from `ENVIRONMENT` events (`lux_node` picks which sensor node applies). Weight comes from `WEIGHT`
events; `sensors.cell_map` says which slot each load-cell channel sits under, and unstable readings are ignored.

**Reorder drafts** (`analytics/reorder.py`): EMPTY/LOW opens a draft line and FULL closes it. The drafts are kept
in `reorder.db` next to the event DB and are available as WhatsApp-ready text (`pipeline.reorder.whatsapp_text()`).
They are suggestions only: nothing is sent automatically. The **lost-sales estimate** already exists
(`LOST_SALE_RISK`: shoppers dwelling at an EMPTY slot × price, labelled an estimate).

## 3. Evaluation

### Synthetic lighting timelines (bucket C: logic, not accuracy)

`tools/shelf_synth.py` renders a 6-slot shelf. It starts with a restock in daylight, then slots go LOW, EMPTY or
WRONG_ITEM while the light moves between day, evening, tube, dim, dark and glare in runs, with a simulated lux.
Thresholds were searched on **seeds 1–10** (`--grid`, 48 settings). **Seeds 11–40** are the report:

| engine | slot-state acc | EMPTY F1 | EMPTY F1 evening | EMPTY F1 dim | dark → UNKNOWN |
|---|---|---|---|---|---|
| v1 (before M3) | 54.7% | 0.74 | 0.15 | 0.33 | 0 of 714 |
| **v2** | **91.7%** | **0.92** | **0.93** | **0.89** | **714 of 714** (0 false EMPTY) |

Honest notes:
- **Generator revision.** The first version of the generator changed the light at random on *every* step. That is
  not how a store behaves, and it made v2 skip half its looks (the light-jump rule). It was changed to runs of
  3–7 steps (2–4 for dark) **before any test seed was scored**.
- **The test seeds were scored twice.** A unit test found a bug: confidence was computed from unclipped estimates,
  so the reference bank almost never learnt. After the fix, EMPTY F1 went from 0.93 to 0.92.
- **The BH1750 shows no benefit in simulation.** The synthetic light is uniform, so frame brightness is a perfect
  proxy for lux. Whether the light sensor helps can only be shown on a real shelf, where camera auto-exposure and
  stock changes also move the brightness.
- **v1 never raised a false EMPTY in the dark either.** It held its last state. v2 says UNKNOWN, which is honest
  on the dashboard, but it is not a fix for an alert that v1 actually raised.
- Several chosen values sit at the edge of the searched grid. Searching further was stopped on purpose: it would
  fit the renderer, not shelves.

### Our own shelf (bucket B): the real acceptance test, **not measured yet**

M3's acceptance is **EMPTY F1 ≥ 0.85 in both day and evening light on our own shelf photos**. The tools are ready:
`tools/shelf_capture.py` → `tools/shelf_label.py` → `python -m storemind.eval.eval_shelf_photos`, which prints
v1 and v2. Capture steps: `docs/HARDWARE_TODO.md` ("M3 - shelf photos").

## 4. Configuration

```yaml
cameras:
  - name: shelf-cam
    role: shelf
    shelf_period_s: 60
    shelves:
      - name: shelf-a
        vote_k: 2
        vote_n: 3
        empty_threshold: 0.28     # v2 fill scale (defaults)
        low_threshold: 0.5
        lux_node: stm32-01        # optional: which BH1750 lights this shelf
        slots:
          - {name: A1, points: [[0.10,0.22],[0.30,0.22],[0.30,0.55],[0.10,0.55]], sku: Atta 5kg, price: 260}
          - {name: A2, points: [...], sku: Toor Dal 1kg, price: 165, deep: true}   # load cell wins
sensors:
  cell_map: {shelf-a/A2: "1"}     # load-cell channel 1 is under A2
```

To get the old engine back for a comparison, set the v1 values in section 2's table (`eval_shelf_lighting.V1_SWITCHES`).

## Not done in M3 (stretch, after bucket-B results)
- PatchCore anomaly per slot (anomalib) and the Kaggle SKU-110K detector notebook: only worth it once our own
  photos show where the reference method fails.

## Sources
- research/23 §3.3 (shelf problems and fixes); CLAUDE_CODE_PROMPT_V2 M3.
- Gray-world white balance: G. Buchsbaum, "A spatial processor model for object colour perception",
  J. Franklin Inst. 310(1), 1980. CLAHE: K. Zuiderveld, Graphics Gems IV, 1994.
- Focal Systems shelf cadence / people-free frames: https://focal.systems/shelf-cameras/
