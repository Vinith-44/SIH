# Promotions: who walked past, who stopped, who picked it up

**Owner:** Vinith (Person A): `analytics/promo.py`, `eval/promo_sim.py`, `eval/eval_promo.py`.
Dashboard panel and Ask wiring: Ram (Person B), see §7.

The owner puts up a promotion (an end-cap, a stand, a "Buy 2 get 1" display) and asks:

- *Is anyone even looking at it?*
- *Of the people who walk past, how many stop?*
- *How long do they stay?*
- *Do they pick the product up?*

The system cannot know what a promotion is, so **the owner or installer marks it** in the config. The system then
**measures** it from the camera (and the load cell, if the promo shelf has one).

## 1. Marking a promotion (config)

A promo is a zone with `kind: promo` on the camera that sees the display. All promo keys are optional. The table
is in [CONFIG_REFERENCE.md §2.2](CONFIG_REFERENCE.md).

```yaml
cameras:
  - name: aisle
    zones:
      - name: promo-endcap
        kind: promo
        points: [[0.60, 0.58], [0.92, 0.58], [0.92, 0.92], [0.60, 0.92]]   # the floor in front of the display
        min_dwell_s: 3.0                 # this long inside = "stopped"
        promo_name: Diwali offer
        sku: [DAL-1KG, RICE-5KG]
        offer_text: Buy 2 get 1 free
        price: 149
        start_date: 2026-10-15           # inclusive; outside these dates nothing is counted
        end_date: 2026-11-05
        shelf: endcap                    # optional linked slot: its load-cell picks = "took the item"
        slot: E1
        # approach_band: 0.08            # frame heights; see §2
        # report_every_s: 300            # one PROMO_STATE per window
```

The polygon is **the floor in front of the display**, where shoppers' feet are when they look at it. It is not
the display itself. It is drawn the same way as any zone (`tools/calibrate.py`).

## 2. Definitions

