"""Generate synthetic test clips with exact ground truth.

Why this exists: until the team records real footage, we still have to prove that
the *logic* (line crossing, hysteresis, queue timers, shelf voting) is correct.
A synthetic scene is the only input where the ground truth is known to the
millisecond, so bugs cannot hide behind "well, the detector missed someone".

Each scene writes three things next to each other:

    <name>.mp4                  the video (for eyeballing and for --show)
    <name>_detections.json      exact person boxes per frame
    <name>_gt_*.csv             ground truth in the same format the team fills in
                                by hand for real videos

`storemind.inference.scripted.ScriptedDetector` replays the detections JSON, so a
pipeline run over a synthetic clip measures our analytics with a perfect
detector.  That separation is deliberate and is stated in RESULTS.md: synthetic
numbers grade the *logic*, real videos grade the *system*.

    python tools/make_synthetic_video.py --out ../videos --scene all
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

WIDTH, HEIGHT, FPS = 960, 540, 25
FLOOR = (38, 42, 48)


@dataclass
class Person:
    pid: int
    path: list[tuple[float, float]]      # one (x, y) of the FOOT point per frame
    start_frame: int
    height_px: float = 150.0
    width_px: float = 55.0
    colour: tuple[int, int, int] = (200, 200, 200)

    def box_at(self, frame: int) -> tuple[float, float, float, float] | None:
        index = frame - self.start_frame
        if index < 0 or index >= len(self.path):
            return None
        x, y = self.path[index]
        return (x - self.width_px / 2, y - self.height_px, x + self.width_px / 2, y)


@dataclass
class Scene:
    name: str
    people: list[Person] = field(default_factory=list)
    frames: int = 0
    decor: list = field(default_factory=list)
    gt: dict = field(default_factory=dict)
    width: int = WIDTH
    height: int = HEIGHT
    fps: int = FPS
    gt_kind: str = ""          # which ground-truth writer to use


def _draw_person(canvas: np.ndarray, box: tuple[float, float, float, float],
                 colour: tuple[int, int, int]) -> None:
    x1, y1, x2, y2 = (int(v) for v in box)
    cx = (x1 + x2) // 2
    head_r = max(4, (x2 - x1) // 4)
    body_top = y1 + 2 * head_r
    cv2.ellipse(canvas, (cx, (body_top + y2) // 2),
                ((x2 - x1) // 2, (y2 - body_top) // 2), 0, 0, 360, colour, -1)
    cv2.circle(canvas, (cx, y1 + head_r), head_r, (int(colour[0] * 0.7),
                                                   int(colour[1] * 0.7),
                                                   int(colour[2] * 0.7)), -1)


def _render(scene: Scene, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / f"{scene.name}.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             scene.fps, (scene.width, scene.height))
    detections: dict[str, list] = {}
    rng = np.random.default_rng(7)

    for frame in range(scene.frames):
        canvas = np.full((scene.height, scene.width, 3), FLOOR, dtype=np.uint8)
        canvas += rng.integers(0, 7, canvas.shape, dtype=np.uint8)   # mild sensor noise
        for draw in scene.decor:
            draw(canvas, frame)
        boxes = []
        for person in scene.people:
            box = person.box_at(frame)
            if box is None:
                continue
            _draw_person(canvas, box, person.colour)
            boxes.append([round(v, 1) for v in box])
        detections[str(frame)] = boxes
        writer.write(canvas)
    writer.release()

    json_path = out_dir / f"{scene.name}_detections.json"
    json_path.write_text(json.dumps({"fps": scene.fps, "width": scene.width,
                                     "height": scene.height, "frames": detections}),
                         encoding="utf-8")
    return video_path, json_path


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #

def scene_entrance(seconds: int = 120, seed: int = 11) -> Scene:
    """People walk up and down through a horizontal counting line at y = 0.5.

    Ground truth is exact: we know who crossed, in which direction, and when.
    Two deliberate traps are included:
      * a "loiterer" who walks onto the line, hovers on it, and returns the way
        they came - a centre-point counter without hysteresis double-counts them;
      * two people who cross side by side, close enough to swap IDs in a weak
        tracker.
    """
    rng = random.Random(seed)
    frames = seconds * FPS
    line_y = HEIGHT * 0.5
    people: list[Person] = []
    crossings: list[tuple[float, int, str]] = []
    pid = 1

    def walker(start_frame: int, x: float, downward: bool, speed: float) -> Person:
        nonlocal pid
        path = []
        y = (line_y - 220.0) if downward else (line_y + 220.0)
        step = speed if downward else -speed
        drift = rng.uniform(-0.25, 0.25)
        for i in range(int(440 / speed) + 1):
            path.append((x + drift * i, y + step * i))
        person = Person(pid, path, start_frame,
                        colour=(rng.randint(120, 235), rng.randint(120, 235), rng.randint(120, 235)))
        pid += 1
        return person

    # 18 clean crossings, alternating direction, spread over the clip
    schedule = [(int(6 * FPS + i * 5.5 * FPS), rng.uniform(180, WIDTH - 180), i % 3 != 2)
                for i in range(18)]
    for start, x, downward in schedule:
        speed = rng.uniform(3.0, 5.0)
        person = walker(start, x, downward, speed)
        people.append(person)
        cross_index = next((i for i, (_, y) in enumerate(person.path)
                            if (y >= line_y) == downward), None)
        if cross_index is not None:
            crossings.append(((start + cross_index) / FPS, person.pid,
                              "in" if downward else "out"))

    # Trap 1: loiterer - approaches, sits on the line, goes back. Must count 0.
    loiter_start = int(70 * FPS)
    path = []
    y = line_y - 200
    for _ in range(40):
        y += 5
        path.append((300.0, y))
    for i in range(60):
        path.append((300.0 + math.sin(i / 4) * 6, line_y + math.sin(i / 3) * 7))
    for _ in range(40):
        y = path[-1][1] - 5
        path.append((300.0, y))
    people.append(Person(pid, path, loiter_start, colour=(90, 200, 250)))
    pid += 1

    # Trap 2: two people crossing shoulder to shoulder.
    pair_start = int(95 * FPS)
    for offset in (-32, 32):
        path = [(560.0 + offset, line_y - 210 + 4.2 * i) for i in range(110)]
        people.append(Person(pid, path, pair_start, colour=(250, 180, 90)))
        cross_index = next((i for i, (_, yy) in enumerate(path) if yy >= line_y), None)
        if cross_index is not None:
            crossings.append(((pair_start + cross_index) / FPS, pid, "in"))
        pid += 1

    def draw_line(canvas: np.ndarray, _frame: int) -> None:
        cv2.line(canvas, (0, int(line_y)), (WIDTH, int(line_y)), (70, 70, 90), 1)

    crossings.sort()
    return Scene(name="synthetic_entrance", people=people, frames=frames, decor=[draw_line],
                 gt={"crossings": crossings,
                     "entries": sum(1 for c in crossings if c[2] == "in"),
                     "exits": sum(1 for c in crossings if c[2] == "out"),
                     "line_y_norm": 0.5})


def _step_towards(current: tuple[float, float], target: tuple[float, float],
                  speed: float) -> tuple[float, float]:
    dx, dy = target[0] - current[0], target[1] - current[1]
    distance = math.hypot(dx, dy)
    if distance <= speed:
        return target
    return (current[0] + dx / distance * speed, current[1] + dy / distance * speed)


def scene_queue(seconds: int = 180, seed: int = 23) -> Scene:
    """A single billing counter.

    Each shopper walks into the lane, shuffles to the head, holds the billing
    spot for a known service time, then leaves.  Ground truth therefore contains
    an exact wait and service time per shopper, and an exact queue length at any
    instant - which is what `eval/eval_queue.py` scores against.
    """
    rng = random.Random(seed)
    frames = seconds * FPS
    people: list[Person] = []
    records: list[dict] = []
    lane_x, billing_y = WIDTH * 0.55, HEIGHT * 0.34
    lane_bottom = HEIGHT * 0.95
    head_y = HEIGHT * 0.56          # where the first person in line stands
    spacing = 62.0                  # pixels between people in the line

    # First plan the schedule: arrivals and service durations, then serve
    # first-come-first-served on one counter.
    plan: list[dict] = []
    arrival = 3.0
    counter_free_at = 4.0
    pid = 1
    while arrival < seconds - 35:
        service_s = rng.uniform(14.0, 30.0)
        start_service = max(arrival + 3.0, counter_free_at)
        if start_service + service_s > seconds - 4:
            break
        counter_free_at = start_service + service_s
        plan.append({
            "shopper": pid,
            "arrival_s": arrival,
            "service_start_s": start_service,
            "service_end_s": start_service + service_s,
            "service_s": service_s,
            "lane_x": lane_x + rng.uniform(-16, 16),
            "colour": (rng.randint(120, 235), rng.randint(120, 235), rng.randint(120, 235)),
        })
        pid += 1
        arrival += rng.uniform(9.0, 26.0)

    # Then simulate positions frame by frame.  People stand in a real line and
    # shuffle forward as those ahead are served - if everyone stood on the same
    # spot the clip would be testing overlapping boxes, not a queue.
    paths: dict[int, list[tuple[float, float]]] = {p["shopper"]: [] for p in plan}
    starts: dict[int, int] = {}
    position: dict[int, tuple[float, float]] = {}

    for frame in range(frames):
        t = frame / FPS
        waiting = [p for p in plan if p["arrival_s"] <= t < p["service_start_s"]]
        waiting.sort(key=lambda p: p["arrival_s"])
        for rank, person in enumerate(waiting):
            target = (person["lane_x"], min(lane_bottom, head_y + rank * spacing))
            if person["shopper"] not in starts:
                starts[person["shopper"]] = frame
                position[person["shopper"]] = (person["lane_x"], lane_bottom + 10)
            position[person["shopper"]] = _step_towards(position[person["shopper"]], target, 4.5)
            jitter = math.sin(frame / 13.0 + rank) * 2.0
            x, y = position[person["shopper"]]
            paths[person["shopper"]].append((x + jitter, y))

        for person in plan:
            pid_ = person["shopper"]
            if person["service_start_s"] <= t < person["service_end_s"]:
                target = (person["lane_x"], billing_y)
                position[pid_] = _step_towards(position.get(pid_, (person["lane_x"], head_y)),
                                               target, 6.0)
                x, y = position[pid_]
                paths[pid_].append((x + math.sin(frame / 9.0) * 2.5,
                                    y + math.cos(frame / 13.0) * 2.5))
            elif person["service_end_s"] <= t < person["service_end_s"] + 2.5:
                target = (lane_x - 300, HEIGHT * 0.18)
                position[pid_] = _step_towards(position.get(pid_, (person["lane_x"], billing_y)),
                                               target, 9.0)
                paths[pid_].append(position[pid_])

    for person in plan:
        pid_ = person["shopper"]
        if not paths[pid_]:
            continue
        people.append(Person(pid_, paths[pid_], starts.get(pid_, int(person["arrival_s"] * FPS)),
                             height_px=140, width_px=52, colour=person["colour"]))
        # "joined" = the moment the foot point enters the lane polygon, which is
        # also how QueueEngine defines it.  Ground truth and engine must share a
        # definition or the MAE measures the disagreement, not the accuracy.
        records.append({
            "shopper": pid_,
            "joined_s": round(person["arrival_s"], 2),
            "service_start_s": round(person["service_start_s"], 2),
            "service_s": round(person["service_s"], 2),
            "wait_s": round(person["service_start_s"] - person["arrival_s"], 2),
        })

    def draw_counter(canvas: np.ndarray, _frame: int) -> None:
        cv2.rectangle(canvas, (int(lane_x - 150), int(billing_y - 70)),
                      (int(lane_x + 150), int(billing_y - 20)), (90, 80, 70), -1)

    # exact queue length every 10 s, counting people who have joined but whose
    # service has not started
    queue_series = []
    for t in range(0, seconds, 10):
        count = sum(1 for r in records if r["joined_s"] <= t < r["service_start_s"])
        queue_series.append((t, count))

    return Scene(name="synthetic_queue", people=people, frames=frames, decor=[draw_counter],
                 gt={"shoppers": records, "queue_every_10s": queue_series,
                     "lane_x_norm": 0.55, "billing_y_norm": billing_y / HEIGHT})


def scene_shelf(seconds: int = 150, seed: int = 31) -> Scene:
    """A four-slot shelf that is emptied slot by slot, with a shopper standing in
    front of it for 20 s to exercise the occlusion gate."""
    rng = random.Random(seed)
    frames = seconds * FPS
    slots = [(0.10, 0.22, 0.30, 0.55), (0.34, 0.22, 0.54, 0.55),
             (0.58, 0.22, 0.78, 0.55), (0.10, 0.60, 0.30, 0.90)]
    # (slot index, second at which it becomes empty)
    empty_at = [("A1", 0, 45.0), ("A2", 1, 90.0), ("A3", 2, 120.0), ("B1", 3, 1e9)]
    packet_colours = [(60, 90, 230), (70, 200, 120), (230, 170, 60), (200, 110, 210)]

    def draw_shelf(canvas: np.ndarray, frame: int) -> None:
        t = frame / FPS
        cv2.rectangle(canvas, (int(0.06 * WIDTH), int(0.16 * HEIGHT)),
                      (int(0.84 * WIDTH), int(0.95 * HEIGHT)), (58, 62, 70), -1)
        for name, index, when in empty_at:
            x1, y1, x2, y2 = slots[index]
            px1, py1 = int(x1 * WIDTH), int(y1 * HEIGHT)
            px2, py2 = int(x2 * WIDTH), int(y2 * HEIGHT)
            cv2.rectangle(canvas, (px1, py1), (px2, py2), (44, 47, 54), -1)
            if t >= when:
                continue
            colour = packet_colours[index]
            cols, rows = 4, 3
            w = (px2 - px1) // cols
            h = (py2 - py1) // rows
            for r in range(rows):
                for c in range(cols):
                    bx, by = px1 + c * w + 3, py1 + r * h + 3
                    cv2.rectangle(canvas, (bx, by), (bx + w - 6, by + h - 6), colour, -1)
                    cv2.rectangle(canvas, (bx, by), (bx + w - 6, by + h - 6), (20, 20, 20), 1)
                    cv2.line(canvas, (bx + 4, by + h // 2), (bx + w - 10, by + h // 2),
                             (250, 250, 250), 1)

    # One shopper stands in front of slots A1/A2 from t=60 s for 20 s.
    occluder_start = int(60 * FPS)
    path = [(0.30 * WIDTH, 0.95 * HEIGHT)] * 1
    path = [(0.30 * WIDTH, 0.95 * HEIGHT + 0) for _ in range(1)]
    path = []
    for i in range(int(20 * FPS)):
        path.append((0.28 * WIDTH + math.sin(i / 20.0) * 8, 0.97 * HEIGHT))
    occluder = Person(1, path, occluder_start, height_px=330, width_px=170,
                      colour=(120, 120, 130))

    return Scene(name="synthetic_shelf", people=[occluder], frames=frames,
                 decor=[draw_shelf],
                 gt={"slots": [{"slot": name, "polygon": slots[i], "empty_from_s": when}
                               for name, i, when in empty_at],
                     "occlusion_window_s": [60.0, 80.0]})


def scene_rush(seconds: int = 1500, seed: int = 41) -> list[Scene]:
    """The scenario the whole forecast idea stands or falls on.

    Two clips on ONE timeline: an entrance camera and a two-counter checkout.
    Shoppers who walk in at time t reach a counter at t + L, with a fixed
    shopping-trip lag plus jitter.  A rush starts part way through the clip, so
    the door sees the surge roughly L minutes before the checkout does.

    `eval_forecast.py` then answers the question a judge will ask: how many
    minutes before the queue actually got long did the system say "open another
    counter"?  Ground truth is the true queue length per counter, taken from the
    same simulation that drew the pixels.

    Rendered at 640x360 / 8 fps: 25 minutes at full resolution would be hundreds
    of megabytes for no extra information.
    """
    rng = random.Random(seed)
    width, height, fps = 640, 360, 8
    frames = seconds * fps
    lag_s = 360.0                 # 6-minute shopping trip
    conversion = 0.7
    counters = ["counter-1", "counter-2"]
    service_mean_s = 48.0

    # --- arrivals at the door ------------------------------------------- #
    # Two counters at ~48 s per bill can serve about 2.5 shoppers/min.  The quiet
    # rate sits below that and the rush sits above it, so a real backlog builds
    # and then drains - which is what makes the lead-time measurement meaningful.
    def rate_per_min(t: float) -> float:
        if t < 420:
            return 2.0                                   # quiet: 1.4/min at checkout
        if t < 540:
            return 2.0 + 4.0 * (t - 420) / 120.0         # ramp up over 2 minutes
        if t < 900:
            return 6.0                                   # rush: 4.2/min at checkout
        if t < 1020:
            return 6.0 - 4.0 * (t - 900) / 120.0         # ramp down
        return 2.0

    door_times: list[float] = []
    t = 2.0
    while t < seconds - 120:
        t += rng.expovariate(max(0.2, rate_per_min(t)) / 60.0)
        if t < seconds - 120:
            door_times.append(t)

    # --- entrance clip ---------------------------------------------------- #
    line_y = height * 0.5
    entrance_people: list[Person] = []
    crossings: list[tuple[float, int, str]] = []
    pid = 1
    for at in door_times:
        speed = rng.uniform(2.4, 3.6)
        x = rng.uniform(90, width - 90)
        path = [(x, line_y - 130 + speed * i) for i in range(int(260 / speed) + 1)]
        start_frame = int(at * fps)
        entrance_people.append(Person(pid, path, start_frame, height_px=95, width_px=34,
                                      colour=(rng.randint(120, 235), rng.randint(120, 235),
                                              rng.randint(120, 235))))
        cross = next((i for i, (_, y) in enumerate(path) if y >= line_y), None)
        if cross is not None:
            crossings.append(((start_frame + cross) / fps, pid, "in"))
        pid += 1

    def draw_line(canvas: np.ndarray, _frame: int) -> None:
        cv2.line(canvas, (0, int(line_y)), (width, int(line_y)), (70, 70, 90), 1)

    entrance = Scene(name="synthetic_rush_entrance", people=entrance_people, frames=frames,
                     decor=[draw_line], width=width, height=height, fps=fps,
                     gt_kind="entrance",
                     gt={"crossings": sorted(crossings),
                         "entries": len(crossings), "exits": 0,
                         "true_lag_min": lag_s / 60.0,
                         "conversion": conversion})

    # --- checkout clip ----------------------------------------------------- #
    checkout_times = sorted(at + lag_s + rng.uniform(-45, 45)
                            for at in door_times if rng.random() < conversion)
    lane_x = {"counter-1": width * 0.32, "counter-2": width * 0.68}
    billing_y = height * 0.30
    head_y = height * 0.52
    spacing = 42.0
    lane_bottom = height * 0.95

    free_at = {name: 0.0 for name in counters}
    plan: list[dict] = []
    cid = 1
    for arrival in checkout_times:
        # Shoppers join whichever counter will serve them soonest.
        counter = min(counters, key=lambda c: max(free_at[c], arrival))
        service_s = max(15.0, rng.gauss(service_mean_s, 12.0))
        start = max(arrival, free_at[counter])
        free_at[counter] = start + service_s
        # A shopper whose service would start after the clip ends is kept: they
        # are still standing in the queue for the whole remaining clip, and
        # dropping them would make the ground-truth queue shorter than reality.
        plan.append({"id": cid, "counter": counter, "arrival_s": arrival,
                     "start_s": start, "end_s": start + service_s, "service_s": service_s,
                     "x": lane_x[counter] + rng.uniform(-10, 10),
                     "colour": (rng.randint(120, 235), rng.randint(120, 235),
                                rng.randint(120, 235))})
        cid += 1

    paths: dict[int, list[tuple[float, float]]] = {p["id"]: [] for p in plan}
    starts: dict[int, int] = {}
    position: dict[int, tuple[float, float]] = {}
    queue_series: dict[str, list[tuple[float, int]]] = {c: [] for c in counters}

    for frame in range(frames):
        now = frame / fps
        for counter in counters:
            waiting = [p for p in plan
                       if p["counter"] == counter and p["arrival_s"] <= now < p["start_s"]]
            waiting.sort(key=lambda p: p["arrival_s"])
            if frame % (fps * 10) == 0:
                queue_series[counter].append((now, len(waiting)))
            for rank, person in enumerate(waiting):
                target = (person["x"], min(lane_bottom, head_y + rank * spacing))
                if person["id"] not in starts:
                    starts[person["id"]] = frame
                    position[person["id"]] = (person["x"], lane_bottom + 8)
                position[person["id"]] = _step_towards(position[person["id"]], target, 5.0)
                x, y = position[person["id"]]
                paths[person["id"]].append((x + math.sin(frame / 11.0 + rank) * 1.5, y))

        for person in plan:
            key = person["id"]
            if person["start_s"] <= now < person["end_s"]:
                position[key] = _step_towards(position.get(key, (person["x"], head_y)),
                                              (person["x"], billing_y), 6.0)
                paths[key].append(position[key])
            elif person["end_s"] <= now < person["end_s"] + 2.0:
                position[key] = _step_towards(position.get(key, (person["x"], billing_y)),
                                              (width * 0.5, height * 0.12), 9.0)
                paths[key].append(position[key])

    counter_people = [
        Person(p["id"], paths[p["id"]], starts.get(p["id"], int(p["arrival_s"] * fps)),
               height_px=90, width_px=32, colour=p["colour"])
        for p in plan if paths[p["id"]]
    ]

    def draw_counters(canvas: np.ndarray, _frame: int) -> None:
        for x in lane_x.values():
            cv2.rectangle(canvas, (int(x - 55), int(billing_y - 44)),
                          (int(x + 55), int(billing_y - 14)), (90, 80, 70), -1)

    checkout = Scene(name="synthetic_rush_counter", people=counter_people, frames=frames,
                     decor=[draw_counters], width=width, height=height, fps=fps,
                     gt_kind="rush_counter",
                     gt={"counters": counters,
                         "queue_every_10s": {c: queue_series[c] for c in counters},
                         "shoppers": [{"shopper": p["id"], "counter": p["counter"],
                                       "joined_s": round(p["arrival_s"], 2),
                                       "service_start_s": round(p["start_s"], 2),
                                       "service_s": round(p["service_s"], 2),
                                       "wait_s": round(p["start_s"] - p["arrival_s"], 2)}
                                      for p in plan],
                         "true_lag_min": lag_s / 60.0,
                         "lane_x_norm": {c: lane_x[c] / width for c in counters},
                         "open_counters": len(counters)})
    return [entrance, checkout]


# --------------------------------------------------------------------------- #

def write_ground_truth(scene: Scene, out_dir: Path) -> list[Path]:
    written: list[Path] = []
    if scene.gt_kind == "rush_counter":
        path = out_dir / f"{scene.name}_gt_queue_length.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["video_time_s", "counter", "queue_length"])
            for counter, series in scene.gt["queue_every_10s"].items():
                for at, count in series:
                    writer.writerow([f"{at:.1f}", counter, count])
        written.append(path)
        path = out_dir / f"{scene.name}_gt_waits.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["customer", "counter", "joined_s", "service_start_s",
                             "service_s", "wait_s"])
            for record in scene.gt["shoppers"]:
                writer.writerow([record["shopper"], record["counter"], record["joined_s"],
                                 record["service_start_s"], record["service_s"],
                                 record["wait_s"]])
        written.append(path)
        meta = out_dir / f"{scene.name}_truth.json"
        meta.write_text(json.dumps(scene.gt, indent=2), encoding="utf-8")
        written.append(meta)
        return written
    if scene.name.endswith("entrance"):
        path = out_dir / f"{scene.name}_gt_entries.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["video_time_s", "direction", "note"])
            for at, _pid, direction in scene.gt["crossings"]:
                writer.writerow([f"{at:.2f}", direction, ""])
        written.append(path)
    if scene.name.endswith("queue"):
        path = out_dir / f"{scene.name}_gt_queue_length.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["video_time_s", "counter", "queue_length"])
            for at, count in scene.gt["queue_every_10s"]:
                writer.writerow([at, "counter-1", count])
        written.append(path)
        path = out_dir / f"{scene.name}_gt_waits.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["customer", "counter", "joined_s", "service_start_s", "service_s", "wait_s"])
            for record in scene.gt["shoppers"]:
                writer.writerow([record["shopper"], "counter-1", record["joined_s"],
                                 record["service_start_s"], record["service_s"], record["wait_s"]])
        written.append(path)
    if scene.name.endswith("shelf"):
        path = out_dir / f"{scene.name}_gt_slots.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["video_time_s", "shelf", "slot", "state"])
            for entry in scene.gt["slots"]:
                writer.writerow([0.0, "shelf-a", entry["slot"], "FULL"])
                if entry["empty_from_s"] < 1e8:
                    writer.writerow([entry["empty_from_s"], "shelf-a", entry["slot"], "EMPTY"])
        written.append(path)
    meta = out_dir / f"{scene.name}_truth.json"
    meta.write_text(json.dumps(scene.gt, indent=2), encoding="utf-8")
    written.append(meta)
    return written


SCENES = {"entrance": scene_entrance, "queue": scene_queue, "shelf": scene_shelf,
          "rush": scene_rush}

# Where each scene writes its files under videos/
SCENE_DIR = {"entrance": "entrance", "queue": "queue", "shelf": "shelf", "rush": "rush"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="../videos", help="videos/ root")
    parser.add_argument("--scene", default="all", choices=[*SCENES, "all"])
    parser.add_argument("--seconds", type=int, default=None)
    args = parser.parse_args()

    root = Path(args.out)
    chosen = list(SCENES) if args.scene == "all" else [args.scene]
    for key in chosen:
        builder = SCENES[key]
        built = builder(args.seconds) if args.seconds else builder()
        scenes = built if isinstance(built, list) else [built]
        out_dir = root / SCENE_DIR[key]
        for scene in scenes:
            video, detections = _render(scene, out_dir)
            gt_files = write_ground_truth(scene, out_dir)
            print(f"{scene.name}: {scene.frames} frames "
                  f"({scene.frames / scene.fps:.0f}s @ {scene.width}x{scene.height}) -> {video}")
            print(f"  detections: {detections}")
            for path in gt_files:
                print(f"  ground truth: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
