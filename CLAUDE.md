# CLAUDE.md — StoreMind (SIH 2026 · PS 26179 · Qualcomm) — standing rules for Claude Code

This file is read automatically every session. Follow it exactly.

## Project in one line
StoreMind = offline edge-AI retail platform: shopper analytics + shelf/inventory monitoring + queue intelligence, running on Raspberry Pi 5 now (Qualcomm Dragonwing QCS6490 later), with an STM32 Blue Pill FreeRTOS sensor node (HX711 load cell, MEMS accelerometer/IMU — NOT ToF, IR break-beam, PIR, BH1750 light, BME280 environment, buzzer + LED, optional servo; LD2450 radar optional/disabled; NO microphone). Team TechGladiators, BVRIT. We are through the college internal round → competition is intense, quality and honesty matter.

## CURRENT PLAN (24 Sep 2026) — read these first, they override older research where they differ
- `CLAUDE_CODE_PROMPT_V2.md` = the build plan (milestones M0–M11, ground rules).
- `research/26_TEAM_SPLIT_AND_INTEGRATION.md` = who owns what + the interface contract (event schema v2, MQTT topics, serial protocol).
- `research/23_PIPELINE_DEEP_RESEARCH.md` and `research/24_CCTV_INTEGRATION.md` = the research behind it.

## Two people work in parallel (two laptops, two Claude Codes, one GitHub repo)
- **Person A = Vinith** (GitHub `Vinith-44`, RTX 4050): vision & intelligence. **Person B = Ram** (CPU laptop): platform & hardware integration. Ownership table: research/26 §1. The user tells you which person you are at session start; if not, ask.
- **Use the names.** Say "Vinith" and "Ram" (not "your friend" or "the teammate") in chat, PR descriptions, handoffs, logs and docs. Older files that say "friend" mean Ram.
- Only edit paths your person owns. Shared contract files (`core/events.py`, `core/bus.py`, `core/config.py`, config schema, `pipeline.py`, `run.py`, `requirements.txt`, `CLAUDE.md`, `docs/INTERFACES.md`, `docs/PROTOCOL.md`) change only in a small separate PR approved by the other person.
- Never work on `master`. Branch per milestone (`a/...` or `b/...`), PR, the other person approves. Start every session with `git switch master && git pull`, then merge master into your branch.
- Logs are per person: `logs/WORK_LOG_A.md` / `logs/WORK_LOG_B.md` (append-only) and `handoff/HANDOFF_A.md` / `handoff/HANDOFF_B.md` (overwrite each session). The root `WORK_LOG.md` and `HANDOFF_FOR_CLAUDE.md` are frozen history/index files.
- `RESULTS.md` is regenerated only by Person A; Person B writes measured platform results to `storemind/storemind/eval/results/platform/`.
- Secrets never go in git: `configs/secrets.yaml`, `.env`, the Qualcomm AI Hub token (read via `qai-hub configure` / env `QAI_HUB_API_TOKEN`).

## Read before doing anything
1. `research/00_START_HERE.md` then all of `research/01…09` and `research/RESEARCH_LOG.md`.
2. `Problem Statement SIH26179​.txt` (filename contains a zero-width space — use a glob like `Problem*SIH26179*.txt`).
3. Old prototype code is inside the extension-less ZIP files `Queue Management`, `Shelf monitoring`, `Shopper analytics` — extract to `legacy/` (never modify the originals).

## Folder rules
- NEVER modify or delete: `sihfinal.pptx`, the three ZIPs, the PDF, the problem-statement txt, `the solution.txt`, anything in `research/`, anything in `videos/`.
- New code lives in `storemind/` (git repo). Legacy extracted code in `legacy/`. Generated results in `storemind/eval/results/`.
- New research write-ups go in `research/10_*.md`, `research/11_*.md`, … (do not edit 00–09; add corrections in a new file).
- Keep your person's log and handoff (see "Two people" above); do not edit the other person's files.

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
- Never invent accuracy/FPS numbers. Every number in docs/PPT must come from a script in `storemind/eval/` with the command recorded in your `logs/WORK_LOG_<A|B>.md`.
- If there is no ground truth yet, say "not measured yet".
- Cite sources (URLs) for every external fact in research files. If you can't verify something, say so.
- If something is blocked (missing video, missing hardware, download fails), log it in your `handoff/HANDOFF_<A|B>.md` under "Blocked / needs team" (hardware steps also in `docs/HARDWARE_TODO.md`) and move on to the next task.
