"""Promotion analytics: who walked past a promo display, who stopped, for how long,
and whether they picked the item up (docs/PROMO.md).

The system cannot know what a promotion is, so the owner or installer marks it:
a `kind: promo` zone in the config with a name, product, offer and dates.  This
module measures it:

*   **Passer-by** - a track whose foot point comes within `approach_band` (a
    fraction of frame height) of the zone, or crosses it, and does not stay
    inside for `min_dwell_s`.  The zone engine drops these; here they are the
    denominator of the stop rate.
*   **Stopper** - stays inside the zone for at least `min_dwell_s` (the same
    rule as ZONE_VISIT).  Dwell = first to last moment inside; gaps up to
    `gap_tolerance_s` do not end a stop.
*   **Stop rate** = stoppers / (passers-by + stoppers).
*   **Picks** - PICKUP events (MEMS-gated load cell, fusion/interaction.py) at the
    slot linked to the promo zone: "took the item".  Not a sale: nothing here is
    linked to billing.

Every `report_every_s` it publishes one PROMO_STATE per promo zone with the
counts of the people who *finished* passing or stopping in that window, so
windows add up without double counting.  Outside its dates a promo counts
nothing and publishes `active: false`.

Privacy: only session-random track numbers are used and nothing per person is
published.  Staff are removed before tracks reach this module (the pipeline
passes `customers`).

**Track stitching.**  If a tracker swaps a stopper's number mid-stop, the old
visit would close as a short "passer-by" and a new stop would start from zero.
A new track that appears within `STITCH_DIST` frame heights of a visit whose
track vanished less than `gap_tolerance_s` ago takes that visit over (the same
idea as the queue engine).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, median

import numpy as np

from ..core.clock import Clock
from ..core.events import Event, EventType, PromoStateData, make_event
from ..core.geometry import Polygon, foot_point
from ..tracking.tracker import Track

DEFAULT_APPROACH_BAND = 0.08      # frame heights; tuned on simulated seeds 1-10 (eval/eval_promo.py --grid)
DEFAULT_REPORT_EVERY_S = 300.0
STITCH_DIST = 0.10                # frame heights
DEFAULT_STITCH = True             # tuned with the band


@dataclass
class PromoSpec:
    zone: str
    promo: str
    polygon: Polygon                     # already resolved to pixels
    frame_height: float
    min_dwell_s: float = 3.0
    approach_band: float = DEFAULT_APPROACH_BAND
    report_every_s: float = DEFAULT_REPORT_EVERY_S
    gap_tolerance_s: float = 2.0
    linked_slot: str | None = None       # "shelf/slot"
    has_scale: bool = False              # the linked slot has a load cell (sensors.cell_map)
    start_date: date | None = None
    end_date: date | None = None
    stitch: bool = DEFAULT_STITCH

    def active(self, day: date) -> bool:
        return (self.start_date is None or day >= self.start_date) and (self.end_date is None or day <= self.end_date)

    @property
    def band_px(self) -> float:
        return self.approach_band * self.frame_height


@dataclass
class _Visit:
    track_id: int
    last_near_s: float
    last_xy: tuple[float, float]
    first_inside_s: float | None = None
    last_inside_s: float | None = None

    @property
    def dwell_s(self) -> float:
        if self.first_inside_s is None or self.last_inside_s is None:
            return 0.0
        return self.last_inside_s - self.first_inside_s


@dataclass
class _Window:
    start_s: float
    passers_by: int = 0
    dwells: list[float] = field(default_factory=list)
    picks: int = 0
    put_backs: int = 0
    units: int = 0


def distance_to_polygon(point: tuple[float, float], polygon: np.ndarray) -> float:
    """Shortest distance (pixels) from a point to the polygon's outline."""
    p = np.asarray(point, dtype=float)
    a = polygon.astype(float)
    b = np.roll(a, -1, axis=0)
    ab = b - a
    t = np.clip(((p - a) * ab).sum(axis=1) / np.maximum((ab * ab).sum(axis=1), 1e-12), 0.0, 1.0)
    closest = a + ab * t[:, None]
    return float(np.sqrt(((closest - p) ** 2).sum(axis=1)).min())


