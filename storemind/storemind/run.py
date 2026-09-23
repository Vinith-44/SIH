"""StoreMind CLI.

    python -m storemind.run --config configs/demo.yaml
    python -m storemind.run --config configs/demo.yaml --source videos/entrance/x.mp4 --show
    python -m storemind.run --config configs/demo.yaml --live --api

`--show` is the only thing that opens a window; without it the process is fully
headless and runs on a Raspberry Pi over SSH.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

from .core.config import CameraConfig, load_config
from .pipeline import Pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser("storemind", description="StoreMind edge retail analytics")
    parser.add_argument("--config", default="configs/demo.yaml", help="YAML config file")
    parser.add_argument("--source", action="append", default=None,
                        help="override camera source (repeat for several cameras, in config order)")
    parser.add_argument("--camera", action="append", default=None,
                        help="only run these cameras (by name)")
    parser.add_argument("--show", action="store_true", help="open a debug preview window (people blurred)")
    parser.add_argument("--headless", action="store_true", help="force no window (default)")
    parser.add_argument("--live", action="store_true",
                        help="treat sources as live cameras (wall clock) instead of replay")
    parser.add_argument("--realtime", action="store_true",
                        help="replay a file at its real speed instead of as fast as possible")
    parser.add_argument("--backend", default=None, choices=["ultralytics", "litert", "onnx", "scripted", "stub"])
    parser.add_argument("--model", default=None, help="detector weights path")
    parser.add_argument("--imgsz", type=int, default=None, help="detector input size")
    parser.add_argument("--conf", type=float, default=None, help="detection confidence threshold")
    parser.add_argument("--fps", type=float, default=None, help="override per-camera target FPS")
    parser.add_argument("--max-seconds", type=float, default=None, help="stop after N seconds of video")
    parser.add_argument("--db", default=None, help="SQLite path override")
    parser.add_argument("--api", action="store_true", help="also serve the dashboard")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--start-time", default=None,
                        help="ISO datetime the replay should pretend to start at")
    parser.add_argument("--summary-json", default=None, help="write the run summary to this file")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def apply_overrides(config, args) -> None:
    if args.camera:
        wanted = set(args.camera)
        config.cameras = [c for c in config.cameras if c.name in wanted]
        if not config.cameras:
            raise SystemExit(f"no camera named {args.camera} in the config")
    if args.source:
        if len(args.source) == 1 and len(config.cameras) > 1 and not args.camera:
            # One --source with many cameras is almost always a mistake; be explicit.
            raise SystemExit(
                "config has several cameras - pass --camera NAME with a single --source, "
                "or repeat --source once per camera")
        if not config.cameras:
            config.cameras = [CameraConfig(name="cam0", source=args.source[0], role="generic")]
        for camera, source in zip(config.cameras, args.source):
            camera.source = source
    if args.backend:
        config.detector.backend = args.backend
    if args.model:
        config.detector.model = args.model
    if args.imgsz:
        config.detector.imgsz = args.imgsz
    if args.conf is not None:
        config.detector.conf = args.conf
    if args.fps is not None:
        for camera in config.cameras:
            camera.fps = args.fps
    if args.db:
        config.storage.db_path = args.db
    if args.port:
        config.api.port = args.port


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.ERROR if args.quiet else logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config = load_config(args.config)
    apply_overrides(config, args)
    if not config.cameras:
        raise SystemExit("no cameras configured; add one to the config or pass --source")

    show = args.show and not args.headless
    start_time = datetime.fromisoformat(args.start_time) if args.start_time else None

    pipeline = Pipeline(config, replay=not args.live, show=show, realtime=args.realtime,
                        start_time=start_time)

    # Zones that sit in front of a shelf slot feed the lost-sales rule.
    for camera in config.cameras:
        for zone in camera.zones:
            if zone.shelf and zone.slot:
                price = None
                sku = None
                for shelf in camera.shelves:
                    for slot in shelf.slots:
                        if shelf.name == zone.shelf and slot.name == zone.slot:
                            price, sku = slot.price, slot.sku
                pipeline.fusion.register_zone(zone.name, zone.shelf, zone.slot, sku, price)

    server_thread = None
    if args.api:
        from .api.server import serve_in_thread

        server_thread = serve_in_thread(pipeline, host=config.api.host, port=config.api.port)
        print(f"dashboard: http://localhost:{config.api.port}/", flush=True)

    if not args.quiet:
        print(f"StoreMind | store={config.store} node={config.node} "
              f"detector={config.detector.backend}:{config.detector.model} "
              f"mode={'live' if args.live else 'replay'}", flush=True)
        for camera in config.cameras:
            print(f"  camera {camera.name:12s} role={camera.role:9s} src={camera.source}", flush=True)

    summary = pipeline.run(max_seconds=args.max_seconds, progress=not args.quiet)
    summary["db"] = str(Path(config.storage.db_path).resolve())
    summary["events_in_db"] = pipeline.store.counts_by_type()

    text = json.dumps(summary, indent=2, default=str)
    if not args.quiet:
        print("\n=== run summary ===")
        print(text)
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(text, encoding="utf-8")

    if server_thread is not None and args.live:
        print("pipeline finished; dashboard still serving, Ctrl-C to stop", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass

    pipeline.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
