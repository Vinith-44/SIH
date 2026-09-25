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

## 2026-09-24 - M6-fusion (branch `a/m6-fusion`; contract PR #13 `a/m6-contract`)

- Contract #13: PICKUP.action (pick | put_back | touch) + units; slots[].unit_grams; alerts.open_hours.
- Code: `fusion/interaction.py` (ShelfInteractionEngine):
  - MEMS-gated pick/put-back with units; a pending stable reading before SETTLED; a whole-pack check;
  - debounced no-episode changes (weight-only fallback, shrink); touch engagement;
  - KNOCK -> fallen-stock alert; TILT alert;
  - camera-mount + image-tamper fusion; PIR after-hours alert.
- Pipeline hooks: builds the engine from config (cell_map, unit_grams, mems in enabled_sensors, open_hours,
  live zone occupancy); routes SHELF_MOTION / WEIGHT / CAMERA_MOUNT / PRESENCE / ZONE_VISIT; `_due()` processes a
  shelf camera immediately after TOUCH -> SETTLED; image tamper -> engine; alerts and tick in `_periodic`.
- Evals: `eval/sensor_sim.py` (bridge-event simulator with truth) and `eval/eval_fusion.py` (v1 vs weight-only
  vs M6).

Commands and results:
- Tuning seeds 1-10 exposed three engine issues, each fixed:
  - the new weight arrives before SETTLED (recall 0.50 -> 0.99);
  - pushes flagged stable (put-back P 0.59 -> 0.67, then whole-pack check);
  - no-episode debounce (put-back P -> 0.94).
- The harness now feeds zone-visit ends the way the pipeline does (13 false shrinks came from that harness gap).
- `python -m storemind.eval.eval_fusion --grid`: best noise_g 10, settle_timeout 2 s (grid spread ~0.01 F1).
- `python -m storemind.eval.eval_fusion` (seeds 11-40), bucket C:
  - M6: picks P/R/F1 0.98/0.98/0.98, units 99.7%; put-backs 0.90/0.96/0.93; shrinks 0; fallen 15/15;
    touches 375/399.
  - Weight-only: picks 0.66/0.66/0.66.
  - v1: picks 0.05/1.00/0.09 with 243 false shrinks.
