"""Event schema, bus and SQLite storage."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

from storemind.core.bus import EventBus, topic_for
from storemind.core.clock import IST, ManualClock
from storemind.core.events import (
    EntryExitData,
    Event,
    EventType,
    QueueStateData,
    Severity,
    SlotState,
    make_event,
)
from storemind.store.db import EventStore


def entry_event(track: int = 1, cam: str = "entrance") -> Event:
    return make_event(ts=datetime(2026, 9, 23, 18, 4, 11, tzinfo=IST), store="bvrit-demo",
                      node="pi5-01", cam=cam, type=EventType.ENTRY,
                      data=EntryExitData(line="door", track=track, direction="in"))


def test_event_round_trips_through_json():
    event = entry_event()
    restored = Event(**json.loads(event.to_json()))
    assert restored.type is EventType.ENTRY
    assert restored.data == event.data
    assert restored.id == event.id


def test_event_has_a_unique_id_for_idempotent_sync():
    assert entry_event().id != entry_event().id


def test_payload_is_validated_at_construction():
    with pytest.raises(ValidationError):
        make_event(ts=datetime.now(IST), store="s", node="n", type=EventType.ENTRY,
                   data={"line": "door"})          # missing track and direction


def test_unknown_payload_field_is_rejected():
    with pytest.raises(ValidationError):
        make_event(ts=datetime.now(IST), store="s", node="n", type=EventType.QUEUE_STATE,
                   data={"counter": "c1", "queue_len": 2, "queue_len_smooth": 2.0,
                         "typo_field": 1})


def test_enum_payloads_are_normalised_to_strings():
    event = make_event(ts=datetime.now(IST), store="s", node="n", type=EventType.SLOT_STATE,
                       data={"shelf": "a", "slot": "A1", "state": SlotState.EMPTY})
    assert event.data["state"] == "EMPTY"
    assert isinstance(event.data["state"], str)


def test_bad_timestamp_is_rejected():
    with pytest.raises(ValidationError):
        Event(ts="not-a-time", store="s", node="n", type=EventType.HEALTH, data={})


def test_topic_follows_the_documented_convention():
    assert topic_for(entry_event(cam="counter-2")) == \
        "storemind/bvrit-demo/pi5-01/counter-2/ENTRY"


def test_bus_delivers_by_type():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe_types(EventType.ENTRY, seen.append)
    bus.publish(entry_event())
    bus.publish(make_event(ts=datetime.now(IST), store="bvrit-demo", node="pi5-01",
                           type=EventType.HEALTH, data={}))
    assert len(seen) == 1


def test_bus_glob_subscription():
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe("storemind/*/*/counter-2/*", seen.append)
    bus.publish(entry_event(cam="counter-2"))
    bus.publish(entry_event(cam="entrance"))
    assert len(seen) == 1


def test_one_broken_subscriber_does_not_stop_the_others():
    bus = EventBus()
    seen: list[Event] = []

    def explode(_event: Event) -> None:
        raise RuntimeError("subscriber bug")

    bus.subscribe_all(explode)
    bus.subscribe_all(seen.append)
    bus.publish(entry_event())
    assert len(seen) == 1


def test_store_persists_events_and_aggregates(tmp_path):
    store = EventStore(tmp_path / "t.db", store="bvrit-demo")
    try:
        for i in range(5):
            store.handle(entry_event(track=i))
        store.handle(make_event(
            ts=datetime(2026, 9, 23, 18, 4, 11, tzinfo=IST), store="bvrit-demo", node="pi5-01",
            cam="counter-1", type=EventType.QUEUE_STATE,
            data=QueueStateData(counter="counter-1", queue_len=4, queue_len_smooth=3.0)))
        store.flush()
        counts = store.counts_by_type()
        assert counts["ENTRY"] == 5
        entries = store.series("entries")
        assert entries and entries[0][1] == 5.0
        assert store.series("queue_len", "counter-1")[0][1] == 3.0
    finally:
        store.close()


def test_store_appends_across_sessions(tmp_path):
    """Audit S7: the legacy pipeline wiped its log on every start."""
    path = tmp_path / "t.db"
    first = EventStore(path)
    first.handle(entry_event())
    first.flush()
    first.close()

    second = EventStore(path)
    second.handle(entry_event())
    second.flush()
    assert second.counts_by_type()["ENTRY"] == 2
    second.close()


def test_store_is_idempotent_on_duplicate_uuids(tmp_path):
    store = EventStore(tmp_path / "t.db")
    try:
        event = entry_event()
        store.handle(event)
        store.handle(event)
        store.flush()
        assert store.counts_by_type()["ENTRY"] == 1
    finally:
        store.close()


def test_store_config_round_trip(tmp_path):
    store = EventStore(tmp_path / "t.db")
    try:
        store.set_config("zones", {"promo": [[0.1, 0.1]]})
        assert store.get_config("zones")["promo"] == [[0.1, 0.1]]
        assert store.get_config("missing", "fallback") == "fallback"
    finally:
        store.close()


def test_retention_purge_keeps_recent_events(tmp_path):
    store = EventStore(tmp_path / "t.db", retention_days=30)
    try:
        old = make_event(ts=datetime(2020, 1, 1, tzinfo=IST), store="s", node="n",
                         cam="entrance", type=EventType.ENTRY,
                         data=EntryExitData(line="door", track=1, direction="in"))
        store.handle(old)
        store.handle(entry_event())
        store.flush()
        removed = store.purge_old_events(now=datetime(2026, 9, 23))
        assert removed == 1
        assert store.counts_by_type()["ENTRY"] == 1
    finally:
        store.close()
