# WORK_LOG — Person A (Vinith: vision & intelligence)

Append-only. Only Person A writes here. Dated entries; every number comes with the
command that produced it. History before the two-person split: `WORK_LOG_HISTORY.md`.

---

## 2026-09-24 — PR-0 "contracts" (branch `a/pr0-contracts`)

Implements research/26 §3 so both people can build against the same contract.

### Changes
- **Event schema v2** (`core/events.py`, `SCHEMA_VERSION = 2`, additive only): new types
  `SHELF_MOTION, CAMERA_MOUNT, BEAM_CROSS, PRESENCE, ENVIRONMENT, WEIGHT, NODE_HEALTH,
  CAMERA_HEALTH`; `HealthData` gains optional `power_w, mj_per_frame, throttled`. All v1 types
  and fields unchanged; a stored v1 event still validates (tested).
- **MQTT bus** (`core/bus.py`): paho-mqtt 2.x `CallbackAPIVersion.VERSION2`; retained Last Will
  `offline` on `storemind/{store}/{node}/status`, retained `online` on connect, explicit
  `offline` on clean close; QoS/retain table from §3.2 (`QOS1_TYPES`, `RETAINED_TYPES`);
  `listen=True` delivers other processes' events locally and drops the broker echo of our own
  by event id; `command_topic()` / `status_topic()`. `requirements.txt` pins `paho-mqtt>=2,<3`.
  `pipeline.py`: one-line change to pass `store`, `node`, `listen` to `MqttBus`.
- **Config** (`core/config.py`): `sensors.node` (`id`, `enabled_sensors`, `protocol_mode`,
  `time_sync_s`, `cmd_timeout_ms`, `cmd_retries`), `sensors.mems_nodes`, `sensors.has()`,
  `mqtt.listen`; readable validation errors naming the key; `load_secrets()` (file + env vars);
  `configs/secrets.example.yaml`.
- **Serial protocol**: `docs/PROTOCOL.md` + pure-Python `storemind/sensors/protocol.py`
  (framing, XOR checksum, 96-byte limit, typed decode incl. scaled ints and empty = not measured,
  `LineAssembler`, `SeqTracker`). All example lines were produced by the encoder.
- **Docs**: `docs/INTERFACES.md` (envelope, all types, topics, QoS/retain, Last Will, command
  JSON, config contract, platform-result file format), `docs/README.md` index + M0 stubs.
- **run_all**: includes `eval/results/platform/*.json|md` (Person B's results) with their
  bucket; buckets **Q** and **P** added.
- **Logs**: per-person logs/handoffs; root `WORK_LOG.md` / `HANDOFF_FOR_CLAUDE.md` are now
  index files; their old content moved (git mv) to `logs/WORK_LOG_HISTORY.md` and
  `handoff/HANDOFF_HISTORY_2026-09-24.md`. `CLAUDE.md` log references updated.

### Tests
- `tests/test_protocol.py` parses every ```text example in PROTOCOL.md (and round-trips it)
  and checks every ```invalid example is rejected for the stated reason.
- `tests/test_interfaces_doc.py` validates every JSON example in INTERFACES.md and its topic.
- `tests/test_contracts_v2.py` schema v2, bus (fake paho client), config, secrets, platform hook.
- `tests/test_no_secrets.py` fails if `secrets.yaml`/`.env` or a token-like string is tracked or staged.

```
cd storemind
.venv/Scripts/python -m pytest tests -q      # 212 passed (was 124; all old tests unchanged and passing)
```

### Real-broker smoke test (not part of the suite: needs a broker)
Throwaway `amqtt` broker in a scratch venv, two `MqttBus` instances (bridge node + vision node,
`listen=True`) and a raw watcher: the vision process received the bridge's `BEAM_CROSS` exactly
once; closing the bridge's socket without DISCONNECT made the broker publish the retained Will
`offline` on `storemind/smoke/stm32-01/status`. Mosquitto itself not tested yet (not installed).

## 2026-09-24 - M1 counting v2 + tracker bake-off + IR-beam cross-check (branch `a/m1-counting-v2`)

Config keys: contract PR #2 (`a/m1-contract`). Code: `analytics/footfall.py` (GateCounter,
build_counter), `tracking/tracker.py` (ByteTrack / OC-SORT / BoT-SORT / SORT via `trackers`),
`inference/filters.py`, `analytics/staff.py` (zones + ArUco), `fusion/beam.py` (cross-check +
fallback), `inference/cached.py` + `eval/detcache.py` (detection cache + timeline replay),
`eval/bakeoff.py`, device auto-select in `inference/detector.py`. The pipeline gets small hooks
(build_counter, filters, staff, beam check, tamper -> beam).

