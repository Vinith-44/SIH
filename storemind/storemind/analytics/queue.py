"""Per-counter queue intelligence.

This module fixes the worst bugs in the legacy `live_queue_intelligence.py`
(audit section 4):

*   **Q1** - one polygon covering 84% of the frame meant "anyone in the shop is in
    the queue".  Here every counter has its own **lane** polygon plus a small
    **billing** polygon, both drawn by `tools/calibrate.py`.
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

Definitions used consistently everywhere (write these on the slide):

*   *queue length* - tracks whose foot point is inside the lane polygon right now.
*   *wait* - from the moment a track joins the lane to the moment its service
    starts.
*   *service time* - how long a track holds the billing polygon.
*   *mu* - completed services per minute per counter.
*   *lambda* - tracks joining any lane per minute (arrivals to checkout).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

from ..core.clock import Clock
from ..core.events import Event, EventType, QueueStateData, ServiceDoneData, make_event
from ..core.geometry import Polygon, foot_point
from ..tracking.tracker import Track


@dataclass
class CounterSpec:
    name: str
    lane: Polygon
    billing: Polygon
    min_service_s: float = 3.0
    gap_tolerance_s: float = 2.0
    open: bool = True
    congestion_len: int = 5


@dataclass
class _Shopper:
    track_id: int
    joined_s: float
    last_lane_s: float
    billing_since_s: float | None = None
    service_start_s: float | None = None
    last_billing_s: float | None = None
    wait_s: float | None = None


@dataclass
class CounterState:
    spec: CounterSpec
    shoppers: dict[int, _Shopper] = field(default_factory=dict)
    completed_services: list[float] = field(default_factory=list)
    completed_waits: list[float] = field(default_factory=list)
    length_window: deque = field(default_factory=lambda: deque(maxlen=15))
    joins: list[float] = field(default_factory=list)
    in_service_track: int | None = None

    @property
    def queue_len(self) -> int:
        """People waiting in the lane, excluding whoever is being served."""
        return sum(1 for s in self.shoppers.values()
                   if s.service_start_s is None)

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
                in_lane = in_billing or spec.lane.contains(point)
                if not in_lane:
                    continue
                seen_lane.add(track.track_id)
                shopper = state.shoppers.get(track.track_id)
                if shopper is None:
                    shopper = _Shopper(track_id=track.track_id, joined_s=now, last_lane_s=now)
                    state.shoppers[track.track_id] = shopper
                    state.joins.append(now)
                    self.checkout_arrivals.append(now)
                else:
                    shopper.last_lane_s = now

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
            state.length_window.append(state.queue_len)

        if now >= self._next_state_s:
            self._next_state_s = now + self.state_period_s
            for state in self.counters.values():
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
                    ),
                ))
        return events

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

    def summary(self) -> dict:
        return {
            name: {
                "queue_len": state.queue_len,
                "queue_len_smooth": round(state.queue_len_smooth, 2),
                "median_wait_s": state.median_wait_s,
                "median_service_s": state.median_service_s,
                "mu_per_min": state.mu_per_min,
                "services_completed": len(state.completed_services),
                "joins": len(state.joins),
            }
            for name, state in self.counters.items()
        }
