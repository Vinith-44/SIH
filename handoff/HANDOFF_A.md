# HANDOFF — Person A (Vinith) — 2026-09-25 (promotion analytics)

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (Ram approves)
- **#39 Contract: PROMO_STATE + promo keys** (`a/promo-contract` → master): small contract PR, additive.
- **Promotion analytics** (`a/promo`, stacked on `a/promo-contract`): engine, pipeline hook, simulator + eval,
  docs/PROMO.md, RESULTS section. **Merge #39 first, then retarget this PR to master before merging it** (the
  #23/#24 lesson: a stacked PR merged into its base never reaches master).
- Merged: everything up to #37 (all of M1-M11 Person A parts, #25 carry, #26 tech doc; Ram's #27-#37).

## Where each milestone stands (details in docs/)
| Milestone | State | Honest headline |
|---|---|---|
| M1 counting (COUNTING.md) | merged | held-out CAVIAR exit accuracy 76% — **target 90% not met**; IR beam = per-site calibration |
| M3 shelf (SHELF.md) | merged | synthetic EMPTY F1 0.92 (v1 0.74); **real shelf not measured** |
| M4 queue (QUEUE.md) | merged | passers-by fixed (joins 825 vs 833 true); per-person wait 33% under ID switches; **canteen clip not recorded** |
| M6 fusion (MEMS.md) | merged | simulated picks F1 0.98, units 99.7%; **no hardware yet** |
| M8 models (MODELS.md) | merged | FP32 exports lossless on CAVIAR; INT8 moves 2-4 crossings; **Pi speed not measured** |
| M9 Qualcomm (QUALCOMM.md) | merged | AI Hub hosted RB3 Gen 2 (QCS6490), bucket Q: YOLO11n INT8 12.8 ms, 100% NPU |
| M10 ask (ASK.md) | merged | 0 invented numbers in 60 questions; held-out accuracy 13/20; **Pi latency not measured** |
| M11 docs | merged (my part) | Ram's slots marked "Ram fills in" in DEMO_RUNBOOK §2, §3, §7 and TEAM_GUIDE §3, §4 |
| Promotions (PROMO.md) | PR | simulated held-out: noisy stoppers 1.6% err (v1 2.8%), passers-by +6%, stop rate 1.4 pp low; **real display not measured** |

## For Ram (M11, his part)
- Fill the marked slots: DEMO_RUNBOOK §2 (primary demo commands on the Pi), §3 (fake-CCTV backup), §7 (STM32 and
  bridge failures); TEAM_GUIDE §3, §4 (switching on, red lights, photos).
- His own docs, still stubs: FIRMWARE, WIRING, SETUP_PI5, OPERATIONS, TROUBLESHOOTING, HARDWARE_INVENTORY.
- PRIVACY_DPDP §4 points to SETUP_PI5 / CCTV_ONBOARDING for the concrete firewall and DVR-account steps.
- Wire `/api/ask` and `/api/summary` (docs/ASK.md §6); start `IngestManager` from `run.py` when ready
  (ARCHITECTURE §5 lists it as built but not connected).

## Promotions: next steps
- Ram: dashboard panel for the latest PROMO_STATE per promo (PROMO.md §7).
- Vinith: PROMO_STATE aggregate in the Ask query set ("How is the Diwali offer doing?"); a promo-vs-normal-days
  comparison (research/03 idea, not built).
- docs/TECHNICAL_DOCUMENT.md (+ PDF) predates promotions: add a short section when it is next rebuilt.
- Bucket B: record a real display (passers-by, stoppers, dwell hand-labelled) before quoting any promo accuracy.

## Blocked / needs the team
- Ram's review of #39 (contract) and the promotion PR.
- Ram's half of M11 (slots above).
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, 30 walked door crossings with the
  IR beam, and the M6 shelf test on Ram's board (docs/HARDWARE_TODO.md).
- Pi 5 runs: `tools/bench_pi.py` (HARDWARE_TODO "M8") and one Ask question (HARDWARE_TODO "M10").
- Telugu/Hindi summary templates: a native speaker's read before the demo.
- Optional: a real QCS6490 board, to move the Q numbers to S (deploy/qualcomm/README.md checklist).
