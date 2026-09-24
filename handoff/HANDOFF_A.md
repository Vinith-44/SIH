# HANDOFF — Person A (Vinith) — 2026-09-24 (evening)

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (Ram approves)
- **#11 Contract (M4 defaults)**: three default values for queue v2. Merge before the M4 PR.
- **M4 PR** (`a/m4-queue-v2` -> master): queue v2 engine, simulator, eval, docs/QUEUE.md.
- Merged so far: #1-#5 (PR-0, M1, M3 contracts), #7 (M3), #9 (names), #10 (M4 contract), plus Ram's #6/#8 (M2).

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

## M3 shelf v2: done except the bucket-B photo set
- PR #5 (`a/m3-contract`): shelf config keys and the v2 threshold defaults. PR #6 (`a/m3-shelf-v2`): the engine,
  tools and evals.
- Synthetic test seeds: EMPTY F1 0.92 (v1 0.74), evening 0.93 (v1 0.15), dark → UNKNOWN. This is bucket C.
- **Real acceptance (our own shelf, EMPTY F1 ≥ 0.85 day+evening) is not measured.** The team needs to capture one:
  docs/HARDWARE_TODO.md "M3 - shelf photos".
- For Person B:
  - the dashboard can show `pipeline.reorder.whatsapp_text()` and the new slot `reason` strings;
  - the bridge publishes `$R` as `SENSOR {sensor: "restock", channel: <shelf>}`.

## M4 queue v2: done except the bucket-B clip (docs/QUEUE.md)
- Simulated tracks, held out, v2 vs v1:
  - joins 825 vs 2598 (truth 833); passers-by no longer inflate lambda;
  - queue MAE 0.19 vs 0.38; party MAE 0.15;
  - Little's-law W err 12% under ID switches; per-person wait err 33% under ID switches (target 20%: not met).
- **Balk/renege split does not work** (0/65 balks); only "walked away unserved" is usable.
- **Real canteen clip not recorded**: docs/HARDWARE_TODO.md "M4 - canteen queue clip".
- For Ram's dashboard: QUEUE_STATE now carries `queue_parties`, `wait_littles_s`, `arrivals_per_min`, `balks`,
  `reneges`, `tail_overflow`; the new alert key is `QUEUE_OVERFLOW:<counter>`.

## Next: M6-fusion (branch `a/m6-fusion`)
Pick/put-back from MEMS SHELF_MOTION + WEIGHT gating, a TOUCH->SETTLED shelf-check trigger, camera tamper fusion
(CAMERA_MOUNT + image tamper). Then M8 (model export + bench_pi.py), M9 (AI Hub), M10.

## Blocked / needs the team
- Ram's approval of #1, then #2 and #3.
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, and 30 walked
  door crossings with the IR beam (docs/HARDWARE_TODO.md).
- Qualcomm AI Hub token (`qai-hub configure`, never in the repo).