Environment: the venv now has torch 2.14.0+cu130 (`pip install --force-reinstall --no-deps
torch==2.14.0 torchvision==0.29.0 --index-url https://download.pytorch.org/whl/cu130`), RTX 4050.

Commands and results:
- `python -m storemind.eval.detcache` (+ `--model ../models/yolo26n.pt`, `--model ../models/yolo11n.pt --imgsz 960`,
  `--model ../models/yolo11s.pt`): 64 caches in `data/detcache/`. Timeline replay was checked
  identical to video-decode replay on EnterExitCrossingPaths2cor, and it is about 10x faster.
- `python -m storemind.eval.bakeoff` -> `eval/results/tracker_bakeoff.md` (protocol v2,
  2-fold CV over views, 2,336 combos). Held out: entries 31 (30) 96.7%, **exits 16 (21) 76.2%**,
  entry/exit F1 0.82/0.76. Baseline YOLO11n+ByteTrack+v1 on the same cache: 33/24, 90.0%/85.7%,
  F1 0.89/0.76. **M1 exit target (>=90% held out) not met.**
- Run 1 (protocol v1, `tracker_bakeoff_run1.md`) picked SORT on a 0.002 IDF1 margin. The protocol
  was revised after seeing it; both are published.
- Synthetic entrance (bucket C) with the gate counter in demo.yaml: 14/14 in, 6/6 out, F1 1.00.
- CPU-only speed (`STOREMIND_DEVICE=cpu`, vtest.avi, 100 frames, 640 px): YOLO11n 99.4 ms (10.0 FPS),
  YOLO26n 91.9 ms (10.8 FPS), YOLO11s 266.5 ms (3.75 FPS). YOLO11s cannot reach 8 FPS on a Pi 5,
  so it does not ship. It is recorded as the Qualcomm NPU option (docs/QUALCOMM.md).
- RESULTS.md regenerated. The speed section is now explicitly CPU-only: the first regeneration
  after the device change had silently timed on the GPU, so that was fixed before commit.
- Tests: 235 passed.

Decisions (user, 24 Sep): do not tune further on CAVIAR. Ship YOLO11n@640 + ByteTrack + gate
counter defaults (gate 10 px, direction off, age 0, confirm 0.5 s).

## 2026-09-24 - M3 shelf v2 (branch `a/m3-shelf-v2`; config keys in PR #5 `a/m3-contract`)

Code: `analytics/shelf.py`:
- gray-world white balance on non-slot pixels;
- gain-normalised gradient texture, CLAHE, glare mask, gradient SSIM;
- reference bank chosen by lux/brightness, with learning and drift;
- dark → UNKNOWN; a lux jump skips one cycle;
- 4-point rectification, weight fusion with "CHECK SHELF", camera fault / long occlusion → UNKNOWN.

Also: `analytics/reorder.py` (SQLite drafts + WhatsApp text), and pipeline hooks (ENVIRONMENT/WEIGHT/CAMERA_HEALTH/
SENSOR restock → shelf; SLOT_STATE → reorder). Tools: `tools/shelf_synth.py`, `shelf_capture.py`, `shelf_label.py`.
Evals: `eval/eval_shelf_lighting.py`, `eval/eval_shelf_photos.py`.

