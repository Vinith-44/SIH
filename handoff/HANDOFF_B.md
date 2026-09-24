# HANDOFF — Person B (platform & hardware) — not started yet

Overwritten every session by Person B. Seeded by Person A in PR-0 with the starting point.

## Start here
1. Read `CLAUDE.md`, `research/26_TEAM_SPLIT_AND_INTEGRATION.md`, `CLAUDE_CODE_PROMPT_V2.md`.
2. Review and approve PR-0 (`a/pr0-contracts`): check that `docs/INTERFACES.md` and
   `docs/PROTOCOL.md` are what you want to build the bridge, simulator, ingest and dashboard
   against. Changing them later needs a contract PR.
3. Setup (research/26 §2): clone, `storemind/.venv` with **CPU-only** torch, 
   `pip install -r storemind/requirements.txt`, test videos, ffmpeg, go2rtc, MediaMTX.
   `cd storemind; .venv/Scripts/python -m pytest tests -q` should pass (212 at PR-0).

## What PR-0 already gives you
- `storemind/storemind/sensors/protocol.py`: framing, checksum, typed decode, `LineAssembler`,
  `SeqTracker` — extend it; `tests/test_protocol.py` keeps it in sync with PROTOCOL.md.
- `core/bus.py`: `MqttBus(listen=True)`, Last Will, `command_topic()`, QoS/retain table.
- `core/config.py`: `sensors.node.enabled_sensors`, `sensors.mems_nodes`, `config.sensors.has(...)`,
  `load_secrets()`.
- Platform results → `storemind/storemind/eval/results/platform/` (format in INTERFACES.md §5).

## Your milestones
M2 ingest v2 · M5 firmware + bridge + simulator + HIL · M6-firmware (MEMS) · M7 dashboard/ops ·
M8-deploy (Pi 5) · CI · onboarding docs. PROTOCOL.md §6 (binary mode struct layout) is yours to finalise.