All positions are the **foot point** (bottom-centre of the person's box). Staff are removed before tracks reach
this engine.

| term | rule |
|---|---|
| **passer-by** | the foot point comes within `approach_band` of the zone polygon (default 0.08 frame heights, about half a metre on a typical ceiling CCTV view), or crosses the zone, **without** staying `min_dwell_s` inside |
| **stopper** | stays inside the zone at least `min_dwell_s` (the same rule as ZONE_VISIT) |
| **dwell** | first to last moment inside the zone. The visit stays open while the person is within the band; it closes `gap_tolerance_s` (2 s) after they were last near |
| **stop rate** | stoppers / (passers-by + stoppers) |
| **pick** | a `PICKUP` event with `action: pick` at the linked slot (MEMS-gated load cell, `fusion/interaction.py`). `put_back` is counted separately; `touch` is not counted |

**Track stitching.** Suppose the tracker loses a stopper mid-stop and gives them a new number. The old visit
would close as a short "passer-by", and the new one would start from zero. To avoid this, a new track that
appears within 0.10 frame heights of a visit whose track vanished less than 2 s ago takes that visit over. The
queue engine uses the same idea.

**Picks are not sales.** Nothing here is linked to billing. "Picked up" means the load cell under the linked slot
lost weight while the MEMS sensor felt a touch. If the linked slot has no load cell in `sensors.cell_map`, the
pick fields are `null`, not 0.

## 3. What is published

One `PROMO_STATE` per promo zone every `report_every_s` (default 300 s). A partial window is published at the end
of a replay. Payload, topic, QoS and retain: [INTERFACES.md §2.2](INTERFACES.md).

- The counts are the people who **finished** passing or stopping in that window, so summing windows never
  counts anyone twice. A day's mean dwell = sum(`dwell_total_s`) / sum(`stoppers`).
- `ts` is the end of the window, even when a gap in the frames closes several windows at once.
- Outside the promo's dates: `active: false` and every count is `null`.
- The run summary (`run.py --summary-json`) shows the totals per promo zone under `cameras.<cam>.promo`.

## 4. Results (bucket C: simulation, not an accuracy claim)

`eval/promo_sim.py` walks simulated shoppers past a promo end-cap. It generates a 20-minute run per seed, about 9
people a minute with a rush in the middle, at 8 FPS. The people are:

- walkers: 66%, straight past at a random distance;
- walk-throughs: 8%;
- glancers: 10%, pause 1-2.5 s inside;
- stoppers: 16%, log-normal stay with a median of 9 s.

35% of stoppers pick 1-3 packs, and 10% of those put one back. "Noisy" adds foot-point jitter (sigma 0.006 frame
heights), 3% missed detections and 0.3 track-number switches per person-minute. **These are our assumptions, not
measurements of a shop.**

**Tuning** (`eval_promo --grid`, seeds 1-10, bands 0.04-0.12 with and without stitching →
`eval/results/promo_tuning.json`):

- The best was band 0.08 with stitching on.
- The simulator *defines* "walked past" with a 0.08 band. The winning band equals that definition by
  construction, and it gets zero error on clean tracks for the same reason. What the grid does show:
  - a band that does not match the site's definition costs 14-34% passer-by error;
  - stitching cuts the noisy stopper error from 2.5% to 0.9%.

**Held out** (seeds 11-40, `python -m storemind.eval.eval_promo` → `eval/results/promo.json`, 2026-09-25):

| | clean tracks | noisy tracks |
|---|---|---|
| passers-by counted (truth), mean error per run | 1848 (1849), 0.1% | 1959 (1849), **6.0%** |
| stoppers counted (truth), mean error per run | 1206 (1205), 0.1% | 1207 (1205), 1.6% |
| stoppers, v1 zone engine (no stitching) | 1206, 0.1% | 1223, 2.8% |
| stop rate ours vs truth (mean abs. error) | 39.6% vs 39.6% (0.0 pp) | 38.3% vs 39.6% (1.4 pp) |
| mean dwell error | 0.1% | 0.1% |
| picks / units (truth) | 439 / 866 (439 / 866) | 439 / 866 (439 / 866) |

- **Before this engine (v1)**, the system reported ZONE_VISITs, which are stoppers only. Passers-by were dropped,
  so there was no stop rate at all.
- **Passers-by are over-counted with noisy tracks (+6%).** A track switch while someone walks past splits one
  passer-by into two. Stitching only merges visits whose track vanished, and a walker's old number keeps moving
  away, so the stop rate reads about 1.4 pp low.
- **Picks are exact** because the simulated PICKUP events are handed in directly. This checks the counting and
  the windows, not the load cell. The load-cell pick accuracy is its own result (MEMS.md, RESULTS.md "Pick /
  put-back").

**Demo replay** (`configs/demo.yaml`, synthetic clip, no ground truth): the entrance camera's `promo-endcap` zone
reported 5 passers-by and 2 stoppers, while the zone engine reported 1 ZONE_VISIT. Per-frame trace of the
difference: at 82.4 s the tracker lost a shopper standing in the zone, and a new track number appeared at the
same spot 0.7 s later. The zone engine saw two stays of about 1.9 s and 1.6 s and dropped both, since each is
under 3 s. The promo engine stitched them into one 4.2 s stop.

**Real footage: not measured yet.** It needs a recording of a real display with passers-by, stoppers and dwell
hand-labelled (bucket B, docs/HARDWARE_TODO.md).

## 5. Limits (what this does not do)

- **Not sales and not conversion.** It measures attention (stop rate, dwell) and pick-ups. Tying a promo to
  sales needs billing data, which StoreMind does not read.
- **No "vs normal days" comparison yet.** research/03 proposes comparing promo days with normal days. The windows
  carry what that needs (`active`, daily sums), but the comparison is not built.
- **The band is in frame heights, not metres.** With strong perspective, 0.08 of the frame is a longer distance
  far from the camera than near it. Set `approach_band` per zone after looking at the calibration snapshot.
- **A person who walks away and comes back** after more than 2 s is counted twice.
- **Someone standing beside the zone** (within the band, outside the polygon) keeps a stop open. The stop
  measures first-to-last moment *inside*, so that time between two stays inside is included in dwell.
- **One retained message per camera topic.** With several promo zones on one camera, a dashboard that connects
  late gets only the last zone's window from the broker. The database has every window.
- **Dates use the store clock's local date** (IST by default). A replay with `--start-time` uses that date.

## 6. Privacy

Only session-random track numbers are used (tracking/tracker.py, PRIVACY_DPDP.md), and they never leave the engine.
PROMO_STATE carries counts and durations, nothing per person. No image is stored.

## 7. For Ram (not done yet)

- **Dashboard panel:** latest PROMO_STATE per promo: name, stop rate, stoppers, mean dwell, picks, and
  "inactive" outside the dates. The DB already stores the events.
- **Ask / summary:** "How is the Diwali offer doing?" needs a PROMO_STATE aggregate in the Ask query set
  (docs/ASK.md). That is my side (Vinith); it needs a follow-up PR.
