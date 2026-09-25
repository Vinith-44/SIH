# HANDOFF — Person A (Vinith) — 2026-09-25 (M11)

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (Ram approves)
- **#22 M11 docs, Person A part** (`a/m11-docs`): ARCHITECTURE, CONFIG_REFERENCE (+ test), EVALUATION,
  PRIVACY_DPDP, DEMO_RUNBOOK, TEAM_GUIDE §2, HARDWARE_TODO "M10", a RESULTS label fix.
- **Track IDs** (`a/track-ids`, on #22) and **contract fixes** (`a/fix-contract`, on track-ids): see below.
- Merged: PR-0, M1, M3, M4, M6, M8, M9, M10 and their contracts (#1-#5, #7, #9-#16, #19, #21), plus Ram's M2 and CI
  (#6, #8, #17, #18, #20).

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
| M11 docs | PR (my part) | Ram's slots marked "Ram fills in" in DEMO_RUNBOOK §2, §3, §7 and TEAM_GUIDE §3, §4 |

## For Ram (M11, his part)
- Fill the marked slots: DEMO_RUNBOOK §2 (primary demo commands on the Pi), §3 (fake-CCTV backup), §7 (STM32 and
  bridge failures); TEAM_GUIDE §3, §4 (switching on, red lights, photos).
- His own docs, still stubs: FIRMWARE, WIRING, SETUP_PI5, OPERATIONS, TROUBLESHOOTING, HARDWARE_INVENTORY.
- PRIVACY_DPDP §4 points to SETUP_PI5 / CCTV_ONBOARDING for the concrete firewall and DVR-account steps.
- Wire `/api/ask` and `/api/summary` (docs/ASK.md §6); start `IngestManager` from `run.py` when ready
  (ARCHITECTURE §5 lists it as built but not connected).

## Follow-ups found in M11: all fixed, in two stacked PRs (merge after #22)
1. `a/track-ids`: session-random track IDs (`SessionIdTracker`); no measured number changed.
2. `a/fix-contract` (contract PR, needs Ram):
   - `cameras[].infer_size` works (per-size detector; fixed-shape models refuse a mismatch);
   - `forecast.horizon_min` retired (old configs load with a warning);
   - `shelf.method` detector / hybrid wired, `hybrid` implemented, and both refuse to start without
     `shelf.detector_model`. **No product detector is trained**: this is plumbing, not a result;
   - `run.py --backend` takes its choices from the config schema.

## Blocked / needs the team
- Ram's approval of the M11 PR, and his half of M11.
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, 30 walked door crossings with the
  IR beam, and the M6 shelf test on Ram's board (docs/HARDWARE_TODO.md).
- Pi 5 runs: `tools/bench_pi.py` (HARDWARE_TODO "M8") and one Ask question (HARDWARE_TODO "M10").
- Telugu/Hindi summary templates: a native speaker's read before the demo.
- Optional: a real QCS6490 board, to move the Q numbers to S (deploy/qualcomm/README.md checklist).
