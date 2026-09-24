"""Per-counter queue intelligence.

This module fixes the worst bugs in the legacy `live_queue_intelligence.py`
(audit section 4):

*   **Q1** - one polygon covering 84% of the frame meant "anyone in the shop is in
    the queue".  Here every counter has its own **lane** plus a small **billing**
    polygon, both drawn by `tools/calibrate.py`.
*   **Q2** - the old "prediction" was the slope between the first and last sample
    times 30 s, which at start-up turned one person into a predicted 78.  Queue
    length is now reported both raw and **median-smoothed** over a window, and
    prediction has moved to `fusion/forecast.py`, where it is a queueing model
    rather than a slope.
*   **Q3** - "service_seconds: 0.4" came from ID flicker.  Service only starts
    after a track has held the billing polygon for `min_service_s` (default 3 s).
*   **Q6** - wait timers survive detection gaps up to `gap_tolerance_s`, and the
    reported statistic is the **median**, not the mean, so one outlier cannot
    move the headline number.

Queue v2 (M4, research/23 section 3.2, docs/QUEUE.md), switched on per counter
with `membership: dwell` (`polygon` keeps the behaviour above exactly):

*   **Membership, not a polygon.** A person *joins* only after `join_dwell_s`
    in the lane while moving slower than `max_join_speed`; someone walking
    through never joins.  Their wait is back-dated to when they entered.
*   **Parties.** People who joined within `party_join_window_s` of each other and
    stay within `party_dist` are one party (a family pays once): reported as
    `queue_parties` next to people.
*   **Bent queues.** A lane can be a polyline with a width; `tail_zone` (or the
    last 10% of the polyline) held for 5 s means the queue is overflowing.
*   **Balk / renege.** Balk = stopped in the lane (>= `balk_min_s` slow) and left
    without joining.  Renege = joined, then left before service.
*   **Little's law.** W = L / lambda over `littles_window_s`, reported next to
    the timer-based median wait.  It needs no tracks at all, so it survives the
    ID switches that break per-person timers.
*   **Track stitching.** A tracker ID switch looks like "someone left the queue
    and someone new appeared in the same spot".  A waiting person whose track
    vanishes is held for `STITCH_WINDOW_S`; a new track that appears within
    `STITCH_DIST` of them inherits their place and wait timer.  Only if nobody
    does is it counted as a renege.

Definitions used consistently everywhere (write these on the slide):

*   *queue length* - members (v1: tracks in the lane) not yet being served.
*   *wait* - from the moment a track joins the lane to the moment its service
    starts.
*   *service time* - how long a track holds the billing polygon.
*   *mu* - completed services per minute per counter.
*   *lambda* - joins per minute (arrivals to checkout).
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..core.clock import Clock
from ..core.events import Event, EventType, QueueStateData, ServiceDoneData, make_event
from ..core.geometry import Point, Polygon, foot_point, to_pixels
from ..tracking.tracker import Track

TAIL_HOLD_S = 5.0        # overflow must hold this long (and clear this long)
TAIL_FRACTION = 0.9      # last 10% of a lane polyline is its tail
# v2 track stitching: a waiting person whose track vanishes is kept for this long;
# a new track appearing this close (frame heights) inherits their place and timer.
STITCH_WINDOW_S = 4.0
STITCH_DIST = 0.10


class LanePath:
    """A lane given as a centre polyline (billing end first) plus a width, in pixels."""

    def __init__(self, points: list[Point], width_px: float) -> None:
        self.points = np.asarray(points, dtype=np.float64)
        self.half_width = width_px / 2.0
        segments = np.diff(self.points, axis=0)
        self.lengths = np.hypot(segments[:, 0], segments[:, 1])
        self.total = float(self.lengths.sum()) or 1.0

    def locate(self, point: Point) -> tuple[float, float]:
        """(distance to the polyline, fraction of the way from billing end to tail)."""
        p = np.asarray(point, dtype=np.float64)
        best_d, best_s, walked = math.inf, 0.0, 0.0
        for a, b, length in zip(self.points[:-1], self.points[1:], self.lengths):
            ab = b - a
            t = 0.0 if length < 1e-9 else float(np.clip(np.dot(p - a, ab) / (length * length), 0.0, 1.0))
            d = float(np.hypot(*(a + t * ab - p)))
            if d < best_d:
                best_d, best_s = d, walked + t * length
            walked += length
        return best_d, best_s / self.total

    def contains(self, point: Point) -> bool:
        return self.locate(point)[0] <= self.half_width


@dataclass
class CounterSpec:
    name: str
    lane: Polygon | None
    billing: Polygon
    min_service_s: float = 3.0
    gap_tolerance_s: float = 2.0
    open: bool = True
    congestion_len: int = 5
    # --- v2 ---
    membership: str = "polygon"
    join_dwell_s: float = 5.0
    max_join_speed: float = 0.15          # frame heights per second
    speed_window_s: float = 2.0
    lane_path: LanePath | None = None
    tail: Polygon | None = None
    party_dist: float = 0.08              # frame heights
    party_join_window_s: float = 4.0
    balk_min_s: float = 2.0
    littles_window_s: float = 600.0
    frame_h: float = 1.0

    def in_lane(self, point: Point) -> bool:
        if self.lane is not None and self.lane.contains(point):
            return True
        return self.lane_path is not None and self.lane_path.contains(point)


def counter_spec_from_config(c, width: int, height: int) -> CounterSpec:
    """`c` is a `core.config.CounterConfig` (normalised geometry) -> pixel-space spec."""
    def poly(name: str, points) -> Polygon:
        return Polygon(name, [tuple(p) for p in points], name.rsplit("-", 1)[-1]).resolve(width, height)

    return CounterSpec(
        name=c.name,
        lane=poly(f"{c.name}-lane", c.lane) if c.lane else None,
        billing=poly(f"{c.name}-billing", c.billing),
        min_service_s=c.min_service_s, gap_tolerance_s=c.gap_tolerance_s,
        open=c.open, congestion_len=c.congestion_len,
        membership=c.membership, join_dwell_s=c.join_dwell_s,
        max_join_speed=c.max_join_speed, speed_window_s=c.speed_window_s,
        lane_path=(LanePath([tuple(p) for p in to_pixels(c.lane_polyline, width, height)],
                            c.lane_width * height) if len(c.lane_polyline) >= 2 else None),
        tail=poly(f"{c.name}-tail", c.tail_zone) if c.tail_zone else None,
        party_dist=c.party_dist, party_join_window_s=c.party_join_window_s,
        balk_min_s=c.balk_min_s, littles_window_s=c.littles_window_s, frame_h=float(height),
    )


@dataclass
class _Shopper:
    track_id: int
    joined_s: float
    last_lane_s: float
    billing_since_s: float | None = None
    service_start_s: float | None = None
    last_billing_s: float | None = None
    wait_s: float | None = None
    position: Point = (0.0, 0.0)


@dataclass
class _Candidate:
    """v2: someone in the lane who has not (yet) joined the queue."""

    first_s: float
    last_s: float
    track: deque = field(default_factory=lambda: deque(maxlen=64))   # (t, x, y)
    slow_s: float = 0.0


@dataclass
class CounterState:
    spec: CounterSpec
    shoppers: dict[int, _Shopper] = field(default_factory=dict)
    candidates: dict[int, _Candidate] = field(default_factory=dict)
    completed_services: list[float] = field(default_factory=list)
    completed_waits: list[float] = field(default_factory=list)
    length_window: deque = field(default_factory=lambda: deque(maxlen=15))
    joins: list[float] = field(default_factory=list)
    in_service_track: int | None = None
    balks: int = 0
    reneges: int = 0
    length_samples: deque = field(default_factory=deque)      # (t, queue_len) for Little's law
    pairs: dict[tuple[int, int], list[int]] = field(default_factory=dict)  # (close, together)
    tail_since_s: float | None = None
    tail_clear_since_s: float | None = None
    tail_overflow: bool = False
    parties: int | None = None
    lost: dict[int, tuple[_Shopper, float]] = field(default_factory=dict)  # v2 stitching
    stitched: int = 0

    @property
    def queue_len(self) -> int:
        """People waiting, excluding whoever is being served."""
        return sum(1 for s in self.shoppers.values() if s.service_start_s is None)

    @property
    def queue_len_smooth(self) -> float:
        if not self.length_window:
            return float(self.queue_len)
        return float(statistics.median(self.length_window))

    @property
    def median_wait_s(self) -> float | None:
        if not self.completed_waits:
            return None
        return float(statistics.median(self.completed_waits))

    @property
    def median_service_s(self) -> float | None:
        if not self.completed_services:
            return None
        return float(statistics.median(self.completed_services))

    @property
    def mu_per_min(self) -> float | None:
        """Service rate per minute for this counter."""
        median = self.median_service_s
        if not median or median <= 0:
            return None
        return 60.0 / median

    def littles(self, now: float) -> tuple[float | None, float | None]:
        """(W seconds, lambda per minute) over the window; None until 60 s of data."""
        window = self.spec.littles_window_s
        span = min(window, now - self.length_samples[0][0]) if self.length_samples else 0.0
        if span < 60.0:
            return None, None
        recent = [q for t, q in self.length_samples if now - t <= window]
        joins = sum(1 for t in self.joins if now - t <= span)
        lam = joins / span                                 # per second
        mean_len = sum(recent) / len(recent) if recent else 0.0
        return (mean_len / lam if lam > 0 else None), lam * 60.0


class QueueEngine:
    def __init__(self, counters: list[CounterSpec], *, store: str = "demo-store",
                 node: str = "pi5-01", cam: str = "counter",
                 state_period_s: float = 5.0) -> None:
        self.counters = {c.name: CounterState(spec=c) for c in counters}
        self.store, self.node, self.cam = store, node, cam
        self.state_period_s = state_period_s
        self._next_state_s = 0.0
        # Arrivals to checkout, for lambda in the forecast.
        self.checkout_arrivals: list[float] = []

    # ------------------------------------------------------------------ #
    def update(self, tracks: list[Track], clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        events: list[Event] = []

        for state in self.counters.values():
            spec = state.spec
            seen_lane: set[int] = set()
            seen_billing: set[int] = set()

            for track in tracks:
                point = foot_point(track.xyxy)
                in_billing = spec.billing.contains(point)
                if not (in_billing or spec.in_lane(point)):
                    continue
                seen_lane.add(track.track_id)
                shopper = state.shoppers.get(track.track_id)
                if shopper is None and spec.membership == "dwell":
                    shopper = self._stitch(state, track.track_id, point, now)
                if shopper is None:
                    joined_s = self._admit(state, track.track_id, point, in_billing, now)
                    if joined_s is None:
                        continue                   # v2: still a candidate, not in the queue
                    shopper = _Shopper(track_id=track.track_id, joined_s=joined_s, last_lane_s=now)
                    state.shoppers[track.track_id] = shopper
                    state.joins.append(now)
                    self.checkout_arrivals.append(now)
                else:
                    shopper.last_lane_s = now
                shopper.position = point

                if in_billing:
                    seen_billing.add(track.track_id)
                    shopper.last_billing_s = now
                    if shopper.billing_since_s is None:
                        shopper.billing_since_s = now
                    elif (shopper.service_start_s is None
                          and now - shopper.billing_since_s >= spec.min_service_s):
                        # Service genuinely started (Q3): backdate to when they
                        # first reached the billing spot.
                        shopper.service_start_s = shopper.billing_since_s
                        shopper.wait_s = max(0.0, shopper.billing_since_s - shopper.joined_s)
                        state.in_service_track = track.track_id
                else:
                    # Left the billing spot: forgive short gaps, otherwise the
                    # dwell-to-start-service counter resets.
                    if (shopper.billing_since_s is not None and shopper.service_start_s is None
                            and shopper.last_billing_s is not None
                            and now - shopper.last_billing_s > spec.gap_tolerance_s):
                        shopper.billing_since_s = None

            events.extend(self._close_finished(state, now, seen_lane, seen_billing, clock))
            if spec.membership == "dwell":
                self._expire_lost(state, now)
                self._forget_candidates(state, now, seen_lane)
                self._update_parties(state)
                self._update_tail(state, now)
            state.length_window.append(state.queue_len)
            state.length_samples.append((now, state.queue_len))
            while state.length_samples and now - state.length_samples[0][0] > spec.littles_window_s:
                state.length_samples.popleft()

        if now >= self._next_state_s:
            self._next_state_s = now + self.state_period_s
            for state in self.counters.values():
                wait_littles, arrivals = state.littles(now)
                dwell = state.spec.membership == "dwell"
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.QUEUE_STATE,
                    data=QueueStateData(
                        counter=state.spec.name,
                        queue_len=state.queue_len,
                        queue_len_smooth=round(state.queue_len_smooth, 2),
                        median_wait_s=(round(state.median_wait_s, 1)
                                       if state.median_wait_s is not None else None),
                        service_rate_per_min=(round(state.mu_per_min, 3)
                                              if state.mu_per_min is not None else None),
                        in_service=state.in_service_track is not None,
                        queue_parties=state.parties if dwell else None,
                        wait_littles_s=round(wait_littles, 1) if wait_littles is not None else None,
                        arrivals_per_min=round(arrivals, 3) if arrivals is not None else None,
                        balks=state.balks if dwell else None,
                        reneges=state.reneges if dwell else None,
                        tail_overflow=state.tail_overflow if dwell else None,
                    ),
                ))
        return events

    # -- v2 membership ----------------------------------------------------- #
    def _admit(self, state: CounterState, track_id: int, point: Point, in_billing: bool,
               now: float) -> float | None:
        """Returns the join time if this track is (now) a queue member."""
        spec = state.spec
        if spec.membership != "dwell":
            return now                                            # v1: in the lane = in the queue
        candidate = state.candidates.get(track_id)
        if candidate is None:
            candidate = state.candidates[track_id] = _Candidate(first_s=now, last_s=now)
        dt = now - candidate.last_s
        candidate.last_s = now
        candidate.track.append((now, point[0], point[1]))
        while len(candidate.track) > 2 and now - candidate.track[1][0] > spec.speed_window_s:
            candidate.track.popleft()
        speed = self._speed(candidate, spec)
        slow = speed is not None and speed <= spec.max_join_speed
        if slow:
            # The first speed reading covers the time since they appeared; credit it.
            candidate.slow_s += dt if candidate.slow_s > 0 else now - candidate.first_s
        dwelt = now - candidate.first_s >= spec.join_dwell_s
        if in_billing or (dwelt and slow):
            del state.candidates[track_id]
            return candidate.first_s                              # wait counts from arrival
        return None

    @staticmethod
    def _stitch(state: CounterState, track_id: int, point: Point, now: float) -> _Shopper | None:
        """A new track where a waiting person just vanished takes over their place."""
        limit = STITCH_DIST * state.spec.frame_h
        best, best_d = None, limit
        for old_id, (shopper, _lost_at) in state.lost.items():
            d = math.hypot(point[0] - shopper.position[0], point[1] - shopper.position[1])
            if d <= best_d:
                best, best_d = old_id, d
        if best is None:
            return None
        shopper, _ = state.lost.pop(best)
        shopper.track_id = track_id
        shopper.last_lane_s = now
        state.shoppers[track_id] = shopper
        state.candidates.pop(track_id, None)
        state.stitched += 1
        return shopper

    @staticmethod
    def _expire_lost(state: CounterState, now: float) -> None:
        for old_id, (_shopper, lost_at) in list(state.lost.items()):
            if now - lost_at > STITCH_WINDOW_S:
                del state.lost[old_id]
                state.reneges += 1                            # joined, left before service

    @staticmethod
    def _speed(candidate: _Candidate, spec: CounterSpec) -> float | None:
        if len(candidate.track) < 2:
            return None
        t0, x0, y0 = candidate.track[0]
        t1, x1, y1 = candidate.track[-1]
        if t1 - t0 < 0.5:
            return None
        return math.hypot(x1 - x0, y1 - y0) / (t1 - t0) / max(spec.frame_h, 1e-6)

    def _forget_candidates(self, state: CounterState, now: float, seen: set[int]) -> None:
        for track_id, candidate in list(state.candidates.items()):
            if track_id in seen or now - candidate.last_s <= state.spec.gap_tolerance_s:
                continue
            if candidate.slow_s >= state.spec.balk_min_s:
                state.balks += 1                                  # stopped, looked, left
            del state.candidates[track_id]

    def _update_parties(self, state: CounterState) -> None:
        spec = state.spec
        waiting = {tid: s for tid, s in state.shoppers.items() if s.service_start_s is None}
        ids = sorted(waiting)
        live_pairs = set()
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if abs(waiting[a].joined_s - waiting[b].joined_s) > spec.party_join_window_s:
                    continue
                key = (a, b)
                live_pairs.add(key)
                stats = state.pairs.setdefault(key, [0, 0])
                stats[1] += 1
                pa, pb = waiting[a].position, waiting[b].position
                if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= spec.party_dist * spec.frame_h:
                    stats[0] += 1
        for key in [k for k in state.pairs if k not in live_pairs]:
            del state.pairs[key]
        parent = {tid: tid for tid in ids}

        def root(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (a, b), (close, together) in state.pairs.items():
            if together >= 3 and close / together >= 0.7:
                parent[root(a)] = root(b)
        state.parties = len({root(tid) for tid in ids})

    def _update_tail(self, state: CounterState, now: float) -> None:
        spec = state.spec
        # Waiting members, plus people standing still who have not been in the
        # lane long enough to join yet (the newest arrivals are at the tail).
        positions = [sh.position for sh in state.shoppers.values() if sh.service_start_s is None]
        for candidate in state.candidates.values():
            speed = self._speed(candidate, spec)
            if speed is not None and speed <= spec.max_join_speed:
                positions.append((candidate.track[-1][1], candidate.track[-1][2]))
        at_tail = False
        for position in positions:
            if spec.tail is not None and spec.tail.contains(position):
                at_tail = True
            elif spec.lane_path is not None and spec.lane_path.locate(position)[1] >= TAIL_FRACTION:
                at_tail = True
        if at_tail:
            state.tail_clear_since_s = None
            if state.tail_since_s is None:
                state.tail_since_s = now
            if now - state.tail_since_s >= TAIL_HOLD_S:
                state.tail_overflow = True
        else:
            state.tail_since_s = None
            if state.tail_clear_since_s is None:
                state.tail_clear_since_s = now
            if now - state.tail_clear_since_s >= TAIL_HOLD_S:
                state.tail_overflow = False

    # ------------------------------------------------------------------ #
    def _close_finished(self, state: CounterState, now: float, seen_lane: set[int],
                        seen_billing: set[int], clock: Clock) -> list[Event]:
        events: list[Event] = []
        for track_id, shopper in list(state.shoppers.items()):
            still_here = track_id in seen_lane
            if still_here and track_id in seen_billing:
                continue
            gone_from_lane = not still_here and now - shopper.last_lane_s > state.spec.gap_tolerance_s
            left_billing = (shopper.service_start_s is not None
                            and shopper.last_billing_s is not None
                            and now - shopper.last_billing_s > state.spec.gap_tolerance_s)
            if not (gone_from_lane or left_billing):
                continue

            if shopper.service_start_s is not None:
                end = shopper.last_billing_s or now
                service_s = max(0.0, end - shopper.service_start_s)
                state.completed_services.append(service_s)
                if shopper.wait_s is not None:
                    state.completed_waits.append(shopper.wait_s)
                if state.in_service_track == track_id:
                    state.in_service_track = None
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.SERVICE_DONE,
                    data=ServiceDoneData(counter=state.spec.name, track=track_id,
                                         service_s=round(service_s, 2),
                                         wait_s=(round(shopper.wait_s, 2)
                                                 if shopper.wait_s is not None else None)),
                ))
            elif gone_from_lane and state.spec.membership == "dwell":
                # Maybe an ID switch: hold them; renege only if nobody takes over.
                state.lost[track_id] = (shopper, now)
            if gone_from_lane:
                del state.shoppers[track_id]
        return events

    # ------------------------------------------------------------------ #
    def arrivals_per_min(self, now_s: float, window_s: float = 300.0) -> float:
        recent = [t for t in self.checkout_arrivals if now_s - t <= window_s]
        span = min(window_s, max(1e-6, now_s)) / 60.0
        return len(recent) / span if span > 0 else 0.0

    def mu_per_min(self) -> float | None:
        """Pooled service rate across counters that have measured services."""
        rates = [s.mu_per_min for s in self.counters.values() if s.mu_per_min]
        if not rates:
            return None
        return float(statistics.mean(rates))

    def open_counters(self) -> int:
        return sum(1 for s in self.counters.values() if s.spec.open)

    def summary(self, now_s: float | None = None) -> dict:
        out = {}
        for name, state in self.counters.items():
            wait_littles = state.littles(now_s)[0] if now_s is not None else None
            out[name] = {
                "queue_len": state.queue_len,
                "queue_len_smooth": round(state.queue_len_smooth, 2),
                "queue_parties": state.parties,
                "median_wait_s": state.median_wait_s,
                "wait_littles_s": wait_littles,
                "median_service_s": state.median_service_s,
                "mu_per_min": state.mu_per_min,
                "services_completed": len(state.completed_services),
                "joins": len(state.joins),
                "balks": state.balks,
                "reneges": state.reneges,
                "tail_overflow": state.tail_overflow,
            }
        return out
