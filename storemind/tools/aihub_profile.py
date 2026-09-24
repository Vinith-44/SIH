"""Compile, quantize, profile and run our detector on Qualcomm AI Hub hosted devices (M9).

Bucket Q: every number this writes comes from a device **hosted by Qualcomm AI
Hub**, not from a board we own.  Say "AI Hub hosted RB3 Gen 2 (QCS6490)", never
"our device".

Per model (ONNX from `storemind.inference.export`) and per device:
1.  **FP32**: compile to TFLite (LiteRT) -> profile.
2.  **INT8 (w8a8)**: AI Hub quantize job with our calibration frames -> compile
    to TFLite -> profile.  The Hexagon NPU runs integer models; the profile's
    per-layer compute unit (NPU / GPU / CPU) shows how much of the network
    actually landed on it.
3.  **Inference job** on the first device: real frames through the compiled
    model, decoded exactly like `OnnxDetector`, compared with the local ONNX
    FP32 model (person boxes matched at IoU >= 0.5).
4.  The compiled .tflite files are downloaded to `models/` (git-ignored):
    `<stem>_aihub_fp32.tflite`, `<stem>_aihub_w8a8.tflite` - usable locally with
    `backend: litert`, and on a QCS6490 with the QNN delegate.

The API token comes from `qai-hub configure` (never from this repo).

    PYTHONIOENCODING=utf-8 python tools/aihub_profile.py \
        --models ../models/yolo11n.onnx ../models/yolo26n.onnx \
        --devices "Dragonwing RB3 Gen 2 Vision Kit" "QCS8550 (Proxy)" --calib <folder of jpgs>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storemind.inference.detector import letterbox, merge_yolo_outputs, nms, unletterbox_xyxy  # noqa: E402

RESULTS = Path(__file__).resolve().parents[1] / "storemind" / "eval" / "results"
IMGSZ = 640


def log(*args) -> None:
    print(f"[{datetime.now():%H:%M:%S}]", *args, flush=True)


def clean_onnx(path: Path) -> Path:
    """Prepare an Ultralytics ONNX export for AI Hub:

    *   drop value_info entries that duplicate graph outputs (an onnxslim
        artifact AI Hub's validator rejects);
    *   **split the head**: remove the final Concat(boxes, scores) and expose
        `boxes` (1, 4, N) and `scores` (1, C, N) as two outputs.  With one
        output, INT8 quantization uses one scale for 0-640 px boxes and 0-1
        scores and every score becomes 0 (measured: 0 detections on the first
        run).  The host concatenates them again (`merge_yolo_outputs`).
    """
    import onnx
    from onnx import helper

    model = onnx.load(str(path))
    graph = model.graph
    producer = {o: n for n in graph.node for o in n.output}
    last = producer[graph.output[0].name]
    if last.op_type == "Concat" and len(last.input) == 2:
        box_in, score_in = last.input
        graph.node.remove(last)
        graph.node.extend([helper.make_node("Identity", [box_in], ["boxes"]),
                           helper.make_node("Identity", [score_in], ["scores"])])
        n_anchor = graph.output[0].type.tensor_type.shape.dim[2].dim_value
        n_classes = graph.output[0].type.tensor_type.shape.dim[1].dim_value - 4
        del graph.output[:]
        graph.output.extend([
            helper.make_tensor_value_info("boxes", onnx.TensorProto.FLOAT, [1, 4, n_anchor]),
            helper.make_tensor_value_info("scores", onnx.TensorProto.FLOAT, [1, n_classes, n_anchor])])
    io = {t.name for t in list(graph.input) + list(graph.output)}
    keep = [v for v in graph.value_info if v.name not in io]
    del graph.value_info[:]
    graph.value_info.extend(keep)
    onnx.checker.check_model(model)
    out = path.with_name(f"{path.stem}_aihub.onnx")
    onnx.save(model, str(out))
    return out


def tensor(image: np.ndarray) -> tuple[np.ndarray, tuple]:
    padded, info = letterbox(image, IMGSZ)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.transpose(rgb, (2, 0, 1))[None, ...], info


def decode(raw: np.ndarray, info, width: int, height: int, conf: float = 0.25, iou: float = 0.5):
    """Same post-processing as `OnnxDetector.detect` (person class 0)."""
    pred = raw[0] if raw.ndim == 3 else raw
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T
    scores = pred[:, 4:]
    cls, confs = scores.argmax(axis=1), scores.max(axis=1)
    mask = (confs >= conf) & (cls == 0)
    if not mask.any():
        return []
    c = pred[mask, :4]
    xyxy = np.stack([c[:, 0] - c[:, 2] / 2, c[:, 1] - c[:, 3] / 2, c[:, 0] + c[:, 2] / 2, c[:, 1] + c[:, 3] / 2], 1)
    keep = nms(xyxy, confs[mask], iou)
    return [tuple(b) for b in unletterbox_xyxy(xyxy[keep], info, width, height)]


def agreement(reference: list[list], candidate: list[list]) -> dict:
    def iou(a, b):
        inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0

    matched = ref_total = cand_total = 0
    for ref, cand in zip(reference, candidate):
        used = set()
        for r in ref:
            best = max(((i, iou(r, c)) for i, c in enumerate(cand) if i not in used), key=lambda x: x[1],
                       default=(None, 0))
            if best[0] is not None and best[1] >= 0.5:
                used.add(best[0])
                matched += 1
        ref_total += len(ref)
        cand_total += len(cand)
    return {"recall_vs_local_fp32": matched / ref_total if ref_total else None,
            "precision_vs_local_fp32": matched / cand_total if cand_total else None,
            "boxes_reference": ref_total, "boxes_device": cand_total}


def profile_summary(job) -> dict:
    profile = job.download_profile()
    summary = profile["execution_summary"]
    units = Counter(d.get("compute_unit") for d in profile.get("execution_detail", []))
    return {"profile_job": job.job_id, "profile_url": job.url,
            "inference_ms": round(summary["estimated_inference_time"] / 1000.0, 3),
            "peak_memory_mb": round(summary.get("estimated_inference_peak_memory", 0) / 1e6, 1),
            "first_load_ms": round(summary.get("first_load_time", 0) / 1000.0, 1),
            "layers_by_unit": dict(units),
            "npu_share": round(units.get("NPU", 0) / max(1, sum(units.values())), 3)}


def main(argv: list[str] | None = None) -> int:
    import onnxruntime as ort
    import qai_hub as hub

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--devices", nargs="+", default=["Dragonwing RB3 Gen 2 Vision Kit"])
    parser.add_argument("--calib", required=True, help="folder of calibration JPEGs")
    parser.add_argument("--calib-count", type=int, default=20)
    parser.add_argument("--frames", default="../videos/other/vtest.avi")
    parser.add_argument("--infer-count", type=int, default=6)
    parser.add_argument("--out", default=str(RESULTS / "qualcomm_aihub.json"))
    parser.add_argument("--append", action="store_true", help="add to an existing results file")
    args = parser.parse_args(argv)

    calib = [tensor(cv2.imread(str(p)))[0] for p in sorted(Path(args.calib).glob("*.jpg"))[:args.calib_count]]
    capture = cv2.VideoCapture(args.frames)
    frames = []
    for index in range(0, 600, 600 // args.infer_count):
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if ok:
            frames.append(frame)
    capture.release()
    prepared = [tensor(f) for f in frames]
    log(f"uploading {len(calib)} calibration and {len(prepared)} test tensors (once)")
    calib_ds = hub.upload_dataset({"images": calib}, name="storemind-calibration")
    infer_ds = hub.upload_dataset({"images": [t for t, _ in prepared]}, name="storemind-vtest")

    devices = [hub.Device(name) for name in args.devices]
    report = {"bucket": "Q", "note": "Qualcomm AI Hub hosted devices - not our own board",
              "date": datetime.now().isoformat(timespec="seconds"), "devices": args.devices,
              "command": "python tools/aihub_profile.py " + " ".join(argv if argv is not None else sys.argv[1:]),
              "results": []}
    if args.append and Path(args.out).is_file():
        previous = json.loads(Path(args.out).read_text(encoding="utf-8"))
        report["results"] = previous["results"]
        report["command"] = previous["command"] + " && " + report["command"]
    for model_path in map(Path, args.models):
        stem = model_path.stem
        cleaned = clean_onnx(model_path)
        local = ort.InferenceSession(str(cleaned), providers=["CPUExecutionProvider"])
        reference = [decode(merge_yolo_outputs(local.run(None, {"images": t})), info, f.shape[1], f.shape[0])
                     for (t, info), f in zip(prepared, frames)]
        log(f"{stem}: quantize job (w8a8)")
        quant = hub.submit_quantize_job(model=str(cleaned), calibration_data=calib_ds,
                                        weights_dtype=hub.QuantizeDtype.INT8,
                                        activations_dtype=hub.QuantizeDtype.INT8,
                                        name=f"storemind-{stem}-w8a8")
        sources = {"fp32": str(cleaned), "w8a8": quant.get_target_model()}
        for precision, source in sources.items():
            if source is None:
                report["results"].append({"model": stem, "precision": precision, "error": "quantize job failed"})
                continue
            for i, device in enumerate(devices):
                row = {"model": stem, "precision": precision, "device": device.name}
                started = time.time()
                compile_job = hub.submit_compile_job(model=source, device=device, options="--target_runtime tflite",
                                                     input_specs=dict(images=(1, 3, IMGSZ, IMGSZ)),
                                                     name=f"storemind-{stem}-{precision}-tflite")
                row.update(compile_job=compile_job.job_id, compile_url=compile_job.url)
                compiled = compile_job.get_target_model()
                if compiled is None:
                    row["error"] = f"compile failed: {compile_job.get_status().message}"
                    report["results"].append(row)
                    log(row)
                    continue
                if i == 0:
                    compiled.download(str(model_path.with_name(f"{stem}_aihub_{precision}.tflite")))
                profile = hub.submit_profile_job(model=compiled, device=device,
                                                 name=f"storemind-{stem}-{precision}-profile")
                if profile.wait().success:
                    row.update(profile_summary(profile))
                else:
                    row["error"] = f"profile failed: {profile.get_status().message}"
                if i == 0:                                  # check outputs once, on the main device
                    inference = hub.submit_inference_job(model=compiled, device=device, inputs=infer_ds,
                                                         name=f"storemind-{stem}-{precision}-inference")
                    if inference.wait().success:
                        data = inference.download_output_data()          # {name: [per-frame arrays]}
                        per_frame = list(zip(*[data[k] for k in sorted(data)]))
                        device_boxes = [decode(merge_yolo_outputs(list(o)), info, f.shape[1], f.shape[0])
                                        for o, (_t, info), f in zip(per_frame, prepared, frames)]
                        row.update(inference_job=inference.job_id, **agreement(reference, device_boxes))
                row["wall_s"] = round(time.time() - started)
                report["results"].append(row)
                log(row)
                Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")   # keep partial results
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
