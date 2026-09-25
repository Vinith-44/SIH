# Privacy and DPDP

**Owner:** A + B · **Rules:** CLAUDE.md "Privacy rules" · **Research:** research/24 §8

StoreMind counts and times people. It does not recognise anyone. This page says exactly what is and is not kept,
checked against the code on master. It is also a pitch point, so every claim here must stay true: if you change
what is stored, change this page in the same PR.

> This is an engineering description, not legal advice. India's Digital Personal Data Protection Act, 2023 applies
> to digital personal data; before a real deployment the store owner should confirm the notice and consent wording
> with someone qualified.

## 1. What never happens

| never | how the code guarantees it |
|---|---|
| video or frames written to disk by the pipeline | frames live in RAM only; nothing in `storemind/storemind/` opens a `VideoWriter`. The health event's `video_bytes_stored` counter is 0 by construction (`health/monitor.py`) |
| face recognition | no face model anywhere in the code |
| re-identification across cameras or days | the trackers use motion only; BoT-SORT runs **without** its ReID model (`tracking/tracker.py`) |
| identifying staff | the staff filter only answers "staff or not" from a staff-zone dwell or a printed ArUco badge; it emits no badge id (`analytics/staff.py`) |
| audio | no microphone input in the pipeline |
| data leaving the box | no cloud call in the pipeline; the "Ask your store" LLM runs locally through Ollama on localhost (docs/ASK.md). The database has a `sync_outbox` table for an optional head-office sync, but nothing on master sends it; if one is added, update this page |

## 2. What is kept

| kept | where | how long | contains a person's image? |
|---|---|---|---|
| events (ENTRY/EXIT, ZONE_VISIT, QUEUE_STATE, SLOT_STATE, PICKUP, ALERT, ...) | SQLite `events` table | `storage.retention_days` (default 30), then deleted | no: counts, times, zone names and track numbers |
| per-minute totals | SQLite `agg_minute` | kept (no individual rows) | no |
| track numbers | inside events | as the event | no. A track number is ByteTrack's counter: it restarts every run and is linked to nothing. (CLAUDE.md asks for *random* per-session ids; today they are sequential. Randomising them is a small follow-up, see the handoff.) |
| one calibration snapshot per camera | path in `cameras[].reference_frame` (`tools/calibrate.py`) | until recalibration | **possibly**: take it when nobody is in view |
| shelf reference crops | `cameras[].shelves[].reference_dir` | until the next restock | shelf slots only; taken only when no person box overlaps the shelf |
| floor heatmap image | written on request | overwritten | no: an aggregate colour map |
| debug preview window (`--show`) | screen only, never saved | - | people are **blurred** before anything is drawn (`overlay.py`) |

## 3. Tools that do write images (opt-in, never part of the running system)

- `tools/make_overlay_clip.py`: demo/PPT clips. Every person is blurred before drawing. It is used on the public
  CAVIAR dataset.
- `tools/shelf_capture.py`: shelf photos for the bucket-B shelf dataset. Point it at shelves only, with the owner's
  permission.
- `tools/make_synthetic_video.py`, `tools/shelf_synth.py`: synthetic images with no people.
- `tools/caviar_preview.py`: previews of the public CAVIAR dataset.

None of these files may be committed: `.gitignore` excludes videos and snapshots (CLAUDE.md).

## 4. Deployment rules (with Ram's install; research/24 §8)

Ram fills in the concrete steps for these in docs/SETUP_PI5.md and docs/CCTV_ONBOARDING.md.

- **Permission letter** (one page, signed by the owner):
  - read-only live-view access to named cameras, for anonymous analytics only;
  - no recording and no internet upload; data stays on the device;
  - can be withdrawn at any time; names a contact person.
- **Notice at the entrance** in English, Telugu and Hindi: *"Anonymous footfall and queue analytics in use. No
  faces, no recordings."*
- **DVR access:**
  - a separate read-only DVR account with a strong unique password, stored in `configs/secrets.yaml` (git-ignored)
    or environment variables, never in a config file;
  - sub-stream only;
  - **never change the DVR's recording settings**.
- **Network:**
  - no port forwarding; a private cable or VLAN to the DVR;
  - the Pi's firewall allows only the dashboard port on the shop LAN.
- **After a pilot:** rotate the DVR password and delete the database if the owner asks.

## 5. Answers for judges

- *"Do you store video?"* No. Frames are processed in memory and dropped. The dashboard shows a live counter of
  video bytes stored, and it is 0.
- *"Can you track a person across days?"* No. There is no appearance model and track numbers restart every run.
- *"What about staff?"* They are excluded from customer counts by zone or by a printed badge, never identified.
- *"Does the AI send data anywhere?"* No. The language model runs on the box, and it only writes a database query.
- *"How long do you keep data?"* 30 days of events by default (configurable); after that only per-minute totals.
