# HANDOFF — Person A (Vinith) — 2026-09-24

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (merge in this order)
1. **#4 Contract (M1 follow-up)** (`a/m1-contract` -> `a/pr0-contracts`): the gate-default commit
   `c454662`. PR #2 was merged into `a/pr0-contracts` *before* that commit was pushed, so the commit
   was stranded. #4 carries it.
2. **#1 PR-0 contracts** (`a/pr0-contracts` -> `master`). It now also contains #2 (merged) and, once
   merged, #4. This is the PR that gets everything to master.
3. **#3 M1** (`a/m1-counting-v2` -> `a/pr0-contracts`). Retarget it to `master` if #1 merges first.

## M1 outcome (docs/COUNTING.md)
- Code done: gate counter, 4 trackers, per-zone filters, staff zones + ArUco badges, IR-beam
  cross-check + fallback, detection cache, cross-validated bake-off. 235 tests pass.
- **Held-out CAVIAR: exit accuracy 76.2% (target ≥ 90%: not met)**, entry 96.7%, F1 0.82/0.76.
  Today's default scores 85.7% exits, F1 0.89/0.76. Settings do not transfer between the two
  views. Per the user, **no more CAVIAR tuning**. The IR beam is the per-site calibration path.
- Shipped: YOLO11n@640 + ByteTrack + gate defaults. YOLO11s runs at 3.75 FPS on the laptop CPU,
  so it cannot hit 8 FPS on a Pi. It is recorded as the Qualcomm NPU option (M9). YOLO26n is the
  Pi candidate for M8.
- The venv has CUDA torch (cu130). Speed numbers in RESULTS.md are forced to CPU.

## Next: M3 shelf v2 (branch `a/m3-shelf-v2`, config keys in a small `a/m3-contract`)
Already started (untracked on disk, not in the M1 commit):
- `storemind/tools/shelf_synth.py`: synthetic shelf timelines under day/evening/tube/dim/dark/glare
  with exact truth and simulated lux.
- `storemind/storemind/eval/eval_shelf_lighting.py`: v1 vs v2 vs v2-without-lux. Tune on seeds
  1-10, report on seeds 11-40.

Design (from reading `analytics/shelf.py`):
- Texture measured as gain-normalised gradient density, so it does not depend on brightness.
- Gray-world white balance, CLAHE, glare mask.
- Reference bank chosen by lux (or frame brightness); dark → UNKNOWN; lux jump → skip one cycle.
- Drift update, 4-point rectification, weight fusion.
- Tools: `shelf_capture` / `shelf_label`; reorder drafts.

## Blocked / needs the team
- Person B's approval of #1, then #2 and #3.
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, and 30 walked
  door crossings with the IR beam (docs/HARDWARE_TODO.md).
- Qualcomm AI Hub token (`qai-hub configure`, never in the repo).
