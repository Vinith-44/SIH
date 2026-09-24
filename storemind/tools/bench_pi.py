"""Detector speed, temperature, throttling and energy on the Raspberry Pi 5 (M8).

Run this ON THE PI (after `docs/SETUP_PI5.md`), plugged into its official 27 W
supply, with nothing else running.  It prints and saves one JSON file that is
the source for every Pi number in RESULTS.md (bucket S: speed/energy, no
accuracy).  Paste nothing by hand: commit the JSON.

For each `backend:model` it measures, back to back on the same frames:
*   latency median / p95 (ms), FPS, detections per frame;
*   CPU temperature at start / max, and `vcgencmd get_throttled` flags;
*   power from the Pi 5 PMIC (`vcgencmd pmic_read_adc`, sum of V x I over the
    rails), corrected with the published fit *real W ~= 1.1451 x PMIC W + 0.5879*
    (the PMIC misses USB/HAT loads - research/23 section 4.5), idle vs busy;
*   **mJ per frame** = busy power (W) x mean latency (ms).

    python tools/bench_pi.py \
        --models ultralytics:../models/yolo11n_ncnn_model onnx:../models/yolo11n_int8.onnx \
                 litert:../models/yolo11n_saved_model/yolo11n_int8.tflite ultralytics:../models/yolo11n.pt \
        --source ../videos/other/vtest.avi --frames 200

Off the Pi it still runs (telemetry reads "not available"), which is how it is tested.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PMIC_CORRECTION = (1.1451, 0.5879)          # real W = a * PMIC W + b  (jfikar/RPi5-power)
THROTTLE_BITS = {0: "under-voltage now", 1: "frequency capped now", 2: "throttled now",
                 3: "soft temperature limit now", 16: "under-voltage occurred",
                 17: "frequency capping occurred", 18: "throttling occurred",
                 19: "soft temperature limit occurred"}


# --------------------------------------------------------------------------- #
# Telemetry parsers (pure functions - unit tested)
# --------------------------------------------------------------------------- #

def parse_pmic(text: str) -> float | None:
    """Sum of V x I over rails from `vcgencmd pmic_read_adc` (uncorrected W)."""
    currents, volts = {}, {}
    for name, kind, value in re.findall(r"(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)[AV]", text):
        (currents if kind == "A" else volts)[name] = float(value)
    rails = currents.keys() & volts.keys()
    if not rails:
        return None
    return sum(currents[r] * volts[r] for r in rails)


def corrected_watts(pmic_w: float | None) -> float | None:
    if pmic_w is None:
        return None
    a, b = PMIC_CORRECTION
    return a * pmic_w + b


def parse_temp(text: str) -> float | None:
    """`temp=52.1'C` (vcgencmd) or `52100` (sysfs millidegrees)."""
    match = re.search(r"temp=([\d.]+)", text)
    if match:
        return float(match.group(1))
    text = text.strip()
    return int(text) / 1000.0 if text.isdigit() else None


def parse_throttled(text: str) -> list[str]:
    match = re.search(r"throttled=(0x[0-9a-fA-F]+)", text)
    if not match:
        return []
    value = int(match.group(1), 16)
    return [label for bit, label in THROTTLE_BITS.items() if value & (1 << bit)]


def _run(cmd: list[str]) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def read_temp() -> float | None:
    value = parse_temp(_run(["vcgencmd", "measure_temp"]))
    if value is None and Path("/sys/class/thermal/thermal_zone0/temp").is_file():
        value = parse_temp(Path("/sys/class/thermal/thermal_zone0/temp").read_text())
    return value


def read_power() -> float | None:
    return corrected_watts(parse_pmic(_run(["vcgencmd", "pmic_read_adc"])))


def device_name() -> str:
    model = Path("/proc/device-tree/model")
    if model.is_file():
        return model.read_text(errors="ignore").strip("\x00 \n")
    return f"{platform.node()} ({platform.processor() or platform.machine()})"


class Sampler(threading.Thread):
    """Samples power and temperature every `period_s` while a benchmark runs."""

    def __init__(self, period_s: float = 0.5) -> None:
        super().__init__(daemon=True)
        self.period_s = period_s
        self.power: list[float] = []
        self.temps: list[float] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            watts, temp = read_power(), read_temp()
            if watts is not None:
                self.power.append(watts)
            if temp is not None:
                self.temps.append(temp)
            self._halt.wait(self.period_s)

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2)


# --------------------------------------------------------------------------- #

def load_frames(source: str, count: int):
    import cv2

    capture = cv2.VideoCapture(source)
    frames = []
    while len(frames) < count:
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
            if not ok:
                break
        frames.append(frame)
    capture.release()
    if not frames:
        raise SystemExit(f"could not read frames from {source}")
    return frames


def bench(spec: str, frames, imgsz: int, threads: int, warmup: int, conf: float) -> dict:
    from storemind.core.config import DetectorConfig
    from storemind.inference.detector import build_detector

    backend, _, model = spec.partition(":")
    detector = build_detector(DetectorConfig(backend=backend, model=model, imgsz=imgsz, conf=conf,
                                             num_threads=threads))
    for frame in frames[:warmup]:
        detector.detect(frame)
    temp_start = read_temp()
    sampler = Sampler()
    sampler.start()
    times, detections = [], 0
    for frame in frames:
        started = time.perf_counter()
        found = detector.detect(frame)
        times.append((time.perf_counter() - started) * 1000.0)
        detections += len(found)
    sampler.stop()
    ms_mean = statistics.mean(times)
    busy_w = statistics.mean(sampler.power) if sampler.power else None
    return {
        "backend": backend, "model": model, "imgsz": imgsz, "threads": threads, "frames": len(frames),
        "ms_median": round(statistics.median(times), 2),
        "ms_p95": round(sorted(times)[int(0.95 * (len(times) - 1))], 2),
        "ms_mean": round(ms_mean, 2), "fps": round(1000.0 / ms_mean, 2),
        "detections_per_frame": round(detections / len(frames), 2),
        "temp_c_start": temp_start, "temp_c_max": max(sampler.temps) if sampler.temps else None,
        "throttled": parse_throttled(_run(["vcgencmd", "get_throttled"])),
        "power_w_busy": round(busy_w, 3) if busy_w is not None else None,
        "mj_per_frame": round(busy_w * ms_mean, 1) if busy_w is not None else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", required=True, help="backend:path, e.g. onnx:../models/yolo11n_int8.onnx")
    parser.add_argument("--source", default="../videos/other/vtest.avi")
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--idle-s", type=float, default=10.0, help="idle power sampling before the runs")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    frames = load_frames(args.source, args.frames)
    idle = Sampler()
    idle.start()
    time.sleep(args.idle_s)
    idle.stop()
    report = {
        "bucket": "S", "device": device_name(), "os": platform.platform(), "python": platform.python_version(),
        "cpu_count": os.cpu_count(), "date": datetime.now().isoformat(timespec="seconds"),
        "source": args.source, "power_correction": "real W = 1.1451 x PMIC W + 0.5879",
        "power_w_idle": round(statistics.mean(idle.power), 3) if idle.power else None,
        "temp_c_idle": statistics.mean(idle.temps) if idle.temps else None,
        "command": "python tools/bench_pi.py " + " ".join(argv if argv is not None else sys.argv[1:]),
        "results": [],
    }
    for spec in args.models:
        print(f"--- {spec}", flush=True)
        try:
            row = bench(spec, frames, args.imgsz, args.threads, args.warmup, args.conf)
        except (SystemExit, Exception) as error:              # one broken model must not lose the rest
            row = {"backend": spec.partition(":")[0], "model": spec.partition(":")[2], "error": str(error)}
        report["results"].append(row)
        print(json.dumps(row), flush=True)
    out = Path(args.out) if args.out else (Path(__file__).resolve().parents[1] / "storemind" / "eval" / "results"
                                          / "pi" / f"bench_{platform.node()}_{datetime.now():%Y%m%d_%H%M}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
