"""Run the pure-TensorFlow grid shelf-void model exported by the v2 Kaggle notebook.

Usage (Windows PowerShell):
  py -m pip install tensorflow opencv-python numpy
  py live_shelf_void_webcam_grid.py --model shelf_void_grid_int8.tflite
"""

from __future__ import annotations

import argparse
import time
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
        raise SystemExit('Install tensorflow or tflite-runtime first.') from error


def dequantize(value: np.ndarray, detail: dict) -> np.ndarray:
    scale, zero_point = detail['quantization']
    return (value.astype(np.float32) - zero_point) * scale if scale else value.astype(np.float32)


class GridVoidDetector:
    """Decoder for the [objectness, local-cx, local-cy, width, height] 10x10 grid model."""

    def __init__(self, model_path: Path, score_threshold: float) -> None:
        self.interpreter = Interpreter(model_path=str(model_path), num_threads=4)
        self.interpreter.allocate_tensors()
        self.input = self.interpreter.get_input_details()[0]
        self.output = self.interpreter.get_output_details()[0]
        self.input_height, self.input_width = self.input['shape'][1:3]
        self.score_threshold = score_threshold

    def detect(self, bgr: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        height, width = bgr.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(bgr, (self.input_width, self.input_height)), cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0
        scale, zero_point = self.input['quantization']
        if self.input['dtype'] == np.float32:
            tensor = normalized
        elif scale:
            info = np.iinfo(self.input['dtype'])
            tensor = np.clip(np.round(normalized / scale + zero_point), info.min, info.max).astype(self.input['dtype'])
        else:
            tensor = rgb.astype(self.input['dtype'])
        self.interpreter.set_tensor(self.input['index'], tensor[None, ...])
        self.interpreter.invoke()
        grid = dequantize(self.interpreter.get_tensor(self.output['index']), self.output)[0]
        rows, cols = grid.shape[:2]
        boxes = []
        for gy in range(rows):
            for gx in range(cols):
                score, tx, ty, box_w, box_h = grid[gy, gx]
                if score < self.score_threshold:
                    continue
                cx = (gx + tx) / cols * width
                cy = (gy + ty) / rows * height
                bw, bh = box_w * width, box_h * height
                x1, y1 = max(0, int(cx - bw / 2)), max(0, int(cy - bh / 2))
                x2, y2 = min(width, int(cx + bw / 2)), min(height, int(cy + bh / 2))
                if x2 > x1 and y2 > y1:
                    boxes.append((x1, y1, x2, y2, float(score)))
        return boxes


def shelf_status(void_ratio: float, low_threshold: float, empty_threshold: float) -> str:
    if void_ratio >= empty_threshold:
        return 'EMPTY'
    if void_ratio >= low_threshold:
        return 'LOW'
    return 'FULL'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--score', type=float, default=0.45)
    parser.add_argument('--low-threshold', type=float, default=0.08)
    parser.add_argument('--empty-threshold', type=float, default=0.25)
    args = parser.parse_args()
    if not args.model.is_file():
        raise SystemExit(f'Model not found: {args.model}')

    detector = GridVoidDetector(args.model, args.score)
    camera = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    if not camera.isOpened():
        raise SystemExit(f'Cannot open webcam {args.camera}')

    # Normalised shelf rectangle; change only after fixing the webcam position for the POC shelf.
    shelf_roi = (0.05, 0.10, 0.95, 0.90)
    last_beep = 0.0
    print('Webcam running; press q to quit.')
    while True:
        ok, frame = camera.read()
        if not ok:
            break
        height, width = frame.shape[:2]
        rx1, ry1, rx2, ry2 = shelf_roi
        x1, y1, x2, y2 = int(rx1*width), int(ry1*height), int(rx2*width), int(ry2*height)
        shelf = frame[y1:y2, x1:x2]
        detections = detector.detect(shelf)
        void_mask = np.zeros(shelf.shape[:2], dtype=np.uint8)
        for bx1, by1, bx2, by2, score in detections:
            cv2.rectangle(void_mask, (bx1, by1), (bx2, by2), 255, -1)
            cv2.rectangle(shelf, (bx1, by1), (bx2, by2), (0, 0, 255), 2)
            cv2.putText(shelf, f'VOID {score:.2f}', (bx1, max(18, by1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 0, 255), 2)
        ratio = float((void_mask > 0).mean())
        status = shelf_status(ratio, args.low_threshold, args.empty_threshold)
        colour = {'FULL': (0, 180, 0), 'LOW': (0, 180, 255), 'EMPTY': (0, 0, 255)}[status]
        cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 3)
        cv2.putText(frame, f'SHELF: {status} | void: {ratio:.1%}', (20, 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
        if status in {'LOW', 'EMPTY'} and time.monotonic() - last_beep > 10:
            print(f'ALERT: shelf {status}; void area={ratio:.1%}')
            print('\a', end='', flush=True)
            last_beep = time.monotonic()
        cv2.imshow('Smart Retail — TensorFlow Shelf Void Detection', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    camera.release()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
