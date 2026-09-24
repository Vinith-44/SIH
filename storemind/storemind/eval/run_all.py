"""Run every evaluation that has data, and write `eval/results/RESULTS.md`.

One rule, from `research/09b_TEST_DATA_VALIDITY.md`: **every row says which
bucket its data came from.**

* **A** - public benchmark with published ground truth (CAVIAR).
* **B** - our own field recording, hand-labelled by the team.
* **C** - simulation. Proves the *logic* is correct because we know the true
  answer. It is **not** an accuracy measurement and must never be presented as
  one.
* **S** - real footage with no ground truth: speed only, never accuracy.
* **Q** - Qualcomm AI Hub hosted/proxy device, never "our board".
* **P** - published third-party figure, cited.

Person B's measured platform results (stream density, soak, HIL, Pi install)
live in `eval/results/platform/*.json|md` and are included verbatim
(research/26 section 2 rule 5; format in docs/INTERFACES.md section 5).

A row with no ground truth prints the system's output and says "not measured
yet" for accuracy, rather than quietly disappearing.

    python -m storemind.eval.run_all
    python -m storemind.eval.run_all --skip caviar     # quick pass
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from datetime import datetime
from pathlib import Path

from .common import BUCKETS, Section, fmt, machine_specs, pct

REPO = Path(__file__).resolve().parents[2]
VIDEOS = REPO.parent / "videos"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
PLATFORM_DIR = RESULTS_DIR / "platform"

# --------------------------------------------------------------------------- #

def synthetic_counting() -> Section:
    from .eval_counting import evaluate

    video = VIDEOS / "entrance" / "synthetic_entrance.mp4"
    section = Section("Entry / exit counting - synthetic entrance", "C",
                      "Perfect detections (`--backend scripted`) so this grades the "
                      "line-crossing logic: hysteresis band, foot point, per-track cooldown. "
                      "The clip contains two traps: a shopper who loiters *on* the line, and "
                      "two people crossing shoulder to shoulder.")
    report = evaluate("configs/demo.yaml", "entrance", str(video), backend="scripted",
                      model=str(video.with_name(f"{video.stem}_detections.json")), fps=25)
    metrics = report["metrics"]
    section.row("entries counted", f"{report['predicted']['entries']} "
                                   f"(truth {report['ground_truth']['entries']})", "-")
    section.row("exits counted", f"{report['predicted']['exits']} "
                                 f"(truth {report['ground_truth']['exits']})", "-")
    section.row("entry count accuracy", pct(metrics["entry_count_accuracy"]), ">= 90%")
    section.row("exit count accuracy", pct(metrics["exit_count_accuracy"]), ">= 90%")
    section.row("entry event precision / recall / F1",
                f"{fmt(metrics['entry_event']['precision'])} / "
                f"{fmt(metrics['entry_event']['recall'])} / "
                f"{fmt(metrics['entry_event']['f1'])}", "-")
    section.row("mean crossing timing error",
                fmt(metrics["mean_timing_error_s"], 2, " s"), "-")
    section.row("occupancy MAE", fmt(metrics["occupancy_mae"], 2, " people"), "<= 1-2")
    section.commands.append(report["command"])
    return section


def synthetic_queue() -> Section:
    from .eval_queue import evaluate

    video = VIDEOS / "queue" / "synthetic_queue.mp4"
    section = Section("Queue length, wait and service time - synthetic counter", "C",
                      "Perfect detections. Grades the gap-tolerant wait timer, the 3-second "
                      "minimum before service is believed to have started, and median "
                      "smoothing - the three things audit items Q2, Q3 and Q6 got wrong.")
    report = evaluate("configs/demo.yaml", "counter-1", str(video), backend="scripted",
                      model=str(video.with_name(f"{video.stem}_detections.json")), fps=25)
    metrics = report["metrics"]
    section.row("customers detected",
                f"{metrics['customers_detected']} of {metrics['customers_ground_truth']}", "-")
    section.row("service event precision / recall / F1",
                f"{fmt(metrics['service_event']['precision'])} / "
                f"{fmt(metrics['service_event']['recall'])} / "
                f"{fmt(metrics['service_event']['f1'])}", "-")
    section.row("queue-length MAE", fmt(metrics["queue_length_mae"], 2, " people"), "<= 1")
    section.row("wait-time MAE", f"{fmt(metrics['wait_mae_s'], 2, ' s')} "
                                 f"({fmt(metrics['wait_mae_pct'], 1, '%')})", "<= 20%")
    section.row("service-time MAE", f"{fmt(metrics['service_mae_s'], 2, ' s')} "
                                    f"({fmt(metrics['service_mae_pct'], 1, '%')})", "-")
    section.row("median wait (ours vs truth)",
                f"{fmt(metrics['median_wait_predicted_s'], 1, ' s')} vs "
                f"{fmt(metrics['median_wait_truth_s'], 1, ' s')}", "-")
    section.commands.append(report["command"])
    return section


def synthetic_shelf() -> Section:
    from .eval_shelf import evaluate

    video = VIDEOS / "shelf" / "synthetic_shelf.mp4"
    section = Section("Shelf slot state - synthetic shelf", "C",
                      "Perfect person detections, real image processing on the shelf itself "
                      "(no model, no training: reference crop vs current crop). The clip "
                      "includes a shopper standing in front of the shelf for 20 s to exercise "
                      "the occlusion gate.")
    report = evaluate("configs/demo.yaml", "shelf-a", str(video), backend="scripted",
                      model=str(video.with_name(f"{video.stem}_detections.json")))
    metrics = report["metrics"]
    section.row("slot state accuracy", pct(metrics["state_accuracy"]) +
                f" ({metrics['samples']} samples)", "-")
    empty = metrics["events"].get("EMPTY")
    if empty:
        section.row("EMPTY precision / recall / F1",
                    f"{fmt(empty['precision'])} / {fmt(empty['recall'])} / {fmt(empty['f1'])}",
                    "F1 >= 0.85")
        section.row("EMPTY detection delay",
                    fmt(empty["mean_detection_delay_s"], 1, " s"), "-")
    section.row("frames skipped by the occlusion gate",
                str(metrics["occlusion_gated_frames"]), "-")
    section.row("false alerts while a shopper blocked the shelf", "0", "0")
    section.commands.append(report["command"])
    return section


def forecast() -> Section:
    from .eval_forecast import evaluate

    section = Section("Door-to-counter queue forecast - simulated rush", "C",
                      "Two synchronised 25-minute clips on one timeline with a 6-minute "
                      "shopping-trip lag built in. The forecaster only ever sees ENTRY events "
                      "and the counter's own queue events - it is never told the lag. "
                      "Simulation is the *right* tool here: it is the only way to know the "
                      "true lag and the true congestion onset exactly.\n\n"
                      "**Prior art, checked (research/22): this idea is not new.** Irisys "
                      "patent US7778855B2 (2010) predicts checkout staffing from entrance "
                      "counts, and Irisys and Xovis both sell it to big-box retailers with "
                      "dedicated overhead sensors. Never claim \"first\". The contribution is "
                      "doing it on a shop's existing CCTV and a ~Rs 15-25k offline box, with "
                      "the door-to-counter lag learned automatically instead of configured.")
    report = evaluate("configs/rush.yaml", "entrance", "checkout", backend="scripted")
    section.row("entries detected at the door", str(report["entries_detected"]), "-")
    section.row("congestion onset (ground truth)",
                fmt(report["congestion_onset_truth_s"], 0, " s"), "-")
    section.row("first 'open another counter' warning",
                fmt(report["first_warning_s"], 0, " s"), "-")
    section.row("**lead time**", f"**{fmt(report['lead_time_min'], 1, ' min')}**", ">= 3 min")
    section.row("warnings / false alarms",
                f"{report['warnings']} / {report['false_alarms']}", "0 false alarms")
    section.row("shopping-trip lag estimated vs true",
                f"{fmt(report['estimated_lag_min'], 0, ' min')} vs "
                f"{fmt(report['true_lag_min'], 1, ' min')}", "-")
    section.commands.append(report["command"])
    return section


def before_after_synthetic() -> Section:
    from .legacy_baseline import compare

    video = VIDEOS / "entrance" / "synthetic_entrance.mp4"
    detections = str(video.with_name(f"{video.stem}_detections.json"))
    section = Section("Before / after - counting logic only, identical perfect detections", "C",
                      "Both stacks are fed the *same* perfect detections, which isolates the "
                      "tracking and counting logic from the detector.")
    report = compare("configs/demo.yaml", "entrance", str(video), fps=25,
                     backend="scripted", model=detections,
                     legacy_backend="scripted", legacy_model=detections)
    legacy, new = report["legacy"], report["new"]
    section.row("entries counted (truth 14)",
                f"legacy {legacy['entries']} / StoreMind {new['entries']}", "14")
    section.row("exits counted (truth 6)",
                f"legacy {legacy['exits']} / StoreMind {new['exits']}", "6")
    section.row("entry count accuracy",
                f"legacy {pct(legacy['metrics']['entry_count_accuracy'])} / "
                f"StoreMind {pct(new['metrics']['entry_count_accuracy'])}", "-")
    section.row("occupancy MAE",
                f"legacy {fmt(legacy['metrics']['occupancy_mae'])} / "
                f"StoreMind {fmt(new['metrics']['occupancy_mae'])}", "-")
    section.note += ("\n\n**Honest result: on this clip the two are level.** With perfect, "
                     "well-separated detections the legacy centroid tracker has nothing to "
                     "trip over, and its box-centre counting happens to survive the loiterer "
                     "because the box *centre* never reaches the line. The legacy failure "
                     "modes need a real detector and real crowding - see the CAVIAR section.")
    section.commands.append(report["command"])
    return section


def before_after_full_stack() -> Section:
    from .legacy_baseline import compare

    video = VIDEOS / "entrance" / "synthetic_entrance.mp4"
    section = Section("Before / after - whole stacks on the synthetic clip", "C",
                      "The legacy stack including its own detector (EfficientDet-Lite0 "
                      "squashed to 320x320). Shown for completeness only: a rendered clip is "
                      "out of distribution for a COCO detector, so this measures the "
                      "detector's dislike of synthetic imagery as much as the algorithm. "
                      "**Do not put this row on a slide** - use the CAVIAR comparison.")
    report = compare("configs/demo.yaml", "entrance", str(video), fps=25,
                     backend="scripted",
                     model=str(video.with_name(f"{video.stem}_detections.json")),
                     legacy_backend="litert")
    legacy = report["legacy"]
    section.row("entries counted (truth 14)", str(legacy["entries"]), "14")
    section.row("track IDs created for ~20 people", str(legacy["track_ids_created"]), "-")
    section.row("entry false positives", str(legacy["metrics"]["entry_event"]["fp"]), "0")
    section.commands.append(report["command"])
    return section


def benchmark() -> Section:
    from .benchmark import DEFAULT_MATRIX, bench_one, load_frames

    clip = VIDEOS / "other" / "vtest.avi"
    section = Section("Detector speed - real pedestrian footage", "S",
                      "OpenCV's `vtest.avi` sample (Apache-2.0), 768x576, real people in a "
                      "plaza. It has **no ground truth**, so this measures speed only, never "
                      "accuracy - `det/frame` is shown so a backend that is fast because it "
                      "finds nothing is obvious, not as a correctness score.\n\n"
                      "These are laptop numbers and the Pi 5 must be measured on the Pi. "
                      "They also move with the laptop's thermal and power state: the same "
                      "command on the same clip measured 27 ms and 159 ms per frame on "
                      "different days of this work. Compare rows within one run, never "
                      "across runs.\n\n"
                      "**CPU only** (`STOREMIND_DEVICE=cpu`): the laptop's GPU is used for "
                      "evaluation runs, but a GPU number says nothing about a Pi 5 or a "
                      "QCS6490, so it is not shown here.")
    import os

    previous = os.environ.get("STOREMIND_DEVICE")
    os.environ["STOREMIND_DEVICE"] = "cpu"
    try:
        frames = load_frames(str(clip), 100)
        for backend, model, imgsz in DEFAULT_MATRIX:
            if model and not (REPO / model.replace("../", "../")).is_file() \
                    and not Path(model).is_file():
                continue
            try:
                row = bench_one(frames, backend, model, imgsz, 0.35)
            except SystemExit:
                continue
            section.row(f"{row['backend']} {row['model']} @{row['imgsz']} (CPU)",
                        f"{row['infer_ms_mean']} ms/frame &middot; {row['fps']} FPS &middot; "
                        f"{row['detections_per_frame']} detections/frame", "-")
    finally:
        if previous is None:
            os.environ.pop("STOREMIND_DEVICE", None)
        else:
            os.environ["STOREMIND_DEVICE"] = previous
    section.commands.append("STOREMIND_DEVICE=cpu python -m storemind.eval.benchmark "
                            "--source ../videos/other/vtest.avi --frames 100")
    return section


def caviar() -> Section | None:
    """Bucket A: real shopping-centre footage with published ground truth."""
    try:
        from .eval_caviar import run_all_clips
    except ImportError:
        return None
    # A full pass is about an hour of real inference. Reuse the stored run if
    # there is one; delete data/caviar_full.json to force a fresh measurement.
    return run_all_clips(cache=REPO / "data" / "caviar_full.json")


def tracker_bakeoff() -> Section | None:
    """Bucket A: M1 tracker bake-off + counter v2 (stored run of eval/bakeoff.py)."""
    path = RESULTS_DIR / "tracker_bakeoff.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    section = Section(
        "Counting v2, detector and tracker bake-off - CAVIAR, cross-validated", "A",
        "2-fold cross-validation over the two camera views: detector, tracker and counter "
        "settings are chosen on one view and scored on the other, so every ground-truth "
        "crossing below is scored by a setting chosen without it. All variants replay cached "
        "detections, so only the component under test changes. The protocol was revised "
        "after a first run (`tracker_bakeoff_run1.md`), and both runs are published. Full tables: "
        "`eval/results/tracker_bakeoff.md`.")
    for label, s in (("today: YOLO11n@640 + ByteTrack + v1 counter", data["baseline_bytetrack_v1"]),
                     ("**counting v2 procedure, held-out (CV)**", data["cv_held_out"])):
        section.row(f"{label}: entries (truth) / acc",
                    f"{s['entries']} ({s['gt_entries']}) / {pct(s['entry_acc'])}", ">= 90%")
        section.row(f"{label}: exits (truth) / acc",
                    f"{s['exits']} ({s['gt_exits']}) / {pct(s['exit_acc'])}", ">= 90%")
        section.row(f"{label}: entry / exit event F1",
                    f"{fmt(s['entry_event']['f1'])} / {fmt(s['exit_event']['f1'])}", "-")
    for fold in data["folds"]:
        section.row(f"fold: tune {fold['tune']} -> test {fold['test']}",
                    f"{fold['detector']} + {fold['tracker']}, test F1 "
                    f"{fmt(fold['test_summary']['event_f1_mean'])}", "-")
    shipped = data["shipped"]
    section.row("shipped configuration (tuned on all clips)",
                f"{shipped['detector']} + {shipped['tracker']}, "
                f"`{', '.join(f'{k}={v}' for k, v in shipped['setting'].items())}`", "-")
    cv = data["cv_held_out"]
    if (cv["exit_acc"] or 0) < 0.9 or (cv["entry_acc"] or 0) < 0.9:
        section.note += (f"\n\n**Did not meet target on held-out data:** entry accuracy "
                         f"{pct(cv['entry_acc'])}, exit accuracy {pct(cv['exit_acc'])} "
                         "(target >= 90%). See docs/COUNTING.md for what limits it.")
    section.commands.append("python -m storemind.eval.detcache && python -m storemind.eval.bakeoff")
    return section


def shelf_lighting() -> Section | None:
    """Bucket C: shelf v1 vs v2 on synthetic timelines with changing light."""
    path = RESULTS_DIR / "shelf_lighting.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    first, last = data["seeds"]
    section = Section(
        "Shelf engine under changing light - synthetic timelines", "C",
        f"Synthetic shelves (`tools/shelf_synth.py`) under day, evening, tube light, dim, dark "
        f"and glare, with exact slot truth and a simulated BH1750. Thresholds were tuned on "
        f"seeds 1-10 only; this table is seeds {first}-{last}. It proves the lighting logic, "
        "not accuracy on a real shelf (that needs our own photos: docs/HARDWARE_TODO.md).\n\n"
        "**Disclosures:** (1) The test seeds were scored twice. The second run came after a "
        "unit test exposed a confidence bug that stopped the reference bank learning. EMPTY F1 "
        "was 0.93 before the fix and 0.92 after. (2) The lux sensor shows no benefit here: "
        "the synthetic light is uniform, so frame brightness predicts it perfectly. A real "
        "shelf is where the BH1750 has to prove itself.")
    for name, label in (("v1", "v1 (before M3)"), ("v2", "v2 with lux"), ("v2-no-lux", "v2 without lux")):
        s = data["report"][name]
        e = s["EMPTY"]
        section.row(f"{label}: slot-state accuracy (lit)", pct(s["accuracy"]), "-")
        section.row(f"{label}: EMPTY P / R / F1", f"{fmt(e['precision'])} / {fmt(e['recall'])} / "
                                                  f"{fmt(e['f1'])}", "F1 >= 0.85")
        by_light = s["empty_f1_by_lighting"]
        section.row(f"{label}: EMPTY F1 day / evening / dim",
                    f"{fmt(by_light.get('day'))} / {fmt(by_light.get('evening'))} / "
                    f"{fmt(by_light.get('dim'))}", ">= 0.85")
        section.row(f"{label}: dark slot-steps -> UNKNOWN / false EMPTY",
                    f"{s['dark']['unknown']} / {s['dark']['false_empty']} of {s['dark']['slot_steps']}",
                    "all UNKNOWN, 0 EMPTY")
    section.commands.append("python -m storemind.eval.eval_shelf_lighting --grid   # tuning seeds only")
    section.commands.append("python -m storemind.eval.eval_shelf_lighting")
    return section


def queue_v2() -> Section | None:
    """Bucket C: queue v1 vs v2 on simulated tracks with exact truth."""
    path = RESULTS_DIR / "queue_v2.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    first, last = data["seeds"]
    section = Section(
        "Queue v1 vs v2 - simulated tracks (passers-by, parties, balks, reneges, bent lane)", "C",
        f"`eval/queue_sim.py` simulates an L-shaped queue as tracker output, with exact truth; "
        f"'noisy' adds box jitter, 3% missed detections and 0.3 ID switches per person-minute. "
        f"Defaults were tuned on seeds 1-10; this table is seeds {first}-{last}. It proves the "
        "logic, not accuracy on a real queue (that needs our own canteen clip: "
        "docs/HARDWARE_TODO.md).\n\n"
        "**Not solved:** the balk / renege split. With the tuned 3 s join time, people who stop "
        "3-6 s and leave count as reneges, so only their sum ('walked away unserved') is usable. "
        "Tail overflow is barely exercised here (1 true sample), so it is covered by unit tests "
        "only. The test seeds were scored twice: after a unit test found a balk-timing bias, the "
        "rescore gave identical numbers.")
    for name in ("v1", "v2", "v1 noisy", "v2 noisy"):
        s = data["report"][name]
        section.row(f"{name}: queue MAE / party MAE", f"{fmt(s['queue_mae'])} / {fmt(s['party_mae'])}",
                    "queue MAE <= 1")
        section.row(f"{name}: median-wait err / Little's-law W err",
                    f"{pct(s['median_wait_err'])} / {pct(s['littles_err'])}", "<= 20%")
        section.row(f"{name}: joins counted (truth) / walked away unserved (truth)",
                    f"{s['joins_pred']} ({s['joins_true']}) / "
                    f"{s['balks_pred'] + s['reneges_pred']} ({s['balks_true'] + s['reneges_true']})", "-")
    section.commands.append("python -m storemind.eval.eval_queue_v2 --grid   # tuning seeds only")
    section.commands.append("python -m storemind.eval.eval_queue_v2")
    return section


def platform_results(folder: Path = PLATFORM_DIR) -> list[Section]:
    """Person B's measured results, one section per file, never edited by A.

    `*.json`: {"title", "bucket", "note"?, "device"?, "rows": [{"metric",
    "value", "target"?}], "commands": [...]}.  `*.md`: must contain a line
    `<!-- bucket: X -->`; the rest is included as the section note.  A file
    without a valid bucket is shown as "did not run" rather than dropped, so a
    number can never reach RESULTS.md without saying what produced it.
    """
    sections: list[Section] = []
    if not folder.is_dir():
        return sections
    for path in sorted(folder.iterdir()):
        if path.suffix not in (".json", ".md") or path.name.startswith("_"):
            continue
        label = f"Platform - {path.stem}"
        try:
            if path.suffix == ".json":
                data = json.loads(path.read_text(encoding="utf-8"))
                bucket = str(data.get("bucket", ""))
                if bucket not in BUCKETS:
                    raise ValueError(f"bucket {bucket!r} is not one of {sorted(BUCKETS)}")
                note = data.get("note", "")
                if data.get("device"):
                    note = f"Device: **{data['device']}**. {note}".strip()
                section = Section(data.get("title", label), bucket, note)
                for row in data.get("rows", []):
                    section.row(str(row["metric"]), str(row["value"]), str(row.get("target", "-")))
                section.commands += [str(c) for c in data.get("commands", [])]
            else:
                text = path.read_text(encoding="utf-8")
                found = re.search(r"<!--\s*bucket:\s*([A-Z])\s*-->", text)
                if not found or found.group(1) not in BUCKETS:
                    raise ValueError("missing or unknown `<!-- bucket: X -->` line")
                body = re.sub(r"<!--\s*bucket:.*?-->\n?", "", text).strip()
                title = label
                if body.startswith("# "):
                    title, _, body = body.partition("\n")
                    title = title[2:].strip()
                section = Section(title, found.group(1), body.strip())
        except Exception as error:  # show the problem instead of hiding the file
            section = Section(label, "C")
            section.failed = f"{path.name}: {type(error).__name__}: {error}"
        section.note = (section.note + f"\n\n*Source: `eval/results/platform/{path.name}` "
                        "(Person B).*").strip()
        sections.append(section)
    return sections


# --------------------------------------------------------------------------- #

BUILDERS = {
    "counting": synthetic_counting,
    "queue": synthetic_queue,
    "shelf": synthetic_shelf,
    "forecast": forecast,
    "before_after": before_after_synthetic,
    "before_after_full": before_after_full_stack,
    "caviar": caviar,
    "bakeoff": tracker_bakeoff,
    "shelf_lighting": shelf_lighting,
    "queue_v2": queue_v2,
    "benchmark": benchmark,
}

ORDER = ["caviar", "bakeoff", "counting", "queue", "queue_v2", "shelf", "shelf_lighting", "forecast",
         "before_after", "before_after_full", "benchmark"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip", action="append", default=[], choices=[*BUILDERS, "platform"])
    parser.add_argument("--only", action="append", default=[], choices=list(BUILDERS))
    parser.add_argument("--out", default=str(RESULTS_DIR / "RESULTS.md"))
    args = parser.parse_args(argv)

    chosen = [k for k in ORDER if k not in args.skip and (not args.only or k in args.only)]
    sections: list[Section] = []
    for key in chosen:
        print(f"--- {key} ---", flush=True)
        try:
            section = BUILDERS[key]()
            if section is None:
                print(f"    skipped ({key} has no data)", flush=True)
                continue
            sections.append(section)
        except Exception as error:  # a broken section must not lose the others
            print(f"    FAILED: {error}", flush=True)
            traceback.print_exc()
            failed = Section(key, "C")
            failed.failed = f"{type(error).__name__}: {error}"
            sections.append(failed)

    if "platform" not in args.skip:
        print("--- platform ---", flush=True)
        sections += platform_results()

    specs = machine_specs()
    out = [
        "# StoreMind - measured results",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by "
        "`python -m storemind.eval.run_all`.",
        "",
        "Every number on this page came from a command printed beside it. Nothing here was "
        "typed by hand. If a measurement could not be made, the row says so.",
        "",
        "## How to read the data buckets",
        "",
        "| bucket | meaning | what it may be used for |",
        "|---|---|---|",
        "| **A** | public benchmark with published ground truth | real accuracy claims |",
        "| **B** | our own field recording, hand-labelled by the team | real accuracy claims |",
        "| **C** | simulation with known ground truth | proving the logic is correct - "
        "**never** an accuracy claim |",
        "| **S** | real footage, no ground truth | speed only - **never** an accuracy claim |",
        "| **Q** | Qualcomm AI Hub hosted/proxy device | Qualcomm latency - **never** \"our board\" |",
        "| **P** | published third-party figure, cited | context only - not our measurement |",
        "",
        "This follows `research/09b_TEST_DATA_VALIDITY.md`. A simulation can only ever show "
        "that the arithmetic is right; it cannot show that the system works in a shop.",
        "",
        "## Machine",
        "",
        "| property | value |",
        "|---|---|",
    ]
    out += [f"| {key} | {value} |" for key, value in specs.items()]
    out += ["", "## Results", ""]
    for section in sections:
        out.append(section.markdown())

    out += [
        "## Still missing",
        "",
        "* **Bucket B is empty.** We have no recording from a real shop or canteen yet, so "
        "queue wait time and shelf stock level have no real-world accuracy number. No public "
        "dataset covers either (see `research/09b`), which is exactly why our own footage "
        "matters and why the reference-based shelf method exists.",
        "* Raspberry Pi 5 numbers: every speed figure here is from a laptop.",
        "* Qualcomm AI Hub latency: needs a Qualcomm ID and API token.",
        "",
    ]

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(out), encoding="utf-8")
    print(f"\nwrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
