"""Shared helpers for the evaluation harness.

Every number that appears in `RESULTS.md`, in `HANDOFF_FOR_CLAUDE.md` or on a
slide is produced by one of the scripts in this package, from a recorded command
that anybody on the team can re-run.  That is the whole point: research/01
section 2 lists "no accuracy numbers anywhere" as our biggest weakness, and the
fix is not better claims, it is a harness.

Ground truth is discovered by filename: a clip `x.mp4` is evaluated against
`x_gt_entries.csv`, `x_gt_queue_length.csv`, `x_gt_waits.csv`, `x_gt_slots.csv`
in the same folder.  Missing ground truth is reported as "not measured yet",
never silently skipped.
"""

from __future__ import annotations

import csv
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import StoreMindConfig, load_config
from ..core.events import Event, EventType
from ..pipeline import Pipeline
from ..store.db import EventStore


# --------------------------------------------------------------------------- #
# Ground truth
# --------------------------------------------------------------------------- #

def gt_path(video: Path, kind: str) -> Path:
    return video.with_name(f"{video.stem}_gt_{kind}.csv")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_entries_gt(video: Path) -> dict[str, Any] | None:
    rows = read_csv(gt_path(video, "entries"))
    if not rows:
        return None
    crossings = [(float(r["video_time_s"]), r["direction"].strip().lower()) for r in rows]
    return {
        "crossings": sorted(crossings),
        "entries": sum(1 for _, d in crossings if d == "in"),
        "exits": sum(1 for _, d in crossings if d == "out"),
        "source": str(gt_path(video, "entries")),
    }


def load_queue_length_gt(video: Path) -> dict[str, list[tuple[float, float]]] | None:
    rows = read_csv(gt_path(video, "queue_length"))
    if not rows:
        return None
    out: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        out.setdefault(row["counter"].strip(), []).append(
            (float(row["video_time_s"]), float(row["queue_length"])))
    for series in out.values():
        series.sort()
    return out


def load_waits_gt(video: Path) -> list[dict[str, float | str]] | None:
    rows = read_csv(gt_path(video, "waits"))
    if not rows:
        return None
    return [{"customer": r["customer"], "counter": r["counter"].strip(),
             "joined_s": float(r["joined_s"]), "service_start_s": float(r["service_start_s"]),
             "service_s": float(r["service_s"]), "wait_s": float(r["wait_s"])}
            for r in rows]


def load_slots_gt(video: Path) -> dict[tuple[str, str], list[tuple[float, str]]] | None:
    rows = read_csv(gt_path(video, "slots"))
    if not rows:
        return None
    out: dict[tuple[str, str], list[tuple[float, str]]] = {}
    for row in rows:
        key = (row["shelf"].strip(), row["slot"].strip())
        out.setdefault(key, []).append((float(row["video_time_s"]), row["state"].strip().upper()))
    for series in out.values():
        series.sort()
    return out


def state_at(series: list[tuple[float, str]], t: float) -> str | None:
    """Ground-truth slot state at time `t` (state changes are step functions)."""
    current = None
    for when, state in series:
        if when <= t:
            current = state
        else:
            break
    return current


# --------------------------------------------------------------------------- #
# Running the pipeline for evaluation
# --------------------------------------------------------------------------- #

@dataclass
class RunResult:
    events: list[Event]
    summary: dict
    config: StoreMindConfig
    command: str = ""
    events_by_type: dict[str, list[Event]] = field(default_factory=dict)
    pipeline: Any = None

    def checkout_arrivals(self) -> list[float]:
        """Clip-seconds at which a track joined any checkout lane.

        This is exactly what the live pipeline feeds the forecaster, so the
        evaluation cannot accidentally grade a different signal."""
        out: list[float] = []
        if self.pipeline is None:
            return out
        for camera in self.pipeline.cameras:
            if camera.queue is not None:
                out += list(camera.queue.checkout_arrivals)
        return sorted(out)

    def of(self, event_type: EventType) -> list[Event]:
        return self.events_by_type.get(event_type.value, [])


class _NullStore(EventStore):
    """Evaluation runs must not pollute the demo database."""

    def __init__(self) -> None:  # noqa: D107 - deliberately does not call super
        self.written = 0
        self.store = "eval"

    def handle(self, event: Event) -> None:
        self.written += 1

    def flush(self, timeout: float = 5.0) -> None:
        return None

    def close(self) -> None:
        return None

    def counts_by_type(self) -> dict[str, int]:
        return {}


