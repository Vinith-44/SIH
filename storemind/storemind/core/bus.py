"""Event bus.

`research/04_ARCHITECTURE.md` specifies Mosquitto MQTT between services.  For the
hackathon everything runs in one process, so the default bus is in-process with
exactly the MQTT interface (`publish` / `subscribe` by topic pattern).  `MqttBus`
is a drop-in that speaks to a real broker when `paho-mqtt` is installed.

Topic convention (04 section 4, research/26 section 3.2, docs/INTERFACES.md):
    storemind/{store}/{node}/{cam|_}/{TYPE}      events
    storemind/{store}/{node}/status               "online" / "offline" (Last Will)
    storemind/{store}/{node}/cmd/{COMMAND}        commands to a sensor node
"""

from __future__ import annotations

import fnmatch
import json
import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from .events import Event, EventType

log = logging.getLogger(__name__)

Handler = Callable[[Event], None]


# QoS 1 = must not be lost; everything else is a state that the next message
# replaces (QoS 0).  Retained = a dashboard that connects late still gets the
# current value.  research/26 section 3.2.
QOS1_TYPES = frozenset({
    EventType.ENTRY, EventType.EXIT, EventType.SERVICE_DONE, EventType.SLOT_STATE,
    EventType.ALERT, EventType.BEAM_CROSS, EventType.SHELF_MOTION,
})
RETAINED_TYPES = frozenset({
    EventType.QUEUE_STATE, EventType.SLOT_STATE, EventType.HEALTH,
    EventType.NODE_HEALTH, EventType.CAMERA_HEALTH, EventType.ENVIRONMENT,
})
COMMANDS = ("LED", "BUZZER", "SERVO", "TARE", "CAL", "CONFIG")


def topic_for(event: Event) -> str:
    return f"storemind/{event.store}/{event.node}/{event.cam or '_'}/{event.type.value}"


def status_topic(store: str, node: str) -> str:
    return f"storemind/{store}/{node}/status"


def command_topic(store: str, node: str, command: str) -> str:
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}; expected one of {COMMANDS}")
    return f"storemind/{store}/{node}/cmd/{command}"


def qos_for(event_type: EventType) -> int:
    return 1 if event_type in QOS1_TYPES else 0


def retain_for(event_type: EventType) -> bool:
    return event_type in RETAINED_TYPES


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
    """Same interface, real broker.  Only imported/used when `mqtt.enabled` is set.

    *   paho-mqtt 2.x: the client is built with `CallbackAPIVersion.VERSION2`
        (the 1.x form `mqtt.Client()` raises in 2.x).
    *   Liveness: a retained Last Will `offline` on `status_topic(store, node)`,
        and a retained `online` on every (re)connect, so the dashboard sees a
        dead process within the keep-alive time.
    *   `listen=True` also subscribes to `storemind/{store}/#` and delivers
        events published by *other* processes (e.g. the serial bridge) to the
        local subscribers.  Our own events are delivered locally once, at
        publish time, and skipped when the broker echoes them back.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 1883,
                 username: str | None = None, password: str | None = None, *,
                 store: str = "demo-store", node: str = "pi5-01",
                 keepalive: int = 30, listen: bool = False,
                 client: Any | None = None) -> None:
        super().__init__()
        self.store = store
        self.node = node
        self.listen = listen
        self._own_ids: deque[str] = deque(maxlen=4096)
        self._own_set: set[str] = set()
        self.status = status_topic(store, node)

        if client is None:  # tests inject a fake client instead of a broker
            import paho.mqtt.client as mqtt  # imported lazily: optional dependency

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                 client_id=f"storemind-{store}-{node}")
        self._client = client
        if username:
            self._client.username_pw_set(username, password or "")
        self._client.will_set(self.status, "offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(host, port, keepalive=keepalive)
        self._client.loop_start()

    # -- paho callbacks (VERSION2 signatures) ------------------------------ #
    def _on_connect(self, client: Any, _userdata: Any, _flags: Any,
                    reason_code: Any, _properties: Any = None) -> None:
        if getattr(reason_code, "is_failure", False):
            log.error("MQTT connect failed: %s", reason_code)
            return
        client.publish(self.status, "online", qos=1, retain=True)
        if self.listen:
            client.subscribe(f"storemind/{self.store}/#", qos=1)

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        topic = message.topic
        if topic.endswith("/status") or "/cmd/" in topic:
            return
        try:
            event = Event(**json.loads(message.payload))
        except Exception:
            log.warning("dropping malformed event on %s", topic)
            return
        if event.id in self._own_set:
            return
        super().publish(event)

    # -- producer side ----------------------------------------------------- #
    def _remember(self, event_id: str) -> None:
        if len(self._own_ids) == self._own_ids.maxlen:
            self._own_set.discard(self._own_ids[0])
        self._own_ids.append(event_id)
        self._own_set.add(event_id)

    def publish(self, event: Event) -> None:
        self._remember(event.id)
        self._client.publish(topic_for(event), event.to_json(),
                             qos=qos_for(event.type), retain=retain_for(event.type))
        super().publish(event)

    def close(self) -> None:
        # A clean shutdown is not a crash: say so explicitly, because the broker
        # does not send the Last Will after a normal DISCONNECT.
        self._client.publish(self.status, "offline", qos=1, retain=True)
        self._client.loop_stop()
        self._client.disconnect()
