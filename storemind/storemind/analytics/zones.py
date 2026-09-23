"""Zone dwell time.

Audit item S6: the legacy dwell timer restarted whenever a track was lost for a
few frames, so dwell was systematically under-counted.  Here a visit stays open
through a `gap_tolerance_s` window (default 2 s) and only closes when the track
has genuinely been absent from the zone for longer than that.

A visit shorter than `min_dwell_s` is dropped rather than published - walking
past a promo display is not dwelling at it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.clock import Clock
from ..core.events import Event, EventType, ZoneVisitData, make_event
from ..core.geometry import Polygon, foot_point
from ..tracking.tracker import Track


@dataclass
class Visit:
    zone: str
    track_id: int
    start_s: float
    last_inside_s: float
    kind: str = "zone"

    @property
    def dwell_s(self) -> float:
        return max(0.0, self.last_inside_s - self.start_s)


@dataclass
class ZoneSpec:
    polygon: Polygon
    min_dwell_s: float = 3.0
    gap_tolerance_s: float = 2.0
    shelf: str | None = None
    slot: str | None = None


class ZoneEngine:
    def __init__(self, zones: list[ZoneSpec], *, store: str = "demo-store",
                 node: str = "pi5-01", cam: str = "cam") -> None:
        self.zones = {z.polygon.name: z for z in zones}
        self.store, self.node, self.cam = store, node, cam
        self._open: dict[tuple[str, int], Visit] = {}
        self.completed: list[Visit] = []
        # Live dwell per zone, used by fusion for LOST_SALE_RISK while the
        # shopper is still standing there.
        self.current: dict[str, dict[int, float]] = {name: {} for name in self.zones}

    def occupancy(self, zone_name: str) -> int:
        return len(self.current.get(zone_name, {}))

    def update(self, tracks: list[Track], clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        events: list[Event] = []
        inside_now: set[tuple[str, int]] = set()

        for name, spec in self.zones.items():
            live = self.current.setdefault(name, {})
            live.clear()
            for track in tracks:
                if not spec.polygon.contains(foot_point(track.xyxy)):
                    continue
                key = (name, track.track_id)
                inside_now.add(key)
                visit = self._open.get(key)
                if visit is None:
                    visit = Visit(zone=name, track_id=track.track_id, start_s=now,
                                  last_inside_s=now, kind=spec.polygon.kind)
                    self._open[key] = visit
                else:
                    visit.last_inside_s = now
                live[track.track_id] = visit.dwell_s

        for key, visit in list(self._open.items()):
            if key in inside_now:
                continue
            spec = self.zones[visit.zone]
            if now - visit.last_inside_s <= spec.gap_tolerance_s:
                continue  # brief detection gap - keep the visit open
            del self._open[key]
            if visit.dwell_s >= spec.min_dwell_s:
                self.completed.append(visit)
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.ZONE_VISIT,
                    data=ZoneVisitData(zone=visit.zone, zone_kind=visit.kind,
                                       track=visit.track_id, dwell_s=round(visit.dwell_s, 2)),
                ))
        return events

    def flush(self, clock: Clock) -> list[Event]:
        """Close every open visit (called at end of stream so the last shoppers
        are not silently dropped from the totals)."""
        events: list[Event] = []
        for key, visit in list(self._open.items()):
            spec = self.zones[visit.zone]
            del self._open[key]
            if visit.dwell_s >= spec.min_dwell_s:
                self.completed.append(visit)
                events.append(make_event(
                    ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                    type=EventType.ZONE_VISIT,
                    data=ZoneVisitData(zone=visit.zone, zone_kind=visit.kind,
                                       track=visit.track_id, dwell_s=round(visit.dwell_s, 2)),
                ))
        return events
