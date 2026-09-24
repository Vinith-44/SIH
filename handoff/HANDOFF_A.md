# HANDOFF — Person A (Vinith) — 2026-09-25

Overwritten every session. Read `CLAUDE.md` first, then this.

## Open PRs (Ram approves)
- **M10** (`a/m10-ask`): `llm/` package (Ask your store + trilingual daily summary), `eval/eval_ask.py`,
  docs/ASK.md, run_all section. No contract change.
- Merged: PR-0, M1, M3, M4, M6, M8, M9 and their contracts (#1-#5, #7, #9-#16, #19), plus Ram's M2 (#6, #8, #17, #18).

## Where each milestone stands (details in docs/)
| Milestone | State | Honest headline |
|---|---|---|
| M1 counting (COUNTING.md) | merged | held-out CAVIAR exit accuracy 76% — **target 90% not met**; IR beam = per-site calibration |
| M3 shelf (SHELF.md) | merged | synthetic EMPTY F1 0.92 (v1 0.74); **real shelf not measured** |
| M4 queue (QUEUE.md) | merged | passers-by fixed (joins 825 vs 833 true); per-person wait 33% under ID switches; **canteen clip not recorded** |
| M6 fusion (MEMS.md) | merged | simulated picks F1 0.98, units 99.7%; **no hardware yet** |
| M8 models (MODELS.md) | merged | FP32 exports lossless on CAVIAR; INT8 moves 2-4 crossings; **Pi speed not measured** |
| M9 Qualcomm (QUALCOMM.md) | merged | AI Hub hosted RB3 Gen 2 (QCS6490), bucket Q: YOLO11n INT8 12.8 ms, 100% NPU; YOLO11s INT8 11.0 ms |
| M10 ask (ASK.md) | PR | **0 invented numbers** in 60 questions × all backends; held-out accuracy 13/20 (7 wrong queries, cited); **Pi latency not measured** |

## M10 notes
- The model never supplies a number: it writes SQL (guarded by SQLite's authorizer on a read-only connection), and
  a sentence is only a `{column}` template the code fills in.
- Wrong answers are real numbers from the wrong query, so the dashboard must show `sql` + rows.
- The keyword rules overfit to the questions they were written for; they are only the fallback.
- The Telugu/Hindi summary templates need a native speaker's read before the demo (`TEMPLATES` in llm/summary.py).
- Ollama + `qwen2.5-coder:1.5b` (and `:3b`) are installed on this laptop.

## For Ram (docs/ASK.md section 6)
- Wire `GET /api/ask?q=` and `GET /api/summary?day=&lang=` in `api/` (Ram's path). Call `ask()` off the event loop
  (≈5 s on the laptop GPU; slower on the Pi).
- Pi: install Ollama, `ollama pull qwen2.5-coder:1.5b`, time one question.
- Optional contract PR: an `llm:` config section (`enabled`, `model`, `host`).

## Next: M11 (final docs + demo pack, shared with Ram)

## Blocked / needs the team
- Ram's approval of the M10 PR.
- Our own recordings (bucket B): shelf photos across a day, a canteen queue clip, 30 walked door crossings with the
  IR beam, and the M6 shelf test on Ram's board (docs/HARDWARE_TODO.md).
- Pi 5 run of `tools/bench_pi.py` (docs/HARDWARE_TODO.md "M8") and of one Ask question.
- Optional: a real QCS6490 board, to move the Q numbers to S (deploy/qualcomm/README.md checklist).
