# HANDOFF — Person B (Ram) — 2026-09-25

Overwritten every session. Read `CLAUDE.md` first, then this. Work log: `logs/WORK_LOG_B.md`.

## Open PRs (Vinith reviews; nobody merges with the admin bypass)

A stack: each PR's base is the one before it, so each shows only its own diff. Merge **in this order**
(GitHub retargets the next PR to `master` when its base branch is merged and deleted; if the branch is kept,
edit the next PR's base to `master` by hand). Every branch already contains today's master (#22).

| # | Branch | What |
|---|---|---|
| 1 | `b/m5a-protocol` | portable C protocol library + host tests; golden vectors from PROTOCOL.md; CI host-C job; CI runs on stacked PRs |
| 2 | `b/m5b-bridge` | serial bridge + STM32 simulator + sd_notify helper + sensor e2e in CI |
| 2a | `b/m5b-run-hook` | **contract (run.py)**: `--sensors` starts the bridge in-process; systemd READY/WATCHDOG. Needs Vinith's approval as a contract change |
| 3 | `b/m5c-firmware` | STM32F103 firmware: FreeRTOS CMSIS-RTOS2, the 8 tasks, CMake, CI build with a flash/RAM budget |
| 4 | `b/m5d-i2c-hil` | I2C1 mutex + bus recovery, `tools/hil_test.py` |
| 5 | `b/m6-mems` | MEMS state machine + task, `$M`, MEMS.md §1-3, `tools/mems_test.py` |
| 6 | `b/m7a-dashboard` | platform panels, alert → LED/buzzer policy, Pi power / mJ per frame |
| 7 | `b/m7b-ops` | systemd units, `install_pi5.sh`, DB maintenance, soak + chaos scripts and laptop results |
| 8 | `b/m8-pi5` | Pi 5 UART / udev / chrony / RTC scripts, `pi5_check.sh`, SETUP_PI5.md |
| 9 | `b/m9-m10-platform` | Ram's part of M9/M10: `/api/ask`, `/api/summary`, Ask + summary panels, accelerator row, `--with-llm`, `ask_latency.py` |

## Where each milestone stands

| Milestone | State | Honest headline |
|---|---|---|
| M5a protocol (C) | PR | 989 host checks pass (gcc, clang, ASan/UBSan in CI); 4.8 KB flash |
| M5b bridge + simulator | PR | 1 simulated hour: 0 framing / 0 checksum / 0 lost; a simulated pick → Vinith's engine → 1 PICKUP units=2 |
| M5c firmware | PR | builds warning-free: 31.5 KB flash (49 %), 12.7 KB RAM (62 %) after M6; **never run on a board** |
| M5d I2C + HIL | PR | recovery host-tested with a fake bus; HIL tool: 60 min vs simulator, 238,525 lines, 0 errors (bucket C) |
| M6 MEMS | PR | state machine tested on synthetic signals (0 false events in 10 idle min); **real shelf not measured** |
| M7a dashboard | PR | checked live in a browser on fake data; SHELF_TILT → node LED + buzzer ALERT |
| M7b ops | PR | laptop soak (1 h, stub detector) + chaos results in `eval/results/platform/` (see work log); **Pi not run** |
| M8 Pi 5 deploy | PR | scripts dry-run + unit-tested; Pi 5 UART facts checked in the official docs; **no Pi run** |
| M9 / M10 (Ram's part) | PR | Ask API + panel tested (rules backend); **Pi latency not measured** |

## For Vinith
- **Contract PR 2a** (`run.py`): please review; the systemd units (M7b) need its READY/WATCHDOG, and the laptop
  single-process demo needs `--sensors`. It does not touch the lines your open PRs #25/#26 change in `run.py`.
- PROTOCOL.md §5 says seq gaps go to a "NODE_HEALTH lost-line counter", but `NodeHealthData` has no such field.
  The bridge adds lost lines to `uart_err` (the "rest" bucket) and shows `lost` separately on the dashboard. If
  you want a `lost_lines` field, that is a small additive contract PR.
- PROTOCOL.md §6 (binary mode struct layout) is still open: firmware and simulator answer `$C,MODE,BIN` with
  `ERR 3` until it is written. CRC-16 + COBS are done and tested on both sides.
- `ingest.wiring.IngestManager` (M2) is not started by `run.py`, so on the Pi CAMERA_HEALTH comes only from the
  pipeline's own FPS/tamper view. Wiring it in is a `run.py`/`pipeline.py` change: agree first.
- ASK.md §6 optional `llm:` config section: not done; the dashboard uses `STOREMIND_LLM_MODEL` / `STOREMIND_LLM_HOST`.
- The simulator's `$M` lines follow the MEMS.md §5 assumptions (5 % missed TOUCH etc.), same as your sensor_sim.

## Next (Ram)
1. Ask the hardware team for the Blue Pill + sensors + the Pi 5, then HARDWARE_TODO.md in order: "M5" (flash, HIL),
   "M8-deploy" (install, 24 h soak), "M6" (MEMS shelf), "M10" step 5 (Ask latency), "M4" / "M1" with Vinith.
2. M11 (shared with Vinith): the platform half of the demo pack.

## Blocked / needs the team
- No STM32 board, no sensors, no Pi 5 on this laptop: every hardware number above is "not measured".
- No model weights on this laptop: the laptop soak used the `stub` detector (platform soak only).
- No MQTT broker on this laptop: the "kill MQTT" chaos case runs on the Pi.
