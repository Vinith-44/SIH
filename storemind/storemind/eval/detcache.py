"""Record the detector's output once per clip, for tracker/counter experiments.

The frames are chosen by the pipeline's own `FpsScheduler` on the pipeline's own
`open_source`, so a replay through `CachedDetector` sees exactly the frames a
live run would.  Boxes are stored down to `--conf-floor` with their scores;
filtering to the operating threshold happens at replay time.  (Greedy NMS keeps
the same boxes above any threshold >= the floor, so this is lossless.)

    python -m storemind.eval.detcache                  # every CAVIAR clip, both views
    python -m storemind.eval.detcache --view corridor --force

Caches go to `data/detcache/` (git-ignored).  They are derived data: delete and
re-run to regenerate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from ..core.config import load_config
from ..ingest.sources import FpsScheduler, open_source

REPO = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO / "data" / "detcache"


def cache_path(video: Path, model: str, imgsz: int, fps: float) -> Path:
    return CACHE_DIR / f"{video.stem}__{Path(model).stem}_{imgsz}_{fps:g}fps.json"


def build_cache(video: Path, *, model: str, imgsz: int, fps: float, iou: float = 0.5,
                conf_floor: float = 0.05, person_class: int = 0, out: Path | None = None,
                force: bool = False, detector=None) -> Path:
    out = out or cache_path(video, model, imgsz, fps)
    if out.is_file() and not force:
        return out
    if detector is None:
        from ..inference.detector import UltralyticsDetector

        detector = UltralyticsDetector(model, conf=conf_floor, iou=iou, imgsz=imgsz,
                                       person_class=person_class)
    source = open_source(str(video), name=video.stem, realtime=False)
    scheduler = FpsScheduler(fps)
    frames: dict[str, list[list[float]]] = {}
    times: dict[str, float] = {}
    size: tuple[int, int] | None = None
    infer_ms: list[float] = []
    while True:
        frame = source.read()
        if frame is None:
            break
        if not scheduler.should_process(frame.video_s):
            continue
        started = time.perf_counter()
        detections = detector.detect(frame.image)
        infer_ms.append((time.perf_counter() - started) * 1000.0)
        frames[str(frame.index)] = [[round(v, 2) for v in d.xyxy] + [round(d.conf, 4), d.cls]
                                    for d in detections]
        times[str(frame.index)] = frame.video_s
        size = frame.size
    source.close()
    payload = {
        "video": str(video), "model": model, "imgsz": imgsz, "iou": iou,
        "conf_floor": conf_floor, "fps": fps,
        "device": getattr(detector, "device", "cpu"),
        "frames_processed": len(frames),
        "size": list(size) if size else None,
        "times": times,
        "infer_ms_median": round(statistics.median(infer_ms), 2) if infer_ms else None,
        "frames": frames,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def caviar_caches(config_path: str = "configs/caviar.yaml", views=("corridor", "front"),
                  force: bool = False, model: str | None = None,
                  imgsz: int | None = None) -> dict[tuple[str, str], Path]:
    """Caches for every CAVIAR clip; `model` / `imgsz` override the config's detector."""
    from .caviar import SCENARIOS
    from .eval_caviar import CAVIAR_DIR

    config = load_config(config_path)
    det = config.detector.model_copy(update={k: v for k, v in
                                             (("model", model), ("imgsz", imgsz)) if v})
    detector = None
    out: dict[tuple[str, str], Path] = {}
    for scenario in SCENARIOS:
        for view in views:
            cam = config.camera(view)
            video = CAVIAR_DIR / f"{scenario}{'front' if view == 'front' else 'cor'}.mpg"
            path = cache_path(video, det.model, det.imgsz, cam.fps)
            if path.is_file() and not force:
                out[(scenario, view)] = path
                continue
            if detector is None:
                from ..inference.detector import UltralyticsDetector

                detector = UltralyticsDetector(det.model, conf=0.05, iou=det.iou,
                                               imgsz=det.imgsz, person_class=det.person_class)
            print(f"    caching {video.name} ({detector.device})", flush=True)
            out[(scenario, view)] = build_cache(video, model=det.model, imgsz=det.imgsz,
                                                fps=cam.fps, iou=det.iou, force=True,
                                                detector=detector)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="configs/caviar.yaml")
    parser.add_argument("--view", action="append", choices=["corridor", "front"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--model", default=None, help="override, e.g. ../models/yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=None)
    args = parser.parse_args(argv)
    paths = caviar_caches(args.config, tuple(args.view or ("corridor", "front")), args.force,
                          model=args.model, imgsz=args.imgsz)
    for (scenario, view), path in sorted(paths.items()):
        meta = json.loads(path.read_text(encoding="utf-8"))
        print(f"{scenario:28s} {view:8s} {meta['frames_processed']:5d} frames  "
              f"{meta['infer_ms_median']} ms/frame median on {meta['device']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