def run_pipeline(config_path: str | Path, *, camera: str, source: str | Path | None = None,
                 backend: str | None = None, model: str | None = None,
                 fps: float | None = None, imgsz: int | None = None,
                 max_seconds: float | None = None) -> RunResult:
    """Run one camera of a config over a clip and collect the events in memory."""
    config = load_config(config_path)
    config.cameras = [c for c in config.cameras if c.name == camera]
    if not config.cameras:
        raise SystemExit(f"camera {camera!r} not in {config_path}")
    if source is not None:
        config.cameras[0].source = str(source)
    if fps is not None:
        config.cameras[0].fps = fps
        if config.cameras[0].role == "shelf":
            config.cameras[0].shelf_period_s = 1.0 / fps
    if backend:
        config.detector.backend = backend
    if model:
        config.detector.model = str(model)
    if imgsz:
        config.detector.imgsz = imgsz
    config.alerts.console = False
    config.alerts.sound = False
    config.alerts.voice_enabled = False

    collected: list[Event] = []
    pipeline = Pipeline(config, replay=True, show=False, store=_NullStore())
    pipeline.bus.subscribe_all(collected.append)
    for cam in config.cameras:
        for zone in cam.zones:
            if zone.shelf and zone.slot:
                price = sku = None
                for shelf in cam.shelves:
                    for slot in shelf.slots:
                        if shelf.name == zone.shelf and slot.name == zone.slot:
                            price, sku = slot.price, slot.sku
                pipeline.fusion.register_zone(zone.name, zone.shelf, zone.slot, sku, price)

    summary = pipeline.run(max_seconds=max_seconds, progress=False)
    by_type: dict[str, list[Event]] = {}
    for event in collected:
        by_type.setdefault(event.type.value, []).append(event)

    command = (f"python -m storemind.run --config {config_path} --camera {camera}"
               + (f" --source {source}" if source else "")
               + (f" --backend {backend}" if backend else "")
               + (f" --model {model}" if model else "")
               + (f" --fps {fps}" if fps else ""))
    return RunResult(events=collected, summary=summary, config=config,
                     command=command, events_by_type=by_type, pipeline=pipeline)


def event_time(event: Event, start) -> float:
    """Seconds into the clip for an event produced by a VideoClock run."""
    return (event.dt - start).total_seconds()


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def machine_specs() -> dict[str, str]:
    specs = {
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
    }
    try:
        import psutil

        specs["cpu_cores"] = str(psutil.cpu_count(logical=False))
        specs["cpu_threads"] = str(psutil.cpu_count(logical=True))
        specs["ram_gb"] = f"{psutil.virtual_memory().total / 1e9:.1f}"
    except Exception:
        pass
    try:
        import cv2

        specs["opencv"] = cv2.__version__
    except Exception:
        pass
    try:
        specs["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[2]).stdout.strip() or "unknown"
    except Exception:
        specs["git_commit"] = "unknown"
    return specs


def mae(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum(abs(a - b) for a, b in pairs) / len(pairs)


def accuracy_from_counts(predicted: int, truth: int) -> float | None:
    """1 - |pred - gt| / gt, clamped at 0.  Undefined when the truth is zero."""
    if truth <= 0:
        return None
    return max(0.0, 1.0 - abs(predicted - truth) / truth)


def match_events(predicted: list[float], truth: list[float], tolerance_s: float
                 ) -> tuple[int, int, int, list[float]]:
    """Greedy nearest matching within `tolerance_s`.

    Returns (true positives, false positives, false negatives, timing errors).
    """
    remaining = sorted(truth)
    used = [False] * len(remaining)
    tp = 0
    errors: list[float] = []
    for p in sorted(predicted):
        best_index, best_delta = None, None
        for i, t in enumerate(remaining):
            if used[i]:
                continue
            delta = abs(p - t)
            if delta <= tolerance_s and (best_delta is None or delta < best_delta):
                best_index, best_delta = i, delta
        if best_index is None:
            continue
        used[best_index] = True
        tp += 1
        errors.append(best_delta)
    return tp, len(predicted) - tp, len(remaining) - tp, errors


def prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision and recall and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0 if (precision is not None and recall is not None) else None
    return {"precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn}


def fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "not measured yet"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def pct(value) -> str:
    return "not measured yet" if value is None else f"{value * 100:.1f}%"


# --------------------------------------------------------------------------- #
# RESULTS.md sections
# --------------------------------------------------------------------------- #

BUCKETS = {
    "A": "public benchmark with published ground truth",
    "B": "our own field recording, hand-labelled",
    "C": "simulation - logic validation only, NOT an accuracy measurement",
}


class Section:
    """One block of RESULTS.md.

    Carries its data bucket with it so a row can never reach a slide without
    saying what kind of data produced it (research/09b).
    """

    def __init__(self, title: str, bucket: str, note: str = "") -> None:
        self.title = title
        self.bucket = bucket
        self.note = note
        self.rows: list[tuple[str, str, str]] = []
        self.commands: list[str] = []
        self.failed: str | None = None

    def row(self, metric: str, value: str, target: str = "") -> None:
        self.rows.append((metric, value, target))

    def markdown(self) -> str:
        out = [f"### {self.title}", "",
               f"**Data bucket {self.bucket}** - {BUCKETS[self.bucket]}", ""]
        if self.note:
            out += [self.note, ""]
        if self.failed:
            out += [f"> Did not run: `{self.failed}`", ""]
            return chr(10).join(out)
        if self.rows:
            out += ["| metric | result | target |", "|---|---|---|"]
            out += [f"| {m} | {v} | {t} |" for m, v, t in self.rows]
            out += [""]
        if self.commands:
            out += ["<details><summary>commands</summary>", "", "```bash"]
            out += self.commands
            out += ["```", "", "</details>", ""]
        return chr(10).join(out)
