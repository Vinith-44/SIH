"""Bucket A: the real detector on real footage with published ground truth.

This is the only place in the project where an accuracy number comes from real
video. Everything else is either simulation (logic validation) or speed.

Data: EC Funded CAVIAR project / IST 2001 37540, CC BY-SA.

What is measured, per clip and pooled over all 16 clips:

*   **entry / exit count accuracy** against crossings derived from the
    ground-truth trajectories over the same line the system uses;
*   **crossing precision / recall / F1** with a 3-second matching tolerance,
    which is far harsher than totals and exposes a system that misses two
    crossings and invents two others;
*   **people-in-frame MAE** - how many people we are tracking versus how many
    the annotation says are there. This needs no line at all;
*   **ID switches** - how often a ground-truth person's identity jumps to a
    different predicted track id, matched by IoU. This is the metric the legacy
    greedy centroid tracker was expected to lose on (audit S2);
*   **shop enter / exit** counts on the front view against CAVIAR's own activity
    labels - a second ground truth that does not depend on where a line was
    drawn.

Honest framing that must travel with these numbers: CAVIAR is a Portuguese
shopping mall at 384x288 with a sparse crowd. It is *easier* than a crowded
Indian kirana at peak hour, and the people are small, which cuts the other way.
It proves the pipeline works on real video; it does not prove it works in our
target store. Only bucket B can do that.

    python -m storemind.eval.eval_caviar
    python -m storemind.eval.eval_caviar --scenario OneStopEnter1 --view front
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import numpy as np

from ..core.config import load_config
from ..core.events import EventType
from ..core.geometry import foot_point
from .caviar import (
    CAVIAR_CREDIT,
    CAVIAR_FPS,
    SCENARIOS,
    crossing_sensitivity,
    gt_crossings,
    parse_cvml,
    write_entries_csv,
    write_people_csv,
    write_shop_csv,
)
from .common import Section, accuracy_from_counts, fmt, mae, match_events, pct, prf

CAVIAR_DIR = Path(__file__).resolve().parents[3] / "videos" / "entrance" / "caviar"
CONFIG = "configs/caviar.yaml"
VIEWS = ("corridor", "front")


def iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 1e-9 else 0.0


def count_id_switches(gt_by_frame: dict[int, list], pred_by_frame: dict[int, list],
                      iou_threshold: float = 0.4) -> dict:
    """Greedy IoU matching per frame, then count identity changes.

    An ID switch is a ground-truth person whose matched predicted track id
    changes from one confirmed id to a *different* confirmed id. A gap (the
    person was not detected for a while) is not counted as a switch unless the
    identity actually changed - losing someone briefly and picking them up again
    with the same id is what `lost_track_buffer` is for.
    """
    last_id: dict[int, int] = {}
    switches = 0
    matched = 0
    gt_total = 0
    # Only frames the pipeline actually looked at. The entrance camera runs at
    # 8 FPS on 25 FPS footage, so two frames in three are never seen; counting
    # those as misses would report a match rate three times worse than reality
    # and would say nothing about the detector.
    for frame in sorted(set(gt_by_frame) & set(pred_by_frame)):
        gt_boxes = gt_by_frame[frame]
        predictions = pred_by_frame.get(frame, [])
        gt_total += len(gt_boxes)
        pairs = []
        for gi, gt in enumerate(gt_boxes):
            for pi, pred in enumerate(predictions):
                score = iou(gt.xyxy, pred.xyxy)
                if score >= iou_threshold:
                    pairs.append((score, gi, pi))
        pairs.sort(reverse=True)
        used_gt: set[int] = set()
        used_pred: set[int] = set()
        for _score, gi, pi in pairs:
            if gi in used_gt or pi in used_pred:
                continue
            used_gt.add(gi)
            used_pred.add(pi)
            matched += 1
            gt_track = gt_boxes[gi].track
            pred_id = predictions[pi].track_id
            previous = last_id.get(gt_track)
            if previous is not None and previous != pred_id:
                switches += 1
            last_id[gt_track] = pred_id
    return {"id_switches": switches, "matched_boxes": matched,
            "gt_boxes_in_processed_frames": gt_total,
            "match_rate": matched / gt_total if gt_total else None}


def mot_metrics(gt_by_frame: dict[int, list], pred_by_frame: dict[int, list],
                threshold: float = 0.5) -> dict:
    """IDF1 / MOTA / IDSW with the TrackEval-compatible implementations in
    `trackers.eval`, over the frames the pipeline processed."""
    from trackers.eval import compute_clear_metrics, compute_identity_metrics

    gt_ids, tr_ids, sims = [], [], []
    for frame in sorted(pred_by_frame):
        gts = gt_by_frame.get(frame, [])
        preds = pred_by_frame[frame]
        gt_ids.append(np.array([g.track for g in gts], dtype=int))
        tr_ids.append(np.array([p.track_id for p in preds], dtype=int))
        sims.append(np.array([[iou(g.xyxy, p.xyxy) for p in preds] for g in gts],
                             dtype=float).reshape(len(gts), len(preds)))
    if not gt_ids:
        return {}
    identity = compute_identity_metrics(gt_ids, tr_ids, sims, threshold)
    clear = compute_clear_metrics(gt_ids, tr_ids, sims, threshold)
    return {"IDF1": float(identity["IDF1"]), "IDTP": int(identity["IDTP"]),
            "IDFP": int(identity["IDFP"]), "IDFN": int(identity["IDFN"]),
            "MOTA": float(clear["MOTA"]), "IDSW": int(clear["IDSW"]),
            "gt_dets": int(sum(len(g) for g in gt_ids)),
            "pred_dets": int(sum(len(t) for t in tr_ids))}


def run_clip(scenario: str, view: str, config_path: str = CONFIG,
             caviar_dir: Path = CAVIAR_DIR, write_csv: bool = True,
             backend: str | None = None, model: str | None = None,
             detector=None, configure=None, timeline: bool = False,
             track_log: list | None = None) -> dict:
    """`detector` injects e.g. a `CachedDetector`; `configure(config)` may edit
    the loaded config (tracker type, counter mode) before the run."""
    from ..pipeline import Pipeline

    corridor_xml, front_xml = SCENARIOS[scenario]
    xml = caviar_dir / (front_xml if view == "front" else corridor_xml)
    suffix = "front" if view == "front" else "cor"
    video = caviar_dir / f"{scenario}{suffix}.mpg"
    if not video.is_file():
        raise SystemExit(f"missing clip {video}")

    clip = parse_cvml(xml)

    config = load_config(config_path)
    config.cameras = [c for c in config.cameras if c.name == view]
    if not config.cameras:
        raise SystemExit(f"config {config_path} has no camera named {view!r}")
    camera_config = config.cameras[0]
    camera_config.source = str(video)
    if backend:
        config.detector.backend = backend
    if model:
        config.detector.model = model
    config.alerts.console = False
    if configure is not None:
        configure(config)

    line = camera_config.line
    width, height = 384, 288           # every CAVIAR clip
    line_a = (line.a[0] * width, line.a[1] * height)
    line_b = (line.b[0] * width, line.b[1] * height)
    truth = gt_crossings(clip, line_a, line_b, line.entry_direction)
    sensitivity = crossing_sensitivity(clip, line_a, line_b, line.entry_direction)

    if write_csv:
        write_entries_csv(video.with_name(f"{video.stem}_gt_entries.csv"), truth)
        write_people_csv(video.with_name(f"{video.stem}_gt_people.csv"),
                         clip.people_per_frame())
        if view == "front":
            write_shop_csv(video.with_name(f"{video.stem}_gt_shop.csv"), clip)

    # --- run the real pipeline ------------------------------------------- #
    from .common import _NullStore

    per_frame: dict[int, list] = {}

    def observe(_camera: str, frame, tracks) -> None:
        per_frame[frame.index] = list(tracks)
        if track_log is not None:  # (video time, tracks) for counter-only replays
            track_log.append((frame.video_s, list(tracks)))

    events: list = []
    pipeline = Pipeline(config, replay=True, show=False, store=_NullStore(),
                        on_tracks=observe, detector=detector)
    if timeline:
        # Replay only the cached frames with their timestamps: no video decode.
        from ..inference.cached import CachedTimeline

        pipeline.cameras[0].source.close()
        pipeline.cameras[0].source = CachedTimeline(detector, name=view)
    pipeline.bus.subscribe_all(events.append)
    summary = pipeline.run(progress=False)

    base = min((e.dt for e in events), default=None)
    predicted_in = [(e.dt - base).total_seconds() for e in events if e.type is EventType.ENTRY]
    predicted_out = [(e.dt - base).total_seconds() for e in events if e.type is EventType.EXIT]

    gt_in = [t for t, _track, d in truth if d == "in"]
    gt_out = [t for t, _track, d in truth if d == "out"]
    tp_in, fp_in, fn_in, err_in = match_events(predicted_in, gt_in, 3.0)
    tp_out, fp_out, fn_out, err_out = match_events(predicted_out, gt_out, 3.0)

    # --- people in frame -------------------------------------------------- #
    gt_by_frame = clip.by_frame()
    people_pairs = []
    for index, tracks in per_frame.items():
        if index >= clip.frames:
            continue
        people_pairs.append((len(tracks), len(gt_by_frame.get(index, []))))
    tracking = count_id_switches(gt_by_frame, per_frame)
    tracking.update(mot_metrics(gt_by_frame, {i: t for i, t in per_frame.items()
                                              if i < clip.frames}))
    counter = pipeline.cameras[0].footfall

    report = {
        "scenario": scenario,
        "view": view,
        "video": str(video),
        "ground_truth_xml": str(xml),
        "credit": CAVIAR_CREDIT,
        "frames": clip.frames,
        "duration_s": round(clip.duration_s, 1),
        "annotated_people": len(clip.by_track()),
        "line": {"a": list(line.a), "b": list(line.b),
                 "entry_direction": line.entry_direction, "margin_px": line.margin_px},
        "gt_crossing_sensitivity": {str(k): v for k, v in sensitivity.items()},
        "detector": summary["detector"],
        "frames_processed": summary["cameras"][view]["frames_processed"],
        "infer_ms_mean": summary["cameras"][view].get("infer_ms_mean"),
        "gt": {"entries": len(gt_in), "exits": len(gt_out)},
        "predicted": {"entries": len(predicted_in), "exits": len(predicted_out)},
        "entry_event": prf(tp_in, fp_in, fn_in),
        "exit_event": prf(tp_out, fp_out, fn_out),
        "mean_timing_error_s": (statistics.mean(err_in + err_out)
                                if (err_in + err_out) else None),
        "people_in_frame_mae": mae([(a, b) for a, b in people_pairs]),
        "people_in_frame_samples": len(people_pairs),
        "gt_mean_people_in_frame": (statistics.mean([b for _a, b in people_pairs])
                                    if people_pairs else None),
        "tracking": tracking,
        "tracker": getattr(pipeline.cameras[0].tracker, "impl", type(pipeline.cameras[0].tracker).__name__),
        "counter": type(counter).__name__ if counter is not None else None,
        "rejected": dict(getattr(counter, "rejected", {}) or {}),
    }
    if view == "front":
        report["shop_enter_episodes"] = len(clip.context_episodes("shop enter"))
        report["shop_exit_episodes"] = len(clip.context_episodes("shop exit"))
    pipeline.close()
    return report


def run_legacy_clip(scenario: str, view: str, config_path: str = CONFIG,
                    caviar_dir: Path = CAVIAR_DIR) -> dict:
    from .legacy_baseline import run_legacy

    corridor_xml, front_xml = SCENARIOS[scenario]
    xml = caviar_dir / (front_xml if view == "front" else corridor_xml)
    suffix = "front" if view == "front" else "cor"
    video = caviar_dir / f"{scenario}{suffix}.mpg"
    clip = parse_cvml(xml)

    config = load_config(config_path)
    camera_config = next(c for c in config.cameras if c.name == view)
    line = camera_config.line
    width, height = 384, 288
    truth = gt_crossings(clip, (line.a[0] * width, line.a[1] * height),
                         (line.b[0] * width, line.b[1] * height), line.entry_direction)
    gt_in = [t for t, _t, d in truth if d == "in"]
    gt_out = [t for t, _t, d in truth if d == "out"]

    result = run_legacy(str(video), line, fps=camera_config.fps)
    legacy_in = [t for t, d in result.crossings if d == "in"]
    legacy_out = [t for t, d in result.crossings if d == "out"]
    tp_in, fp_in, fn_in, _ = match_events(legacy_in, gt_in, 3.0)
    tp_out, fp_out, fn_out, _ = match_events(legacy_out, gt_out, 3.0)
    return {
        "scenario": scenario, "view": view,
        "gt": {"entries": len(gt_in), "exits": len(gt_out)},
        "predicted": {"entries": result.entries, "exits": result.exits},
        "entry_event": prf(tp_in, fp_in, fn_in),
        "exit_event": prf(tp_out, fp_out, fn_out),
        "track_ids_created": result.track_ids_created,
        "infer_ms_mean": (round(sum(result.infer_ms) / len(result.infer_ms), 2)
                          if result.infer_ms else None),
    }


def pool(reports: list[dict], key: str) -> dict:
    tp = sum(r[key]["tp"] for r in reports)
    fp = sum(r[key]["fp"] for r in reports)
    fn = sum(r[key]["fn"] for r in reports)
    return prf(tp, fp, fn)


# --------------------------------------------------------------------------- #

def run_all_clips(views: tuple[str, ...] = VIEWS, with_legacy: bool = True,
                  caviar_dir: Path = CAVIAR_DIR, cache: Path | None = None) -> Section | None:
    """Score every clip.  A full pass is ~16 clips of real inference, so a cached
    result is reused when one exists - otherwise regenerating RESULTS.md would
    mean re-running an hour of detection for numbers that have not changed."""
    if cache is not None and cache.is_file():
        payload = json.loads(cache.read_text(encoding="utf-8"))
        print(f"    reusing {cache}", flush=True)
        return build_section(payload.get("per_clip", []), payload.get("legacy", []))

    if not caviar_dir.is_dir() or not any(caviar_dir.glob("*.mpg")):
        return None

    reports: list[dict] = []
    legacy_reports: list[dict] = []
    for scenario in SCENARIOS:
        for view in views:
            print(f"    {scenario} / {view}", flush=True)
            reports.append(run_clip(scenario, view, caviar_dir=caviar_dir))
            if with_legacy:
                legacy_reports.append(run_legacy_clip(scenario, view, caviar_dir=caviar_dir))
    return build_section(reports, legacy_reports)


def build_section(reports: list[dict], legacy_reports: list[dict]) -> Section | None:
    if not reports:
        return None
    views = sorted({r["view"] for r in reports})
    section = Section(
        "Entry / exit counting on real footage - CAVIAR shopping centre", "A",
        f"{len(reports)} clips ({len(SCENARIOS)} scenarios x {len(views)} camera views), "
        f"real people, real detector (YOLO11n), no ground truth was shown to the system. "
        f"Credit: {CAVIAR_CREDIT}.\n\n"
        "Ground-truth crossings are derived from the published trajectories over the same "
        "line the system uses; the line coordinates and the stability check behind them are "
        "recorded in `configs/caviar.yaml`.\n\n"
        "**Caveat that belongs on the slide:** CAVIAR is a Portuguese mall at 384x288 with a "
        "sparse crowd. It is easier than a crowded kirana at peak hour, though the small "
        "frame makes distant people harder. It shows the pipeline works on real video. It "
        "does not show it works in our target store - only our own recordings can.")

    total_gt_in = sum(r["gt"]["entries"] for r in reports)
    total_gt_out = sum(r["gt"]["exits"] for r in reports)
    total_in = sum(r["predicted"]["entries"] for r in reports)
    total_out = sum(r["predicted"]["exits"] for r in reports)
    entry_pooled = pool(reports, "entry_event")
    exit_pooled = pool(reports, "exit_event")
    switches = sum(r["tracking"]["id_switches"] for r in reports)
    matched = sum(r["tracking"]["matched_boxes"] for r in reports)
    gt_boxes = sum(r["tracking"]["gt_boxes_in_processed_frames"] for r in reports)
    people_mae = [r["people_in_frame_mae"] for r in reports if r["people_in_frame_mae"] is not None]
    timing = [r["mean_timing_error_s"] for r in reports if r["mean_timing_error_s"] is not None]

    section.row("clips / annotated people",
                f"{len(reports)} / {sum(r['annotated_people'] for r in reports)}", "-")
    section.row("total video", f"{sum(r['duration_s'] for r in reports) / 60:.1f} min", "-")
    section.row("entries counted (truth)", f"{total_in} ({total_gt_in})", "-")
    section.row("exits counted (truth)", f"{total_out} ({total_gt_out})", "-")
    section.row("entry count accuracy", pct(accuracy_from_counts(total_in, total_gt_in)), ">= 90%")
    section.row("exit count accuracy", pct(accuracy_from_counts(total_out, total_gt_out)), ">= 90%")
    section.row("total count accuracy",
                pct(accuracy_from_counts(total_in + total_out, total_gt_in + total_gt_out)),
                ">= 90%")
    section.row("entry event precision / recall / F1",
                f"{fmt(entry_pooled['precision'])} / {fmt(entry_pooled['recall'])} / "
                f"{fmt(entry_pooled['f1'])}", "-")
    section.row("exit event precision / recall / F1",
                f"{fmt(exit_pooled['precision'])} / {fmt(exit_pooled['recall'])} / "
                f"{fmt(exit_pooled['f1'])}", "-")
    section.row("mean crossing timing error",
                fmt(statistics.mean(timing) if timing else None, 2, " s"), "-")
    section.row("people-in-frame MAE",
                fmt(statistics.mean(people_mae) if people_mae else None, 2, " people"),
                "<= 1-2")
    section.row("detection match rate (IoU >= 0.4)",
                pct(matched / gt_boxes if gt_boxes else None) + " of annotated people", "-")
    section.row("**ID switches** (ByteTrack)",
                f"**{switches}** over {gt_boxes:,} annotated boxes in processed frames", "-")

    if legacy_reports:
        legacy_in = sum(r["predicted"]["entries"] for r in legacy_reports)
        legacy_out = sum(r["predicted"]["exits"] for r in legacy_reports)
        legacy_entry = pool(legacy_reports, "entry_event")
        section.row("--- legacy pipeline on the same clips ---", "", "")
        section.row("legacy entries counted (truth)", f"{legacy_in} ({total_gt_in})", "-")
        section.row("legacy exits counted (truth)", f"{legacy_out} ({total_gt_out})", "-")
        section.row("legacy entry count accuracy",
                    pct(accuracy_from_counts(legacy_in, total_gt_in)), ">= 90%")
        section.row("legacy entry event F1", fmt(legacy_entry["f1"]), "-")
        section.row("legacy track IDs created",
                    str(sum(r["track_ids_created"] for r in legacy_reports)), "-")

    # Say plainly what missed target. A table where the reader has to spot the
    # failure themselves is a table that is hoping they will not.
    entry_accuracy = accuracy_from_counts(total_in, total_gt_in)
    exit_accuracy = accuracy_from_counts(total_out, total_gt_out)
    misses = []
    if entry_accuracy is not None and entry_accuracy < 0.90:
        misses.append(f"entry count accuracy {entry_accuracy * 100:.1f}%")
    if exit_accuracy is not None and exit_accuracy < 0.90:
        misses.append(f"exit count accuracy {exit_accuracy * 100:.1f}%")
    if misses:
        section.note += (
            "\n\n**Did not meet target: " + "; ".join(misses) + " (target >= 90%).** "
            "We over-count in both directions - "
            f"{total_in} entries against {total_gt_in} and {total_out} exits against "
            f"{total_gt_out} - and the event-level precision figures below show where it "
            "comes from: extra crossings, not missed ones. The likely cause is people "
            "loitering near the line in a mall corridor, which a shop doorway sees far "
            "less of. Do not present the entry figure as 'meets target' without the exit "
            "figure beside it.")

    section.commands.append("python -m storemind.eval.eval_caviar")
    section.per_clip = reports          # type: ignore[attr-defined]
    section.legacy = legacy_reports     # type: ignore[attr-defined]
    return section


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default=None, choices=list(SCENARIOS))
    parser.add_argument("--view", default=None, choices=list(VIEWS))
    parser.add_argument("--no-legacy", action="store_true")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    if args.scenario:
        views = (args.view,) if args.view else VIEWS
        for view in views:
            report = run_clip(args.scenario, view)
            print(json.dumps(report, indent=2, default=str))
        return 0

    views = (args.view,) if args.view else VIEWS
    section = run_all_clips(views=views, with_legacy=not args.no_legacy)
    if section is None:
        print("no CAVIAR clips found")
        return 1
    print(section.markdown())
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps({"per_clip": section.per_clip, "legacy": section.legacy},
                       indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
