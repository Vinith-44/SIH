"""Shelf interaction fusion: MEMS touch + load cell + camera (M6, Person A).

The MEMS accelerometer under a shelf is its sense of touch; the load cell says
*how much* left; the camera says whether a shopper was there.  Each alone is
ambiguous (research/26 section 3.1, CLAUDE_CODE_PROMPT_V2 M6):

| Rule | Evidence | Output |
|---|---|---|
| **Weight gating** | `SHELF_MOTION` TOUCH opens a handling episode; every `WEIGHT` reading until SETTLED is ignored (a hand pressing on a shelf is not stock) | - |
| **Pick / put-back** | stable weight before TOUCH vs first stable weight after SETTLED | `PICKUP action=pick|put_back`, grams, units = grams / `unit_grams` |
| **Touch, no change** | TOUCH -> SETTLED, weight unchanged (or no load cell), shopper at the shelf | `PICKUP action=touch` (engagement) |
| **Shelf check trigger** | TOUCH -> SETTLED | ask the shelf camera to look *now* (`check_requests`) |
| **Fallen stock** | KNOCK, then the weight dropped | alert "stock may have fallen" |
| **Shelf tilted** | TILT | alert "shelf tilted" |
| **Shrink** | stable weight drop with no touch and nobody at the shelf | `SHRINK_FLAG` |
| **Weight only** | no MEMS fitted (or it missed a gentle touch) but a shopper was there | pick/put-back with evidence "weight only" |
| **Camera moved** | `CAMERA_MOUNT` KNOCK/TILT, fused with the image tamper check within 10 s | CRITICAL if both agree; WARN "bumped, view unchanged"; WARN on tilt >= 2 deg |
| **After hours** | `PRESENCE` active outside `alerts.open_hours` | CRITICAL "motion after hours" |

Alerts are returned as (key, message, severity, evidence) for the pipeline's
AlertManager.  Nothing here identifies anyone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time

from ..core.clock import Clock
from ..core.events import Event, EventType, PickupData, Severity, ShrinkFlagData, make_event

CAMERA_FUSE_WINDOW_S = 10.0
CAMERA_TILT_WARN_DEG = 2.0


@dataclass
class SlotInfo:
    shelf: str
    slot: str
    channel: str | None = None          # load-cell channel (WEIGHT.slot)
    unit_grams: float | None = None


@dataclass
class _SlotState:
    info: SlotInfo
    last_stable_g: float | None = None
    baseline_g: float | None = None     # stable weight when the episode started
    resolved: bool = True
    # Latest "stable" reading inside the episode that no unstable reading has
    # followed: it is the post-release weight if SETTLED comes after it.
    pending_g: float | None = None
    # Outside an episode a change must be seen twice in a row (debounce).
    confirm_g: float | None = None
    picks: int = 0
    put_backs: int = 0
    units_picked: int = 0
    units_returned: int = 0


@dataclass
class _Episode:
    kind: str                           # TOUCH | KNOCK
    start_s: float
    slot: str | None                    # MEMS node's slot, if the node is per-slot
    settled_s: float | None = None
    changed: bool = False


@dataclass
class _CameraEvent:
    cam: str
    kind: str
    tilt_deg: float | None
    at_s: float
    image_tamper: bool = False


class ShelfInteractionEngine:
    def __init__(self, slots: list[SlotInfo], *, store: str = "demo-store", node: str = "pi5-01",
                 mems_enabled: bool = True, noise_g: float = 10.0, settle_timeout_s: float = 2.0,
                 episode_max_s: float = 90.0, shrink_g: float = 50.0, person_window_s: float = 20.0,
                 open_hours: list[str] | None = None,
                 person_at_shelf: Callable[[str], bool] | None = None) -> None:
        self.store, self.node = store, node
        self.slots = {f"{s.shelf}/{s.slot}": _SlotState(s) for s in slots}
        self.by_channel = {s.channel: f"{s.shelf}/{s.slot}" for s in slots if s.channel}
        self.mems_enabled = mems_enabled
        self.noise_g = noise_g
        self.settle_timeout_s = settle_timeout_s
        self.episode_max_s = episode_max_s
        self.shrink_g = shrink_g
        self.person_window_s = person_window_s
        self.open_hours = [_parse_span(span) for span in (open_hours or [])]
        self.person_at_shelf = person_at_shelf
        self.episodes: dict[str, _Episode] = {}           # shelf -> open episode
        self.last_person_s: dict[str, float] = {}         # shelf -> last ZONE_VISIT end
        self.camera_events: list[_CameraEvent] = []
        self.last_image_tamper_s: dict[str, float] = {}
        self.check_requests: list[str] = []               # shelves to look at now
        self.alerts: list[tuple[str, str, Severity, dict]] = []
        self.touches: dict[str, int] = {}
        self.gated_readings = 0

    # ------------------------------------------------------------------ #
    def _shelf_slots(self, shelf: str) -> list[_SlotState]:
        return [s for s in self.slots.values() if s.info.shelf == shelf]

    def _person_near(self, shelf: str, now: float) -> bool:
        if self.person_at_shelf is not None and self.person_at_shelf(shelf):
            return True
        return now - self.last_person_s.get(shelf, -1e9) <= self.person_window_s

    def _pickup(self, clock: Clock, state: _SlotState, grams: float, action: str,
                evidence: str) -> Event:
        unit = state.info.unit_grams
        units = int(round(abs(grams) / unit)) if unit else None
        if action == "pick":
            state.picks += 1
            state.units_picked += units or 0
        elif action == "put_back":
            state.put_backs += 1
            state.units_returned += units or 0
        return make_event(ts=clock.now(), store=self.store, node=self.node, type=EventType.PICKUP,
                          data=PickupData(shelf=state.info.shelf, slot=state.info.slot,
                                          grams=round(abs(grams), 1), evidence=evidence,
                                          action=action, units=units))

    @staticmethod
    def _whole_packs(state: _SlotState, grams: float, tolerance: float = 0.25) -> bool:
        """With a known pack weight, a real change is (nearly) a whole number of
        packs; a hand pressing on the shelf is an arbitrary number of grams."""
        unit = state.info.unit_grams
        if not unit or state.baseline_g is None:
            return True
        packs = (grams - state.baseline_g) / unit
        return abs(packs - round(packs)) <= tolerance

    def _threshold(self, state: _SlotState) -> float:
        unit = state.info.unit_grams
        return max(self.noise_g, 0.4 * unit) if unit else self.noise_g

    # ------------------------------------------------------------------ #
    def on_event(self, event: Event, clock: Clock) -> list[Event]:
        now = clock.monotonic_s()
        kind = event.type
        if kind is EventType.SHELF_MOTION:
            return self._on_motion(event.data, now, clock)
        if kind is EventType.WEIGHT:
            return self._on_weight(event.data, now, clock)
        if kind is EventType.ZONE_VISIT:
            return []                                    # handled by note_zone_visit
        if kind is EventType.CAMERA_MOUNT:
            data = event.data
            self.camera_events.append(_CameraEvent(data["cam"], data["kind"], data.get("tilt_deg"), now))
            return []
        if kind is EventType.PRESENCE:
            self._on_presence(event.data, clock)
        return []

    def note_zone_visit(self, shelf: str, now: float) -> None:
        self.last_person_s[shelf] = now

    def note_image_tamper(self, cam: str, now: float) -> None:
        """The image-based tamper detector (health/monitor.py) fired for `cam`."""
        self.last_image_tamper_s[cam] = now
        for ev in self.camera_events:
            if ev.cam == cam and abs(now - ev.at_s) <= CAMERA_FUSE_WINDOW_S:
                ev.image_tamper = True

    def _on_motion(self, data: dict, now: float, clock: Clock) -> list[Event]:
        shelf, motion = data["shelf"], data["kind"]
        if motion == "TILT":
            self.alerts.append((f"SHELF_TILT:{shelf}", f"Shelf {shelf} has tilted - check it is safe "
                                f"and nothing has fallen", Severity.WARN, dict(data)))
            return []
        episode = self.episodes.get(shelf)
        if motion in ("TOUCH", "KNOCK") and episode is None:
            self.episodes[shelf] = _Episode(motion, now, data.get("slot"))
            for state in self._shelf_slots(shelf):
                state.baseline_g, state.resolved, state.pending_g = state.last_stable_g, False, None
            if motion == "KNOCK":                         # a spike: no SETTLED will follow
                self.episodes[shelf].settled_s = now
            return []
        if motion == "SETTLED" and episode is not None and episode.settled_s is None:
            episode.settled_s = now
            self.check_requests.append(shelf)              # look at the shelf now
            # The load cell often reports the new weight before the MEMS says settled.
            events: list[Event] = []
            for state in self._shelf_slots(shelf):
                if not state.resolved and state.pending_g is not None:
                    if not self._whole_packs(state, state.pending_g):
                        state.pending_g = None             # a hand push, not stock: wait for the next reading
                        continue
                    events += self._resolve_slot(state, state.pending_g, episode, clock)
                    state.last_stable_g = state.pending_g
            return events + self._maybe_close(shelf, clock)
        return []

    def _on_weight(self, data: dict, now: float, clock: Clock) -> list[Event]:
        key = self.by_channel.get(str(data["slot"]))
        if key is None:
            return []
        state = self.slots[key]
        shelf = state.info.shelf
        grams = float(data["grams"])
        episode = self.episodes.get(shelf)
        if not data["stable"]:
            self.gated_readings += 1
            state.pending_g = None                       # still being handled
            state.confirm_g = None
            return []
        if episode is not None:
            if episode.settled_s is None:
                self.gated_readings += 1                 # not settled yet: hold it, don't trust it
                state.pending_g = grams
                return []
            events = self._resolve_slot(state, grams, episode, clock)
            state.last_stable_g = grams
            return events + self._maybe_close(shelf, clock)
        # No episode: a change here was not preceded by a touch.
        events = []
        previous = state.last_stable_g
        if previous is None:
            state.last_stable_g = grams
            return events
        delta = grams - previous
        threshold = self._threshold(state)
        if abs(delta) < threshold:
            state.last_stable_g, state.confirm_g = grams, None
            return events                                 # drift / noise
        if state.confirm_g is None or abs(grams - state.confirm_g) >= threshold:
            state.confirm_g = grams                       # seen once: wait for a second reading
            return events
        state.last_stable_g, state.confirm_g = grams, None
        near = self._person_near(shelf, now)
        if self.mems_enabled and not near and delta <= -self.shrink_g:
            events.append(make_event(
                ts=clock.now(), store=self.store, node=self.node, type=EventType.SHRINK_FLAG,
                data=ShrinkFlagData(shelf=shelf, slot=state.info.slot, grams=round(-delta, 1),
                                    evidence="weight drop with no touch and nobody at the shelf")))
        elif near or not self.mems_enabled:
            evidence = "weight only (no MEMS fitted)" if not self.mems_enabled else \
                "weight + shopper (no MEMS touch)"
            events.append(self._pickup(clock, state, delta, "pick" if delta < 0 else "put_back", evidence))
        return events

    def _resolve_slot(self, state: _SlotState, grams: float, episode: _Episode, clock: Clock) -> list[Event]:
        if state.resolved:
            return []
        state.resolved = True
        if state.baseline_g is None:
            return []
        delta = grams - state.baseline_g
        if abs(delta) < self._threshold(state):
            return []
        episode.changed = True
        if episode.kind == "KNOCK":
            if delta < 0:
                self.alerts.append((f"FALLEN_STOCK:{state.info.shelf}/{state.info.slot}",
                                    f"Shelf {state.info.shelf} was knocked and {state.info.slot} lost "
                                    f"{-delta:.0f} g - stock may have fallen", Severity.WARN,
                                    {"grams": round(-delta, 1)}))
            return []
        action = "pick" if delta < 0 else "put_back"
        return [self._pickup(clock, state, delta, action, "mems touch + settled weight")]

    def _maybe_close(self, shelf: str, clock: Clock) -> list[Event]:
        if any(not s.resolved for s in self._shelf_slots(shelf)):
            return []
        return self._close(shelf, clock)

    def _close(self, shelf: str, clock: Clock) -> list[Event]:
        episode = self.episodes.pop(shelf)
        for state in self._shelf_slots(shelf):
            state.resolved, state.baseline_g, state.pending_g = True, None, None
        if episode.kind != "TOUCH" or episode.changed:
            return []
        # Touched, nothing taken or returned: engagement, if a shopper was there.
        self.touches[shelf] = self.touches.get(shelf, 0) + 1
        if not self._person_near(shelf, clock.monotonic_s()):
            return []
        slot = episode.slot or next((s.info.slot for s in self._shelf_slots(shelf)), "*")
        return [make_event(ts=clock.now(), store=self.store, node=self.node, type=EventType.PICKUP,
                           data=PickupData(shelf=shelf, slot=slot, grams=None, action="touch",
                                           evidence="mems touch, no weight change"))]

    def _on_presence(self, data: dict, clock: Clock) -> None:
        if not data["active"] or not self.open_hours:
            return
        now = clock.now().time()
        if any(_within(now, start, end) for start, end in self.open_hours):
            return
        self.alerts.append((f"AFTER_HOURS:{data['zone']}",
                            f"Motion in {data['zone']} at {now:%H:%M}, outside opening hours",
                            Severity.CRITICAL, dict(data)))

    # ------------------------------------------------------------------ #
    def tick(self, clock: Clock) -> list[Event]:
        """Timeouts: settled episodes with no stable reading, stuck episodes,
        and camera-mount events whose fusion window has passed."""
        now = clock.monotonic_s()
        events: list[Event] = []
        for shelf, episode in list(self.episodes.items()):
            settled_long_ago = episode.settled_s is not None and now - episode.settled_s > self.settle_timeout_s
            stuck = now - episode.start_s > self.episode_max_s
            if settled_long_ago or stuck:
                for state in self._shelf_slots(shelf):
                    latest = state.pending_g if state.pending_g is not None else state.last_stable_g
                    if not state.resolved and latest is not None and not stuck:
                        events += self._resolve_slot(state, latest, episode, clock)
                        state.last_stable_g = latest
                events += self._close(shelf, clock)
        for ev in list(self.camera_events):
            if ev.image_tamper:
                self.alerts.append((f"CAMERA_MOVED:{ev.cam}",
                                    f"Camera {ev.cam} was {'knocked' if ev.kind == 'KNOCK' else 'tilted'} "
                                    "and its view changed - recalibrate before trusting its counts",
                                    Severity.CRITICAL, {"cam": ev.cam, "kind": ev.kind,
                                                        "tilt_deg": ev.tilt_deg}))
                self.camera_events.remove(ev)
            elif now - ev.at_s > CAMERA_FUSE_WINDOW_S:
                if ev.kind == "TILT" and (ev.tilt_deg or 0) >= CAMERA_TILT_WARN_DEG:
                    self.alerts.append((f"CAMERA_TILT:{ev.cam}",
                                        f"Camera {ev.cam} tilted {ev.tilt_deg:.1f} deg - check its lines "
                                        "and zones still match the floor", Severity.WARN,
                                        {"cam": ev.cam, "tilt_deg": ev.tilt_deg}))
                else:
                    self.alerts.append((f"CAMERA_BUMP:{ev.cam}",
                                        f"Camera {ev.cam} was bumped; its view looks unchanged",
                                        Severity.WARN, {"cam": ev.cam, "kind": ev.kind}))
                self.camera_events.remove(ev)
        return events

    def drain_alerts(self) -> list[tuple[str, str, Severity, dict]]:
        out, self.alerts = self.alerts, []
        return out

    def drain_check_requests(self) -> list[str]:
        out, self.check_requests = self.check_requests, []
        return out

    def summary(self) -> dict:
        return {
            "slots": {key: {"picks": s.picks, "put_backs": s.put_backs, "units_picked": s.units_picked,
                            "units_returned": s.units_returned, "grams": s.last_stable_g}
                      for key, s in self.slots.items()},
            "touches_without_change": dict(self.touches),
            "gated_weight_readings": self.gated_readings,
            "open_episodes": list(self.episodes),
        }


def _parse_span(span: str) -> tuple[time, time]:
    start, end = span.split("-")
    return time.fromisoformat(start), time.fromisoformat(end)


def _within(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end                     # span across midnight
