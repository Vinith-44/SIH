"""Render CAVIAR frames with ground-truth boxes and a candidate counting line.

Placing a counting line by guessing is how you end up measuring your own
guess. This overlays the ground-truth trajectories so the line can be put where
people actually walk, and prints the resulting ground-truth crossing counts at
several jitter margins so the choice is defensible.

    python tools/caviar_preview.py --scenario OneStopEnter1 --view front
    python tools/caviar_preview.py --scenario OneStopEnter1 --view front \\
        --line 0.0 0.62 1.0 0.62 --out ../ppt_assets/caviar_line_front.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# Run straight from tools/ without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storemind.eval.caviar import (
    CAVIAR_FPS,
    SCENARIOS,
    crossing_sensitivity,
    gt_crossings,
    parse_cvml,
    video_for,
)

CAVIAR_DIR = Path(__file__).resolve().parents[2] / "videos" / "entrance" / "caviar"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="OneStopEnter1", choices=list(SCENARIOS))
    parser.add_argument("--view", default="front", choices=["front", "corridor"])
    parser.add_argument("--line", nargs=4, type=float, default=None,
                        metavar=("AX", "AY", "BX", "BY"),
                        help="normalised counting line, e.g. 0.0 0.62 1.0 0.62")
    parser.add_argument("--entry-direction", default="pos", choices=["pos", "neg"])
    parser.add_argument("--frame", type=int, default=None, help="which frame to render")
    parser.add_argument("--out", default=None)
    parser.add_argument("--dir", default=str(CAVIAR_DIR))
    args = parser.parse_args()

    root = Path(args.dir)
    corridor_xml, front_xml = SCENARIOS[args.scenario]
    xml = root / (front_xml if args.view == "front" else corridor_xml)
    clip = parse_cvml(xml)
    video = video_for(xml, args.scenario, args.view)

    print(f"{clip.name}: {clip.frames} frames ({clip.duration_s:.1f}s), "
          f"{len(clip.by_track())} annotated people, {len(clip.boxes)} boxes")
    for label in ("shop enter", "shop exit"):
        episodes = clip.context_episodes(label)
        if episodes:
            times = ", ".join(f"{start:.1f}s" for _t, start, _e in episodes)
            print(f"  '{label}' episodes: {len(episodes)}  at {times}")

    # Read the whole clip: MPEG-1 has no index, so seeking is unreliable.
    cap = cv2.VideoCapture(str(video))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    print(f"  video: {video.name}, {len(frames)} frames decoded")
    if not frames:
        raise SystemExit(f"could not decode {video}")

    height, width = frames[0].shape[:2]
    by_frame = clip.by_frame()

    if args.line:
        ax, ay, bx, by = args.line
        line_a = (ax * width, ay * height)
        line_b = (bx * width, by * height)
        crossings = gt_crossings(clip, line_a, line_b, args.entry_direction)
        entries = sum(1 for _, _, d in crossings if d == "in")
        exits = sum(1 for _, _, d in crossings if d == "out")
        print(f"\n  line ({ax}, {ay}) -> ({bx}, {by}), entry_direction={args.entry_direction}")
        print(f"  ground-truth crossings: IN {entries}  OUT {exits}")
        print("  sensitivity to the jitter margin:")
        for margin, counts in crossing_sensitivity(clip, line_a, line_b,
                                                   args.entry_direction).items():
            print(f"    margin {margin:5.1f} px -> IN {counts['in']:3d}  OUT {counts['out']:3d}")
    else:
        line_a = line_b = None

    # Pick a busy frame to look at unless one was asked for.
    index = args.frame
    if index is None:
        index = max(range(len(frames)), key=lambda f: len(by_frame.get(f, [])))
    canvas = frames[min(index, len(frames) - 1)].copy()
    canvas = cv2.resize(canvas, (width * 2, height * 2), interpolation=cv2.INTER_NEAREST)
    scale = 2

    # Every trajectory, faintly, so the walking pattern is visible at a glance.
    for track, series in clip.by_track().items():
        colour = ((37 * track) % 255, (91 * track + 60) % 255, (151 * track + 120) % 255)
        points = np.array([[int(b.foot[0] * scale), int(b.foot[1] * scale)] for b in series],
                          dtype=np.int32)
        if len(points) > 1:
            cv2.polylines(canvas, [points], False, colour, 1, cv2.LINE_AA)

    for box in by_frame.get(index, []):
        x1, y1, x2, y2 = (int(v * scale) for v in box.xyxy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (90, 230, 120), 2)
        cv2.circle(canvas, (int(box.foot[0] * scale), int(box.foot[1] * scale)), 4,
                   (70, 70, 240), -1)
        cv2.putText(canvas, f"{box.track} {box.context}", (x1, max(12, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 230, 120), 1, cv2.LINE_AA)

    if line_a is not None:
        cv2.line(canvas, (int(line_a[0] * scale), int(line_a[1] * scale)),
                 (int(line_b[0] * scale), int(line_b[1] * scale)), (70, 70, 240), 2)

    cv2.putText(canvas, f"{clip.name}  frame {index}  t={index / CAVIAR_FPS:.1f}s",
                (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (250, 250, 250), 1, cv2.LINE_AA)

    out = Path(args.out) if args.out else Path(f"caviar_{args.scenario}_{args.view}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
