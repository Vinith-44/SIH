"""Track-level queue simulator with exact ground truth (bucket C test data for M4).

A billing counter with an L-shaped (bent) queue lane, simulated at 5 FPS as the
*tracks* a tracker would output - so the queue engine is tested on the logic it
owns (membership, parties, balk/renege, waits, Little's law, tail overflow)
without a detector in the way.  Optional noise adds box jitter, missed
detections and tracker ID switches.

People and what they do:
*   **customers** (single, or a party of 2 standing side by side, 25%) walk in,
    queue, are served (service ~ N(40, 12) s), leave to the right;
*   **reneges** (8%) join and give up after 30-90 s;
*   **balkers** (10%) walk to the tail, stop 3-6 s, walk away;
*   **passers-by** walk along the lane (an aisle through the queue area) at
    0.2-0.4 frame heights per second without stopping.
Arrivals are Poisson with a rush in the middle of the run.

Ground truth (per the definitions in `analytics/queue.py`):
*   *in the queue* = a customer (or reneger) who has reached a queue slot and is
    not yet at the counter; a party of 2 is 2 people and 1 party;
*   *wait* = service start - first moment the foot point was inside the lane.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.geometry import Polygon
from ..tracking.tracker import Track

WIDTH, HEIGHT, FPS = 640, 360, 5
FH = float(HEIGHT)

# Normalised geometry (x by width, y by height).
BILLING = [(0.45, 0.20), (0.62, 0.20), (0.62, 0.36), (0.45, 0.36)]
BILL_POINT = (0.535, 0.30)
POLYLINE = [(0.535, 0.40), (0.535, 0.80), (0.30, 0.80)]         # billing end first
LANE_WIDTH = 0.12                                               # of frame height
_HALF_X = LANE_WIDTH / 2 * HEIGHT / WIDTH
LANE_POLYGON = [(0.535 - _HALF_X, 0.36), (0.535 + _HALF_X, 0.36), (0.535 + _HALF_X, 0.86),
                (0.30, 0.86), (0.30, 0.74), (0.535 - _HALF_X, 0.74)]
SLOT_GAP = 0.075 * FH            # px between people in the queue
WALK = 0.35 * FH / FPS           # px per frame
PARTY_OFFSET = 0.025 * FH        # px either side of the slot for a party of 2


def px(point):
    return (point[0] * WIDTH, point[1] * HEIGHT)


def _polyline_px():
    return [px(p) for p in POLYLINE]


def _point_at(arc: float) -> tuple[float, float]:
    """Pixel point `arc` px along the polyline from the billing end."""
    pts = _polyline_px()
    for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
        length = math.hypot(bx - ax, by - ay)
        if arc <= length:
            t = arc / length
            return (ax + t * (bx - ax), ay + t * (by - ay))
        arc -= length
    return pts[-1]


def lane_length() -> float:
    pts = _polyline_px()
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts[:-1], pts[1:]))


CAPACITY = int((lane_length() - 0.06 * FH) // SLOT_GAP) + 1


@dataclass
class Unit:
    """One queue unit: a person, or a party of 2 walking and standing together."""

    kind: str                          # customer | renege | balk | passer
    size: int
    arrive_s: float
    ids: list[int]
    pos: list[float]                   # (x, y) px of the unit centre
    state: str = "walk_in"             # walk_in, queued, to_bill, serving, leaving, gone, balking
    slot: int | None = None
    service_s: float = 0.0
    patience_s: float = 1e9
    stop_s: float = 0.0
    target: tuple[float, float] | None = None
    exit: tuple[float, float] = (WIDTH * 0.97, HEIGHT * 0.30)
    lane_entry_s: float | None = None
    queued_at_s: float | None = None
    service_start_s: float | None = None
    queue_exit_s: float | None = None
    left_s: float | None = None
    served: bool = False
    reneged: bool = False
    balked: bool = False
    speed: float = WALK


@dataclass
class Truth:
    samples: list[dict] = field(default_factory=list)   # every 5 s: t, people, parties, tail
    waits: list[float] = field(default_factory=list)    # per served person
    sojourns: list[float] = field(default_factory=list) # per person who joined (Little's W)
    balks: int = 0
    reneges: int = 0
    joins_people: int = 0
    passers: int = 0


def simulate(seed: int, duration_s: float = 900.0, noise: bool = False,
             id_switch_per_min: float = 0.3):
    """Returns (frames, truth) where frames = [(t, [Track, ...]), ...]."""
    rng = np.random.default_rng(seed)
    lane = Polygon("lane", LANE_POLYGON, "lane").resolve(WIDTH, HEIGHT)
    units: list[Unit] = []
    next_id = [1]

    def new_ids(n):
        out = list(range(next_id[0], next_id[0] + n))
        next_id[0] += n
        return out

    # Poisson arrivals with a rush in the middle third.
    t, arrivals = 0.0, []
    while t < duration_s - 60:
        rush = duration_s / 3 <= t <= 2 * duration_s / 3
        t += rng.exponential(24.0 if rush else 55.0)
        arrivals.append(t)
    for when in arrivals:
        r = rng.random()
        kind = "renege" if r < 0.08 else "balk" if r < 0.18 else "customer"
        size = 2 if kind == "customer" and rng.random() < 0.25 else 1
        units.append(Unit(kind, size, when, new_ids(size), list(px((0.05, 0.97))),
                          service_s=float(np.clip(rng.normal(40, 12), 15, 90)),
                          patience_s=float(rng.uniform(30, 90)),
                          stop_s=float(rng.uniform(3, 6))))
    for when in np.arange(8.0, duration_s - 10, rng.uniform(12, 20)):
        speed = rng.uniform(0.2, 0.4) * FH / FPS
        y = rng.uniform(0.77, 0.83)
        units.append(Unit("passer", 1, float(when), new_ids(1), list(px((0.02, y))),
                          target=px((0.98, y)), exit=px((0.98, y)), speed=speed))

    truth = Truth(passers=sum(u.kind == "passer" for u in units))
    frames = []
    track_alias: dict[int, int] = {}          # true id -> tracker id (changes on ID switches)
    billing_busy_by: Unit | None = None
    tail_since = None
    tail_flag = False
    steps = int(duration_s * FPS)
    for step in range(steps):
        now = step / FPS
        active = [u for u in units if u.arrive_s <= now and u.state != "gone"]
        queue = sorted([u for u in active if u.state in ("queued",)], key=lambda u: u.slot)
        # Advance the queue: close gaps, front unit goes to the counter when it is free.
        if billing_busy_by is None and queue and queue[0].slot == 0:
            front = queue.pop(0)
            front.state, front.target = "to_bill", px(BILL_POINT)
            front.queue_exit_s = now
            billing_busy_by = front
        for i, unit in enumerate(queue):
            unit.slot = i
            unit.target = _point_at(0.06 * FH + i * SLOT_GAP)
        for unit in active:
            _step_unit(unit, now, queue, rng)
            if unit.state == "serving" and unit.service_start_s is None:
                unit.service_start_s = now
            if (unit.state == "serving" and now - unit.service_start_s >= unit.service_s):
                unit.state, unit.target, unit.served = "leaving", unit.exit, True
                billing_busy_by = None
            if unit.state == "queued" and unit.kind == "renege" and now - unit.queued_at_s > unit.patience_s:
                unit.state, unit.target, unit.reneged = "leaving", px((0.0, 0.95)), True
                unit.queue_exit_s = now
            if unit.lane_entry_s is None and lane.contains((unit.pos[0], unit.pos[1] + 0)):
                unit.lane_entry_s = now

        # Ground truth every 5 s.
        if step % (5 * FPS) == 0:
            queued = [u for u in active if u.state == "queued"]
            # Same definition as the engine: someone waiting in the last 10% of the lane.
            at_tail = any(u.slot is not None and (0.06 * FH + u.slot * SLOT_GAP) / lane_length() >= 0.9
                          for u in queued)
            if at_tail:
                tail_since = now if tail_since is None else tail_since
                tail_flag = tail_flag or now - tail_since >= 5.0
            else:
                tail_since, tail_flag = None, False
            truth.samples.append({"t": now, "people": sum(u.size for u in queued),
                                  "parties": len(queued), "tail": tail_flag})

        # Tracks the tracker would output.
        tracks = []
        for unit in active:
            for k, true_id in enumerate(unit.ids):
                offset = 0.0 if unit.size == 1 else (-PARTY_OFFSET if k == 0 else PARTY_OFFSET)
                fx, fy = unit.pos[0] + offset, unit.pos[1]
                if noise:
                    if rng.random() < 0.03:
                        continue                              # missed detection
                    fx += rng.normal(0, 0.006 * FH)
                    fy += rng.normal(0, 0.006 * FH)
                    if rng.random() < id_switch_per_min / 60.0 / FPS:
                        track_alias[true_id] = next_id[0]
                        next_id[0] += 1
                tid = track_alias.get(true_id, true_id)
                w, h = 0.1 * FH, 0.35 * FH
                tracks.append(Track(tid, (fx - w / 2, fy - h, fx + w / 2, fy), 0.9))
        frames.append((now, tracks))

    for unit in units:
        if unit.kind == "passer":
            continue
        if unit.balked:
            truth.balks += 1
        if unit.queued_at_s is not None:
            truth.joins_people += unit.size
            if unit.reneged:
                truth.reneges += unit.size
            if unit.queue_exit_s is not None and unit.lane_entry_s is not None:
                truth.sojourns += [unit.queue_exit_s - unit.lane_entry_s] * unit.size
        if unit.served and unit.service_start_s is not None and unit.lane_entry_s is not None:
            truth.waits += [unit.service_start_s - unit.lane_entry_s] * unit.size
    return frames, truth


def _move(unit: Unit, speed: float) -> bool:
    """Step towards the target; True when arrived."""
    if unit.target is None:
        return True
    dx, dy = unit.target[0] - unit.pos[0], unit.target[1] - unit.pos[1]
    dist = math.hypot(dx, dy)
    if dist <= speed:
        unit.pos = [unit.target[0], unit.target[1]]
        return True
    unit.pos[0] += dx / dist * speed
    unit.pos[1] += dy / dist * speed
    return False


def _step_unit(unit: Unit, now: float, queue: list[Unit], rng) -> None:
    if unit.state == "walk_in":
        if unit.kind == "passer":
            unit.state = "leaving"
            return
        tail = _point_at(0.06 * FH + len(queue) * SLOT_GAP + (0.5 * SLOT_GAP if unit.kind == "balk" else 0))
        # Enter via the far end of the lane, then walk up to the tail.
        unit.target = unit.target or tail
        if _move(unit, WALK):
            if unit.kind == "balk":
                unit.state, unit.queued_at_s = "balking", now
            else:
                unit.state, unit.queued_at_s, unit.slot = "queued", now, len(queue)
                queue.append(unit)
        else:
            unit.target = tail
    elif unit.state == "queued":
        _move(unit, WALK * 0.6)
    elif unit.state == "balking":
        if now - unit.queued_at_s >= unit.stop_s:
            unit.state, unit.target, unit.balked = "leaving", px((0.0, 0.97)), True
    elif unit.state == "to_bill":
        if _move(unit, WALK):
            unit.state = "serving"
    elif unit.state == "leaving":
        if _move(unit, unit.speed):
            unit.state = "gone"
            unit.left_s = now
