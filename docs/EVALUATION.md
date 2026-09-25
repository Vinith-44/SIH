# Evaluation

**Owner:** Vinith (Person A) · **Results:** `storemind/storemind/eval/results/RESULTS.md` (generated, never
hand-edited) · **Scripts:** `storemind/storemind/eval/`, `storemind/tools/bench_pi.py`, `storemind/tools/aihub_profile.py`

How every StoreMind number is produced, what it may be used for, and what we did to stop ourselves fooling
ourselves. Read this before putting any number on a slide.

## 1. The one rule: every number says what data it came from

| bucket | data | may be used for | may **not** be used for |
|---|---|---|---|
| **A** | public benchmark with published ground truth (CAVIAR) | real accuracy claims, with the caveat that it is not our store | "works in a kirana" |
| **B** | our own recordings, hand-labelled by the team | real accuracy claims | - (bucket B is **empty today**) |
| **C** | simulation with known ground truth | showing the logic is correct | any accuracy claim |
| **S** | real footage or a real device, no ground truth | speed, power, temperature | accuracy |
| **Q** | Qualcomm AI Hub hosted / proxy device | Qualcomm latency and NPU use | "our board" |
| **P** | published third-party figure, cited | context | our measurement |

Source: `research/09b_TEST_DATA_VALIDITY.md`. RESULTS.md prints the bucket on every section: a `Section` in
`eval/common.py` cannot be created without one.

## 2. How we keep the test set honest

1. **Tune on one part, report on another, and say which.** Every evaluation that has settings to choose splits
   its data before any setting is chosen (section 3). Settings are chosen with `--grid` / `--tune` on the tuning
   part only.
2. **If we look at the test part and then change something, we say so, and we keep both results.** The
   corrections so far are listed in section 4. None were hidden.
3. **The expected answers are computed independently of the system.** Simulators write their own ground truth
   while generating the data; CAVIAR crossings come from the published trajectories; M10's answers are computed
   in Python, not with SQL.
4. **A failed target stays failed.** RESULTS.md prints "Did not meet target" in the section itself (M1 exits
   76.2% against 90%).
5. **Every number comes from a command printed beside it.** RESULTS.md has a `commands` block per section, and
   the work logs record the exact command lines.

## 3. What each evaluation measures

| evaluation | bucket | what it measures | protocol | script → results |
|---|---|---|---|---|
| Counting on CAVIAR | A | entries/exits on 16 real clips (8 scenarios × 2 views, 111 people) | v1 config fixed in advance; lines derived from trajectories and recorded in `configs/caviar.yaml` | `eval_caviar` → RESULTS |
| Counting v2 + tracker bake-off | A | detector × tracker × counter settings | **2-fold cross-validation over the two camera views**: chosen on one view, scored on the other; run 1 (a different rule) also published | `detcache`, `bakeoff` → `tracker_bakeoff*.json/md` |
| Synthetic entrance / queue / shelf videos | C | the whole pipeline on generated clips with exact truth | no tuning | `run` with `--backend scripted` |
| Queue v1 vs v2 | C | passers-by, parties, balks, reneges, bent lanes, ID switches | seeds 1-10 tune, **seeds 11-40 report** | `eval_queue_v2` → `queue_v2*.json` |
| Shelf under changing light | C | EMPTY/LOW/FULL/UNKNOWN through lighting changes | seeds 1-10 tune, **11-40 report** | `eval_shelf_lighting` → `shelf_lighting*.json` |
| Pick / put-back fusion | C | MEMS touch + load cell → picks, units, put-backs, shrink | seeds 1-10 tune, **11-40 report** | `eval_fusion` → `fusion*.json` |
| Ask your store | C | answers from the event DB; invented numbers | three question sets written in turn; **set C's first run** is the held-out result | `eval_ask` → `ask*.json` |
| Door-to-counter forecast | C | "open a counter" lead time on a simulated rush | no tuning on the reported run | `eval_forecast` |
| Before / after | C | v1 counting logic vs the legacy code on identical detections | - | `legacy_baseline` |
| Detector speed | S | laptop CPU ms/frame on real pedestrian video | forced to CPU (`STOREMIND_DEVICE=cpu`) and labelled | `benchmark` |
| Exported detectors | S + A | fidelity of ONNX/INT8/NCNN to FP32; CAVIAR counting per format | same CAVIAR protocol as above | `eval_export` → `model_export.json` |
| Pi 5 benchmark | S | latency, FPS, °C, throttling, W and mJ/frame | **not run yet** (docs/HARDWARE_TODO.md "M8") | `tools/bench_pi.py` → `eval/results/pi/*.json` |
| Qualcomm AI Hub | Q | latency, NPU/GPU/CPU layer split, memory, device vs local detections | hosted RB3 Gen 2 (QCS6490) + QCS8550 proxy | `tools/aihub_profile.py` → `qualcomm_aihub*.json` |
| Real shelf photos | B | slot states on our own photos | **no photos yet** | `eval_shelf_photos` |
| Platform (Ram) | S | stream density, soak, hardware-in-the-loop, Pi install | Ram's results, included verbatim (INTERFACES.md §5) | `eval/results/platform/` |