Commands and results:
- `python -m storemind.eval.eval_shelf_lighting --grid` (tuning seeds 1-10, 48 settings, 8 workers, ~5 min).
  Best: empty 0.28, low 0.5, dark_lux 15, blur 5, texture threshold 0.5 -> EMPTY F1 0.92, acc 0.92.
- `python -m storemind.eval.eval_shelf_lighting` (test seeds 11-40): v1 acc 54.7%, EMPTY F1 0.74 (evening 0.15);
  v2 acc 91.7%, EMPTY F1 0.92 (day 0.91 / evening 0.93 / dim 0.89), dark: 714/714 UNKNOWN, 0 false EMPTY.
  Bucket C.
- Disclosures:
  - The generator was changed (light in runs, and the restock-index bug fixed) before any test seed was scored.
  - The test seeds were scored twice: 0.93 before and 0.92 after the confidence-clipping fix found by
    `test_the_bank_learns_new_lighting...`.
  - No measurable lux benefit in simulation.
- Synthetic shelf clip (bucket C, demo.yaml now on the v2 defaults): unchanged at 95.8% acc, EMPTY F1 1.00.
- Photo scorer smoke test on synthetic frames with generated labels: v1 EMPTY F1 0.82, v2 0.89. This is a
  tool check only, not a result.
- M3 acceptance on our own shelf (bucket B): **not measured yet**. Steps are in docs/HARDWARE_TODO.md.
- Tests: 249 passed.

Note: the files `ANTIGRAVITY_HANDOVER.md`, `antigravity/` and `.agents/` appeared in C:\SIH during this
session. They are not Person A's and are left untracked.

## 2026-09-24 - Names contract + M4 queue v2 (branch `a/m4-queue-v2`)

- Names: PR #9 (merged) records Vinith = Person A, Ram = Person B in CLAUDE.md.
- Contracts: PR #10 (merged) adds the queue v2 config keys and QUEUE_STATE fields. PR #11 (open) sets the tuned
  defaults (join_dwell_s 3, max_join_speed 0.10, party_dist 0.06).
- Code: `analytics/queue.py` gets:
  - dwell membership, a lane polyline (LanePath), parties (pairwise closeness + union-find);
  - track stitching (4 s, 0.1 frame heights), balk/renege, tail overflow with a 5 s hold, Little's law;
  - `counter_spec_from_config()`, which is now shared by the pipeline and the eval.
- Pipeline: builds specs with `counter_spec_from_config()`; adds a QUEUE_OVERFLOW alert.
- demo.yaml: counter-1 uses `membership: dwell`.
- Evals: `eval/queue_sim.py` (track simulator with exact truth) and `eval/eval_queue_v2.py` (v1 vs v2, clean and
  noisy; tune on 1-10, report on 11-40).

Commands and results:
- `python -m storemind.eval.eval_queue_v2 --grid` (tuning seeds, 27 settings, ~70 s). Best: dwell 3 s,
  speed 0.10, party 0.06. Queue MAE 0.20, party MAE 0.15, wait err 18% (clean and noisy pooled).
- `python -m storemind.eval.eval_queue_v2` (test seeds 11-40). Bucket C, clean / noisy:
  - v1: queue MAE 0.38 / 0.41, joins 2598 / 3081 (truth 833), Little's W err 53% / 61%.
  - v2: queue MAE 0.19 / 0.21, party MAE 0.15 / 0.18, joins 825 / 969, Little's W err 20% / 12%,
    median-wait err 2.8% / 33%.
  - Balks found 0 of 65; reneges 87 vs 30; walked away unserved 87 vs 95. Tail overflow: 1 true sample only.
- Disclosures:
  - The tail truth definition was aligned to the engine's on the tuning seeds (it was one position off).
  - The balk-timing fix came after the first test scoring; the rescore was identical.
- Synthetic queue video (bucket C) with dwell: 7/7 customers, queue MAE 0.06 (v1 0.00), wait err 1.1%.
- Real canteen clip (bucket B): not recorded yet. Steps are in docs/HARDWARE_TODO.md.
- Tests: 362 passed.
