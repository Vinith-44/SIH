# Architecture

**Owner:** A (shared) · **Filled in:** M0 / M11 · **Status:** stub (created in PR-0).

## What goes here

Pipeline diagram (research/23 §2, updated): ingest → motion gate → detect → track → analytics → fusion → bus → SQLite → API/dashboard; where the STM32 node and the serial bridge plug in; process layout on the Pi 5 (one systemd unit per process).

## Read first

- research/23_PIPELINE_DEEP_RESEARCH.md §2
- research/04_ARCHITECTURE.md
- INTERFACES.md
