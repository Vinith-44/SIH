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

    def update(self, tracks: list[Track], clock: Clock,
               exclude: set[int] | frozenset[int] = frozenset()) -> list[Event]:
        now = clock.monotonic_s()
        events: list[Event] = []
        for track in tracks:
            if track.track_id in exclude:
                continue
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


# --------------------------------------------------------------------------- #
# Counting v2: gate + plausibility checks (research/23 section 3.1, M1)
# --------------------------------------------------------------------------- #

# cos(angle) between the track's motion and the line normal that a crossing
# must reach.  "balanced" = within 75 degrees of straight across, "strict" =
# within 60 degrees (the nvdsanalytics direction-mode idea).
DIRECTION_COS = {"off": -1.0, "balanced": 0.26, "strict": 0.5}


@dataclass
class _GateState:
    first_seen_s: float
    last_seen_s: float
    side: int = 0                         # last decided side (+1 / -1), 0 = never decided
    history: list[tuple[float, float, float]] = field(default_factory=list)  # (t, x, y)
    counted: set[str] = field(default_factory=set)
    last_count_s: dict[str, float] = field(default_factory=dict)
    pending: tuple[str, float, object] | None = None  # (direction, at_s, ts)


class GateCounter:
    """Entry / exit counting v2.

    Same foot point and event output as `FootfallCounter`, plus the fixes for the
    CAVIAR over-count (research/23 section 3.1):

    *   **Two-line gate.** The configured line is the centre of a band
        `gate_px` wide; a crossing needs the foot point to go from beyond one
        edge to beyond the other.  Equivalent to two parallel lines A and B
        with the order A->B deciding the direction.
    *   **Minimum track age** before a track may count, so a flickering
        detection that becomes a fresh track right at the line cannot count.
    *   **Direction check.** The motion over the last `direction_window_s`
        must point across the line (not along it) within the configured mode.
    *   **Minimum displacement** across the line over that window.
    *   **One count per track per direction**, plus the v1 cooldown.
    *   **Confirmation.** Optionally the track must stay on the far side for
        `confirm_s` (or vanish there) before the count is committed; a quick
        step back cancels it.

    Rejected candidates are tallied in `rejected` by reason, which is what the
    evaluation uses to explain where the over-count went.
    """

    def __init__(self, line: Line, *, entry_direction: str = "pos", cooldown_s: float = 3.0,
                 gate_px: float = 10.0, min_track_age_s: float = 0.0,
                 min_displacement_px: float = 0.0, direction_mode: str = "off",
                 direction_window_s: float = 1.0, confirm_s: float = 0.5,
                 store: str = "demo-store", node: str = "pi5-01", cam: str = "entrance",
                 forget_after_s: float = 60.0) -> None:
        if direction_mode not in DIRECTION_COS:
            raise ValueError(f"direction_mode must be one of {sorted(DIRECTION_COS)}")
        self.line = line
        self.entry_direction = entry_direction
        self.cooldown_s = cooldown_s
        self.half_gate = max(gate_px, 0.0) / 2.0
        self.min_track_age_s = min_track_age_s
        self.min_displacement_px = min_displacement_px
        self.min_cos = DIRECTION_COS[direction_mode]
        self.direction_window_s = direction_window_s
        self.confirm_s = confirm_s
        self.store, self.node, self.cam = store, node, cam
        self.forget_after_s = forget_after_s
        self._tracks: dict[int, _GateState] = {}
        self.entries = 0
        self.exits = 0
        self.crossings: list[Crossing] = []
        self.rejected: dict[str, int] = {"too_young": 0, "direction": 0, "displacement": 0,
                                         "repeat": 0, "cooldown": 0, "reverted": 0}

    @property
    def occupancy(self) -> int:
        return max(0, self.entries - self.exits)

    def _side(self, distance: float) -> int:
        if distance > self.half_gate:
            return 1
        if distance < -self.half_gate:
            return -1
        return 0

    def _direction_for(self, from_side: int, to_side: int) -> str:
        positive = to_side > from_side
        entering = positive if self.entry_direction == "pos" else not positive
        return "in" if entering else "out"

    def _normal(self) -> tuple[float, float]:
        (ax, ay), (bx, by) = self.line._pa, self.line._pb
        dx, dy = float(bx - ax), float(by - ay)
        length = max(1e-9, (dx * dx + dy * dy) ** 0.5)
        return (-dy / length, dx / length)       # points to positive signed distance

    def _plausible(self, state: _GateState, to_side: int, now: float) -> str | None:
        """Return the rejection reason, or None if the crossing is plausible."""
        if now - state.first_seen_s < self.min_track_age_s:
            return "too_young"
        window = [h for h in state.history if now - h[0] <= self.direction_window_s]
        if len(window) < 2:
            window = state.history
        end = window[-1]
        # Measure from the point that was furthest on the side being left, not
        # from the oldest point: after a U-turn inside the window the oldest
        # point can lie beyond the turn and would point the wrong way.
        start = min(window[:-1], key=lambda h: self.line.signed_distance((h[1], h[2])) * to_side)
        vx, vy = end[1] - start[1], end[2] - start[2]
        nx, ny = self._normal()
        across = (vx * nx + vy * ny) * to_side          # >0 = moving towards the new side
        length = (vx * vx + vy * vy) ** 0.5
        if length > 1e-6 and across / length < self.min_cos:
            return "direction"
        if across < self.min_displacement_px:
            return "displacement"
        return None

    def _commit(self, track_id: int, state: _GateState) -> Event:
        direction, at_s, ts = state.pending
        state.pending = None
        state.counted.add(direction)
        state.last_count_s[direction] = at_s
        if direction == "in":
            self.entries += 1
        else:
            self.exits += 1
        self.crossings.append(Crossing(track_id, direction, at_s))
        return make_event(
            ts=ts, store=self.store, node=self.node, cam=self.cam,
            type=EventType.ENTRY if direction == "in" else EventType.EXIT,
            data=EntryExitData(line=self.line.name, track=track_id, direction=direction),
        )

    def update(self, tracks: list[Track], clock: Clock,
               exclude: set[int] | frozenset[int] = frozenset()) -> list[Event]:
        """`exclude` = track ids that must never count (staff)."""
        now = clock.monotonic_s()
        events: list[Event] = []
        seen: set[int] = set()
        for track in tracks:
            seen.add(track.track_id)
            state = self._tracks.get(track.track_id)
            if state is None:
                state = self._tracks[track.track_id] = _GateState(now, now)
            state.last_seen_s = now
            foot = foot_point(track.xyxy)
            state.history.append((now, foot[0], foot[1]))
            # Keep only what the direction window needs (plus one older point).
            while len(state.history) > 2 and now - state.history[1][0] > self.direction_window_s:
                state.history.pop(0)
            if track.track_id in exclude:
                state.pending = None
                continue

            side = self._side(self.line.signed_distance(foot))
            if side == 0:
                continue
            if state.side == 0:
                state.side = side
                continue
            if side == state.side:
                if state.pending and now - state.pending[1] >= self.confirm_s:
                    events.append(self._commit(track.track_id, state))
                continue

            # The decided side flipped.
            previous, state.side = state.side, side
            if state.pending is not None:          # stepped back before confirmation
                state.pending = None
                self.rejected["reverted"] += 1
                continue
            direction = self._direction_for(previous, side)
            reason = self._plausible(state, side, now)
            if reason is None and direction in state.counted:
                reason = "repeat"
            if reason is None and now - state.last_count_s.get(direction, -1e9) < self.cooldown_s:
                reason = "cooldown"
            if reason is not None:
                self.rejected[reason] += 1
                continue
            state.pending = (direction, now, clock.now())
            if self.confirm_s <= 0.0:
                events.append(self._commit(track.track_id, state))

        for track_id, state in list(self._tracks.items()):
            if track_id in seen:
                continue
            # Vanished on the far side after a crossing: that is a completed
            # crossing (they walked on out of view), so commit it.
            if state.pending is not None and now - state.last_seen_s >= self.confirm_s:
                events.append(self._commit(track_id, state))
            if now - state.last_seen_s > self.forget_after_s:
                del self._tracks[track_id]
        return events


def build_counter(line: Line, line_config, *, store: str, node: str, cam: str):
    """v1 `FootfallCounter` or v2 `GateCounter`, chosen by `line.mode`."""
    common = dict(entry_direction=line_config.entry_direction,
                  cooldown_s=line_config.cooldown_s, store=store, node=node, cam=cam)
    if getattr(line_config, "mode", "single") == "gate":
        return GateCounter(line, gate_px=line_config.gate_px,
                           min_track_age_s=line_config.min_track_age_s,
                           min_displacement_px=line_config.min_displacement_px,
                           direction_mode=line_config.direction_mode,
                           direction_window_s=line_config.direction_window_s,
                           confirm_s=line_config.confirm_s, **common)
    return FootfallCounter(line, **common)
