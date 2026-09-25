"""REST + WebSocket API and the offline dashboard.

Everything here has to work with the internet unplugged (07 section 4), so:

*   no CDN, no external fonts, no analytics - the dashboard is one HTML file
    plus one CSS and one JS file served from disk, and the charts are inline SVG
    drawn by hand rather than a charting library;
*   the WebSocket is a convenience, not a requirement: the page falls back to
    polling `/api/state` if the socket drops, so a flaky store Wi-Fi degrades
    instead of breaking;
*   nothing is fetched at page load except from this server.

The API is read-mostly. The only writes are the two things staff actually do:
acknowledge an alert, and tell the system a shelf has been restocked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..core.events import Event, EventType, make_event

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"


class Hub:
    """Fan-out of pipeline events to connected browsers.

    The pipeline publishes from its own thread; FastAPI lives in an asyncio loop.
    This is the one place those two worlds meet, so it is also the only place
    that needs to be thread-safe.
    """

    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self.recent: list[dict] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, socket: WebSocket) -> None:
        await socket.accept()
        with self._lock:
            self._sockets.add(socket)

    def unregister(self, socket: WebSocket) -> None:
        with self._lock:
            self._sockets.discard(socket)

    def publish(self, event: Event) -> None:
        """Called from the pipeline thread."""
        payload = {"ts": event.ts, "cam": event.cam, "type": event.type.value,
                   "data": event.data}
        with self._lock:
            self.recent.append(payload)
            del self.recent[:-200]
            loop = self._loop
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)
        except RuntimeError:
            pass

    async def _broadcast(self, payload: dict) -> None:
        with self._lock:
            sockets = list(self._sockets)
        text = json.dumps(payload)
        for socket in sockets:
            try:
                await socket.send_text(text)
            except Exception:
                self.unregister(socket)


def snapshot(pipeline, panels=None) -> dict[str, Any]:
    """Everything the dashboard needs in one object."""
    config = pipeline.config
    entries = exits = 0
    zone_dwell: dict[str, float] = {}
    counters: dict[str, Any] = {}
    shelves: dict[str, Any] = {}
    cameras: list[dict] = []

    for camera in pipeline.cameras:
        entry = {
            "name": camera.config.name,
            "role": camera.config.role,
            "frames": camera.processed,
            "tracks_now": len(camera.last_tracks),
            "tamper": pipeline.health.tamper.get(camera.config.name, False),
            "ok": pipeline.health.camera_ok.get(camera.config.name, True),
        }
        if camera.infer_ms:
            entry["infer_ms"] = round(sum(camera.infer_ms[-50:]) / len(camera.infer_ms[-50:]), 1)
        cameras.append(entry)

        if camera.footfall is not None:
            entries += camera.footfall.entries
            exits += camera.footfall.exits
        if camera.zones is not None:
            for visit in camera.zones.completed:
                zone_dwell[visit.zone] = zone_dwell.get(visit.zone, 0.0) + visit.dwell_s
        if camera.queue is not None:
            for name, state in camera.queue.counters.items():
                counters[name] = {
                    "queue_len": state.queue_len,
                    "queue_len_smooth": round(state.queue_len_smooth, 1),
                    "median_wait_s": state.median_wait_s,
                    "median_service_s": state.median_service_s,
                    "services": len(state.completed_services),
                    "open": state.spec.open,
                    "congested": state.queue_len_smooth >= state.spec.congestion_len,
                }
        if camera.shelf is not None:
            for shelf_name, entry_data in camera.shelf.summary().items():
                shelves[shelf_name] = {"camera": camera.config.name, **entry_data}

    health = pipeline.health.last
    forecast = pipeline.forecaster.last
    acked = set()
    try:
        acked = pipeline.store.acked()
    except Exception:
        pass

    alerts = []
    for alert in reversed(pipeline.alerts.log[-40:]):
        alerts.append({
            "alert_id": alert.alert_id,
            "severity": alert.severity.value,
            "message": alert.message,
            "message_key": alert.message_key,
            "ack": alert.ack or alert.alert_id in acked,
            "context": alert.context,
        })

    return {
        "store": config.store,
        "node": config.node,
        "clock_s": round(pipeline.clock.monotonic_s(), 1),
        "now": pipeline.clock.now().isoformat(),
        "footfall": {"entries": entries, "exits": exits, "occupancy": max(0, entries - exits)},
        "counters": counters,
        "forecast": forecast.model_dump(mode="json") if forecast else None,
        "shelves": shelves,
        "zone_dwell_s": {k: round(v, 1) for k, v in zone_dwell.items()},
        "alerts": alerts,
        "cameras": cameras,
        "health": health.model_dump(mode="json") if health else None,
        "lost_sales": pipeline.fusion.summary(),
        # The privacy counter (N7). It is zero because nothing in this codebase
        # opens a VideoWriter, not because it is reset.
        "video_bytes_stored": pipeline.health.video_bytes_stored,
        "events_emitted": pipeline.events_emitted,
        # M7 platform panels: camera health, sensor node, hardware, privacy, reorder list.
        "platform": panels.snapshot(pipeline) if panels is not None else None,
    }


def create_app(pipeline, hub: Hub | None = None) -> FastAPI:
    hub = hub or Hub()
    app = FastAPI(title="StoreMind", docs_url=None, redoc_url=None)
    app.state.pipeline = pipeline
    app.state.hub = hub

    pipeline.bus.subscribe_all(hub.publish)
    from .panels import PlatformPanels

    panels = PlatformPanels().attach(pipeline.bus)
    app.state.panels = panels

    @app.on_event("startup")
    async def _bind() -> None:
        hub.bind_loop(asyncio.get_running_loop())

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    def state() -> JSONResponse:
        return JSONResponse(snapshot(pipeline, panels))

    @app.get("/api/events")
    def events(limit: int = 50, type: str | None = None) -> JSONResponse:
        types = [t.strip() for t in type.split(",")] if type else None
        try:
            rows = pipeline.store.recent_events(limit=limit, types=types)
        except Exception:
            rows = hub.recent[-limit:]
        return JSONResponse(rows)

    @app.get("/api/series/{metric}")
    def series(metric: str, key: str | None = None, by: str = "minute") -> JSONResponse:
        try:
            if by == "hour":
                data = pipeline.store.hourly(metric)
            elif by == "day":
                data = pipeline.store.daily(metric)
            else:
                data = pipeline.store.series(metric, key)
        except Exception as error:
            raise HTTPException(500, str(error)) from error
        return JSONResponse([{"t": t, "v": v} for t, v in data])

    @app.post("/api/alerts/{alert_id}/ack")
    def acknowledge(alert_id: str) -> JSONResponse:
        ok = pipeline.alerts.acknowledge(alert_id)
        try:
            pipeline.store.ack_alert(alert_id)
        except Exception:
            pass
        # Tell other processes too (the sensor bridge service turns the tower LED
        # off): the same alert, re-published with ack=true.
        for entry in pipeline.alerts.log:
            if entry.alert_id == alert_id:
                pipeline.bus.publish(make_event(ts=pipeline.clock.now(), store=pipeline.config.store,
                                                node=pipeline.config.node, type=EventType.ALERT,
                                                data=entry.model_copy(update={"ack": True})))
                break
        return JSONResponse({"ok": ok, "alert_id": alert_id})

    @app.post("/api/restock")
    def restock(camera: str, shelf: str | None = None) -> JSONResponse:
        """The one-button supervision the shelf engine needs (novelty N2)."""
        count = pipeline.restock(camera, shelf)
        return JSONResponse({"ok": count > 0, "slots_referenced": count})

    @app.get("/api/heatmap.png")
    def heatmap(camera: str | None = None) -> FileResponse:
        for cam in pipeline.cameras:
            if cam.heatmap is None or (camera and cam.config.name != camera):
                continue
            out = Path(pipeline.config.storage.db_path).parent / f"heatmap_{cam.config.name}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            cam.heatmap.as_png(str(out))
            return FileResponse(out, media_type="image/png")
        raise HTTPException(404, "no heatmap for that camera")

    @app.get("/api/platform")
    def platform() -> JSONResponse:
        """Camera health, sensor node, hardware, privacy and reorder panels (M7)."""
        return JSONResponse(panels.snapshot(pipeline))

    @app.get("/api/reorder")
    def reorder() -> JSONResponse:
        data = panels.snapshot(pipeline)["reorder"]
        return JSONResponse(data)

    @app.post("/api/node/{command}")
    def node_command(command: str, pattern: str | None = None, angle: int | None = None,
                     slot: str | None = None, grams: int | None = None) -> JSONResponse:
        """Test the sensor node from the dashboard: LED / BUZZER / SERVO / TARE / CAL.
        Needs the bridge in this process (`run.py --sensors`); on the Pi use MQTT cmd/*."""
        bridge = getattr(pipeline, "sensor_bridge", None)
        if bridge is None:
            raise HTTPException(409, "no sensor bridge in this process")
        from ..sensors.bridge import command_line_fields

        payload = {k: v for k, v in {"pattern": pattern, "angle": angle, "slot": slot,
                                     "grams": grams}.items() if v is not None}
        try:
            msg_type, fields = command_line_fields(command.upper(), payload)
        except (KeyError, ValueError, TypeError) as error:
            raise HTTPException(400, f"bad command: {error}") from error
        queued = bridge.submit(msg_type, fields)
        return JSONResponse({"queued": queued, "line_type": msg_type, "fields": fields})

    @app.get("/api/health")
    def health() -> JSONResponse:
        data = pipeline.health.last
        return JSONResponse(data.model_dump(mode="json") if data else {})

    @app.websocket("/ws")
    async def websocket(socket: WebSocket) -> None:
        await hub.register(socket)
        try:
            await socket.send_text(json.dumps({"type": "STATE", "data": snapshot(pipeline, panels)}))
            while True:
                await socket.receive_text()   # keepalive / ignore client chatter
        except WebSocketDisconnect:
            hub.unregister(socket)
        except Exception:
            hub.unregister(socket)

    if STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def serve_in_thread(pipeline, host: str = "0.0.0.0", port: int = 8000) -> threading.Thread:
    """Run uvicorn beside a live pipeline."""
    import uvicorn

    app = create_app(pipeline)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="storemind-api", daemon=True)
    thread.start()
    return thread
