"""Entry / exit counting.

Fixes audit items S3, S4 and S6:

*   counts the **foot point** (bottom-centre), not the box centre;
*   a **hysteresis band** either side of the line, so a track must be decisively
    on one side before its side is recorded - centre jitter can no longer count a
    person twice;
*   the decision is made on the **track's own history of decided sides**, so a
    momentary detection gap does not lose the crossing;
*   a per-track, per-direction **cooldown** so one person loitering on the line
    cannot pump the counter.

Occupancy is `entries - exits`, floored at zero, which is what the dashboard
shows and what the evaluation harness scores against a manual count.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.clock import Clock
from ..core.events import EntryExitData, Event, EventType, make_event
from ..core.geometry import Line, foot_point
from ..tracking.tracker import Track


@dataclass
class _TrackState:
    side: int = 0                 # last *decided* side (+1 / -1), 0 = never decided
    last_count_s: dict[str, float] = field(default_factory=dict)
    last_seen_s: float = 0.0


@dataclass
class Crossing:
    track_id: int
    direction: str        # "in" | "out"
    at_s: float


class FootfallCounter:
    def __init__(self, line: Line, *, entry_direction: str = "pos", cooldown_s: float = 3.0,
                 store: str = "demo-store", node: str = "pi5-01", cam: str = "entrance",
                 forget_after_s: float = 60.0) -> None:
        self.line = line
        self.entry_direction = entry_direction
        self.cooldown_s = cooldown_s
        self.store, self.node, self.cam = store, node, cam
        self.forget_after_s = forget_after_s
        self._tracks: dict[int, _TrackState] = {}
        self.entries = 0
        self.exits = 0
        self.crossings: list[Crossing] = []

    @property
    def occupancy(self) -> int:
        return max(0, self.entries - self.exits)

    def _direction_for(self, from_side: int, to_side: int) -> str:
        """A -1 -> +1 crossing is 'pos'.  Config says which of the two is entering."""
        positive = to_side > from_side
        entering = positive if self.entry_direction == "pos" else not positive
        return "in" if entering else "out"

    def update(self, tracks: list[Track], clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        events: list[Event] = []
        for track in tracks:
            state = self._tracks.setdefault(track.track_id, _TrackState())
            state.last_seen_s = now
            side = self.line.side(foot_point(track.xyxy))
            if side == 0:
                continue  # inside the dead band: undecided, keep the previous side
            if state.side == 0:
                state.side = side
                continue
            if side == state.side:
                continue

            direction = self._direction_for(state.side, side)
            state.side = side
            last = state.last_count_s.get(direction, -1e9)
            if now - last < self.cooldown_s:
                continue
            state.last_count_s[direction] = now

            if direction == "in":
                self.entries += 1
            else:
                self.exits += 1
            self.crossings.append(Crossing(track.track_id, direction, now))
            events.append(make_event(
                ts=clock.now(), store=self.store, node=self.node, cam=self.cam,
                type=EventType.ENTRY if direction == "in" else EventType.EXIT,
                data=EntryExitData(line=self.line.name, track=track.track_id, direction=direction),
            ))

        # Bound memory on long runs: a track not seen for a minute will never
        # come back with the same id.
        stale = [tid for tid, st in self._tracks.items() if now - st.last_seen_s > self.forget_after_s]
        for tid in stale:
            del self._tracks[tid]
        return events
