"""Alert -> LED / buzzer patterns on the STM32 node (M7, Person B).

Analogy: the tower light at the billing counter is the store's traffic light.
Staff do not read dashboards while serving customers, so what needs a human
*now* must be visible across the room (LED) and, if it is urgent, audible
(buzzer, which the node silences on its own after 5 s).

One policy, two places it runs:
*   in-process: `TowerLightSink` in `alerts/manager.py` calls `TowerPolicy`;
*   on the Pi, where the bridge is its own service: the bridge hears ALERT
    events over MQTT and calls the same `TowerPolicy` (`sensors/bridge.py`).

The LED shows the most severe alert that is still open; acknowledging the last
one turns it off.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

# message_key prefix -> (LED pattern, buzzer pattern or None).  Keys come from
# pipeline.py, fusion/interaction.py (Vinith) and sensors/bridge.py.
PATTERNS: dict[str, tuple[str, str | None]] = {
    # stock and shelves
    "SLOT_EMPTY": ("ALERT", "FAST"),
    "SLOT_LOW": ("SLOW", None),
    "WRONG_ITEM": ("SLOW", None),
    "HIDDEN_DEPLETION": ("SLOW", None),
    "SHRINK": ("SLOW", None),
    "FALLEN_STOCK": ("FAST", "FAST"),         # a pack may be on the floor: look now
    "SHELF_TILT": ("ALERT", "ALERT"),         # safety: a shelf leaning
    # queues
    "QUEUE_FORECAST": ("SLOW", None),
    "QUEUE_CONGESTED": ("FAST", None),
    "QUEUE_OVERFLOW": ("FAST", "FAST"),       # the queue has reached the end of its lane
    # cameras
    "CAMERA_TAMPER": ("ALERT", "ALERT"),
    "CAMERA_MOVED": ("ALERT", "ALERT"),       # moved + view changed: recalibrate
    "CAMERA_BUMP": ("SLOW", None),            # bumped, view unchanged
    "CAMERA_TILT": ("FAST", None),            # tilted >= 2 deg: check lines and zones
    "CAMERA_CHECK": ("SLOW", None),
    # security and the node itself
    "AFTER_HOURS": ("ALERT", "ALERT"),        # motion while closed
    "SENSOR_LINK": ("SLOW", None),            # the sensor node went silent
}

SEVERITY_DEFAULT: dict[str, tuple[str, str | None]] = {
    "CRITICAL": ("ALERT", "ALERT"),
    "WARN": ("SLOW", None),
    "INFO": ("ON", None),
}
RANK = {"OFF": 0, "ON": 1, "SLOW": 2, "FAST": 3, "ALERT": 4}


class Actuator(Protocol):
    def set_led(self, pattern: str) -> bool: ...
    def set_buzzer(self, pattern: str) -> bool: ...


def patterns_for(message_key: str, severity: str) -> tuple[str, str | None]:
    prefix = message_key.split(":", 1)[0]
    if prefix in PATTERNS:
        return PATTERNS[prefix]
    return SEVERITY_DEFAULT.get(severity, ("ON", None))


@dataclass
class _Open:
    led: str
    buzzer: str | None


class TowerPolicy:
    """Keeps the set of open alerts and drives the node's LED to the worst one."""

    def __init__(self, actuator: Actuator, *, buzzer_enabled: bool = True) -> None:
        self.actuator = actuator
        self.buzzer_enabled = buzzer_enabled
        self._open: dict[str, _Open] = {}
        self._led = "OFF"
        self._lock = threading.Lock()

    @property
    def led(self) -> str:
        return self._led

    def raised(self, alert_id: str, message_key: str, severity: str) -> None:
        led, buzzer = patterns_for(message_key, severity)
        with self._lock:
            self._open[alert_id] = _Open(led, buzzer)
            self._apply_led()
        if buzzer and self.buzzer_enabled:
            self.actuator.set_buzzer(buzzer)

    def acknowledged(self, alert_id: str) -> None:
        with self._lock:
            if self._open.pop(alert_id, None) is not None:
                self._apply_led()

    def clear(self) -> None:
        with self._lock:
            self._open.clear()
            self._apply_led()

    def _apply_led(self) -> None:
        worst = max((o.led for o in self._open.values()), key=RANK.__getitem__, default="OFF")
        if worst != self._led:
            self._led = worst
            self.actuator.set_led(worst)

    # ALERT events from the bus (the bridge service on the Pi).
    def on_alert_event(self, event) -> None:
        data = event.data
        if data.get("ack"):
            self.acknowledged(data["alert_id"])
        else:
            self.raised(data["alert_id"], data["message_key"], data["severity"])
