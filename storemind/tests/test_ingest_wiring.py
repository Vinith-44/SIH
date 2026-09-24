"""The seam between the ingest watchdog and the PR-0 CAMERA_HEALTH contract.

Every assertion here is really about one question: if Person A changes
docs/INTERFACES.md, does something fail loudly?  The payload is built through
the real contract model, so a renamed field or a dropped state breaks these
tests rather than shipping a silently wrong health panel.
"""

from __future__ import annotations

import pytest

from storemind.core.bus import EventBus
from storemind.core.events import EventType, PresenceData, make_event
from storemind.ingest.urls import CameraEndpoint
from storemind.ingest.watchdog import OFFLINE, ONLINE, STALE, STARTING, StreamWatchdog
from storemind.ingest.wiring import (
    WATCHDOG_TO_CONTRACT,
    IngestManager,
    health_to_contract,
)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _health(state: str, **over) -> dict:
    base = {"camera": "entrance", "state": state, "online": state == ONLINE,
            "fps": 9.8, "last_frame_age_s": 0.12, "reconnects": 0}
    base.update(over)
    return base


@pytest.mark.parametrize(("watchdog_state", "contract_state"), [
    (ONLINE, "ok"),
    (STALE, "stale"),
    (OFFLINE, "reconnecting"),
    (STARTING, "reconnecting"),
])
def test_every_watchdog_state_maps_to_a_contract_state(watchdog_state, contract_state):
    assert health_to_contract(_health(watchdog_state)).state == contract_state


def test_mapping_covers_every_state_the_watchdog_can_produce():
    # If the watchdog gains a state, this fails here rather than in the field.
    assert set(WATCHDOG_TO_CONTRACT) == {ONLINE, STALE, OFFLINE, STARTING}


def test_unknown_state_raises_instead_of_guessing():
    with pytest.raises(ValueError, match="no CAMERA_HEALTH equivalent"):
        health_to_contract(_health("melted"))


def test_frame_age_becomes_lag_in_milliseconds():
    assert health_to_contract(_health(ONLINE, last_frame_age_s=0.12)).lag_ms == 120.0


def test_lag_is_none_when_no_frame_has_ever_arrived():
    payload = health_to_contract(_health(STARTING, last_frame_age_s=None))
    assert payload.lag_ms is None


def test_payload_round_trips_through_the_contract_model():
    payload = health_to_contract(_health(ONLINE, reconnects=3))
    dumped = payload.model_dump(mode="json")
    assert dumped == {"cam": "entrance", "state": "ok", "fps": 9.8,
                      "lag_ms": 120.0, "reconnects": 3}


def test_a_health_dict_becomes_one_camera_health_event():
    bus = EventBus()
    seen = []
    bus.subscribe_types(EventType.CAMERA_HEALTH, seen.append)
    manager = IngestManager([CameraEndpoint(name="entrance", brand="tapo", ip="192.0.2.10")],
                            bus, store="bvrit-demo", node="pi5-01")

    manager.publish_health(_health(ONLINE))

    assert len(seen) == 1
    event = seen[0]
    assert event.type == EventType.CAMERA_HEALTH
    assert event.cam == "entrance"
    assert event.store == "bvrit-demo" and event.node == "pi5-01"
    assert event.data["state"] == "ok"


def test_an_unmappable_state_publishes_nothing_rather_than_crashing_the_reader():
    # publish_health runs on the reader thread; an exception there would kill
    # the camera, which is a worse outcome than a missing health event.
    bus = EventBus()
    seen = []
    bus.subscribe_types(EventType.CAMERA_HEALTH, seen.append)
    manager = IngestManager([], bus)
    manager.publish_health(_health("melted"))
    assert seen == []


def test_presence_wakes_only_its_own_zone():
    bus = EventBus()
    manager = IngestManager([], bus, zone_cameras={"aisle1": ["cam1"], "aisle2": ["cam2"]})
    manager.on_presence(make_event(
        ts="2026-09-24T18:04:11+05:30", store="s", node="n", type=EventType.PRESENCE,
        data=PresenceData(node="pir-01", zone="aisle1", active=True),
    ))
    assert manager.pir.is_active("cam1")
    assert not manager.pir.is_active("cam2")


def test_presence_inactive_does_not_end_the_hold_early():
    # A person standing still stops triggering the PIR; dropping to 1 FPS then
    # would blind the camera exactly when someone is in the aisle.
    bus = EventBus()
    manager = IngestManager([], bus, zone_cameras={"aisle1": ["cam1"]})
    for active in (True, False):
        manager.on_presence(make_event(
            ts="2026-09-24T18:04:11+05:30", store="s", node="n",
            type=EventType.PRESENCE,
            data=PresenceData(node="pir-01", zone="aisle1", active=active),
        ))
    assert manager.pir.is_active("cam1")


def test_a_camera_with_no_pir_zone_is_always_active():
    # research/26: a missing sensor must never be able to blind a camera.
    manager = IngestManager([], EventBus(), zone_cameras={"aisle1": ["cam1"]})
    assert manager.pir.is_active("unwatched-camera")


def test_watchdog_health_feeds_the_mapping_unchanged():
    # Guards the field names the watchdog and the mapping agree on.
    clock = Clock()
    watchdog = StreamWatchdog("entrance", stale_after_s=5, clock=clock)
    watchdog.on_frame()
    clock.t += 0.1
    watchdog.on_frame()
    payload = health_to_contract(watchdog.health())
    assert payload.cam == "entrance"
    assert payload.state == "ok"
    assert payload.fps > 0
