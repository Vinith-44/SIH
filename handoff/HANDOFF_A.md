# HANDOFF — Person A (Vinith) — 2026-09-24 (night)

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (Ram approves)
- **#19 M9** (`a/m9-aihub`): AI Hub tool + results, QNN backends, docs/QUALCOMM.md, deploy/qualcomm/.
  Master (with M8) was merged into it and the run_all.py conflict resolved (both sets of sections kept).
- Merged: PR-0, M1, M3, M4, M6, M8 and their contracts (#1-#5, #7, #9-#16), plus Ram's M2 (#6, #8, #17, #18).

## Where each milestone stands (details in docs/)
| Milestone | State | Honest headline |
|---|---|---|
| M1 counting (COUNTING.md) | merged | held-out CAVIAR exit accuracy 76% — **target 90% not met**; IR beam = per-site calibration |
| M3 shelf (SHELF.md) | merged | synthetic EMPTY F1 0.92 (v1 0.74); **real shelf not measured** |
| M4 queue (QUEUE.md) | merged | passers-by fixed (joins 825 vs 833 true); per-person wait 33% under ID switches; **canteen clip not recorded** |
| M6 fusion (MEMS.md) | merged | simulated picks F1 0.98, units 99.7%; **no hardware yet** |
| M8 models (MODELS.md) | merged | FP32 exports lossless on CAVIAR; INT8 moves 2-4 crossings; **Pi speed not measured** |
| M9 Qualcomm (QUALCOMM.md) | PR #19 | AI Hub hosted RB3 Gen 2 (QCS6490), bucket Q: YOLO11n INT8 12.8 ms, 100% NPU; YOLO11s INT8 11.0 ms; FP32 ~140-240 ms |

## M9 notes
- INT8 needs the boxes/scores head split (the first AI Hub run detected nothing; fixed; run 1 is kept as evidence).
- `models/yolo11n_aihub_w8a8.tflite` also runs on any CPU with `backend: litert`: this resolves M8's LiteRT INT8
  item without WSL.
- Run the tool with `PYTHONIOENCODING=utf-8` on Windows (qai-hub prints emoji).

## Next: M10 (stretch) "Ask your store" + daily summary
Local small LLM -> read-only SQL over whitelisted views -> every answer cites rows; 20 test questions, 0 invented
numbers. Then M11 (final docs + demo pack, shared with Ram).

## Blocked / needs the team
- Ram's approval of #19 (M9).
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, 30 walked door crossings with the
  IR beam, and the M6 shelf test on Ram's board (docs/HARDWARE_TODO.md).
- Pi 5 run of `tools/bench_pi.py` (docs/HARDWARE_TODO.md "M8").
- Optional: a real QCS6490 board, to move the Q numbers to S (deploy/qualcomm/README.md checklist).
