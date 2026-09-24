# HANDOFF — Person A (Vinith) — 2026-09-24

Overwritten every session. Read `CLAUDE.md` first, then this.

## Where I stopped
- **PR-0 "contracts"** (branch `a/pr0-contracts`) is pushed and open for Person B's approval.
  It is the base for everything else: schema v2, paho 2.x bus + Last Will, config v2 +
  secrets, `docs/INTERFACES.md`, `docs/PROTOCOL.md` + `sensors/protocol.py`, per-person logs,
  `run_all` platform hook, docs skeleton. 212 tests pass.
- Note: `origin/master` was still at the initial commit, so this PR also carries the three
  earlier local commits (CAVIAR results, dashboard, calibration) and the research/plan docs
  commit. After it merges, master = everything.

## Next (in order, each on its own `a/…` branch from master after PR-0 merges)
1. `a/m1-counting-v2` — two-line gate / zone sequence, direction check, min displacement +
   track age, per-zone filters; tracker bake-off (ByteTrack / OC-SORT / BoT-SORT, same cached
   detections); IR-beam cross-check logic consuming `BEAM_CROSS`. Tune on CAVIAR corridor,
   report on front. Accept: exit accuracy ≥ 90 % on the held-out view.
2. `a/m3-shelf-v2` → 3. `a/m4-queue-v2` → 4. `a/m6-fusion` → 5. `a/m8-models` → 6. `a/m9-aihub` → 7. `a/m10-ask`.

## Blocked / needs the team
- **PR-0 needs Person B's approval** before dependent work merges.
- Our own recordings (bucket B) for queue and shelf — still none.
- Qualcomm AI Hub token: team runs `qai-hub configure --api_token …` themselves (never in the repo).
- Git history still contains ~200 MB of video blobs from the initial commit (already pushed).