## 4. Corrections and re-runs we disclosed

| milestone | what happened | where it is written up |
|---|---|---|
| M1 | Bake-off run 1 picked SORT on a 0.002 IDF1 margin (noise); the protocol became cross-validation over views *after* run 1 was seen. Both runs are published. | COUNTING.md §5, `tracker_bakeoff_run1.md` |
| M1 | An early speed table mixed GPU and CPU timings; the speed section is now forced to CPU and says so. | WORK_LOG_A.md |
| M1 / RESULTS | The bake-off row said "shipped configuration" for the all-clips pick (YOLO11s). The Pi ships YOLO11n@640; the row now says so (fixed in M11). | this page |
| M3 | A unit test found confidence computed from unclipped estimates. After the fix the test seeds were scored a second time. | SHELF.md |
| M4 | The "tail" truth was aligned to the engine on the tuning seeds. After a balk-timing fix the test seeds were scored again, with identical numbers. | QUEUE.md "Protocol notes" |
| M9 | AI Hub run 1: INT8 models detected nothing (one scale for boxes and scores). Fixed with a head split; run 1 is kept. | QUALCOMM.md §2, `qualcomm_aihub_run1_single_output.json` |
| M10 | Keyword rules overfit to set A; a unit check was added to the grader after run 4 and runs 1-3 re-graded (scores fell); the simulator's zone-visit data changed after run 1. | ASK.md §3 |

## 5. Regenerating RESULTS.md

```bash
cd storemind
.venv/Scripts/python -m storemind.eval.run_all                  # everything that has data
.venv/Scripts/python -m storemind.eval.run_all --skip caviar    # quick pass
.venv/Scripts/python -m storemind.eval.run_all --only ask       # one section
```

- `run_all` reruns the fast evaluations. For the slow ones it reads their saved JSON: bake-off, queue v2, shelf
  lighting, fusion, ask, exports, Qualcomm. Rerun those with the commands in their RESULTS section.
- CAVIAR and the speed sections need the clips in `../videos/` (not in git: see `videos/README.md`).
- A section with no data is skipped and says so. A section that crashes is printed as "Did not run" with the
  error; it is never dropped silently.
- Only Person A regenerates RESULTS.md. Ram's measured results go in `eval/results/platform/` and appear
  verbatim.

## 6. Not measured yet (say this out loud when asked)

- **Bucket B is empty.** We have no labelled recording from a real shop or canteen: no real-world number for
  queue wait, shelf state or doorway counting (docs/HARDWARE_TODO.md lists the recordings to make).
- **Raspberry Pi 5:** every speed figure is from the laptop; `tools/bench_pi.py` has not run on a Pi.
- **Qualcomm:** only AI Hub hosted devices (bucket Q); no board of our own.
- **Sensors:** the STM32 node and the fusion logic are tested against simulators only (bucket C).

## 7. Putting numbers on slides

- Copy the number *and* its bucket letter from RESULTS.md. Never put an A number and a C number in one chart
  without the letters.
- Bucket C numbers go on slides only as "the logic works on simulated data", never as accuracy.
- Put the M1 caveat beside the CAVIAR number: a Portuguese mall at 384×288 with a sparse crowd, not a kirana.
- Q and P numbers are someone else's hardware or someone else's measurement: label them as such.
