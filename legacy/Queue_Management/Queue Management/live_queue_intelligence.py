"""Real-time queue intelligence with TensorFlow Lite person detection.

Usage:
  py -m pip install tensorflow opencv-python numpy
  py live_queue_intelligence.py --config queue_intelligence_config.json

The customer in service_zone is excluded from queue_count. The laptop speaker
beeps only after the threshold persists, then rate-limits repeat alerts.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
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


def dequantize(value: np.ndarray, detail: dict) -> np.ndarray:
    scale, zero = detail["quantization"]
    return (value.astype(np.float32) - zero) * scale if scale else value.astype(np.float32)


def emit(path: Path, event_type: str, **payload: object) -> dict:
    event = {"event_id": str(uuid.uuid4()), "timestamp": datetime.now(timezone.utc).isoformat(),
             "event_type": event_type, "camera_id": "poc_cashier_camera", **payload}
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event) + "\n")
    return event


def beep() -> None:
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.Beep(880, 250)
            return
        except RuntimeError:
            pass
    print("\a", end="", flush=True)


def norm_point(point: list[float], width: int, height: int) -> tuple[int, int]:
    return int(point[0] * width), int(point[1] * height)


def in_polygon(point: tuple[float, float], polygon: np.ndarray) -> bool:
    return cv2.pointPolygonTest(polygon, point, False) >= 0


class PersonDetector:
    """Standard TFLite Detection_PostProcess model: boxes, classes, scores, count."""
    def __init__(self, model_path: Path, threshold: float, person_id: int, threads: int) -> None:
        if not model_path.is_file():
            raise SystemExit(f"Person TFLite model not found: {model_path}")
        self.interpreter = Interpreter(model_path=str(model_path), num_threads=threads)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.outputs = self.interpreter.get_output_details()
        self.height, self.width = self.input["shape"][1:3]
        self.threshold, self.person_id = threshold, person_id
        if len(self.outputs) < 3:
            raise SystemExit("Unsupported TFLite model: expected boxes, classes, scores outputs.")

    def detect(self, frame: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame, (self.width, self.height)), cv2.COLOR_BGR2RGB)
        normal = rgb.astype(np.float32) / 255.0
        scale, zero = self.input["quantization"]
        if self.input["dtype"] == np.float32:
            value = normal
        elif scale:
            info = np.iinfo(self.input["dtype"])
            value = np.clip(np.round(normal / scale + zero), info.min, info.max).astype(self.input["dtype"])
        else:
            value = rgb.astype(self.input["dtype"])
        self.interpreter.set_tensor(self.input["index"], value[None, ...]); self.interpreter.invoke()
        values = [dequantize(self.interpreter.get_tensor(item["index"]), item) for item in self.outputs]
        boxes, classes, scores = values[0][0], values[1][0], values[2][0]
        people = []
        for box, cls, score in zip(boxes, classes, scores):
            if int(round(cls)) != self.person_id or score < self.threshold:
                continue
            y1, x1, y2, x2 = box
            x1, x2 = sorted((max(0, int(x1 * width)), min(width, int(x2 * width))))
            y1, y2 = sorted((max(0, int(y1 * height)), min(height, int(y2 * height))))
            if x2 > x1 and y2 > y1: people.append((x1, y1, x2, y2, float(score)))
        return people


def centre(box: tuple[int, int, int, int, float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


class Tracker:
    """Ephemeral IDs only; no customer identity or images are persisted."""
    def __init__(self, max_distance: int, max_misses: int) -> None:
        self.max_distance, self.max_misses, self.next_id, self.tracks = max_distance, max_misses, 1, {}

    def update(self, boxes: list[tuple[int, int, int, int, float]]) -> dict:
        points, available = [centre(box) for box in boxes], set(range(len(boxes)))
        for tid, track in list(self.tracks.items()):
            distance = [math.dist(track["centre"], point) if i in available else float("inf") for i, point in enumerate(points)]
            best = int(np.argmin(distance)) if distance else -1
            if best >= 0 and distance[best] <= self.max_distance:
                track.update(centre=points[best], box=boxes[best], misses=0); available.remove(best)
            else:
                track["misses"] += 1
                if track["misses"] > self.max_misses: del self.tracks[tid]
        for index in available:
            self.tracks[self.next_id] = {"centre": points[index], "box": boxes[index], "misses": 0,
                                         "queue_started": None, "service_started": None,
                                         "was_in_service": False}
            self.next_id += 1
        return self.tracks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("queue_intelligence_config.json"))
    parser.add_argument("--model", type=Path, default=None)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    log_path, summary_path = Path(config["event_log_path"]), Path(config["summary_path"])
    log_path.write_text("", encoding="utf-8")
    model_path = args.model or Path(config["model_path"])
    detector = PersonDetector(model_path, config["detection_threshold"], config["person_class_id"], config["num_threads"])
    tracker = Tracker(config["tracker_max_distance_px"], config["tracker_max_misses"])

    source = config["camera_source"]
    camera = cv2.VideoCapture(source, cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY) if isinstance(source, int) else cv2.VideoCapture(source)
    if not camera.isOpened(): raise SystemExit(f"Cannot open camera source: {source}")
    history, alert_since, last_beep, last_summary = [], None, -1e9, time.monotonic()
    total_service_starts, completed_waits, frame_count, fps_mark, fps = 0, [], 0, time.monotonic(), 0.0
    print("Queue intelligence running. Press q to quit.")

    while True:
        ok, frame = camera.read()
        if not ok: break
        now, height, width = time.monotonic(), frame.shape[0], frame.shape[1]
        queue_poly = np.array([norm_point(point, width, height) for point in config["queue_zone"]], np.int32)
        service_poly = np.array([norm_point(point, width, height) for point in config["service_zone"]], np.int32)
        tracks = tracker.update(detector.detect(frame))
        queue_ids, wait_times = [], []
        for tid, track in tracks.items():
            if track["misses"]: continue
            point = track["centre"]
            in_service = in_polygon(point, service_poly)
            in_queue = in_polygon(point, queue_poly) and not in_service
            if in_queue:
                track["queue_started"] = track["queue_started"] or now
                queue_ids.append(tid); wait_times.append(now - track["queue_started"])
            if in_service and not track["was_in_service"]:
                total_service_starts += 1
                if track["queue_started"] is not None:
                    wait = now - track["queue_started"]; completed_waits.append(wait)
                    emit(log_path, "SERVICE_STARTED", anonymous_track=tid, wait_seconds=round(wait, 1))
                else:
                    emit(log_path, "SERVICE_STARTED", anonymous_track=tid, wait_seconds=None)
                track["service_started"] = now
            if not in_service and track["was_in_service"] and track["service_started"] is not None:
                emit(log_path, "SERVICE_COMPLETED", anonymous_track=tid, service_seconds=round(now-track["service_started"], 1))
                track["queue_started"], track["service_started"] = None, None
            track["was_in_service"] = in_service
            x1, y1, x2, y2, score = track["box"]
            colour = (0, 90, 255) if in_service else ((0, 220, 0) if in_queue else (160, 160, 160))
            cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
            cv2.putText(frame, f"P{tid}", (x1, max(18, y1-6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2)

        queue_count = len(queue_ids)  # The active billing customer is intentionally excluded.
        history.append((now, queue_count)); history = [(t, n) for t, n in history if now-t <= config["prediction_window_seconds"]]
        slope = (history[-1][1]-history[0][1])/(history[-1][0]-history[0][0]) if len(history) > 1 and history[-1][0] > history[0][0] else 0.0
        predicted = max(0.0, queue_count + slope * config["prediction_horizon_seconds"])
        threshold = config["queue_threshold_excluding_billing"]
        trigger = queue_count >= threshold
        alert_since = alert_since if trigger and alert_since is not None else (now if trigger else None)
        sustained = alert_since is not None and now-alert_since >= config["threshold_persistence_seconds"]
        long_wait = max(wait_times, default=0.0) >= config["long_wait_seconds"]
        predictive = predicted >= threshold and queue_count < threshold
        reason = "QUEUE_THRESHOLD" if sustained else ("LONG_WAIT" if long_wait else ("PREDICTED_CONGESTION" if predictive else None))
        if reason and now-last_beep >= config["buzzer_cooldown_seconds"]:
            beep(); emit(log_path, "QUEUE_ALERT", reason=reason, queue_count=queue_count, predicted_30s=round(predicted, 1), action="OPEN_ADDITIONAL_COUNTER")
            last_beep = now

        cv2.polylines(frame, [queue_poly], True, (0, 220, 0), 2)
        cv2.polylines(frame, [service_poly], True, (0, 90, 255), 2)
        cv2.putText(frame, "QUEUE (billing excluded)", tuple(queue_poly[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 0), 1)
        cv2.putText(frame, "SERVICE / BILLING", tuple(service_poly[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 90, 255), 1)
        frame_count += 1
        if now-fps_mark >= 1.0: fps, frame_count, fps_mark = frame_count/(now-fps_mark), 0, now
        panel = [f"QUEUE: {queue_count}/{threshold} (billing excluded)", f"AVG WAIT: {np.mean(wait_times) if wait_times else 0:.1f}s", f"MAX WAIT: {max(wait_times, default=0):.1f}s", f"PREDICTED +{int(config['prediction_horizon_seconds'])}s: {predicted:.1f}", f"FPS: {fps:.1f}"]
        cv2.rectangle(frame, (0, 0), (400, 155), (20, 20, 20), -1)
        for index, text in enumerate(panel): cv2.putText(frame, text, (10, 28+27*index), cv2.FONT_HERSHEY_SIMPLEX, 0.60, (230, 230, 230), 2)
        if reason: cv2.putText(frame, f"ALERT: {reason} - OPEN COUNTER", (20, height-20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        if now-last_summary >= 2.0:
            summary_path.write_text(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "queue_count_excluding_billing": queue_count, "average_wait_seconds": round(float(np.mean(wait_times)) if wait_times else 0, 1), "max_wait_seconds": round(max(wait_times, default=0), 1), "predicted_queue_count": round(predicted, 1), "service_starts": total_service_starts, "average_completed_wait_seconds": round(float(np.mean(completed_waits)) if completed_waits else 0, 1), "fps": round(fps, 1)}, indent=2), encoding="utf-8")
            last_summary = now
        cv2.imshow("Smart Retail - Queue Intelligence", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"): break
    camera.release(); cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
