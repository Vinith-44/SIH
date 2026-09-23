# Test videos — how to record them (30–60 minutes total, whole team)

Put files here:
```
videos/entrance/   people walking in/out of a door (canteen, library, lab, shop)
videos/queue/      a queue at a counter (canteen billing, stationery shop, fee counter)
videos/shelf/      a shelf / rack with products; take items out, put back, stand in front
videos/other/      anything else (e.g. the old Kaggle poc_session_01.mp4 / 02.mp4)
```

## Rules
- Get permission from whoever runs the place; put up a simple notice ("Video recorded for a student AI project, deleted after testing, no faces stored").
- Phone on a **fixed** mount (tripod / taped to a wall / on a shelf). **Don't move it** during the clip.
- Landscape, 720p or 1080p, 25–30 FPS. 5–10 minutes per clip is enough.
- Entrance: phone high (2–3 m), looking down at 45°+, the full door width visible.
- Queue: from behind/side so the whole line + billing spot is visible.
- Shelf: face the shelf 1.5–3 m away; during the clip remove all items from one slot, put some back, put a *wrong* product in a slot, and stand in front of the shelf for ~20 s (tests the occlusion gate).
- Name files like `entrance_canteen_2026-09-24_1300.mp4`.

## Ground truth (makes our accuracy numbers real)
For each clip, one person watches and writes a small CSV next to it (templates will be created by Claude Code in `GROUND_TRUTH_TEMPLATES/`):
- entrance: total people IN and OUT (better: the video time of each crossing)
- queue: number of people waiting every 10 s; and for ~10 customers, when they joined the queue and when billing started
- shelf: the time each slot went empty / low / full / wrong-item

These videos stay on this laptop only — never upload them publicly or commit them to git.
