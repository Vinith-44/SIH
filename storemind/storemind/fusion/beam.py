"""IR break-beam cross-check for the entrance camera (M1).

Two IR beams across the door give an exact crossing time and direction (decided
on the MCU, `$D` -> `BEAM_CROSS`).  They can't tell a person from a trolley and
they can't see a group walking side by side, but they never lose a track.  The
camera is the opposite.  So each checks the other:

*   **Agreement.** Every beam crossing is matched to a camera crossing of the
    same direction within `tolerance_s`.  Agreement over the last `window`
    crossings = 2 x matched / (beam crossings + camera crossings), i.e. an F1
    between the two counters.  Shown on the dashboard; below `alert_below`
    (after `min_samples`) it raises "check entrance calibration".
*   **Fallback.** While the entrance camera is unhealthy (`CAMERA_HEALTH`
    state other than `ok`), beam crossings are published as `ENTRY`/`EXIT`
    with `line = "beam:<door>"` and `track = -1`, so footfall and the queue
    forecast keep working.  The dashboard can tell them apart by the line name.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from ..core.events import EntryExitData, Event, EventType, make_event


@dataclass
class _Crossing:
    t: float          # seconds (event timestamp)
    direction: str    # "in" | "out"
    matched: bool = False


@dataclass
class _Door:
    cam: str
    beams: deque = field(default_factory=deque)
    camera: deque = field(default_factory=deque)


class BeamCrossCheck:
    def __init__(self, doors: dict[str, str], *, tolerance_s: float = 2.0, window: int = 30,
                 min_samples: int = 10, alert_below: float = 0.8, fallback: bool = True,
                 store: str = "demo-store", node: str = "pi5-01") -> None:
        """`doors` maps beam door id -> camera name (from `line.beam_door`)."""
        self.doors = {door: _Door(cam, deque(maxlen=window), deque(maxlen=window))
                      for door, cam in doors.items()}
        self.cam_to_door = {cam: door for door, cam in doors.items()}
        self.tolerance_s = tolerance_s
        self.min_samples = min_samples
        self.alert_below = alert_below
        self.fallback = fallback
        self.store, self.node = store, node
        self.camera_ok: dict[str, bool] = {cam: True for cam in doors.values()}
        self.fallback_counts = 0

    # ------------------------------------------------------------------ #
    def _match(self, new: _Crossing, others: deque) -> None:
        best, best_dt = None, None
        for other in others:
            if other.matched or other.direction != new.direction:
                continue
            dt = abs(other.t - new.t)
            if dt <= self.tolerance_s and (best_dt is None or dt < best_dt):
                best, best_dt = other, dt
        if best is not None:
            best.matched = new.matched = True

    def on_event(self, event: Event) -> list[Event]:
        """Feed every BEAM_CROSS, ENTRY, EXIT and CAMERA_HEALTH event."""
        t = event.dt.timestamp()
        if event.type is EventType.CAMERA_HEALTH:
            cam = event.data["cam"]
            if cam in self.camera_ok:
                self.camera_ok[cam] = event.data["state"] == "ok"
            return []
        if event.type in (EventType.ENTRY, EventType.EXIT):
            if event.data["line"].startswith("beam:") or event.cam not in self.cam_to_door:
                return []                       # our own fallback output, or another camera
            door = self.doors[self.cam_to_door[event.cam]]
            crossing = _Crossing(t, event.data["direction"])
            self._match(crossing, door.beams)
            door.camera.append(crossing)
            return []
        if event.type is not EventType.BEAM_CROSS or event.data["door"] not in self.doors:
            return []

        door_id = event.data["door"]
        door = self.doors[door_id]
        crossing = _Crossing(t, event.data["direction"])
        self._match(crossing, door.camera)
        door.beams.append(crossing)
        if not (self.fallback and not self.camera_ok.get(door.cam, True)):
            return []
        self.fallback_counts += 1
        direction = event.data["direction"]
        return [make_event(ts=event.ts, store=self.store, node=self.node, cam=door.cam,
                           type=EventType.ENTRY if direction == "in" else EventType.EXIT,
                           data=EntryExitData(line=f"beam:{door_id}", track=-1,
                                              direction=direction))]

    # ------------------------------------------------------------------ #
    def agreement(self, door_id: str) -> dict:
        door = self.doors[door_id]
        beams, camera = len(door.beams), len(door.camera)
        matched = sum(1 for c in door.beams if c.matched)
        total = beams + camera
        return {"door": door_id, "cam": door.cam, "beam_crossings": beams,
                "camera_crossings": camera, "matched": matched,
                "agreement": (2 * matched / total) if total else None,
                "camera_ok": self.camera_ok.get(door.cam, True)}

    def alerts(self) -> list[tuple[str, str, dict]]:
        """(alert key, message, evidence) for every door whose counters disagree."""
        out = []
        for door_id in self.doors:
            stats = self.agreement(door_id)
            if not stats["camera_ok"] or stats["beam_crossings"] < self.min_samples:
                continue
            if stats["agreement"] is not None and stats["agreement"] < self.alert_below:
                out.append((f"BEAM_MISMATCH:{door_id}",
                            f"Entrance camera {stats['cam']} and door beam {door_id} disagree "
                            f"({stats['agreement'] * 100:.0f}% agreement over the last "
                            f"{stats['beam_crossings']} beam crossings) - check entrance "
                            "calibration", stats))
        return out
