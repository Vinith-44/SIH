"""CAVIAR ground truth: CVML XML -> our CSV format.

Data: EC Funded CAVIAR project / IST 2001 37540, CC BY-SA.
Clips <https://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1/>, ground truth
<https://homepages.inf.ed.ac.uk/rbf/CAVIAR/gt.htm>. Credit is required and is
reproduced in `videos/entrance/caviar/SOURCE.txt` and in RESULTS.md.

Per `research/09b_TEST_DATA_VALIDITY.md` this is **bucket A**: a public benchmark
with published ground truth, valid for entrance counting, occupancy and
tracking, and not valid for queues or shelves. It is also *easier* than an
Indian store - 384x288, sparse crowd - and RESULTS.md says so.

The XML gives, per frame, per person: a box (`xc`, `yc`, `w`, `h` - centre plus
size), an `appearance` state, and a `context` activity label that includes
**"shop enter"** and **"shop exit"**. That yields two independent kinds of
ground truth:

1.  **Line crossings** derived from the ground-truth trajectories. This is what
    our line counter is actually trying to reproduce, so it is the fair target.
    A crossing is counted when a track's foot point moves from more than
    `gt_margin` px on one side of the line to more than `gt_margin` px on the
    other. The margin exists to absorb annotation jitter, not to flatter us -
    `crossing_sensitivity()` reports the count at several margins so a reader can
    see the ground truth is not tuned.
2.  **Shop enter / exit episodes** from the `context` label - a semantic
    check that does not depend on where anyone drew a line.

Counting *people per frame* also gives an occupancy reference that needs no line
at all.
"""

from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CAVIAR_CREDIT = "EC Funded CAVIAR project/IST 2001 37540 (CC BY-SA)"
CAVIAR_FPS = 25.0

# scenario stem -> (corridor xml, front xml)
SCENARIOS: dict[str, tuple[str, str]] = {
    "EnterExitCrossingPaths1": ("ceecp1gt.xml", "feecp1gt.xml"),
    "EnterExitCrossingPaths2": ("ceecp2gt.xml", "feecp2gt.xml"),
    "OneShopOneWait1": ("cosow1gt.xml", "fosow1gt.xml"),
    "OneShopOneWait2": ("cosow2gt.xml", "fosow2gt.xml"),
    "OneStopEnter1": ("cose1gt.xml", "fose1gt.xml"),
    "OneStopEnter2": ("cose2gt.xml", "fose2gt.xml"),
    "ThreePastShop1": ("c3ps1gt.xml", "f3ps1gt.xml"),
    "WalkByShop1": ("cwbs1gt.xml", "fwbs1gt.xml"),
}


@dataclass
class GtBox:
    frame: int
    track: int
    xc: float
    yc: float
    w: float
    h: float
    context: str = ""
    appearance: str = ""

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (self.xc - self.w / 2, self.yc - self.h / 2,
                self.xc + self.w / 2, self.yc + self.h / 2)

    @property
    def foot(self) -> tuple[float, float]:
        return (self.xc, self.yc + self.h / 2)


@dataclass
class GtClip:
    name: str
    xml_path: Path
    frames: int
    boxes: list[GtBox] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.frames / CAVIAR_FPS

    def by_frame(self) -> dict[int, list[GtBox]]:
        out: dict[int, list[GtBox]] = {}
        for box in self.boxes:
            out.setdefault(box.frame, []).append(box)
        return out

    def by_track(self) -> dict[int, list[GtBox]]:
        out: dict[int, list[GtBox]] = {}
        for box in self.boxes:
            out.setdefault(box.track, []).append(box)
        for series in out.values():
            series.sort(key=lambda b: b.frame)
        return out

    def people_per_frame(self) -> list[tuple[float, int]]:
        """(video seconds, number of annotated people). Frames with nobody in
        them are annotated as empty, so absent frames count as zero."""
        counts = {f: len(boxes) for f, boxes in self.by_frame().items()}
        return [(f / CAVIAR_FPS, counts.get(f, 0)) for f in range(self.frames)]

    def context_episodes(self, label: str) -> list[tuple[int, float, float]]:
        """Maximal runs of a context label per track.

        Returns (track, start seconds, end seconds). One episode is one event:
        a shopper entering the shop is annotated over many consecutive frames.
        """
        episodes: list[tuple[int, float, float]] = []
        for track, series in self.by_track().items():
            run_start: int | None = None
            previous_frame: int | None = None
            for box in series:
                matches = box.context.strip().lower() == label
                if matches and run_start is None:
                    run_start = box.frame
                elif not matches and run_start is not None:
                    episodes.append((track, run_start / CAVIAR_FPS,
                                     (previous_frame or box.frame) / CAVIAR_FPS))
                    run_start = None
                if matches:
                    previous_frame = box.frame
            if run_start is not None:
                episodes.append((track, run_start / CAVIAR_FPS,
                                 (previous_frame or run_start) / CAVIAR_FPS))
        episodes.sort(key=lambda e: e[1])
        return episodes


