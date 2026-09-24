"""One `Detector` interface, several backends.

Backends implemented here:

*   `ultralytics`  — YOLO11n / YOLO26n (.pt, .onnx, .ncnn, exported TFLite).
*   `litert`       — TFLite via ai-edge-litert / tflite_runtime / tf.lite.  This is
                     what lets the legacy EfficientDet-Lite0 (`legacy/.../1.tflite`)
                     and Qualcomm's Person-Foot-Detection TFLite plug straight in.
*   `onnx`         — ONNXRuntime, for models exported for the Qualcomm path.
*   `stub`         — no model at all; used by unit tests and by `--backend stub`
                     so the whole pipeline can be exercised without weights.

Everything is **letterboxed**, never squashed.  Audit item S1: the legacy code
resized 1920x1080 straight to 320x320, which distorts people into short wide
blobs and is a large part of why the old entry counter returned IN=0, OUT=0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import sys
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    xyxy: tuple[float, float, float, float]
    conf: float
    cls: int


@dataclass
class LetterboxInfo:
    scale: float
    pad_x: float
    pad_y: float
    # Letterboxing is isotropic, so one scale is enough.  The legacy squash
    # preprocessing is not: it stretches x and y by different factors, and
    # `eval/legacy_baseline.py` needs to reproduce that faithfully.  When
    # `scale_y` is set it overrides `scale` on the vertical axis.
    scale_y: float | None = None

    @property
    def sy(self) -> float:
        return self.scale_y if self.scale_y is not None else self.scale


def letterbox(image: np.ndarray, size: int, pad_value: int = 114) -> tuple[np.ndarray, LetterboxInfo]:
    """Resize preserving aspect ratio and pad to a square `size`."""
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    new_w, new_h = max(1, int(round(width * scale))), max(1, int(round(height * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), pad_value, dtype=image.dtype)
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
    return canvas, LetterboxInfo(scale=scale, pad_x=float(pad_x), pad_y=float(pad_y))


def unletterbox_xyxy(box: np.ndarray, info: LetterboxInfo, width: int, height: int) -> np.ndarray:
    """Map boxes from letterboxed space back onto the original frame."""
    out = box.astype(np.float32).copy()
    out[..., [0, 2]] = (out[..., [0, 2]] - info.pad_x) / info.scale
    out[..., [1, 3]] = (out[..., [1, 3]] - info.pad_y) / info.sy
    out[..., [0, 2]] = np.clip(out[..., [0, 2]], 0, width - 1)
    out[..., [1, 3]] = np.clip(out[..., [1, 3]], 0, height - 1)
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Plain greedy NMS (numpy only, so backends do not need torch)."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
        order = order[1:][iou <= iou_threshold]
    return keep


def merge_yolo_outputs(outputs: list[np.ndarray]) -> np.ndarray:
    """YOLO heads exported for INT8 keep boxes (4 channels) and class scores as
    two outputs, so each gets its own quantization scale (one shared scale
    crushes 0-1 scores next to 0-640 pixel boxes).  Put them back together as
    the usual (1, 4 + classes, N) tensor.  A single output passes through."""
    if len(outputs) == 1:
        return outputs[0]
    arrays = [np.asarray(o) for o in outputs]
    # The channel axis is the one where boxes (4) and scores (classes) differ;
    # runtimes may return the two outputs in either order.
    differ = [axis for axis in range(1, arrays[0].ndim) if arrays[0].shape[axis] != arrays[1].shape[axis]]
    channel_axis = differ[0] if differ else 1
    arrays.sort(key=lambda a: a.shape[channel_axis] != 4)          # boxes first
    return np.concatenate(arrays, axis=channel_axis)


class Detector:
    """The interface every backend implements."""

    name = "detector"
    input_size = 640
    # What actually runs the network: "cpu", "cuda:0", "qnn-htp (...)" or
    # "cpu (fallback: ...)".  Reported so a demo never claims an NPU it is not using.
    accelerator = "cpu"

    def detect(self, image: np.ndarray) -> list[Detection]:
        raise NotImplementedError

    def warmup(self, width: int = 640, height: int = 480) -> None:
        try:
            self.detect(np.zeros((height, width, 3), dtype=np.uint8))
        except Exception:
            log.debug("warmup failed for %s", self.name, exc_info=True)


class StubDetector(Detector):
    """Returns whatever it is told to.  Lets the whole pipeline (tracking,
    counting, queueing, storage, dashboard) be tested with no model file."""

    name = "stub"

    def __init__(self, boxes_per_frame: list[list[Detection]] | None = None) -> None:
        self.scripted = boxes_per_frame or []
        self._i = 0

    def detect(self, image: np.ndarray) -> list[Detection]:
        if not self.scripted:
            return []
        out = self.scripted[min(self._i, len(self.scripted) - 1)]
        self._i += 1
        return out


def resolve_device(device: str = "auto") -> str:
    """`auto` = CUDA when a CUDA build of torch sees a GPU (Person A's laptop),
    otherwise CPU (the Pi).  `STOREMIND_DEVICE=cpu` forces CPU, e.g. for speed
    numbers that must describe CPU inference."""
    import os

    device = os.environ.get("STOREMIND_DEVICE", device)
    if device != "auto":
        return device
    try:
        import torch

        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class UltralyticsDetector(Detector):
    """YOLO11n / YOLO26n and Ultralytics' exported formats.

    Two code paths:

    *   **fast** (PyTorch `.pt` weights) - letterbox, one forward pass, Ultralytics'
        own NMS, unletterbox.  `YOLO.predict()` rebuilds a predictor and a results
        object on every call; measured on this laptop that costs about 65 ms per
        frame on top of a ~40 ms forward pass.  On a Pi that overhead is the
        difference between serving one camera and serving three, so we pay it once
        at import instead of once per frame.
    *   **predict** - everything else (NCNN, ONNX, TFLite loaded through
        Ultralytics), where the engine owns pre/post-processing.

    Both paths are checked against each other in `tests/test_detector_backends.py`.
    """

    name = "ultralytics"

    def __init__(self, model: str = "yolo11n.pt", conf: float = 0.35, iou: float = 0.5,
                 imgsz: int = 640, person_class: int = 0, device: str = "auto") -> None:
        from ultralytics import YOLO  # heavy import, kept lazy

        self.model = YOLO(model)
        self.device = resolve_device(device)
        self.accelerator = self.device
        self.conf = conf
        self.iou = iou
        self.input_size = imgsz
        self.person_class = person_class
        self.model_name = model
        self._nms = None
        self._torch = None
        self.fast = False

        if str(model).endswith(".pt"):
            try:
                import torch

                try:
                    from ultralytics.utils.nms import non_max_suppression
                except ImportError:  # older Ultralytics kept it in utils.ops
                    from ultralytics.utils.ops import non_max_suppression

                self.model.fuse()
                self.model.model.to(self.device).eval()
                self._torch = torch
                self._nms = non_max_suppression
                self.fast = True
            except Exception:
                log.info("fast path unavailable for %s, using YOLO.predict()", model,
                         exc_info=True)

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self.fast:
            return self._detect_fast(image)
        return self._detect_predict(image)

    def _detect_fast(self, image: np.ndarray) -> list[Detection]:
        torch = self._torch
        height, width = image.shape[:2]
        padded, info = letterbox(image, self.input_size)
        rgb = np.ascontiguousarray(padded[:, :, ::-1])
        tensor = torch.from_numpy(rgb).to(self.device).permute(2, 0, 1).float().div_(255.0)
        tensor = tensor.unsqueeze(0)
        with torch.inference_mode():
            raw = self.model.model(tensor)
        if isinstance(raw, (list, tuple)):
            raw = raw[0]
        detections = self._nms(raw, self.conf, self.iou, classes=[self.person_class])[0]
        if detections is None or len(detections) == 0:
            return []
        values = detections.cpu().numpy()
        mapped = unletterbox_xyxy(values[:, :4], info, width, height)
        return [Detection((float(b[0]), float(b[1]), float(b[2]), float(b[3])),
                          float(row[4]), int(row[5]))
                for b, row in zip(mapped, values)]

    def _detect_predict(self, image: np.ndarray) -> list[Detection]:
        results = self.model.predict(
            image, imgsz=self.input_size, conf=self.conf, iou=self.iou,
            classes=[self.person_class], verbose=False, device=self.device,
        )
        out: list[Detection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            clss = boxes.cls.cpu().numpy().astype(int)
            for box, conf, cls in zip(xyxy, confs, clss):
                out.append(Detection(tuple(float(v) for v in box), float(conf), int(cls)))
        return out


def _load_qnn_delegate(lib: str):
    """Qualcomm QNN TFLite delegate on the HTP (NPU) backend."""
    try:
        from ai_edge_litert.interpreter import load_delegate
    except ImportError:
        from tflite_runtime.interpreter import load_delegate
    return load_delegate(lib, options={"backend_type": "htp"})


class LiteRTDetector(Detector):
    """TFLite detector.

    Handles both shapes of model we care about:

    * TFLite **Detection_PostProcess** graphs (EfficientDet-Lite0, SSD-MobileNet,
      Qualcomm's exported detectors): 4 outputs boxes/classes/scores/count, boxes
      normalised as (ymin, xmin, ymax, xmax).
    * YOLO-style exports: one output of shape (1, 4+nc, N) or (1, N, 4+nc).
    """

    name = "litert"

    def __init__(self, model: str, conf: float = 0.35, iou: float = 0.5,
                 person_class: int = 0, num_threads: int = 4,
                 letterbox_input: bool = True, qnn_lib: str | None = None,
                 require_accelerator: bool = False) -> None:
        # `letterbox_input=False` reproduces the legacy squash-to-square
        # preprocessing (audit S1) and exists only so `eval/legacy_baseline.py`
        # can measure what that cost us.  Never use it in production.
        self.letterbox_input = letterbox_input
        delegates = []
        self.accelerator = "cpu"
        if qnn_lib is not None:                     # litert_qnn: Hexagon NPU via the QNN delegate
            try:
                delegates = [_load_qnn_delegate(qnn_lib)]
                self.accelerator = "qnn-htp (LiteRT QNN delegate)"
            except Exception as error:              # noqa: BLE001 - any load failure means CPU
                if require_accelerator:
                    raise SystemExit(f"QNN delegate {qnn_lib!r} could not be loaded: {error}") from error
                log.warning("QNN delegate %s not available (%s): running on CPU", qnn_lib, error)
                self.accelerator = f"cpu (fallback: QNN delegate not loaded: {error})"
        self.interpreter = self._make_interpreter(model, num_threads, delegates)
        self.interpreter.allocate_tensors()
        self.input_detail = self.interpreter.get_input_details()[0]
        self.output_details = self.interpreter.get_output_details()
        shape = self.input_detail["shape"]
        # Models compiled by Qualcomm AI Hub keep our NCHW input (1, 3, H, W).
        self.channels_first = int(shape[1]) == 3 and int(shape[3]) != 3
        self.in_h, self.in_w = (int(shape[2]), int(shape[3])) if self.channels_first else \
            (int(shape[1]), int(shape[2]))
        self.input_size = max(self.in_h, self.in_w)
        self.conf = conf
        self.iou = iou
        self.person_class = person_class
        self.model_name = model
        self.postprocess = "tflite_detection" if len(self.output_details) >= 4 else "yolo"

    @staticmethod
    def _make_interpreter(model: str, num_threads: int, delegates: list | None = None):
        if not Path(model).is_file():
            raise SystemExit(f"TFLite model not found: {model}")
        try:  # the modern package (audit item S10: tf.lite.Interpreter is deprecated)
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            try:
                from tflite_runtime.interpreter import Interpreter
            except ImportError:
                try:
                    import tensorflow as tf
                    Interpreter = tf.lite.Interpreter
                except ImportError as error:
                    raise SystemExit(
                        "No TFLite runtime. Install one of: ai-edge-litert, tflite-runtime, tensorflow"
                    ) from error
        if delegates:
            return Interpreter(model_path=str(model), num_threads=num_threads,
                               experimental_delegates=delegates)
        return Interpreter(model_path=str(model), num_threads=num_threads)

    def _prepare(self, image: np.ndarray) -> tuple[np.ndarray, LetterboxInfo]:
        square = max(self.in_h, self.in_w)
        if self.letterbox_input:
            padded, info = letterbox(image, square)
            if (self.in_h, self.in_w) != (square, square):
                padded = cv2.resize(padded, (self.in_w, self.in_h))
        else:
            # Legacy behaviour: stretch the frame to the model's square input.
            # Expressed as a LetterboxInfo with no padding, so the rest of the
            # pipeline maps boxes back with one code path either way.  Note the
            # scale is anisotropic, which is exactly the bug - we keep the
            # x-scale here and correct y separately in `detect`.
            padded = cv2.resize(image, (self.in_w, self.in_h))
            height, width = image.shape[:2]
            info = LetterboxInfo(scale=square / width, pad_x=0.0, pad_y=0.0,
                                 scale_y=square / height)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        dtype = self.input_detail["dtype"]
        scale, zero_point = self.input_detail.get("quantization", (0.0, 0))
        if dtype == np.float32:
            tensor = (rgb.astype(np.float32) / 255.0)
        elif scale:
            info_dtype = np.iinfo(dtype)
            tensor = np.clip(np.round((rgb.astype(np.float32) / 255.0) / scale + zero_point),
                             info_dtype.min, info_dtype.max).astype(dtype)
        else:
            tensor = rgb.astype(dtype)
        if self.channels_first:
            tensor = np.transpose(tensor, (2, 0, 1))
        return tensor[None, ...], info

    def _dequant(self, index: int, detail: dict) -> np.ndarray:
        raw = self.interpreter.get_tensor(index)
        scale, zero_point = detail.get("quantization", (0.0, 0))
        if scale:
            return (raw.astype(np.float32) - zero_point) * scale
        return raw.astype(np.float32)

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        tensor, info = self._prepare(image)
        self.interpreter.set_tensor(self.input_detail["index"], tensor)
        self.interpreter.invoke()
        outputs = [self._dequant(d["index"], d) for d in self.output_details]

        if self.postprocess == "tflite_detection":
            boxes, classes, scores = self._pick_detection_outputs(outputs)
            square = max(self.in_h, self.in_w)
            out: list[Detection] = []
            for box, cls, score in zip(boxes, classes, scores):
                if score < self.conf or int(round(float(cls))) != self.person_class:
                    continue
                ymin, xmin, ymax, xmax = box
                # normalised in letterboxed space -> letterbox pixels -> frame
                pixel = np.array([xmin * square, ymin * square, xmax * square, ymax * square])
                x1, y1, x2, y2 = unletterbox_xyxy(pixel, info, width, height)
                if x2 > x1 and y2 > y1:
                    out.append(Detection((float(x1), float(y1), float(x2), float(y2)),
                                         float(score), self.person_class))
            return out

        return self._decode_yolo(merge_yolo_outputs(outputs), info, width, height)

    @staticmethod
    def _pick_detection_outputs(outputs: list[np.ndarray]):
        """Output order differs between converters; identify by shape."""
        boxes = classes = scores = None
        for array in outputs:
            squeezed = array[0] if array.ndim >= 2 else array
            if squeezed.ndim == 2 and squeezed.shape[-1] == 4:
                boxes = squeezed
            elif squeezed.ndim == 1 and squeezed.size > 1:
                if scores is None:
                    scores = squeezed
                else:
                    classes = squeezed
        if boxes is None:
            raise RuntimeError("could not identify TFLite detection outputs")
        n = len(boxes)
        if scores is None:
            scores = np.ones(n, dtype=np.float32)
        if classes is None:
            classes = np.zeros(n, dtype=np.float32)
        # Scores are monotonically decreasing in these graphs; classes are not.
        if len(scores) == n and len(classes) == n and scores[0] < classes[0]:
            scores, classes = classes, scores
        return boxes[:n], classes[:n], scores[:n]

    def _decode_yolo(self, raw: np.ndarray, info: LetterboxInfo, width: int, height: int) -> list[Detection]:
        pred = raw[0] if raw.ndim == 3 else raw
        if pred.shape[0] < pred.shape[1]:   # (4+nc, N) -> (N, 4+nc)
            pred = pred.T
        boxes_cxcywh = pred[:, :4]
        class_scores = pred[:, 4:]
        if class_scores.shape[1] == 0:
            return []
        cls_ids = class_scores.argmax(axis=1)
        confs = class_scores.max(axis=1)
        keep_mask = (confs >= self.conf) & (cls_ids == self.person_class)
        if not keep_mask.any():
            return []
        boxes_cxcywh = boxes_cxcywh[keep_mask]
        confs = confs[keep_mask]
        square = max(self.in_h, self.in_w)
        if boxes_cxcywh.max() <= 1.5:      # normalised export
            boxes_cxcywh = boxes_cxcywh * square
        xyxy = np.stack([
            boxes_cxcywh[:, 0] - boxes_cxcywh[:, 2] / 2,
            boxes_cxcywh[:, 1] - boxes_cxcywh[:, 3] / 2,
            boxes_cxcywh[:, 0] + boxes_cxcywh[:, 2] / 2,
            boxes_cxcywh[:, 1] + boxes_cxcywh[:, 3] / 2,
        ], axis=1)
        keep = nms(xyxy, confs, self.iou)
        mapped = unletterbox_xyxy(xyxy[keep], info, width, height)
        return [Detection(tuple(float(v) for v in box), float(conf), self.person_class)
                for box, conf in zip(mapped, confs[keep])]


class OnnxDetector(Detector):
    """ONNXRuntime backend (YOLO-style output).  Also the format we hand to
    Qualcomm AI Hub for compilation and profiling (see tools/qai_hub_profile.py)."""

    name = "onnx"

    def __init__(self, model: str, conf: float = 0.35, iou: float = 0.5,
                 imgsz: int = 640, person_class: int = 0, num_threads: int = 4,
                 qnn_lib: str | None = None, use_qnn: bool = False,
                 require_accelerator: bool = False) -> None:
        import onnxruntime as ort

        if not Path(model).is_file():
            raise SystemExit(f"ONNX model not found: {model}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = num_threads
        providers: list = ["CPUExecutionProvider"]
        reason = ""
        if use_qnn:                                   # ort_qnn: Hexagon NPU via the QNN EP
            if "QNNExecutionProvider" in ort.get_available_providers():
                lib = qnn_lib or ("QnnHtp.dll" if sys.platform == "win32" else "libQnnHtp.so")
                providers = [("QNNExecutionProvider", {"backend_path": lib}), "CPUExecutionProvider"]
            else:
                reason = "onnxruntime build has no QNNExecutionProvider (install onnxruntime-qnn)"
        self.session = ort.InferenceSession(model, options, providers=providers)
        if use_qnn and self.session.get_providers()[0] == "QNNExecutionProvider":
            self.accelerator = "qnn-htp (ORT QNN EP)"
        elif use_qnn:
            reason = reason or "QNN EP did not initialise"
            if require_accelerator:
                raise SystemExit(f"ort_qnn: {reason}")
            log.warning("ort_qnn: %s - running on CPU", reason)
            self.accelerator = f"cpu (fallback: {reason})"
        else:
            self.accelerator = "cpu"
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        static = [s for s in shape[2:] if isinstance(s, int)]
        self.input_size = static[0] if static else imgsz
        self.conf = conf
        self.iou = iou
        self.person_class = person_class
        self.model_name = model

    def detect(self, image: np.ndarray) -> list[Detection]:
        height, width = image.shape[:2]
        padded, info = letterbox(image, self.input_size)
        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        tensor = np.transpose(rgb, (2, 0, 1))[None, ...]
        raw = merge_yolo_outputs(self.session.run(None, {self.input_name: tensor}))
        pred = raw[0] if raw.ndim == 3 else raw
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T
        class_scores = pred[:, 4:]
        if class_scores.shape[1] == 0:
            return []
        cls_ids = class_scores.argmax(axis=1)
        confs = class_scores.max(axis=1)
        mask = (confs >= self.conf) & (cls_ids == self.person_class)
        if not mask.any():
            return []
        cxcywh = pred[mask, :4]
        confs = confs[mask]
        xyxy = np.stack([
            cxcywh[:, 0] - cxcywh[:, 2] / 2, cxcywh[:, 1] - cxcywh[:, 3] / 2,
            cxcywh[:, 0] + cxcywh[:, 2] / 2, cxcywh[:, 1] + cxcywh[:, 3] / 2,
        ], axis=1)
        keep = nms(xyxy, confs, self.iou)
        mapped = unletterbox_xyxy(xyxy[keep], info, width, height)
        return [Detection(tuple(float(v) for v in box), float(conf), self.person_class)
                for box, conf in zip(mapped, confs[keep])]


def build_detector(config) -> Detector:
    """`config` is a `storemind.core.config.DetectorConfig`."""
    backend = config.backend
    if backend == "stub":
        return StubDetector()
    if backend == "scripted":
        if not config.model:
            # Each camera builds its own from the clip beside it (see
            # Pipeline._per_camera_detector); this shared one sees nothing.
            return StubDetector()
        from .scripted import ScriptedDetector
        return ScriptedDetector(config.model)
    if backend == "ultralytics":
        return UltralyticsDetector(config.model, conf=config.conf, iou=config.iou,
                                   imgsz=config.imgsz, person_class=config.person_class)
    if backend == "litert":
        return LiteRTDetector(config.model, conf=config.conf, iou=config.iou,
                              person_class=config.person_class, num_threads=config.num_threads)
    if backend == "onnx":
        return OnnxDetector(config.model, conf=config.conf, iou=config.iou,
                            imgsz=config.imgsz, person_class=config.person_class,
                            num_threads=config.num_threads)
    if backend == "litert_qnn":
        return LiteRTDetector(config.model, conf=config.conf, iou=config.iou,
                              person_class=config.person_class, num_threads=config.num_threads,
                              qnn_lib=getattr(config, "qnn_lib", None) or "libQnnTFLiteDelegate.so",
                              require_accelerator=getattr(config, "require_accelerator", False))
    if backend == "ort_qnn":
        return OnnxDetector(config.model, conf=config.conf, iou=config.iou,
                            imgsz=config.imgsz, person_class=config.person_class,
                            num_threads=config.num_threads, use_qnn=True,
                            qnn_lib=getattr(config, "qnn_lib", None),
                            require_accelerator=getattr(config, "require_accelerator", False))
    raise SystemExit(f"unknown detector backend: {backend}")
