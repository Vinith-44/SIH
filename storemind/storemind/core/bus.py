"""Event bus.

`research/04_ARCHITECTURE.md` specifies Mosquitto MQTT between services.  For the
hackathon everything runs in one process, so the default bus is in-process with
exactly the MQTT interface (`publish` / `subscribe` by topic pattern).  `MqttBus`
is a drop-in that speaks to a real broker when `paho-mqtt` is installed.

Topic convention (04 section 4):
    storemind/{store}/{node}/{cam|sensor}/{TYPE}
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable, Iterable

from .events import Event, EventType

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


def topic_for(event: Event) -> str:
    return f"storemind/{event.store}/{event.node}/{event.cam or '_'}/{event.type.value}"


class EventBus:
    """Synchronous in-process pub/sub.

    Handlers run on the publishing thread.  That is deliberate: the vision loop is
    the only high-rate producer and handlers are cheap (append to a list, write a
    row).  The SQLite writer does its own queueing internally, so a slow disk
    cannot stall the pipeline.
    """

    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler]] = []
        self._lock = threading.RLock()
        self._published = 0

    # -- producer side ----------------------------------------------------- #
    def publish(self, event: Event) -> None:
        topic = topic_for(event)
        with self._lock:
            self._published += 1
            handlers = [h for pattern, h in self._subs if fnmatch.fnmatch(topic, pattern)]
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # a broken subscriber must not kill the pipeline
                log.exception("subscriber %r failed on %s", handler, topic)

    def publish_all(self, events: Iterable[Event]) -> None:
        for event in events:
            self.publish(event)

    # -- consumer side ----------------------------------------------------- #
    def subscribe(self, pattern: str, handler: Handler) -> None:
        """`pattern` is an fnmatch glob over the topic, e.g. `storemind/*/*/*/ENTRY`."""
        with self._lock:
            self._subs.append((pattern, handler))

    def subscribe_types(self, types: Iterable[EventType] | EventType, handler: Handler) -> None:
        if isinstance(types, EventType):
            types = [types]
        for event_type in types:
            self.subscribe(f"storemind/*/*/*/{event_type.value}", handler)

    def subscribe_all(self, handler: Handler) -> None:
        self.subscribe("storemind/*", handler)

    @property
    def published(self) -> int:
        return self._published


class MqttBus(EventBus):
    """Same interface, real broker.  Only imported/used when `mqtt.enabled` is set."""

    def __init__(self, host: str = "127.0.0.1", port: int = 1883,
                 username: str | None = None, password: str | None = None) -> None:
        super().__init__()
        import paho.mqtt.client as mqtt  # imported lazily: optional dependency

        self._client = mqtt.Client()
        if username:
            self._client.username_pw_set(username, password or "")
        self._client.connect(host, port, keepalive=30)
        self._client.loop_start()

    def publish(self, event: Event) -> None:
        topic = topic_for(event)
        # QoS 1 for things that must not be lost (07 section 2), QoS 0 for states.
        qos = 1 if event.type in {EventType.ALERT, EventType.ENTRY, EventType.EXIT,
                                  EventType.SERVICE_DONE, EventType.SLOT_STATE} else 0
        retain = event.type in {EventType.QUEUE_STATE, EventType.SLOT_STATE, EventType.HEALTH}
        self._client.publish(topic, event.to_json(), qos=qos, retain=retain)
        super().publish(event)

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