class PromoEngine:
    def __init__(self, specs: list[PromoSpec], *, store: str = "demo-store", node: str = "pi5-01",
                 cam: str = "cam") -> None:
        self.specs = {s.zone: s for s in specs}
        self.store, self.node, self.cam = store, node, cam
        self._open: dict[str, dict[int, _Visit]] = {s.zone: {} for s in specs}
        self._window: dict[str, _Window] = {}
        self.totals: dict[str, dict[str, int]] = {s.zone: {"passers_by": 0, "stoppers": 0, "picks": 0}
                                                  for s in specs}

    # ------------------------------------------------------------------ #
    def update(self, tracks: list[Track], clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        day = clock.now().date()
        events: list[Event] = []
        present = {t.track_id for t in tracks}
        for zone, spec in self.specs.items():
            window = self._window.setdefault(zone, _Window(start_s=now))
            visits = self._open[zone]
            if spec.active(day):
                for track in tracks:
                    xy = foot_point(track.xyxy)
                    inside = spec.polygon.contains(xy)
                    if not inside and distance_to_polygon(xy, spec.polygon.pixels) > spec.band_px:
                        continue
                    visit = visits.get(track.track_id)
                    if visit is None:
                        visit = self._stitch(spec, visits, xy, now, present) or _Visit(track.track_id, now, xy)
                        visits[track.track_id] = visit
                    visit.last_near_s, visit.last_xy = now, xy
                    if inside:
                        if visit.first_inside_s is None:
                            visit.first_inside_s = now
                        visit.last_inside_s = now
            for track_id, visit in list(visits.items()):
                if now - visit.last_near_s > spec.gap_tolerance_s:
                    del visits[track_id]
                    self._close(spec, window, visit)
            while now - window.start_s >= spec.report_every_s:
                # Stamp each window at its own end, even when a gap in the frames closes several at once.
                late_s = now - (window.start_s + spec.report_every_s)
                events.append(self._publish(spec, window, spec.report_every_s, clock, day, late_s))
                window = self._window[zone] = _Window(start_s=window.start_s + spec.report_every_s)
        return events

    def _stitch(self, spec: PromoSpec, visits: dict[int, _Visit], xy: tuple[float, float], now: float,
                present: set[int]) -> _Visit | None:
        if not spec.stitch:
            return None
        best, best_d = None, STITCH_DIST * spec.frame_height
        for track_id, visit in visits.items():
            if track_id in present or now - visit.last_near_s > spec.gap_tolerance_s:
                continue
            d = float(np.hypot(xy[0] - visit.last_xy[0], xy[1] - visit.last_xy[1]))
            if d <= best_d:
                best, best_d = track_id, d
        if best is None:
            return None
        return visits.pop(best)

    def _close(self, spec: PromoSpec, window: _Window, visit: _Visit) -> None:
        if visit.dwell_s >= spec.min_dwell_s:
            window.dwells.append(visit.dwell_s)
            self.totals[spec.zone]["stoppers"] += 1
        else:
            window.passers_by += 1
            self.totals[spec.zone]["passers_by"] += 1

    def _publish(self, spec: PromoSpec, window: _Window, window_s: float, clock: Clock, day: date,
                 late_s: float = 0.0) -> Event:
        active = spec.active(day)
        data: dict = {"promo": spec.promo, "zone": spec.zone, "active": active, "window_s": round(window_s, 1)}
        if active:
            stoppers = len(window.dwells)
            seen = window.passers_by + stoppers
            data |= {
                "passers_by": window.passers_by, "stoppers": stoppers,
                "stop_rate": round(stoppers / seen, 3) if seen else None,
                "dwell_mean_s": round(mean(window.dwells), 1) if window.dwells else None,
                "dwell_median_s": round(median(window.dwells), 1) if window.dwells else None,
                "dwell_total_s": round(sum(window.dwells), 1),
            }
            if spec.has_scale:
                data |= {"picks": window.picks, "put_backs": window.put_backs, "units_picked": window.units}
        return make_event(ts=clock.now() - timedelta(seconds=late_s), store=self.store, node=self.node, cam=self.cam,
                          type=EventType.PROMO_STATE, data=PromoStateData(**data))

    # ------------------------------------------------------------------ #
    def on_pickup(self, event: Event, clock: Clock) -> None:
        """A PICKUP at a promo's linked slot counts in that promo's current window."""
        key = f"{event.data.get('shelf')}/{event.data.get('slot')}"
        day = clock.now().date()
        for zone, spec in self.specs.items():
            if spec.linked_slot != key or not spec.has_scale or not spec.active(day):
                continue
            window = self._window.setdefault(zone, _Window(start_s=clock.monotonic_s()))
            action = event.data.get("action", "pick")
            if action == "pick":
                window.picks += 1
                window.units += int(event.data.get("units") or 0)
                self.totals[zone]["picks"] += 1
            elif action == "put_back":
                window.put_backs += 1

    def flush(self, clock: Clock) -> list[Event]:
        """End of stream: close every open visit and publish the partial window."""
        now = clock.monotonic_s()
        day = clock.now().date()
        events = []
        for zone, spec in self.specs.items():
            window = self._window.setdefault(zone, _Window(start_s=now))
            for visit in self._open[zone].values():
                self._close(spec, window, visit)
            self._open[zone].clear()
            if now > window.start_s:
                events.append(self._publish(spec, window, now - window.start_s, clock, day))
                self._window[zone] = _Window(start_s=now)
        return events


def promo_spec_from_config(zone, width: int, height: int, scales: frozenset[str] | set[str] = frozenset()) -> PromoSpec:
    """`zone` is a `core.config.ZoneConfig` with kind: promo.  `scales` = the "shelf/slot" keys that
    have a load cell (sensors.cell_map), so picks are only reported where they can be measured."""
    linked = f"{zone.shelf}/{zone.slot}" if zone.shelf and zone.slot else None
    return PromoSpec(
        zone=zone.name, promo=zone.promo_name or zone.name,
        polygon=Polygon(zone.name, [tuple(p) for p in zone.points], "promo").resolve(width, height),
        frame_height=float(height), min_dwell_s=zone.min_dwell_s,
        approach_band=zone.approach_band if zone.approach_band is not None else DEFAULT_APPROACH_BAND,
        report_every_s=zone.report_every_s if zone.report_every_s is not None else DEFAULT_REPORT_EVERY_S,
        linked_slot=linked, has_scale=linked in scales if linked else False,
        start_date=zone.start_date, end_date=zone.end_date)
