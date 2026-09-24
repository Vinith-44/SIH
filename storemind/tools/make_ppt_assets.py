"""Charts for the deck, built from measured results only.

Reads the JSON the evaluation harness writes and renders PNGs into
`ppt_assets/`. It never takes a number as an argument, so a chart cannot drift
away from what was actually measured - if the evaluation was not run, the chart
is not produced and the script says so.

    python -m storemind.eval.eval_caviar --json data/caviar_full.json
    python -m storemind.eval.eval_forecast --config configs/rush.yaml --backend scripted \\
        --json data/forecast.json
    python tools/make_ppt_assets.py

Design notes (the palette was validated, not eyeballed):
  * two series -> legend present *and* every bar directly labelled;
  * ground truth is a reference rule, not a third bar - it is the target, not a
    competitor;
  * one hue per entity, assigned in fixed order and never recycled;
  * aqua sits below 3:1 on a light surface, so direct labels are mandatory
    relief rather than decoration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Validated categorical slots (light mode, surface #fcfcfb).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8983"
SERIES_1 = "#2a78d6"     # StoreMind
SERIES_2 = "#eb6834"     # legacy
SERIES_3 = "#1baf7a"     # supporting
GRID = "#e5e4df"

ASSETS = Path(__file__).resolve().parents[2] / "ppt_assets"
DATA = Path(__file__).resolve().parents[1] / "data"


def style(ax, title: str, subtitle: str = "", ylabel: str = "") -> None:
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK_2, fontsize=9)
    ax.set_title(title, color=INK, fontsize=13, fontweight="600", loc="left", pad=18)
    if subtitle:
        ax.text(0, 1.03, subtitle, transform=ax.transAxes, color=INK_2, fontsize=9.5,
                va="bottom")


def before_after_chart(caviar: dict, out: Path) -> Path | None:
    per_clip = caviar.get("per_clip") or []
    legacy = caviar.get("legacy") or []
    if not per_clip:
        return None

    gt_in = sum(r["gt"]["entries"] for r in per_clip)
    gt_out = sum(r["gt"]["exits"] for r in per_clip)
    new_in = sum(r["predicted"]["entries"] for r in per_clip)
    new_out = sum(r["predicted"]["exits"] for r in per_clip)
    old_in = sum(r["predicted"]["entries"] for r in legacy) if legacy else None
    old_out = sum(r["predicted"]["exits"] for r in legacy) if legacy else None

    groups = ["People entering", "People leaving"]
    truth = [gt_in, gt_out]
    ours = [new_in, new_out]
    theirs = [old_in, old_out] if old_in is not None else None

    # One panel per direction. A single panel forced the ground-truth rule and
    # its label to run across the neighbouring group, and the labels collided.
    figure, axes = plt.subplots(1, len(groups), figsize=(8.6, 4.4), dpi=200, sharey=True)
    top = max(max(truth), max(ours), max(theirs or [0])) * 1.3
    handles = None

    for index, ax in enumerate(axes):
        series = [("StoreMind", ours[index], SERIES_1)]
        if theirs:
            series.append(("Legacy pipeline", theirs[index], SERIES_2))
        for position, (label, value, colour) in enumerate(series):
            ax.bar([position], [value], 0.55, label=label, color=colour, zorder=3)
            ax.text(position, value, f"{int(value)}", ha="center", va="bottom",
                    color=INK, fontsize=11, fontweight="600")

        # Ground truth is the target, drawn as a rule rather than a rival bar.
        ax.axhline(truth[index], linestyle=(0, (4, 3)), color=INK_2, linewidth=2, zorder=4)
        ax.text(len(series) - 0.35, truth[index], f"ground truth {truth[index]}",
                color=INK_2, fontsize=9, va="bottom", ha="right")

        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(GRID)
        ax.tick_params(colors=INK_2, labelsize=9, length=0)
        ax.yaxis.grid(True, color=GRID, linewidth=1)
        ax.set_axisbelow(True)
        ax.set_xticks([])
        ax.set_xlim(-0.6, len(series) - 0.4)
        ax.set_ylim(0, top)
        ax.set_xlabel(groups[index], color=INK, fontsize=11, labelpad=10)
        if index == 0:
            ax.set_ylabel("crossings counted", color=INK_2, fontsize=9)
            handles = ax.get_legend_handles_labels()

    clips = len(per_clip)
    figure.set_facecolor(SURFACE)
    figure.suptitle("Entry and exit counting on real CCTV footage", color=INK, fontsize=14,
                    fontweight="600", x=0.012, ha="left", y=1.045)
    figure.text(0.012, 0.975,
                f"CAVIAR shopping centre, {clips} clips, {gt_in + gt_out} ground-truth "
                "crossings. Closer to the dashed line is better.",
                color=INK_2, fontsize=9.5, ha="left", va="top")
    if handles:
        legend = figure.legend(*handles, frameon=False, loc="lower left",
                               bbox_to_anchor=(0.012, -0.035), ncol=2, fontsize=10)
        for text in legend.get_texts():
            text.set_color(INK_2)
    figure.text(0.012, -0.075,
                "Data bucket A - public benchmark. Credit: EC Funded CAVIAR project / "
                "IST 2001 37540.", color=INK_3, fontsize=7.5, ha="left")
    figure.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(figure)
    return out


def forecast_chart(forecast: dict, truth_json: Path, out: Path) -> Path | None:
    if not truth_json.is_file():
        return None
    truth = json.loads(truth_json.read_text(encoding="utf-8"))
    series = truth.get("queue_every_10s") or {}
    if not series:
        return None

    figure, ax = plt.subplots(figsize=(8.2, 4.2), dpi=200)
    # One measure (people waiting), summed across counters: one line, no legend
    # box needed - the title names it.
    times: list[float] = []
    totals: list[float] = []
    per_counter = list(series.values())
    for index in range(min(len(s) for s in per_counter)):
        times.append(per_counter[0][index][0] / 60.0)
        totals.append(sum(s[index][1] for s in per_counter))
    ax.plot(times, totals, color=SERIES_1, linewidth=2, zorder=3)
    ax.fill_between(times, totals, color=SERIES_1, alpha=0.10, zorder=2)

    warning = forecast.get("first_warning_s")
    onset = forecast.get("congestion_onset_truth_s")
    top = max(totals) * 1.3 if totals else 10

    # The two markers are close together, so they label outwards - left of the
    # warning, right of the onset - rather than colliding in the middle.
    if warning is not None:
        ax.axvline(warning / 60.0, color=SERIES_2, linewidth=2, zorder=4)
        ax.text(warning / 60.0 - 0.3, top * 0.97,
                "StoreMind:\n“open another counter”",
                color=SERIES_2, fontsize=9.5, va="top", ha="right", fontweight="600")
    if onset is not None:
        ax.axvline(onset / 60.0, color=INK_2, linewidth=2, linestyle=(0, (4, 3)), zorder=4)
        ax.text(onset / 60.0 + 0.3, top * 0.97, "queue actually\nbecame long",
                color=INK_2, fontsize=9.5, va="top", ha="left")
    if warning is not None and onset is not None:
        mid = (warning + onset) / 2 / 60.0
        lead = forecast.get("lead_time_min")
        ax.annotate("", xy=(onset / 60.0, top * 0.62), xytext=(warning / 60.0, top * 0.62),
                    arrowprops={"arrowstyle": "<->", "color": INK_2, "linewidth": 1.5})
        ax.text(mid, top * 0.66, f"{lead:.1f} min of warning", color=INK, fontsize=11,
                fontweight="700", ha="center")

    style(ax, "The door warns us before the queue does",
          "Simulated rush with a known 6-minute shopping trip. The forecast sees only "
          "entrance counts and the counters' own queue events.",
          "people waiting at checkout")
    ax.set_xlabel("minutes into the clip", color=INK_2, fontsize=9)
    ax.set_ylim(0, top)
    ax.set_xlim(0, max(times) if times else 25)
    figure.text(0.01, 0.005,
                "Data bucket C - simulation: proves the logic, not real-store accuracy. "
                "Predictive checkout staffing is prior art (Irisys US7778855B2, Xovis); "
                "ours runs on existing CCTV, offline, and learns the lag itself.",
                color=INK_3, fontsize=7.5)
    figure.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(figure)
    return out


def tracking_chart(caviar: dict, out: Path) -> Path | None:
    per_clip = caviar.get("per_clip") or []
    legacy = caviar.get("legacy") or []
    if not per_clip or not legacy:
        return None

    ours = sum(r["tracking"]["id_switches"] for r in per_clip)
    gt_boxes = sum(r["tracking"].get("gt_boxes_in_processed_frames")
                   or r["tracking"].get("gt_boxes", 0) for r in per_clip)
    legacy_ids = sum(r["track_ids_created"] for r in legacy)
    real_people = sum(r["annotated_people"] for r in per_clip)

    figure, (left, right) = plt.subplots(1, 2, figsize=(8.6, 3.8), dpi=200,
                                         gridspec_kw={"width_ratios": [1, 1.25]})

    # One number with nothing to compare it against is a stat tile, not a bar
    # chart - a lone bar against an arbitrary axis maximum says nothing.
    left.axis("off")
    left.set_facecolor(SURFACE)
    rate = ours / gt_boxes * 100 if gt_boxes else 0
    left.text(0.0, 0.72, f"{ours}", color=SERIES_1, fontsize=58, fontweight="700",
              ha="left", va="center")
    left.text(0.0, 0.45, "identity switches", color=INK, fontsize=13, fontweight="600",
              ha="left", va="center")
    left.text(0.0, 0.32, f"across {gt_boxes:,} annotated boxes\n"
                         f"({rate:.2f}% of tracked detections)",
              color=INK_2, fontsize=10, ha="left", va="top")

    right.bar(["real people", "legacy track IDs"], [real_people, legacy_ids], 0.5,
              color=[SERIES_3, SERIES_2], zorder=3)
    for index, value in enumerate([real_people, legacy_ids]):
        right.text(index, value, f"{value}", ha="center", va="bottom", color=INK,
                   fontsize=12, fontweight="700")
    style(right, "Legacy tracker fragmentation",
          "one person should be one ID", "identities")
    right.set_ylim(0, max(real_people, legacy_ids) * 1.3)

    figure.text(0.01, 0.005,
                "Data bucket A - CAVIAR. Credit: EC Funded CAVIAR project / IST 2001 37540.",
                color=INK_3, fontsize=7.5)
    figure.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, facecolor=SURFACE, bbox_inches="tight")
    plt.close(figure)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--caviar-json", default=str(DATA / "caviar_full.json"))
    parser.add_argument("--forecast-json", default=str(DATA / "forecast.json"))
    parser.add_argument("--out", default=str(ASSETS))
    args = parser.parse_args()

    out_dir = Path(args.out)
    written: list[Path] = []
    missing: list[str] = []

    caviar_path = Path(args.caviar_json)
    if caviar_path.is_file():
        caviar = json.loads(caviar_path.read_text(encoding="utf-8"))
        for builder, name in ((before_after_chart, "caviar_before_after.png"),
                              (tracking_chart, "caviar_tracking.png")):
            path = builder(caviar, out_dir / name)
            (written if path else missing).append(path or name)
    else:
        missing.append(f"{caviar_path} (run: python -m storemind.eval.eval_caviar "
                       f"--json {caviar_path})")

    forecast_path = Path(args.forecast_json)
    if forecast_path.is_file():
        forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
        truth = Path(forecast["counter_video"])
        truth = truth.with_name(f"{truth.stem}_truth.json")
        path = forecast_chart(forecast, truth, out_dir / "forecast_timeline.png")
        (written if path else missing).append(path or "forecast_timeline.png")
    else:
        missing.append(f"{forecast_path} (run: python -m storemind.eval.eval_forecast "
                       f"--config configs/rush.yaml --backend scripted --json {forecast_path})")

    for path in written:
        print(f"wrote {path}")
    for item in missing:
        print(f"SKIPPED - needs {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
