"""Privacy-aware, real-time shopper analytics using a TensorFlow Lite person detector.

Usage:
  py -m pip install tensorflow opencv-python numpy
  py live_shopper_analytics.py --config shopper_analytics_config.json

The config requires a standard TensorFlow Lite COCO detector whose class 0 is
person (for example, EfficientDet-Lite0). No camera frames or faces are saved;
only anonymous event metadata is written as JSONL.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    try:
        import tensorflow as tf
        Interpreter = tf.lite.Interpreter
    except ImportError as error:
        raise SystemExit("Install tensorflow or tflite-runtime first.") from error


def read_config(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Configuration file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def dequantize(value: np.ndarray, detail: dict) -> np.ndarray:
    scale, zero_point = detail["quantization"]
    return (value.astype(np.float32) - zero_point) * scale if scale else value.astype(np.float32)


class PersonDetector:
    """For standard TFLite Detection_PostProcess outputs: boxes/classes/scores/count."""

    def __init__(self, model_path: Path, threshold: float, person_class_id: int, num_threads: int) -> None:
        if not model_path.is_file():
            raise SystemExit(
                f"Person TFLite model not found: {model_path}\n"
                "Place efficientdet_lite0.tflite beside this script or set model_path in the config."
            )
        self.interpreter = Interpreter(model_path=str(model_path), num_threads=num_threads)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.outputs = self.interpreter.get_output_details()
        self.input_height, self.input_width = self.input["shape"][1:3]
        self.threshold = threshold
        self.person_class_id = person_class_id
        if len(self.outputs) < 3:
            raise SystemExit("Unsupported detector: expected standard TFLite boxes/classes/scores outputs.")

    def detect(self, bgr: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        height, width = bgr.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(bgr, (self.input_width, self.input_height)), cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0
        scale, zero_point = self.input["quantization"]
        if self.input["dtype"] == np.float32:
            tensor = normalized
        elif scale:
            info = np.iinfo(self.input["dtype"])
            tensor = np.clip(np.round(normalized / scale + zero_point), info.min, info.max).astype(self.input["dtype"])
        else:
            tensor = rgb.astype(self.input["dtype"])
        self.interpreter.set_tensor(self.input["index"], tensor[None, ...])
        self.interpreter.invoke()
        values = [dequantize(self.interpreter.get_tensor(item["index"]), item) for item in self.outputs]
        boxes, classes, scores = values[0][0], values[1][0], values[2][0]
        people = []
        for box, class_id, score in zip(boxes, classes, scores):
            if int(round(class_id)) != self.person_class_id or score < self.threshold:
                continue
            y1, x1, y2, x2 = box
            x1, x2 = sorted((max(0, int(x1 * width)), min(width, int(x2 * width))))
            y1, y2 = sorted((max(0, int(y1 * height)), min(height, int(y2 * height))))
            if x2 > x1 and y2 > y1:
                people.append((x1, y1, x2, y2, float(score)))
        return people


def box_center(box: tuple[int, int, int, int, float]) -> tuple[float, float]:
    x1, y1, x2, y2, _ = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


class CentroidTracker:
    """Session-only IDs: never re-identifies people across cameras or sessions."""

    def __init__(self, max_distance: float, max_misses: int) -> None:
        self.max_distance, self.max_misses = max_distance, max_misses
        self.next_id, self.tracks = 1, {}

    def update(self, boxes: list[tuple[int, int, int, int, float]]) -> dict:
        centres = [box_center(box) for box in boxes]
        available = set(range(len(boxes)))
        for track_id, track in list(self.tracks.items()):
            distances = [
                math.dist(track["centre"], centre) if index in available else float("inf")
                for index, centre in enumerate(centres)
            ]
            best = int(np.argmin(distances)) if distances else -1
            if best >= 0 and distances[best] <= self.max_distance:
                track["previous_centre"] = track["centre"]
                track["centre"], track["box"], track["misses"] = centres[best], boxes[best], 0
                available.remove(best)
            else:
                track["misses"] += 1
                if track["misses"] > self.max_misses:
                    del self.tracks[track_id]
        for index in available:
            self.tracks[self.next_id] = {
                "centre": centres[index], "previous_centre": None, "box": boxes[index],
                "misses": 0, "last_side": None, "last_cross": 0.0, "zones": set(),
                "promo_started": None, "promo_alerted": False,
            }
            self.next_id += 1
        return self.tracks


def point_side(point: tuple[float, float], a: tuple[int, int], b: tuple[int, int]) -> int:
    cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    return 1 if cross >= 0 else -1


def normalised_point(point: list[float], width: int, height: int) -> tuple[int, int]:
    return int(point[0] * width), int(point[1] * height)


def emit(log_path: Path, event_type: str, **payload: object) -> dict:
    event = {
        "event_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "camera_id": "poc_single_camera",
        **payload,
    }
    with log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
    return event


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("shopper_analytics_config.json"))
    parser.add_argument("--model", type=Path, default=None, help="Overrides model_path in the config")
    args = parser.parse_args()
    config = read_config(args.config)
    model_path = args.model or Path(config["model_path"])
    log_path, summary_path = Path(config["event_log_path"]), Path(config["summary_path"])
    log_path.write_text("", encoding="utf-8")  # new privacy-safe event session; no videos are stored

    detector = PersonDetector(model_path, config["detection_threshold"], config["person_class_id"], config["num_threads"])
    tracker = CentroidTracker(config["tracker_max_distance_px"], config["tracker_max_misses"])
    source = config["camera_source"]
    if isinstance(source, int):
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        camera = cv2.VideoCapture(source, backend)
    else:
        camera = cv2.VideoCapture(source)
    if not camera.isOpened():
        raise SystemExit(f"Cannot open camera source: {source}")

    entered = exited = 0
    heatmap = None
    show_heatmap = True
    frame_count, last_fps_at, last_summary_at = 0, time.monotonic(), time.monotonic()
    fps = 0.0
    print("Live analytics running. Press h to toggle heatmap, q to quit.")
    while True:
        ok, frame = camera.read()
        if not ok:
            break
        height, width = frame.shape[:2]
        if heatmap is None or heatmap.shape != (height, width):
            heatmap = np.zeros((height, width), dtype=np.float32)
        line_a = normalised_point(config["entry_line"]["p1"], width, height)
        line_b = normalised_point(config["entry_line"]["p2"], width, height)
        promo_polygon = np.array([normalised_point(point, width, height) for point in config["promo_zone"]], np.int32)
        zone_polygons = {
            name: np.array([normalised_point(point, width, height) for point in polygon], np.int32)
            for name, polygon in config.get("analytics_zones", {}).items()
        }
        now = time.monotonic()
        tracks = tracker.update(detector.detect(frame))
        heatmap *= 0.998
        active_promo = 0
        live_zone_counts = {name: 0 for name in zone_polygons}

        for track_id, track in tracks.items():
            if track["misses"]:
                continue
            centre = track["centre"]
            cv2.circle(heatmap, (int(centre[0]), int(centre[1])), 20, 1.0, -1)
            side = point_side(centre, line_a, line_b)
            previous_side = track["last_side"]
            if previous_side is not None and side != previous_side and now - track["last_cross"] >= config["entry_line"]["crossing_cooldown_seconds"]:
                direction = "positive_to_negative" if previous_side > side else "negative_to_positive"
                event_type = "ENTRY" if direction == config["entry_line"]["enter_direction"] else "EXIT"
                if event_type == "ENTRY": entered += 1
                else: exited += 1
                emit(log_path, event_type, anonymous_track=track_id, entered=entered, exited=exited)
                track["last_cross"] = now
            track["last_side"] = side

            in_promo = cv2.pointPolygonTest(promo_polygon, centre, False) >= 0
            current_zones = {
                name for name, polygon in zone_polygons.items()
                if cv2.pointPolygonTest(polygon, centre, False) >= 0
            }
            for zone_name in current_zones - track["zones"]:
                emit(log_path, "ZONE_ENTRY", anonymous_track=track_id, zone=zone_name)
            for zone_name in track["zones"] - current_zones:
                emit(log_path, "ZONE_EXIT", anonymous_track=track_id, zone=zone_name)
            track["zones"] = current_zones
            for zone_name in current_zones:
                live_zone_counts[zone_name] += 1
            if in_promo:
                active_promo += 1
                track["promo_started"] = track["promo_started"] or now
                dwell_seconds = now - track["promo_started"]
                if dwell_seconds >= config["promo_dwell_seconds"] and not track["promo_alerted"]:
                    emit(log_path, "PROMO_DWELL", anonymous_track=track_id, dwell_seconds=round(dwell_seconds, 1))
                    track["promo_alerted"] = True
            elif track["promo_started"] is not None:
                dwell_seconds = now - track["promo_started"]
                emit(log_path, "PROMO_EXIT", anonymous_track=track_id, dwell_seconds=round(dwell_seconds, 1))
                track["promo_started"], track["promo_alerted"] = None, False

            x1, y1, x2, y2, score = track["box"]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 80), 2)
            cv2.putText(frame, f"P{track_id} {score:.2f}", (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 220, 80), 2)

        if show_heatmap and heatmap.max() > 0:
            heat8 = cv2.normalize(heatmap, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            colourmap = cv2.applyColorMap(heat8, cv2.COLORMAP_JET)
            mask = heat8 > 15
            frame[mask] = cv2.addWeighted(frame, 0.55, colourmap, 0.45, 0)[mask]

        cv2.line(frame, line_a, line_b, (0, 255, 255), 3)
        for zone_name, polygon in zone_polygons.items():
            cv2.polylines(frame, [polygon], True, (180, 80, 255), 1)
            cv2.putText(frame, zone_name, tuple(polygon[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 80, 255), 1)
        cv2.polylines(frame, [promo_polygon], True, (255, 100, 0), 2)
        cv2.putText(frame, "PROMO ZONE", tuple(promo_polygon[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 100, 0), 2)
        frame_count += 1
        if now - last_fps_at >= 1.0:
            fps, frame_count, last_fps_at = frame_count / (now - last_fps_at), 0, now
        inside_estimate = max(0, entered - exited)
        cv2.rectangle(frame, (0, 0), (330, 125), (20, 20, 20), -1)
        for text, y, colour in [
            (f"IN: {entered}   OUT: {exited}", 30, (80, 220, 80)),
            (f"EST. INSIDE: {inside_estimate}", 58, (220, 220, 220)),
            (f"PROMO NOW: {active_promo}", 86, (255, 160, 80)),
            (f"FPS: {fps:.1f}", 114, (220, 220, 220)),
        ]:
            cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, colour, 2)

        if now - last_summary_at >= 2.0:
            summary_path.write_text(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(), "entered": entered, "exited": exited,
                "estimated_inside": inside_estimate, "promo_now": active_promo, "zones_now": live_zone_counts,
                "fps": round(fps, 1),
            }, indent=2), encoding="utf-8")
            last_summary_at = now
        cv2.imshow("Smart Retail — Live Shopper Analytics", frame)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == ord("h"):
            show_heatmap = not show_heatmap
    camera.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