- Hardware acceptance: not measured (needs Ram's board). Steps are in docs/HARDWARE_TODO.md "M6".
- Tests: 374 passed.

## 2026-09-24 - M8 models: exports, fidelity, CAVIAR per format, bench_pi.py (branch `a/m8-models`)

- `storemind/inference/export.py`: exports ONNX FP32, ONNX INT8 (ORT static QDQ, Conv only), NCNN and LiteRT INT8
  (via a separate TensorFlow venv `.venv-export`, git-ignored).
- Calibration: 198 frames from CAVIAR, vtest and the synthetic scenes (scratchpad only).
- Exported for YOLO11n and YOLO26n @640: ONNX FP32, ONNX INT8 and NCNN are done.
- **LiteRT INT8 is blocked**: Ultralytics 8.4.160 refuses the TFLite export on Windows ("LiteRT export only
  supported on Linux x86 and macOS"), and WSL is not installed (installing it needs admin rights and a reboot).
- Environment note: Ultralytics auto-installed onnx, onnxslim, ncnn and pnnx into the main venv (export-time only,
  not in requirements.txt). Pinned runtime packages were checked unchanged, and all tests pass.
- `python -m storemind.eval.eval_export --caviar --calib <calib>`:
  - Fidelity vs FP32 on vtest (S): INT8 recall 97.6% (11n) / 99.6% (26n).
  - Laptop CPU ms median: ONNX FP32 29.8 / 24.3; INT8 32.9 / 29.7; NCNN 52.3 / 138.3; PyTorch 99.7 / 41.1. On x86,
    INT8 is not faster and NCNN is slower; the Pi decides.
  - CAVIAR per format (A, in-sample settings): FP32 exports identical to PyTorch (11n 28/21, 26n 27/16).
    INT8: 11n 28/19, 26n 25/14.
- `tools/bench_pi.py`: latency median/p95, FPS, temperature, throttle flags, PMIC power (x1.1451 + 0.5879) and
  mJ/frame. Parsers are unit-tested with real vcgencmd formats. **Not run on a Pi yet**: docs/HARDWARE_TODO.md "M8".
- Tests: all pass (see commit).
## 2026-09-24 - M9 Qualcomm AI Hub (branch `a/m9-aihub`; contract PR #16 `a/m9-contract`)

- The AI Hub token was already configured by Vinith (`~/.qai_hub/client.ini`; never read, never in the repo).
- `qai-hub` 0.55 installed in the main venv (optional dependency).
- Devices: the hosted **Dragonwing RB3 Gen 2 Vision Kit (QCS6490)** plus the QCS8550 (Proxy).
- `tools/aihub_profile.py`: ONNX clean-up + head split -> AI Hub quantize (w8a8, 20 calibration frames) -> compile
  TFLite -> profile -> inference job (6 vtest frames) compared with local ONNX FP32. Compiled .tflite files are
  downloaded to models/.
- Issues hit, each fixed:
  - AI Hub rejected value_info duplicating the outputs (onnxslim artifact);
  - qai-hub's progress printer crashes on a cp1252 console (set PYTHONIOENCODING=utf-8);
  - **run 1 INT8 detected nothing**: one shared output scale zeroed every score (verified locally: max score 0.0).
    Fixed by splitting boxes/scores into two outputs. Run 1 is kept as `qualcomm_aihub_run1_single_output.json`.
- Run 2 (`PYTHONIOENCODING=utf-8 python tools/aihub_profile.py --models yolo11n.onnx yolo26n.onnx yolo11s.onnx
  --devices "Dragonwing RB3 Gen 2 Vision Kit" "QCS8550 (Proxy)" --calib <calib>`), bucket Q, on RB3 Gen 2:
  - YOLO11n: FP32 150.9 ms (5% NPU layers); **INT8 12.8 ms, 100% NPU, 17 MB**, 29/29 boxes found, +2 extra.
  - YOLO26n: INT8 13.9 ms.
  - **YOLO11s: INT8 11.0 ms** (FP32 239 ms), 30/30 found, +5 extra.
  - QCS8550 proxy: INT8 2.7-3.5 ms.
- Backends `litert_qnn` and `ort_qnn` with an honest CPU fallback (`accelerator` field, `require_accelerator`);
  channels-first TFLite input; `merge_yolo_outputs()` (a unit test caught an axis bug when scores come first; fixed;
  the AI Hub results were unaffected because the tool passes boxes first).
- Verified locally: the AI Hub INT8 TFLite runs via `backend: litert` (3.92 vs 3.95 boxes/frame; 25 ms laptop CPU).
- Docs: docs/QUALCOMM.md (Q table + P comparison, kept separate) and deploy/qualcomm/README.md (port guide,
  "prepared, not run on hardware").
- Tests: 379 passed.
## 2026-09-25 - M10 "Ask your store" + daily summary (branch `a/m10-ask`)

- New package `storemind/storemind/llm/` (Person A path; no contract change, no new Python dependency):
  - `views.py`: 8 whitelisted TEMP views over `events` on a `mode=ro` connection. A SQLite authorizer allows only
    SELECT on those views plus a whitelist of plain functions; one statement, 50 rows, 2 s.
  - `ask.py`: Ollama backend (`qwen2.5-coder:1.5b`, stdlib HTTP to localhost) first, keyword rules as the fallback.
    Answers are rendered from the rows and carry sql/columns/rows/`model_query`. A model sentence is only a
    `{column}` template filled by code, with units taken from the column name. `verify_numbers()` is the final guard.
  - `summary.py`: daily summary in en/te/hi from fixed queries and templates, no model; "no data" instead of 0.
- `eval/eval_ask.py`: 2 simulated days (2,598 events through the real EventStore), truth computed in Python.
  Three sets of 20 questions written one after another (A, then B held out, then C held out).
  - Run 1 (A, untuned), run 2 (B), run 3 (C first run = the held-out result), run 4 (C, current code).
    Evidence files: `ask_run1_untuned.json`, `ask_run2_before_changes.json`, `ask_run3_c_first_run*.json`,
    `ask.json`, `ask_3b.json`.
  - **0 invented numbers in every run, set and backend** (acceptance met).
  - Held-out accuracy (C, first run, deployed LLM then rules): **13/20 correct, 7 wrong**. Run 4: 15/20 (not held out).
  - Found and disclosed along the way:
    - the keyword rules overfit (A 20/20, B 13/20);
    - the model mislabelled seconds as minutes and the grader missed it; unit check added, runs 1-3 re-graded
      (scores fell 1-2);
    - "the quietest hour was 8" (count used as the hour) led to the template design;
    - recording extra truth before run 2 reordered the zone-visit random draws (only zone data changed).
  - qwen2.5-coder:3b is no better on held-out C (12/20 deployed); the default stays 1.5B. Laptop GPU ≈5 s per question;
    **Pi 5 not measured**.
- run_all: new "ask" section (bucket C); fixed the stale "Qualcomm needs a token" line in "Still missing".
- Docs: docs/ASK.md (design, what the check does and does not prove, full run history, Ram's wiring notes);
  docs/README.md index row.
- After run 4: a rupee template followed by "rupees" no longer prints "Rs 770.5 rupees" (wording only, no number
  changes; unit-tested).
- Tests: 472 passed (30 new in tests/test_ask.py: guard, verifier, templates/units, fallbacks, summary).
## 2026-09-25 - M11 docs, Person A part (branch `a/m11-docs`; split agreed with Vinith: by owner)

- Filled from the code on master (each claim checked against the source):
  - docs/ARCHITECTURE.md: Mermaid diagrams (big picture, per-frame flow, planned Pi process layout), fusion table,
    module map with owners, and a list of what is built but not wired.
  - docs/CONFIG_REFERENCE.md: every config key (type, default, meaning), generated from `core/config.py`.
    `tests/test_config_reference.py` fails if a key is missing.
  - docs/EVALUATION.md: buckets, anti-tuning rules, per-evaluation protocol table, every disclosed correction, how
    to regenerate RESULTS.md, what is not measured.
  - docs/PRIVACY_DPDP.md: what is and is not kept, with the code that guarantees it; deployment rules; judge answers.
  - docs/DEMO_RUNBOOK.md: laptop replay commands (run on 2026-09-25: demo replay, dashboard on --api returned HTTP
    200, summary and ask on the demo DB, CAVIAR corridor replay); 5-minute script; numbers card; failure table.
    Ram's sections are marked slots.
  - docs/TEAM_GUIDE.md: section 2 (what the cameras measure, in simple English); Ram's sections are slots.
  - docs/HARDWARE_TODO.md: M10 item (Ollama on the Pi, time one question).
- Found while checking, and fixed:
  - RESULTS.md labelled the bake-off's all-clips pick (YOLO11s) "shipped configuration". Relabelled, with a row
    saying the Pi ships YOLO11n@640 (run_all.py).
- Found while checking, **not fixed** (behaviour changes; listed in the handoff for separate PRs):
  - Track IDs are sequential per run, not "session-random" as CLAUDE.md and `tracking/tracker.py` say.
  - Config keys no code reads: `cameras[].infer_size`, `forecast.horizon_min`, `shelf.detector_model`.
  - `shelf.method: detector|hybrid` do nothing: the pipeline passes no detector to the shelf engine.
  - `run.py --backend` choices lack `litert_qnn` / `ort_qnn` (config works).
## 2026-09-25 - Session-random track IDs (branch `a/track-ids`, stacked on `a/m11-docs`)

- `tracking/tracker.py`: `build_tracker()` wraps every tracker in `SessionIdTracker`, which adds a secret random
  offset (Python `secrets`, 1e6..1e9) drawn once per run. IDs used to be 1, 2, 3, ... every run, so "track 17"
  meant the 17th person of every day; CLAUDE.md requires session-random IDs. Within a run IDs stay unique and
  stable, and are always positive (the beam fallback uses -1).
- `tests/test_track_ids.py` (3 tests). Full suite: 477 passed.
- RESULTS.md regenerated: no measured number changed (only timestamp, commit id and ~1% laptop-speed noise).
- docs/PRIVACY_DPDP.md updated to describe the new behaviour.
## 2026-09-25 - Contract fixes for the M11 findings (branch `a/fix-contract`, stacked on `a/track-ids`)

- `core/config.py`:
  - `cameras[].infer_size` is now `int | None = None` (null = `detector.imgsz`);
  - `forecast.horizon_min` removed; a `before` validator drops it from old configs with a warning;
  - `shelf.method` detector/hybrid require `shelf.detector_model`.
- `pipeline.py`:
  - one detector per distinct `infer_size`, shared; a fixed-shape model with another input size → SystemExit;
  - an injected detector is never replaced;
  - builds the product detector for `shelf.method != reference` and passes it to every ShelfEngine.
- `analytics/shelf.py`:
  - `hybrid` implemented (`combine_fills`: mean fill, confidence capped by agreement);
  - detector/hybrid without a detector → ValueError;
  - the docstring no longer cites a training notebook that does not exist.
- `run.py`: `--backend` choices come from `DetectorConfig`, so the QNN backends are offered.
- `tests/test_config_semantics.py` (10 tests). Full suite: 487 passed. The demo replay summary is identical to
  before (defaults unchanged), so RESULTS.md is not regenerated. docs/CONFIG_REFERENCE.md updated.
## 2026-09-25 - Technical document + PDF (branch `a/tech-doc`)

- **Found:** #23 and #24 were stacked PRs that merged into their base branches (`a/m11-docs`, `a/track-ids`) after
  #22 had merged into master, so neither reached master. Opened #25 (`a/track-ids` → master; merges cleanly, no new
  code). Lesson: retarget a stacked PR to master before merging it once its base has merged.
- docs/TECHNICAL_DOCUMENT.md (13 chapters, as requested):
  - every software module and every hardware part, each with a status label ("working and tested", "built, not yet
    connected", "in progress", "designed, not yet built");
  - details checked against the code on this branch, docs/INTERFACES.md, docs/PROTOCOL.md, research/23, 24, 26 and
    the unmerged hardware branch `origin/b/m6-mems` (Ram's firmware, bridge, wiring; described as in progress);
  - every measured number is taken from RESULTS.md, with its data label;
  - corrections found while checking: dashboard behaviour (3 s refresh plus WebSocket push); DB queue drops are not
    counted (listed as open); the Hindi/Telugu voice clips do not exist yet; the ESP32 camera and the motion gate are
    designed only.
- tools/build_docs_pdf.py:
  - markdown-it → HTML → Mermaid (jsDelivr) → Edge via Playwright (`channel="msedge"`) → A4 PDF with footer page
    numbers;
  - the table of contents gets its page numbers by reading the PDF bookmarks with pypdf and printing again;
  - the build fails if any Mermaid diagram fails to parse.
- Checked every page: pages rendered to PNG with pypdfium2 (the Read tool needs poppler, which is not installed).
  - Fixed tiny left-to-right diagrams (now top-down, natural size, shrink-only).
  - Fixed a table cell broken by `|` inside code.
  - Turned the reliability diagram into Table 4.1.
  - Final: 53 pages, all diagrams and tables readable.
- Dev dependencies added to the venv only (not requirements.txt): playwright, pypdf, pypdfium2.
- Tests: 487 passed; ruff clean on the new script.
