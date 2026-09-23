"""Inventory the videos and models we actually have.

Resolution, FPS and duration decide how much of the compute budget each camera
costs (04 section 6), so this is the first thing to know about any new clip.  It
also states plainly which folders are still empty, because "we have no real
footage yet" is a fact for the handoff, not something to paper over.

    python tools/inventory.py --videos ../videos --models ../models
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".avi", ".mkv", ".mov", ".m4v", ".webm"}
MODEL_SUFFIXES = {".pt", ".tflite", ".onnx", ".param", ".bin", ".hef"}


def md5(path: Path, limit: int = 8 << 20) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        digest.update(handle.read(limit))
    return digest.hexdigest()[:12]


def probe_video(path: Path) -> dict:
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"file": str(path), "error": "could not open"}
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    info = {
        "file": str(path),
        "size_mb": round(path.stat().st_size / 1e6, 2),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": round(fps, 2),
        "frames": frames,
        "duration_s": round(frames / fps, 1) if fps else None,
    }
    cap.release()
    # Ground truth sitting next to a clip is what makes it usable for evaluation.
    siblings = sorted(p.name for p in path.parent.glob(f"{path.stem}_gt_*.csv"))
    info["ground_truth"] = siblings or []
    info["detections_json"] = (path.parent / f"{path.stem}_detections.json").is_file()
    return info


def probe_model(path: Path) -> dict:
    info = {"file": str(path), "size_mb": round(path.stat().st_size / 1e6, 2),
            "md5_head": md5(path)}
    if path.suffix == ".tflite":
        try:
            try:
                from ai_edge_litert.interpreter import Interpreter
            except ImportError:
                from tensorflow.lite import Interpreter  # type: ignore
            interpreter = Interpreter(model_path=str(path))
            interpreter.allocate_tensors()
            inp = interpreter.get_input_details()[0]
            info["input_shape"] = [int(v) for v in inp["shape"]]
            info["input_dtype"] = str(inp["dtype"].__name__)
            info["outputs"] = len(interpreter.get_output_details())
        except Exception as error:  # a missing runtime is not a failure here
            info["note"] = f"no TFLite runtime installed ({type(error).__name__})"
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", default="../videos")
    parser.add_argument("--models", default="../models")
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    report: dict = {"videos": {}, "models": [], "empty_folders": []}

    videos_root = Path(args.videos)
    for folder in ("entrance", "queue", "shelf", "other"):
        directory = videos_root / folder
        found = sorted(p for p in directory.glob("*") if p.suffix.lower() in VIDEO_SUFFIXES) \
            if directory.is_dir() else []
        report["videos"][folder] = [probe_video(p) for p in found]
        if not found:
            report["empty_folders"].append(str(directory))

    models_root = Path(args.models)
    if models_root.is_dir():
        for path in sorted(models_root.rglob("*")):
            if path.suffix.lower() in MODEL_SUFFIXES and path.is_file():
                report["models"].append(probe_model(path))
    else:
        report["empty_folders"].append(str(models_root))

    print("# Video inventory\n")
    print("| folder | file | resolution | fps | duration | size | ground truth |")
    print("|---|---|---|---|---|---|---|")
    for folder, entries in report["videos"].items():
        if not entries:
            print(f"| {folder} | *(empty - real footage still needed)* | | | | | |")
        for entry in entries:
            if "error" in entry:
                print(f"| {folder} | {Path(entry['file']).name} | ERROR | | | | |")
                continue
            gt = ", ".join(entry["ground_truth"]) or ("detections.json"
                                                      if entry["detections_json"] else "none")
            print(f"| {folder} | {Path(entry['file']).name} | {entry['width']}x{entry['height']} "
                  f"| {entry['fps']} | {entry['duration_s']}s | {entry['size_mb']} MB | {gt} |")

    print("\n# Model inventory\n")
    print("| file | size | md5(head) | input | note |")
    print("|---|---|---|---|---|")
    for entry in report["models"]:
        shape = entry.get("input_shape", "")
        print(f"| {Path(entry['file']).name} | {entry['size_mb']} MB | {entry['md5_head']} "
              f"| {shape} | {entry.get('note', '')} |")
    if not report["models"]:
        print("| *(no model files found)* | | | | |")

    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
