"""Shelf sensor-node event simulator with exact truth (bucket C test data for M6).

Generates the events Ram's serial bridge will publish for one shelf with three
load-cell slots and one MEMS node underneath - `SHELF_MOTION` and `WEIGHT` -
following the firmware behaviour in docs/PROTOCOL.md and CLAUDE_CODE_PROMPT_V2 M6:

*   shoppers visit every ~40 s; the MEMS sends TOUCH when the shelf is handled
    (misses 5% of gentle touches) and SETTLED 0.5-1.5 s after the hand leaves;
*   while the shelf is handled the HX711 sends unstable readings every 0.5 s that
    swing +-50-400 g on the handled slot and +-0-50 g on its neighbours (the shelf
    flexes); 10% of those are wrongly flagged `stable` (the firmware's stability
    test fooled by a steady push);
*   a visit ends in: pick 1-2 packs (55%), put back 1 pack (10%), or just a touch
    or lean (35%);
*   the first stable reading comes 0.3-1.5 s after release, then one every 10 s,
    with +-3 g noise and slow drift;
*   a trolley knocks the shelf every ~5 min (KNOCK, a +-200 g spike, no SETTLED);
    15% of knocks drop one pack on the floor.

Truth: every pick / put-back / touch with its slot, units and release time,
plus fallen packs.  A shopper is "at the shelf" during each visit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SLOTS = {"A1": ("1", 218.0), "A2": ("2", 500.0), "A3": ("3", 90.0)}   # slot: (channel, unit grams)
SHELF = "shelf-a"
NODE = "stm32-01"


@dataclass
class Truth:
    actions: list[dict] = field(default_factory=list)    # {t, slot, action, units}
    fallen: list[dict] = field(default_factory=list)     # {t, slot}
    visits: list[tuple[float, float]] = field(default_factory=list)


def simulate(seed: int, duration_s: float = 1800.0, mems_miss: float = 0.05):
    """Returns (events, truth). events = [(t, type, data)] sorted by time."""
    rng = np.random.default_rng(seed)
    start = {slot: unit * 8 + 350.0 for slot, (_ch, unit) in SLOTS.items()}   # 8 packs + tray
    changes: list[tuple[float, str, float]] = []        # (t, slot, delta grams)
    truth = Truth()
    events: list[tuple[float, str, dict]] = []

    def weight_at(slot: str, t: float) -> float:
        return start[slot] + sum(d for when, s, d in changes if s == slot and when <= t)

    drift_steps = {slot: np.cumsum(rng.normal(0, 0.3, int(duration_s // 10) + 2)) for slot in SLOTS}

    def drift(slot: str, t: float) -> float:
        return float(drift_steps[slot][int(t // 10)])

    def weight(t, slot, stable, value):
        events.append((t, "WEIGHT", {"node": NODE, "slot": SLOTS[slot][0],
                                     "grams": round(value, 1), "stable": stable}))

    def motion(t, kind, peak):
        events.append((t, "SHELF_MOTION", {"node": "m1", "shelf": SHELF, "kind": kind,
                                           "peak_mg": peak, "rms_mg": peak / 3, "dur_ms": 500}))

    # 1. Visits: schedule, outcome, weight change at release.
    visits = []
    t, busy_until = 5.0, 0.0
    while t < duration_s - 30:
        t += rng.exponential(40.0)
        if t < busy_until + 2 or t > duration_s - 30:
            continue
        slot = str(rng.choice(list(SLOTS)))
        release = t + float(rng.uniform(2.0, 8.0))
        r = rng.random()
        action, units = ("pick", int(rng.integers(1, 3))) if r < 0.55 else \
            ("put_back", 1) if r < 0.65 else ("touch", 0)
        unit = SLOTS[slot][1]
        delta = {"pick": -units * unit, "put_back": units * unit, "touch": 0.0}[action]
        if delta:
            changes.append((release, slot, delta))
        visits.append((t, release, slot, bool(rng.random() >= mems_miss)))
        truth.actions.append({"t": release, "slot": slot, "action": action, "units": units})
        truth.visits.append((t - 3.0, release + 4.0))
        busy_until = release

    # 2. Knocks, away from visits.
    knocks = []
    for k in np.arange(rng.uniform(60, 300), duration_s - 20, rng.uniform(240, 360)):
        k = float(k)
        if any(a - 2 <= k <= b + 2 for a, b in truth.visits):
            continue
        slot = str(rng.choice(list(SLOTS)))
        if rng.random() < 0.15:
            changes.append((k + 0.05, slot, -SLOTS[slot][1]))
            truth.fallen.append({"t": k, "slot": slot})
        knocks.append(k)

    # 3. Readings, each with the weight at its own time.
    for t0, release, slot, mems_ok in visits:
        if mems_ok:
            motion(t0, "TOUCH", float(rng.uniform(150, 600)))
            motion(release + float(rng.uniform(0.5, 1.5)), "SETTLED", 30.0)
        for k in np.arange(t0 + 0.2, release, 0.5):
            for s in SLOTS:
                swing = rng.uniform(50, 400) if s == slot else rng.uniform(0, 50)
                weight(float(k), s, bool(rng.random() < 0.10),
                       weight_at(s, t0) + drift(s, k) + rng.choice([-1, 1]) * swing)
        first = release + float(rng.uniform(0.3, 1.5))
        for s in SLOTS:
            weight(first, s, True, weight_at(s, first) + drift(s, first) + rng.normal(0, 3))
    for k in knocks:
        motion(k, "KNOCK", float(rng.uniform(800, 2000)))
        for s in SLOTS:
            weight(k + 0.1, s, False, weight_at(s, k) + rng.uniform(-200, 200))
            weight(k + 1.2, s, True, weight_at(s, k + 1.2) + drift(s, k) + rng.normal(0, 3))
    for k in np.arange(0.0, duration_s, 10.0):
        k = float(k)
        if any(a <= k <= b for a, b in truth.visits):
            continue                                     # the firmware sends changes, not a clock, while handled
        for s in SLOTS:
            weight(k, s, True, weight_at(s, k) + drift(s, k) + rng.normal(0, 3))
    events.sort(key=lambda e: e[0])
    return events, truth


def person_at(truth: Truth, t: float) -> bool:
    return any(a <= t <= b for a, b in truth.visits)
