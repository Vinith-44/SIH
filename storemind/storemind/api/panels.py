"""Platform panels for the dashboard (M7, Person B): cameras, sensor node,
hardware, privacy, reorder list.

Everything here is built from events on the bus (CAMERA_HEALTH, NODE_HEALTH,
the sensor events, ALERTs from other processes) plus the pipeline's own state,
so the panels show the same thing whether the bridge and ingest run in this
process or as separate services talking over MQTT.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

from ..core.events import Event, EventType

SENSOR_TYPES = (EventType.WEIGHT, EventType.ENVIRONMENT, EventType.PRESENCE, EventType.BEAM_CROSS,
                EventType.SHELF_MOTION, EventType.CAMERA_MOUNT, EventType.SENSOR)


def _key(event: Event) -> str:
    data = event.data
    for name in ("slot", "zone", "door", "cam", "node"):
        if data.get(name) not in (None, ""):
            if event.type is EventType.SENSOR:
                return f"{data.get('sensor')}:{data.get('channel')}"
            return str(data[name])
    return event.type.value


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))], 1)


class PlatformPanels:
    """Subscribes to the bus; `snapshot(pipeline)` returns the panel data."""

    def __init__(self, max_external_alerts: int = 40) -> None:
        self._lock = threading.Lock()
        self.camera_health: dict[str, dict[str, Any]] = {}
        self.node_health: dict[str, dict[str, Any]] = {}
        self.latest: dict[str, dict[str, dict[str, Any]]] = {t.value: {} for t in SENSOR_TYPES}
        self.counts: dict[str, int] = {t.value: 0 for t in SENSOR_TYPES}
        self.beam: dict[str, dict[str, int]] = {}
        self.external_alerts: deque[dict[str, Any]] = deque(maxlen=max_external_alerts)

    def attach(self, bus) -> PlatformPanels:
        bus.subscribe_all(self.on_event)
        return self

    def on_event(self, event: Event) -> None:
        with self._lock:
            if event.type is EventType.CAMERA_HEALTH:
                self.camera_health[event.data["cam"]] = {"ts": event.ts, **event.data}
            elif event.type is EventType.NODE_HEALTH:
                self.node_health[event.data["node"]] = {"ts": event.ts, **event.data}
            elif event.type in SENSOR_TYPES:
                self.counts[event.type.value] += 1
                self.latest[event.type.value][_key(event)] = {"ts": event.ts, **event.data}
                if event.type is EventType.BEAM_CROSS:
                    door = self.beam.setdefault(event.data["door"], {"in": 0, "out": 0})
                    door[event.data["direction"]] += 1
            elif event.type is EventType.ALERT and not event.data.get("ack"):
                self.external_alerts.appendleft({"ts": event.ts, "node": event.node, **event.data})

    # ------------------------------------------------------------------ #
    def snapshot(self, pipeline) -> dict[str, Any]:
        with self._lock:
            return {
                "camera_health": self._cameras(pipeline),
                "sensors": self._sensors(pipeline),
                "hardware": self._hardware(pipeline),
                "privacy": self._privacy(pipeline),
                "reorder": self._reorder(pipeline),
                "external_alerts": self._external_alerts(pipeline),
            }

    def _cameras(self, pipeline) -> list[dict[str, Any]]:
        out = []
        names = [c.config.name for c in pipeline.cameras]
        names += [n for n in self.camera_health if n not in names]
        for name in names:
            h = self.camera_health.get(name)
            tamper = pipeline.health.tamper.get(name, False)
            if h is None:
                state = "tampered" if tamper else ("ok" if pipeline.health.camera_ok.get(name, True) else "stale")
                fps = pipeline.health.fps().get(name)
                entry = {"cam": name, "state": state, "fps": fps, "lag_ms": None, "reconnects": None,
                         "source": "pipeline"}
            else:
                entry = {**h, "source": "ingest"}
                if tamper:
                    entry["state"] = "tampered"
            entry["colour"] = {"ok": "green", "reconnecting": "amber", "dark": "amber"}.get(entry["state"], "red")
            out.append(entry)
        return out

    def _sensors(self, pipeline) -> dict[str, Any]:
        config = pipeline.config.sensors
        bridge = getattr(pipeline, "sensor_bridge", None)
        nodes = []
        for node, h in self.node_health.items():
            nodes.append({**h, "colour": "green" if h.get("link") == "up" else "red"})
        return {
            "enabled": config.enabled,
            "fitted": list(config.node.enabled_sensors) if config.enabled else [],
            "nodes": nodes,
            "latest": {k: list(v.values())[-12:] for k, v in self.latest.items() if v},
            "counts": dict(self.counts),
            "doors": dict(self.beam),
            "bridge": bridge.snapshot() if bridge is not None and hasattr(bridge, "snapshot") else None,
        }

    def _hardware(self, pipeline) -> dict[str, Any]:
        h = pipeline.health.last
        infer = {}
        for camera in pipeline.cameras:
            recent = camera.infer_ms[-200:]
            if recent:
                infer[camera.config.name] = {"p50": _percentile(recent, 0.5), "p95": _percentile(recent, 0.95)}
        return {
            "fps": h.fps if h else {},
            "infer_ms": infer,
            "cpu_percent": h.cpu_percent if h else None,
            "cpu_temp_c": h.cpu_temp_c if h else None,
            "mem_percent": h.mem_percent if h else None,
            "throttled": h.throttled if h else None,
            "power_w": h.power_w if h else None,
            "mj_per_frame": h.mj_per_frame if h else None,
            "power_note": "Pi 5 PMIC, corrected: real W = 1.1451 x PMIC W + 0.5879 (misses USB/HAT loads)",
            "detector": f"{pipeline.config.detector.backend}:{pipeline.config.detector.model}",
        }

    def _privacy(self, pipeline) -> dict[str, Any]:
        return {
            "video_bytes_stored": pipeline.health.video_bytes_stored,
            "frames_on_disk": 0,
            "face_recognition": False,
            "reidentification": False,
            "track_ids": "random per session",
            "audio": False,
            "retention_days": pipeline.config.storage.retention_days,
            "statement": "Frames live in RAM only. Only small anonymous events are stored.",
        }

    def _reorder(self, pipeline) -> dict[str, Any]:
        queue = getattr(pipeline, "reorder", None)
        if queue is None:
            return {"open": [], "whatsapp": ""}
        return {"open": queue.open_drafts(), "whatsapp": queue.whatsapp_text(pipeline.config.store)}

    def _external_alerts(self, pipeline) -> list[dict[str, Any]]:
        """ALERTs from other producers (e.g. the bridge's SENSOR_LINK) that the
        pipeline's own alert manager never saw."""
        own = {a.alert_id for a in pipeline.alerts.log}
        try:
            acked = pipeline.store.acked()
        except Exception:
            acked = set()
        return [{**a, "ack": a.get("alert_id") in acked} for a in self.external_alerts
                if a.get("alert_id") not in own]
