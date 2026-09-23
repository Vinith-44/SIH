# CLAUDE.md — StoreMind (SIH 2026 · PS 26179 · Qualcomm) — standing rules for Claude Code

This file is read automatically every session. Follow it exactly.

## Project in one line
StoreMind = offline edge-AI retail platform: shopper analytics + shelf/inventory monitoring + queue intelligence, running on Raspberry Pi 5 now (Qualcomm Dragonwing QCS6490 later), with an STM32 FreeRTOS sensor node (HX711 weight, ToF, IR beam, tower light). Team TechGladiators, BVRIT. We are through the college internal round → competition is intense, quality and honesty matter.

## Read before doing anything
1. `research/00_START_HERE.md` then all of `research/01…09` and `research/RESEARCH_LOG.md`.
2. `Problem Statement SIH26179​.txt` (filename contains a zero-width space — use a glob like `Problem*SIH26179*.txt`).
3. Old prototype code is inside the extension-less ZIP files `Queue Management`, `Shelf monitoring`, `Shopper analytics` — extract to `legacy/` (never modify the originals).

## Folder rules
- NEVER modify or delete: `sihfinal.pptx`, the three ZIPs, the PDF, the problem-statement txt, `the solution.txt`, anything in `research/`, anything in `videos/`.
- New code lives in `storemind/` (git repo). Legacy extracted code in `legacy/`. Generated results in `storemind/eval/results/`.
- New research write-ups go in `research/10_*.md`, `research/11_*.md`, … (do not edit 00–09; add corrections in a new file).
- Keep `WORK_LOG.md` (append-only, dated entries) and `HANDOFF_FOR_CLAUDE.md` (overwrite each session: status summary for the team's other Claude chat) in `C:\SIH`.

## Engineering rules
- Python 3.11/3.12, venv at `storemind/.venv`. Must run on Windows laptop now AND Raspberry Pi 5 (Linux, headless) later: no Windows-only APIs in core code; `--headless` flag everywhere; paths via `pathlib`.
- Every engine must run in **replay mode** on a video file exactly like on a live camera (`--source path.mp4` or `rtsp://…` or `0` or `csi:0`).
- Modules talk only through the event schema in `research/04_ARCHITECTURE.md` §4 (pydantic models). MQTT is optional at first: provide an in-process bus with the same interface, MQTT (paho) as a drop-in.
- Detection backend is swappable (`ultralytics` / LiteRT TFLite / ONNXRuntime; later NCNN, Hailo, QNN) behind one interface.
- Tests with `pytest` for all pure logic (line crossing, queue timers, Erlang-C, shelf state machine, event schema). Run them before every commit.
- Commit after every milestone with a clear message.

## Privacy rules (non-negotiable, also a pitch point)
- Never write frames/video/faces to disk except: one calibration snapshot per camera and opt-in debug clips that are blurred. Never commit videos or snapshots to git (`.gitignore` them).
- No face recognition, no appearance re-ID, session-random track IDs.

## Honesty rules
- Never invent accuracy/FPS numbers. Every number in docs/PPT must come from a script in `storemind/eval/` with the command recorded in `WORK_LOG.md`.
- If there is no ground truth yet, say "not measured yet".
- Cite sources (URLs) for every external fact in research files. If you can't verify something, say so.
- If something is blocked (missing video, missing hardware, download fails), log it in `HANDOFF_FOR_CLAUDE.md` under "Blocked / needs team" and move on to the next task.
