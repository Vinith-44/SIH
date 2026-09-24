"""Wire the M2 ingest to the PR-0 contract (research/26 section 3).

This is the seam between Person B's ingest and the shared bus: the watchdog
speaks in its own vocabulary, `docs/INTERFACES.md` defines another, and this
module is the single place the two are translated.  Keeping it in one file
means a contract change breaks one import rather than five.

    manager = IngestManager(cameras, bus, store="bvrit-demo", node="pi5-01")
    manager.start()      # go2rtc + one reader per camera
    ...
    manager.stop()

**State mapping, and what it costs.**  The watchdog has four states; the
contract's `CAMERA_HEALTH.state` has five, and they are not the same five:

    watchdog            CAMERA_HEALTH     why
    --------            -------------     ---
    online          ->  ok
    stale           ->  stale             same meaning
    offline         ->  reconnecting      we are retrying, which is what the
                                          panel needs to show; the contract has
                                          no "offline"
    starting        ->  reconnecting      connecting for the first time

`tampered` and `dark` are in the contract but are *not* produced here - the
watchdog only knows whether frames arrive, not what is in them.  `tampered`
comes from `health.monitor.TamperDetector` and `dark` from `tools/probe.py`, so
both reach the panel by another route.  Flagged for A in the M2 PR rather than
invented: if the panel needs those states from ingest too, the watchdog needs a
frame-content check it currently does not do.

`lag_ms` is the age of the newest frame, which is the delay that matters for a
queue timer: how far behind reality the picture is.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from ..core.bus import EventBus
from ..core.clock import Clock, WallClock
from ..core.events import CameraHealthData, EventType, PresenceData, make_event
from .go2rtc import Credentials, Go2rtc
from .pir_wake import PirWake
from .rtsp_reader import RtspReader
from .urls import CameraEndpoint
from .watchdog import OFFLINE, ONLINE, STALE, STARTING

log = logging.getLogger(__name__)

# The translation above, as code.
WATCHDOG_TO_CONTRACT: dict[str, str] = {
    ONLINE: "ok",
    STALE: "stale",
    OFFLINE: "reconnecting",
    STARTING: "reconnecting",
}


def health_to_contract(health: dict) -> CameraHealthData:
    """Turn a watchdog health dict into the CAMERA_HEALTH payload.

    Raises on an unknown state rather than guessing: a new watchdog state that
    silently became "ok" on the health panel would be worse than a crash in a
    test.
    """
    state = health["state"]
    try:
        mapped = WATCHDOG_TO_CONTRACT[state]
    except KeyError:
        raise ValueError(
            f"watchdog state {state!r} has no CAMERA_HEALTH equivalent; add it to "
            "WATCHDOG_TO_CONTRACT (and agree the mapping with Person A first)"
        ) from None
    age_s = health.get("last_frame_age_s")
    return CameraHealthData(
        cam=health["camera"],
        state=mapped,
        fps=float(health.get("fps") or 0.0),
        lag_ms=None if age_s is None else round(float(age_s) * 1000.0, 1),
        reconnects=int(health.get("reconnects") or 0),
    )


class IngestManager:
    """go2rtc plus one reader per camera, publishing to the bus.

    `zone_cameras` maps a PIR zone to the cameras it wakes.  Leave it empty and
    every camera runs at full rate: research/24 section 6 wants the saving, but
    a missing sensor must never be able to blind a camera.
    """

    def __init__(self, cameras: list[CameraEndpoint], bus: EventBus, *,
                 store: str = "demo-store", node: str = "pi5-01",
                 credentials: Credentials | None = None,
                 zone_cameras: dict[str, Iterable[str]] | None = None,
                 go2rtc: Go2rtc | None = None, clock: Clock | None = None,
                 idle_fps: float = 1.0, wake_hold_s: float = 30.0,
                 stale_after_s: float = 5.0, health_every_s: float = 10.0,
                 on_frame=None) -> None:
        self.cameras = cameras
        self.bus = bus
        self.store = store
        self.node = node
        self.clock = clock or WallClock()
        self.on_frame = on_frame
        self.stale_after_s = stale_after_s
        self.health_every_s = health_every_s
        self.pir = PirWake(zone_cameras or {}, idle_fps=idle_fps, hold_s=wake_hold_s)
        self.go2rtc = go2rtc or Go2rtc(cameras, credentials)
        self.readers: list[RtspReader] = []

    # -- bus ---------------------------------------------------------------- #

    def publish_health(self, health: dict) -> None:
        """Called by every reader on a state change and on the health tick."""
        try:
            payload = health_to_contract(health)
        except ValueError:
            log.exception("could not map watchdog health for %s", health.get("camera"))
            return
        self.bus.publish(make_event(
            ts=self.clock.now(), store=self.store, node=self.node,
            type=EventType.CAMERA_HEALTH, data=payload, cam=payload.cam,
        ))

    def on_presence(self, event) -> None:
        """PRESENCE from the sensor bridge wakes that zone's cameras.

        `active: False` is the end of motion, not a reason to sleep early: the
        hold is what stops a camera dropping to 1 FPS while somebody is still
        standing in the aisle.
        """
        data = PresenceData(**event.data) if isinstance(event.data, dict) else event.data
        if not data.active:
            return
        woken = self.pir.on_presence(data.zone)
        if woken:
            log.debug("PRESENCE in %s woke %s", data.zone, ", ".join(woken))

    # -- lifecycle ---------------------------------------------------------- #

    def start(self) -> None:
        self.bus.subscribe_types(EventType.PRESENCE, self.on_presence)
        self.go2rtc.start()
        for cam in self.cameras:
            reader = RtspReader(
                cam.name, self.go2rtc.stream_url(cam.name),
                on_frame=self.on_frame, on_health=self.publish_health,
                pir=self.pir, stale_after_s=self.stale_after_s,
                health_every_s=self.health_every_s,
            )
            reader.start()
            self.readers.append(reader)

    def stop(self) -> None:
        for reader in self.readers:
            reader.stop()
        self.readers.clear()
        self.go2rtc.stop()
