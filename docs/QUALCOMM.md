# Qualcomm path

**Owner:** A · **Filled in:** M9 · **Status:** stub (created in PR-0).

## What goes here

AI Hub compile/profile on the hosted QCS6490 proxy (bucket Q), LiteRT-QNN and ORT-QNN backends, RUBIK Pi 3 port guide, Pi 5 vs QCS6490 table, licence note.

## Read first

- research/23 §5
- CLAUDE_CODE_PROMPT_V2.md M9

## Candidates recorded so far

- **YOLO11s@640 (Qualcomm NPU option, from M1).** In the CAVIAR bake-off it gave the best
  corridor IDF1 (0.80 vs 0.75 for YOLO11n). On a CPU it is too slow for the Pi 5 entrance budget:
  3.75 FPS on the dev laptop's CPU (266 ms/frame, 21.6 GFLOPs), against 10.0 FPS for YOLO11n.
  M9 profiles it on the AI Hub QCS6490 proxy to see whether the NPU makes it viable (bucket Q).
- **YOLO26n@640.** Same size as YOLO11n (5.5 GFLOPs, 10.8 FPS on the same CPU). Front-view IDF1
  was 0.62–0.65 against 0.47. It is a Pi candidate once M8 measures it on the Pi.