def parse_cvml(path: str | Path) -> GtClip:
    path = Path(path)
    root = ET.parse(path).getroot()
    boxes: list[GtBox] = []
    highest = -1
    for frame_element in root.iter("frame"):
        number = int(frame_element.get("number", "0"))
        highest = max(highest, number)
        for obj in frame_element.iter("object"):
            box_element = obj.find("box")
            if box_element is None:
                continue
            context_element = obj.find("hypothesislist/hypothesis/context")
            appearance_element = obj.find("appearance")
            boxes.append(GtBox(
                frame=number,
                track=int(obj.get("id", "-1")),
                xc=float(box_element.get("xc", "0")),
                yc=float(box_element.get("yc", "0")),
                w=float(box_element.get("w", "0")),
                h=float(box_element.get("h", "0")),
                context=(context_element.text or "").strip() if context_element is not None else "",
                appearance=(appearance_element.text or "").strip()
                if appearance_element is not None else "",
            ))
    return GtClip(name=root.get("name", path.stem), xml_path=path,
                  frames=highest + 1, boxes=boxes)


# --------------------------------------------------------------------------- #
# Line crossings from ground-truth trajectories
# --------------------------------------------------------------------------- #

def signed_distance(point: tuple[float, float], a: tuple[float, float],
                    b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = float(np.hypot(dx, dy))
    if length < 1e-9:
        return 0.0
    return float((dx * (point[1] - a[1]) - dy * (point[0] - a[0])) / length)


def gt_crossings(clip: GtClip, line_a: tuple[float, float], line_b: tuple[float, float],
                 entry_direction: str = "pos", gt_margin: float = 8.0
                 ) -> list[tuple[float, int, str]]:
    """Crossings of `line` by the ground-truth tracks.

    A track contributes a crossing every time its foot point moves decisively
    (more than `gt_margin` px) from one side to the other. Undecided samples -
    inside the band - are ignored rather than forced to a side, which is what
    stops a person loitering on the line from generating a stream of crossings.
    """
    out: list[tuple[float, int, str]] = []
    for track, series in clip.by_track().items():
        side = 0
        for box in series:
            distance = signed_distance(box.foot, line_a, line_b)
            current = 1 if distance > gt_margin else (-1 if distance < -gt_margin else 0)
            if current == 0:
                continue
            if side == 0:
                side = current
                continue
            if current == side:
                continue
            positive = current > side
            entering = positive if entry_direction == "pos" else not positive
            out.append((box.frame / CAVIAR_FPS, track, "in" if entering else "out"))
            side = current
    out.sort()
    return out


def crossing_sensitivity(clip: GtClip, line_a, line_b, entry_direction: str = "pos",
                         margins: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0)) -> dict[float, dict]:
    """How much the ground truth moves as the jitter margin changes.

    If the counts are stable across margins, the ground truth is a property of
    the data. If they swing wildly, the line is in a bad place and we should say
    so rather than pick the margin that looks best.
    """
    out: dict[float, dict] = {}
    for margin in margins:
        crossings = gt_crossings(clip, line_a, line_b, entry_direction, margin)
        out[margin] = {
            "in": sum(1 for _, _, d in crossings if d == "in"),
            "out": sum(1 for _, _, d in crossings if d == "out"),
        }
    return out


# --------------------------------------------------------------------------- #
# Writing our CSV formats
# --------------------------------------------------------------------------- #

def write_entries_csv(path: Path, crossings: list[tuple[float, int, str]],
                      note: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["video_time_s", "direction", "note"])
        for at, track, direction in crossings:
            writer.writerow([f"{at:.2f}", direction, note or f"gt_track={track}"])
    return path


def write_people_csv(path: Path, series: list[tuple[float, int]], step_s: float = 1.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["video_time_s", "people_in_frame"])
        next_at = 0.0
        for at, count in series:
            if at + 1e-9 >= next_at:
                writer.writerow([f"{at:.2f}", count])
                next_at = at + step_s
    return path


def write_shop_csv(path: Path, clip: GtClip) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["video_time_s", "direction", "note"])
        rows = [(start, "in", f"shop enter, gt_track={track}")
                for track, start, _end in clip.context_episodes("shop enter")]
        rows += [(start, "out", f"shop exit, gt_track={track}")
                 for track, start, _end in clip.context_episodes("shop exit")]
        for at, direction, note in sorted(rows):
            writer.writerow([f"{at:.2f}", direction, note])
    return path


def video_for(xml_path: Path, scenario: str, view: str) -> Path:
    suffix = "cor" if view == "corridor" else "front"
    return xml_path.parent / f"{scenario}{suffix}.mpg"
