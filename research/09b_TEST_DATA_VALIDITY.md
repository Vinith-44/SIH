# 09b — Is our test data valid? (checked 23 Sep 2026)

Rule: every accuracy number must say **which data** it came from and fall in one of 3 buckets:
**(A) public benchmark with ground truth** · **(B) our own field recording, hand-labelled** · **(C) simulation (logic test only)**.
Judges accept all three if labelled honestly. They don't accept unlabelled or mixed-up numbers.

| Dataset | Valid for | NOT valid for | Licence / terms | Verdict |
|---|---|---|---|---|
| **CAVIAR shopping centre** (Lisbon mall, 2004) | Entrance counting, zone dwell, tracking. Ground truth has per-frame boxes with IDs **and activity labels incl. "shop enter" / "shop exit"** (CVML XML) | Queues, shelves. Also easier than an Indian store (low crowd, 384×288, 25 fps) | CC BY-SA, credit "EC Funded CAVIAR project/IST 2001 37540" | **✅ Use (bucket A)** for entry/exit accuracy |
| **MERL Shopping** (106 × ~2 min, fixed overhead camera over grocery shelves) | Shelf *interaction*: reach / hand-in-shelf / retract (temporal labels) → test **occlusion gate** and **pickup events** | Stock level / out-of-stock (no stock labels; shelves are never emptied) | Free for **research**; must cite Singh et al., CVPR 2016 | **✅ Use (A) for interaction/occlusion only** |
| **Mall dataset** (2,000 frames, 640×480, head positions) | Per-frame people **counting** in a crowd (occupancy / queue-length-style count accuracy) | Tracking, line crossing, wait time: **frame rate < 2 Hz**, too slow for tracking | Academic/research only, no commercial use; cite | **⚠️ Use (A) only for counting** |
| **Kaggle "Queue Waiting Time Prediction" CSV** | Maybe for testing forecast code | Presenting as "real store data" — origin not documented; may be synthetic | Unclear | **❌ Don't cite as real data.** Replace with our own simulation (C) + our own queue recording (B) |
| **Queue simulation** (SimPy / our own M/M/c generator with known λ, μ) | Proving the forecast + counter recommendation logic behaves correctly (we know the true answer) | Real-world accuracy | Ours | **✅ Use (C), label "simulation"** |
| **Our own recordings** (canteen queue, entrance, demo shelf) | Everything, especially **queue wait time** and **shelf OOS** — no public dataset covers these with ground truth | — | Ours; consent notice; delete after testing | **✅ Most important (B)** |
| **Our Kaggle void dataset (506 imgs) + SKU-110K + gapDetection** | Shelf detector mAP / precision / recall on images | Temporal shelf-state accuracy | Per dataset (SKU-110K research use) | **✅ (A) for detector metrics** |
| **Pexels stock videos** | Demo visuals, smoke tests | Any accuracy number (no ground truth) | Free, no attribution | **⚠️ Demo only** |
| **YouTube (standard licence)** | — | — | Terms don't allow downloading | **❌ Avoid**; Creative Commons videos only, demo only |

## Why this is actually good news for our pitch
- Research papers on out-of-stock detection use **private** store data (e.g. "Enhanced Out-of-Stock Detection in Retail Shelf Images Based on Deep Learning", *Sensors* 2024: 511 images from Croatian retailers, not public). There is **no public video benchmark for shelf stock levels or checkout wait times**. That's why our own recordings + the reference-based (label-free) shelf method matter — say this in the PPT.

## What the final results table should look like
| Module | Data | Bucket | Metric |
|---|---|---|---|
| Entry/exit | CAVIAR (N clips, M crossings) | A | count accuracy |
| Entry/exit | Our college entrance (N crossings) | B | count accuracy |
| Crowd count | Mall dataset | A | MAE |
| Shelf interaction / occlusion | MERL (N clips) | A | precision/recall of "person blocking shelf" |
| Shelf detector | Void + SKU-110K test split | A | mAP50, P, R |
| Shelf state | Our demo shelf | B | F1 EMPTY/LOW |
| Queue length / wait | Our canteen queue | B | MAE |
| Forecast | Simulated queues (known λ, μ) | C | lead time, correct counter count |
| Forecast | Our canteen (entrance + counter) | B | lead time |

## Sources
- CAVIAR clips: https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/ · ground truth: https://homepages.inf.ed.ac.uk/rbf/CAVIAR/gt.htm
- MERL Shopping: https://www.merl.com/research/downloads/MERL_Shopping_Dataset · https://www.merl.com/research/highlights/merl-shopping-dataset
- Mall dataset: https://personal.ie.cuhk.edu.hk/~ccloy/downloads_mall_dataset.html
- Queue CSV: https://www.kaggle.com/datasets/sanjeebtiwary/queue-waiting-time-prediction · https://ieee-dataport.org/documents/queue-waiting-time-dataset
- OOS paper with private data: https://www.mdpi.com/1424-8220/24/2/693
- Pexels licence: https://www.pexels.com/license/
