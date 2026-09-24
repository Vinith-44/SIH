# MEMS accelerometer

**Owners:** Ram (Person B) — firmware, chip, mounting, calibration (§1–§3) · Vinith (Person A) — fusion on the Pi (§4–§6).
Analogy: the camera sees, the load cell weighs, the MEMS chip under the shelf *feels* when someone touches or bumps it.

## 1. Chip, mounting, axis orientation — *Ram (M6-firmware), to fill*
MPU6050 (GY-521) by default, ADXL345 as the alternative; the part is confirmed in `HARDWARE_INVENTORY.md`.

## 2. Firmware state machine and thresholds — *Ram, to fill*
IDLE → ACTIVE → SETTLING → SETTLED per node, sending `$M` TOUCH / SETTLED / TILT / KNOCK (`PROTOCOL.md`).
Thresholds are set by `$C,MEMS_THR`.

## 3. Calibration — *Ram, to fill*

## 4. What the Pi does with it (fusion, `storemind/storemind/fusion/interaction.py`)

| Situation | Evidence | Output |
|---|---|---|
| Shopper takes 2 packs | TOUCH → unstable readings (ignored) → SETTLED → stable weight −436 g on A1 (unit 218 g) | `PICKUP action=pick units=2` |
| Shopper puts one back | same, +218 g | `PICKUP action=put_back units=1` |
| Leans on the shelf, handles and puts down | TOUCH → SETTLED, weight unchanged, shopper at the shelf | `PICKUP action=touch` (engagement, no stock change) |
| Any touch | TOUCH → SETTLED | the shelf camera looks **now** instead of waiting for its next period |
| Trolley knocks the shelf | KNOCK; readings gated; if the weight then dropped | alert "stock may have fallen" (never a pick) |
| Shelf tilts | TILT | alert "shelf tilted" |
| Weight drops, no touch, nobody there | two agreeing stable readings | `SHRINK_FLAG` |
| No MEMS fitted, or it missed a gentle touch | two agreeing stable readings while a shopper is there | pick / put-back, evidence "weight only" |
| MEMS on a camera bracket: KNOCK or TILT | fused with the image tamper check within 10 s | both → CRITICAL "camera moved, recalibrate"; MEMS only → WARN "bumped, view unchanged"; tilt ≥ 2° → WARN "check lines and zones" |
| PIR motion outside `alerts.open_hours` | `PRESENCE active` | CRITICAL "motion after hours" |

**Three details that matter** (each came from a failure in the evaluation, on the tuning seeds):
1. **The new weight often arrives before SETTLED.** The engine keeps the latest stable reading inside the episode and
   discards it when an unstable reading follows. Without this, half of all picks were lost and 97 false shrink flags
   appeared.
2. **The load cell's "stable" flag is sometimes fooled by a steady push.** When the pack weight is known, a change
   has to be close to a whole number of packs (within 25%) before it is believed at SETTLED; otherwise the engine
   waits for the next reading.
3. **Outside an episode, a change must be seen twice in a row.** Any unstable reading in between cancels it. This
   stops handling pushes from becoming picks when the MEMS missed the touch.

Configure: `sensors.enabled_sensors` includes `mems`; `sensors.mems_nodes` maps each node to a shelf (and slot) or a
camera; `sensors.cell_map` maps each load-cell channel to its slot; `slots[].unit_grams` is the pack weight.

## 5. Evaluation (bucket C — logic against our model of the firmware)

`eval/sensor_sim.py` generates what the bridge will publish, based on these assumptions:
- TOUCH is missed 5% of the time; SETTLED comes 0.5–1.5 s after release;
- readings swing ±50–400 g while the shelf is handled, and 10% of them are wrongly flagged stable;
- visits end in a pick (55%), a put-back (10%) or a touch (35%);
- trolley knocks happen about every 5 min, and 15% of them drop a pack.

Tuned on seeds 1–10; report on seeds 11–40:

| engine | picks P / R / F1 | units correct | put-backs P / R / F1 | false shrinks | fallen packs caught |
|---|---|---|---|---|---|
| v1 (every reading counts) | 0.05 / 1.00 / 0.09 | 0% | — | 243 | 0 / 15 |
| stable weight only | 0.66 / 0.66 / 0.66 | 92% | 0.54 / 0.61 / 0.57 | 0 | 0 / 15 |
| **M6 (MEMS-gated)** | **0.98 / 0.98 / 0.98** | **99.7%** | **0.90 / 0.96 / 0.93** | **0** | **15 / 15** |

**Honest limits:**
- The simulator and the engine were written by the same person, so the result is only as good as the assumptions
  above. If the real HX711 flags 30% of handling readings as stable, or the MEMS misses 20% of touches, the numbers
  will move.
- The whole-pack check assumes one product per slot. For loose goods it should be switched off (leave
  `unit_grams` empty).
- Nothing here has run on the board. The hardware acceptance comes from `tools/mems_test.py` (§6).

## 6. Hardware acceptance — *needs Ram's board* (steps in `docs/HARDWARE_TODO.md` "M6")
From CLAUDE_CODE_PROMPT_V2 M6:
- picks and put-backs detected ≥ 90% with weight gating;
- fewer than 1 false TOUCH per 10 min idle;
- at least 9 of 10 camera knocks detected.
