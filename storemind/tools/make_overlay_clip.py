"""Render an annotated clip for the demo video and the deck.

Runs the real pipeline over a clip and writes an MP4 with the counting line,
zones, tracks and live counters drawn on top. Every person is **blurred** before
anything is drawn (`storemind.overlay.blur_people`), which is why this is the one
place allowed to write pixels to disk.

This is the asset that answers "show us it actually works" without needing a live
camera at the judging table.

    python tools/make_overlay_clip.py --config configs/caviar.yaml --camera corridor \\
        --source ../videos/entrance/caviar/OneStopEnter1cor.mpg \\
        --out ../ppt_assets/caviar_corridor_overlay.mp4

    python tools/make_overlay_clip.py --config configs/demo.yaml --camera entrance \\
        --backend scripted --out ../ppt_assets/entrance_overlay.mp4 --stills 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storemind.core.config import load_config          # noqa: E402
from storemind.overlay import draw                     # noqa: E402
from storemind.pipeline import Pipeline                # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/demo.yaml")
    parser.add_argument("--camera", required=True)
    parser.add_argument("--source", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--scale", type=float, default=2.0,
                        help="upscale factor for the output (CAVIAR is 384x288)")
    parser.add_argument("--max-seconds", type=float, default=None)
    parser.add_argument("--stills", type=int, default=2,
                        help="also save N still frames next to the clip")
    parser.add_argument("--no-blur", action="store_true",
                        help="only for synthetic clips, where there is nobody to protect")
    args = parser.parse_args()

    config = load_config(args.config)
    config.cameras = [c for c in config.cameras if c.name == args.camera]
    if not config.cameras:
        raise SystemExit(f"no camera named {args.camera!r} in {args.config}")
    if args.source:
        config.cameras[0].source = args.source
    if args.fps:
        config.cameras[0].fps = args.fps
    if args.backend:
        config.detector.backend = args.backend
        if args.backend == "scripted" and not args.model:
            config.detector.model = ""
    if args.model:
        config.detector.model = args.model
    config.alerts.console = False

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    writer: cv2.VideoWriter | None = None
    saved_stills = 0
    still_every = None

    from storemind.eval.common import _NullStore

    state = {"pipeline": None}

    def on_tracks(camera_name: str, frame, tracks) -> None:
        nonlocal writer, saved_stills, still_every
        pipeline = state["pipeline"]
        camera = next(c for c in pipeline.cameras if c.config.name == camera_name)

        hud = [f"{camera_name}   t={pipeline.clock.monotonic_s():6.1f}s   tracks {len(tracks)}"]
        if camera.footfall is not None:
            hud.append(f"IN {camera.footfall.entries}   OUT {camera.footfall.exits}"
                       f"   in store {camera.footfall.occupancy}")
        if camera.queue is not None:
            for name, counter in camera.queue.counters.items():
                wait = counter.median_wait_s
                hud.append(f"{name}: {counter.queue_len} waiting"
                           + (f", median wait {wait:.0f}s" if wait else ""))
        if pipeline.forecaster.last is not None:
            last = pipeline.forecaster.last
            if last.basis != "warmup":
                hud.append(f"forecast: open {last.recommended_counters} counters")
        hud.append("video stored: 0 bytes")

        canvas = draw(frame.image, tracks=tracks, line=camera.line,
                      zones=camera.zone_polygons(), lanes=camera.lane_polygons(),
                      billings=camera.billing_polygons(), slots=camera.slot_polygons(),
                      hud=hud, blur=not args.no_blur)

        if args.scale != 1.0:
            canvas = cv2.resize(canvas, None, fx=args.scale, fy=args.scale,
                                interpolation=cv2.INTER_CUBIC)

        if writer is None:
            height, width = canvas.shape[:2]
            fps = config.cameras[0].fps or 10
            writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                     (width, height))
        writer.write(canvas)

        if still_every and saved_stills < args.stills and camera.processed % still_every == 0:
            still = out.with_name(f"{out.stem}_still{saved_stills + 1}.png")
            cv2.imwrite(str(still), canvas)
            saved_stills += 1

    pipeline = Pipeline(config, replay=True, show=False, store=_NullStore(),
                        on_tracks=on_tracks)
    state["pipeline"] = pipeline
    # Roughly spread the stills through the clip.
    still_every = 60

    summary = pipeline.run(max_seconds=args.max_seconds, progress=False)
    if writer is not None:
        writer.release()
    pipeline.close()

    print(f"wrote {out}")
    if saved_stills:
        print(f"  plus {saved_stills} still frames beside it")
    camera_summary = summary["cameras"][args.camera]
    print(f"  frames {camera_summary['frames_processed']}, "
          f"entries {camera_summary.get('entries')}, exits {camera_summary.get('exits')}")
    print("  people are blurred in this output" if not args.no_blur
          else "  WARNING: blurring disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
