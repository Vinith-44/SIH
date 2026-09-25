"""Simulated shoppers around a promotion display, with exact truth (bucket C).

What the simulator encodes (our assumptions, not measurements):

*   A promo zone in front of an end-cap: x 0.42-0.58, y 0.45-0.62 of a 1280x720 frame, processed at 8 FPS.
*   Arrivals are Poisson (about 9 people a minute, a rush in the middle third).  Each person is one of:
    -  a **walker** (66%): a straight path across the frame whose closest distance to the zone is uniform in
       0-0.25 frame heights;
    -  a **walk-through** (8%): crosses the zone without stopping (inside for about 1 s);
    -  a **glancer** (10%): walks to the zone, pauses 1-2.5 s inside, walks on;
    -  a **stopper** (16%): walks in and stays; the stay is log-normal (median 9 s, 3.5-60 s), with a little sway.
*   **Truth definition.**  A passer-by is anyone who crosses the zone or passes within `TRUE_BAND` = 0.08 frame
    heights of it (roughly half a metre on a typical CCTV view) without staying `MIN_DWELL_S` inside.  A stopper
    stays inside at least `MIN_DWELL_S`; their dwell is measured on the exact path.  This is our definition of
    "walked past the display"; a real site may want another, which is why the band is a setting.
*   A stopper picks the promo item with probability 0.35 (1-3 packs); 10% of pickers put one back.  Picks are
    published as PICKUP events at the linked slot, as the MEMS-gated fusion engine would.
*   "Noisy" adds foot-point jitter (sigma 0.006 frame heights), 3% missed detections and 0.3 track-number
    switches per person-minute.  Staff are removed upstream of this module in the pipeline, so none are simulated.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..core.geometry import Polygon

W, H = 1280, 720
FPS = 8.0
MIN_DWELL_S = 3.0
TRUE_BAND = 0.08
ZONE_POINTS = [(0.42, 0.45), (0.58, 0.45), (0.58, 0.62), (0.42, 0.62)]
SHELF, SLOT = "endcap", "E1"
BOX_W, BOX_H = 0.08 * H, 0.25 * H


@dataclass
class Person:
    kind: str
    path: list[tuple[float, float, float]]      # (t, x_px, y_px) at FPS
    picks: list[tuple[float, int]] = field(default_factory=list)
    put_backs: list[float] = field(default_factory=list)


@dataclass
class Truth:
    passers_by: int = 0
    stoppers: int = 0
    dwells: list[float] = field(default_factory=list)
    picks: int = 0
    put_backs: int = 0
    units: int = 0


def zone_polygon() -> Polygon:
    return Polygon("promo-endcap", ZONE_POINTS, "promo").resolve(W, H)


def _distance(x: float, y: float, poly: Polygon) -> float:
    from ..analytics.promo import distance_to_polygon

    return 0.0 if poly.contains((x, y)) else distance_to_polygon((x, y), poly.pixels)


def _line_path(rng: random.Random, t0: float, y: float, speed: float) -> list[tuple[float, float, float]]:
    left = rng.random() < 0.5
    x0, x1 = (-0.05 * W, 1.05 * W) if left else (1.05 * W, -0.05 * W)
    steps = int(abs(x1 - x0) / (speed * H) * FPS)
    tilt = rng.uniform(-0.03, 0.03) * H
    return [(t0 + i / FPS, x0 + (x1 - x0) * i / steps, y + tilt * (i / steps - 0.5)) for i in range(steps + 1)]


def _visit_path(rng: random.Random, t0: float, stay_s: float, speed: float) -> list[tuple[float, float, float]]:
    """Walk in from one side to a point inside the zone, stay (with sway), walk out the other side."""
    zx0, zx1 = ZONE_POINTS[0][0] * W, ZONE_POINTS[1][0] * W
    zy0, zy1 = ZONE_POINTS[0][1] * H, ZONE_POINTS[2][1] * H
    target = (rng.uniform(zx0 + 25, zx1 - 25), rng.uniform(zy0 + 20, zy1 - 15))
    start = (-0.05 * W if rng.random() < 0.5 else 1.05 * W, target[1] + rng.uniform(-0.15, 0.15) * H)
    end = (1.05 * W if start[0] < 0 else -0.05 * W, target[1] + rng.uniform(-0.15, 0.15) * H)
    path: list[tuple[float, float, float]] = []
    t = t0
    for a, b in ((start, target), (None, None), (target, end)):
        if a is None:
            x, y = target
            for _ in range(int(stay_s * FPS)):
                x = min(max(x + rng.gauss(0, 1.5), zx0 + 5), zx1 - 5)
                y = min(max(y + rng.gauss(0, 1.0), zy0 + 5), zy1 - 5)
                path.append((t, x, y))
                t += 1 / FPS
            continue
        steps = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (speed * H) * FPS))
        for i in range(steps):
            path.append((t, a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps))
            t += 1 / FPS
    return path


def simulate(seed: int, minutes: float = 20.0) -> tuple[list[Person], Truth]:
    rng = random.Random(seed)
    poly = zone_polygon()
    people: list[Person] = []
    t = 0.0
    end = minutes * 60
    while True:
        rush = end / 3 <= t <= 2 * end / 3
        t += rng.expovariate((13.0 if rush else 7.0) / 60.0)
        if t >= end:
            break
        speed = rng.uniform(0.15, 0.25)                 # frame heights per second
        roll = rng.random()
        if roll < 0.66:
            kind = "walker"
            d = rng.uniform(0.0, 0.25) * H
            above = rng.random() < 0.5
            y = (ZONE_POINTS[0][1] * H - d) if above else (ZONE_POINTS[2][1] * H + d)
            path = _line_path(rng, t, y, speed)
        elif roll < 0.74:
            kind = "walk_through"
            y = rng.uniform(ZONE_POINTS[0][1] * H + 15, ZONE_POINTS[2][1] * H - 15)
            path = _line_path(rng, t, y, speed)
        elif roll < 0.84:
            kind = "glancer"
            path = _visit_path(rng, t, rng.uniform(1.0, 2.5), speed)
        else:
            kind = "stopper"
            stay = min(60.0, max(3.5, rng.lognormvariate(math.log(9.0), 0.6)))
            path = _visit_path(rng, t, stay, speed)
        person = Person(kind, path)
        people.append(person)

    truth = Truth()
    for person in people:
        inside = [pt for pt in person.path if poly.contains((pt[1], pt[2]))]
        dwell = (inside[-1][0] - inside[0][0]) if inside else 0.0
        closest = min(_distance(x, y, poly) for _t, x, y in person.path)
        if dwell >= MIN_DWELL_S:
            truth.stoppers += 1
            truth.dwells.append(dwell)
            if rng.random() < 0.35:
                t_in, t_out = inside[0][0], inside[-1][0]
                units = rng.randint(1, 3)
                person.picks.append((rng.uniform(t_in, t_out), units))
                truth.picks += 1
                truth.units += units
                if rng.random() < 0.10:
                    person.put_backs.append(rng.uniform(t_in, t_out))
                    truth.put_backs += 1
        elif closest <= TRUE_BAND * H:
            truth.passers_by += 1
    return people, truth


def frames(people: list[Person], seed: int, noisy: bool, minutes: float = 20.0):
    """Yield (t, [(track_id, xyxy)]) per frame, with optional detection noise and ID switches."""
    rng = random.Random(seed * 7919 + (1 if noisy else 0))
    by_frame: dict[int, list[tuple[int, float, float]]] = {}
    next_id = 1
    for person in people:
        track = next_id
        next_id += 1
        for t, x, y in person.path:
            if not (-0.02 * W <= x <= 1.02 * W):
                continue
            if noisy:
                if rng.random() < 0.3 / 60.0 / FPS:
                    track = next_id                      # ID switch from here on
                    next_id += 1
                if rng.random() < 0.03:
                    continue                             # missed detection
                x += rng.gauss(0, 0.006 * H)
                y += rng.gauss(0, 0.006 * H)
            by_frame.setdefault(int(round(t * FPS)), []).append((track, x, y))
    for index in range(int((minutes * 60 + 90) * FPS)):
        dets = by_frame.get(index, [])
        yield index / FPS, [(tid, (x - BOX_W / 2, y - BOX_H, x + BOX_W / 2, y)) for tid, x, y in dets]


def pickups(people: list[Person]) -> list[tuple[float, str, int]]:
    """(t, action, units) for every simulated pick and put-back, sorted by time."""
    out = [(t, "pick", units) for p in people for t, units in p.picks]
    out += [(t, "put_back", 1) for p in people for t in p.put_backs]
    return sorted(out)
