# MEMS accelerometer

**Owners:** Ram (Person B) — firmware, chip, mounting, calibration (§1–§3) · Vinith (Person A) — fusion on the Pi (§4–§6).
Analogy: the camera sees, the load cell weighs, the MEMS chip under the shelf *feels* when someone touches or bumps it.

## 1. Chip, mounting, axis orientation — *Ram (M6-firmware)*

**Chip:** MPU6050 (GY-521 breakout) by default, ADXL345 as the alternative; the part actually bought
goes in `HARDWARE_INVENTORY.md`. Firmware settings (`firmware/stm32/App/Src/mems_task.c`, register map
InvenSense RM-MPU-6000A-00): accelerometer only, **±2 g** (16384 LSB/g), digital low-pass **44 Hz**,
**100 Hz** sample rate. The gyro is not used. Only the MPU6050 driver is written; if the team buys ADXL345s
instead, `mpu6050_init/read_mg` in `mems_task.c` need an ADXL345 twin (0x53 / 0x1D, same mg output).

| Node | I2C address | Mounted on | Role on the wire | Reports |
|---|---|---|---|---|
| `m1` | 0x68 (AD0 → GND) | underside of shelf-a | `S` | TOUCH, SETTLED, KNOCK, TILT |
| `m2` | 0x69 (AD0 → 3.3 V) | entrance camera bracket | `C` | KNOCK, TILT |

Two MPU6050s fit on one I2C bus (AD0 selects 0x68/0x69); a third needs a TCA9548A mux (don't use
I2C2 on the Blue Pill, its pins are shared with USART3). Ids must match `sensors.mems_nodes` in the
store config — the bridge still publishes an unknown id (shelf or cam = the id) and logs a warning.

**Mounting.** Glue (double-sided foam tape is too soft: it damps the touch) or screw the module
**flat under the shelf board**, near the front edge where hands land, chip side down, the X arrow
pointing along the shelf. Orientation does not matter to the firmware: it measures the *angle* between
the current gravity direction and the one it saw at boot, so any mounting works as long as it is rigid.
On the camera: on the bracket arm itself, not on the wall plate.

## 2. Firmware state machine and thresholds — *Ram*

Per node, every 10 ms: read x/y/z (mg) → subtract a slow gravity estimate (exponential average,
~0.64 s) → the dynamic magnitude. Decisions are made per 50 ms window (peak and RMS of 5 samples),
in `firmware/stm32/logic/src/sm_mems.c` (portable C, host-tested):

```
IDLE --window peak > thr--> CANDIDATE --still active at 150 ms--> ACTIVE --quiet window--> SETTLING
                               |          emits TOUCH                  ^   (active again)     |
                               |                                       '--------------------'
                               |                                  quiet for 500 ms: emits SETTLED
                               '--quiet before 150 ms: peak >= knock -> KNOCK (no TOUCH)
                                                       peak <  knock -> a light tap: TOUCH, then SETTLED
any state: gravity direction > tilt threshold away from the boot reference for 2 s -> TILT (once;
           re-armed when it comes back within half the threshold)
```

| Setting | Shelf (`m1`) | Camera (`m2`) | Set by |
|---|---|---|---|
| activity threshold `thr` | 120 mg | 600 mg | `$C,MEMS_THR,<node>,<mg>` (stored in flash) |
| knock level | max(4 × thr, 800 mg) | = thr (any strong burst) | follows `thr` |
| knock window | 150 ms | 150 ms | compile time |
| settle time | 500 ms quiet | — | compile time |
| tilt threshold / hold | 5° / 2 s | 2° / 2 s | compile time |

What the `$M` fields mean: **TOUCH** — peak and RMS of the burst so far, `dur_ms` = time since it
started (~150 ms). **SETTLED** — peak and RMS of the calm that ended it, `dur_ms` = the whole episode
(touch → settled). **KNOCK** — peak and RMS of the spike, its duration. **TILT** — current window
peak/RMS, `dur_ms` = how long it has been tilted, `tilt_ddeg` = the angle in 0.1°. A camera node never
sends TOUCH/SETTLED (PROTOCOL.md drops them anyway), which saves UART bandwidth.

Why wait 150 ms before TOUCH: a trolley bump is one short spike, a hand lasts longer; 150 ms separates
them and is still far shorter than a pick (the load cell needs ~1 s to settle).

Weight gating also happens on the MCU: between the shelf node's TOUCH and SETTLED the Fusion/State
task sends every `$W` with `stable=0`. Vinith's Pi-side fusion (§4) applies the same rule again.

Host tests (`firmware/stm32/logic/tests/test_sm_mems.c`, synthetic 100 Hz streams, **bucket C**):
10 minutes of ±25 mg noise → 0 events; a 0.8 s hand at 350 mg → TOUCH then SETTLED
(duration 1.1–1.6 s); a 40 ms 1500 mg spike → KNOCK only; a 300 mg brush on the camera → nothing,
a 1850 mg knock → KNOCK; a 6.2° lean → one TILT reading 5.5–6.9°, no repeat while tilted, fires again
after returning level; a 1° lean → nothing; the integer atan2 is within 0.3° of `atan2` over 0–180°.
These check the logic against our model of a shelf. **Real thresholds come from the shelf test (§6).**

## 3. Calibration — *Ram*

1. **Orientation (automatic):** at every boot the node waits for 2 s of calm and takes that gravity
   direction as "level". So: power up with the shelf and camera in their normal position. A tilt that
   happens while the node is off is not seen (documented limit; the camera's image tamper check still
   catches a moved camera).
2. **Thresholds:** with nobody near, watch `$M` for 10 minutes (`python tools/mems_test.py --port COM5`
   runs the whole acceptance and reports false TOUCHes). If the shelf is next to a fridge compressor
   or a busy door and false TOUCHes appear, raise the shelf threshold, e.g. `cmd/CONFIG
   {"key": "MEMS_THR", "args": ["m1", 180]}`; if gentle picks are missed, lower it. It is kept in flash.
3. **Load cell pairing:** tare and calibrate the load cell under the same slot first
   (`docs/WIRING.md` §3), then enter the pack weight as `slots[].unit_grams` in the store config.
4. **Acceptance run:** `python tools/mems_test.py --port COM5 --config configs/<store>.yaml`
   (30 picks, 10 put-backs, 10 touches, 10 bumps, 10 camera knocks, 10 min idle) → writes
   `storemind/storemind/eval/results/platform/mems_<date>_board.json`, bucket B.

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
