"""The StoreMind pipeline.

One process, clear module boundaries (04 section 3 says "start as one Python
process with threads ... keep the same module boundaries so it can be split
later").  Each camera gets a `CameraPipeline`; the `Pipeline` merges them onto one
timeline and drives storage, alerts, fusion and health.

Replay determinism
------------------
With file sources the runner does **not** read cameras round-robin at whatever
speed each decodes.  It keeps one pending frame per camera and always processes
the one with the smallest video timestamp, advancing a single `VideoClock` to it.
That reproduces what the cameras would have seen simultaneously, and makes a
replay run byte-for-byte repeatable - which is the only way `eval/RESULTS.md` can
mean anything.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2

from .alerts.manager import AlertManager, build_alert_manager
from .analytics.footfall import FootfallCounter, GateCounter, build_counter
from .analytics.heatmap import FloorHeatmap
from .analytics.queue import QueueEngine, counter_spec_from_config
from .analytics.reorder import ReorderQueue
from .analytics.shelf import ShelfEngine, SlotSpec
from .analytics.staff import StaffFilter
from .analytics.promo import PromoEngine, promo_spec_from_config
from .analytics.zones import ZoneEngine, ZoneSpec
from .core.bus import EventBus, MqttBus
from .core.clock import Clock, VideoClock, WallClock
from .core.config import CameraConfig, StoreMindConfig
from .core.events import Event, EventType, Severity
from .core.geometry import Line, Polygon
from .fusion.beam import BeamCrossCheck
from .fusion.forecast import Forecaster
from .fusion.interaction import ShelfInteractionEngine, SlotInfo
from .fusion.fusion import FusionEngine
from .health.monitor import HealthMonitor, TamperDetector
from .ingest.sources import Frame, FpsScheduler, FrameSource, open_source
from .inference.detector import Detector, build_detector
from .inference.filters import DetectionFilter
from .store.db import EventStore
from .tracking.tracker import Tracker, build_tracker

log = logging.getLogger(__name__)


@dataclass
class CameraPipeline:
    """Everything that is per-camera: source, tracker, engines, geometry."""

    config: CameraConfig
    source: FrameSource
    tracker: Tracker
    scheduler: FpsScheduler
    store: str
    node: str

    line: Line | None = None
    footfall: FootfallCounter | GateCounter | None = None
    det_filter: DetectionFilter | None = None
    staff: StaffFilter | None = None
    zones: ZoneEngine | None = None
    promo: PromoEngine | None = None      # kind: promo zones (docs/PROMO.md)
    heatmap: FloorHeatmap | None = None
    queue: QueueEngine | None = None
    shelf: ShelfEngine | None = None
    tamper: TamperDetector | None = None

    shelf_method: str = "reference"
    shelf_embed_threshold: float = 0.75
    shelf_detector: Detector | None = None  # product detector for shelf.method detector / hybrid
    scales: frozenset[str] = frozenset()  # "shelf/slot" keys with a load cell (sensors.cell_map)
    check_now: bool = False               # M6: a shelf was just touched - look now
    # Per-camera detector override.  Normally every camera shares one model (one
    # model in memory is the whole point on a Pi), but the scripted backend
    # replays a different detections file per clip, and a real deployment could
    # want a shelf-specific model on the shelf camera.
    detector: Detector | None = None
    frame_size: tuple[int, int] | None = None
    shelf_reference_taken: bool = False
    pending: Frame | None = None
    processed: int = 0
    decoded: int = 0
    finished: bool = False
    infer_ms: list[float] = field(default_factory=list)
    last_tracks: list = field(default_factory=list)

    # -- lazy geometry ---------------------------------------------------- #
    def resolve_geometry(self, width: int, height: int) -> None:
        """Config coordinates are normalised; convert once the frame size is known."""
        if self.frame_size is not None:
            return
        self.frame_size = (width, height)
        cfg = self.config

        if cfg.line is not None:
            self.line = Line(cfg.line.name, tuple(cfg.line.a), tuple(cfg.line.b),
                             cfg.line.margin_px).resolve(width, height)
            self.footfall = build_counter(self.line, cfg.line, store=self.store,
                                          node=self.node, cam=cfg.name)
        if cfg.filters:
            self.det_filter = DetectionFilter(cfg.filters, width, height)
        if cfg.staff is not None:
            self.staff = StaffFilter(cfg.staff, width, height)

        if cfg.zones:
            specs = [
                ZoneSpec(
                    polygon=Polygon(z.name, [tuple(p) for p in z.points], z.kind).resolve(width, height),
                    min_dwell_s=z.min_dwell_s, shelf=z.shelf, slot=z.slot,
                )
                for z in cfg.zones
            ]
            self.zones = ZoneEngine(specs, store=self.store, node=self.node, cam=cfg.name)
            promos = [promo_spec_from_config(z, width, height, self.scales) for z in cfg.zones if z.kind == "promo"]
            if promos:
                self.promo = PromoEngine(promos, store=self.store, node=self.node, cam=cfg.name)

        if cfg.counters:
            specs = [counter_spec_from_config(c, width, height) for c in cfg.counters]
            self.queue = QueueEngine(specs, store=self.store, node=self.node, cam=cfg.name)

        if cfg.shelves:
            self.shelf = ShelfEngine(
                [
                    (
                        s.name,
                        [SlotSpec(polygon=Polygon(slot.name, [tuple(p) for p in slot.points], "slot").resolve(width, height),
                                  sku=slot.sku, price=slot.price,
                                  reference_facings=slot.reference_facings,
                                  full_grams=slot.full_grams, deep=slot.deep)
                         for slot in s.slots],
                        s,
                    )
                    for s in cfg.shelves
                ],
                store=self.store, node=self.node, cam=cfg.name,
                method=self.shelf_method, embed_threshold=self.shelf_embed_threshold,
                detector=self.shelf_detector,
                reference_dir=next((s.reference_dir for s in cfg.shelves if s.reference_dir), None),
            )

        if cfg.role == "entrance" or cfg.floor_plan is not None:
            self.heatmap = FloorHeatmap.from_config(cfg.floor_plan, (width, height))

        if cfg.reference_frame:
            path = Path(cfg.reference_frame)
            if path.is_file():
                self.tamper = TamperDetector.from_file(path)
            else:
                log.warning("camera %s: reference frame %s missing, tamper detection off",
                            cfg.name, path)

    # -- geometry accessors for the overlay ------------------------------- #
    def lane_polygons(self) -> list[Polygon]:
        return ([c.spec.lane for c in self.queue.counters.values() if c.spec.lane is not None]
                if self.queue else [])

    def billing_polygons(self) -> list[Polygon]:
        return [c.spec.billing for c in self.queue.counters.values()] if self.queue else []

    def zone_polygons(self) -> list[Polygon]:
        return [z.polygon for z in self.zones.zones.values()] if self.zones else []

    def slot_polygons(self) -> list[Polygon]:
        return self.shelf.slot_polygons() if self.shelf else []


class Pipeline:
    def __init__(self, config: StoreMindConfig, *, replay: bool = True, show: bool = False,
                 detector: Detector | None = None, bus: EventBus | None = None,
                 store: EventStore | None = None, sensor_bridge=None,
                 realtime: bool = False, start_time=None,
                 on_tracks=None) -> None:
        # `on_tracks(camera_name, frame, tracks)` is called once per processed
        # frame.  The evaluation harness uses it to score tracking (ID switches)
        # in the same run that scores counting, and the overlay recorder uses it
        # to draw.  Nothing in the pipeline depends on it.
        self.on_tracks = on_tracks
        self.config = config
        self.show = show
        self.replay = replay
        self.clock: Clock = VideoClock(start=start_time) if replay else WallClock()
        self.bus = bus or (MqttBus(config.mqtt.host, config.mqtt.port,
                                   config.mqtt.username, config.mqtt.password,
                                   store=config.store, node=config.node,
                                   listen=config.mqtt.listen)
                           if config.mqtt.enabled else EventBus())
        self.store = store if store is not None else EventStore(
            config.storage.db_path, store=config.store, retention_days=config.storage.retention_days)
        self.bus.subscribe_all(self.store.handle)

        self.detector = detector or build_detector(config.detector)
        # An injected detector (tests, cached evaluation replays) is used for every
        # camera: a per-camera `infer_size` must not silently swap it for a real model.
        self._detector_injected = detector is not None
        self._sized_detectors: dict[int, Detector] = {}
        self.shelf_detector = self._build_shelf_detector()
        self.sensor_bridge = sensor_bridge
        self.alerts: AlertManager = build_alert_manager(
            config.alerts, config.store, config.node, bridge=sensor_bridge)
        self.health = HealthMonitor(store=config.store, node=config.node)
        self.forecaster = Forecaster(
            target_wait_min=config.forecast.target_wait_min,
            max_prob_over_target=config.forecast.max_prob_over_target,
            max_counters=config.forecast.max_counters,
            min_lag_min=config.forecast.min_lag_min,
            max_lag_min=config.forecast.max_lag_min,
            conversion=config.forecast.conversion,
            default_service_s=config.forecast.default_service_s,
            period_s=config.forecast.period_s,
            warmup_min=config.forecast.warmup_min,
            store=config.store, node=config.node,
        )
        self.fusion = FusionEngine(store=config.store, node=config.node,
                                   cell_map=config.sensors.cell_map)
        self.cameras: list[CameraPipeline] = []
        self.events_emitted = 0
        self._fusion_cursor = 0
        self._wire_internal_subscriptions()
        self._build_cameras(realtime=realtime)

    # ------------------------------------------------------------------ #
    def _wire_internal_subscriptions(self) -> None:
        """Modules talk only through events (CLAUDE.md engineering rules)."""

        def on_entry(event: Event) -> None:
            self.forecaster.note_entry(self.clock.monotonic_s())

        self.bus.subscribe_types(EventType.ENTRY, on_entry)
        # Shelf interaction fusion (M6): MEMS touch + load cell + shopper presence.
        self.interaction = self._build_interaction()
        self.bus.subscribe_types([EventType.SHELF_MOTION, EventType.WEIGHT, EventType.CAMERA_MOUNT,
                                  EventType.PRESENCE, EventType.ZONE_VISIT], self._interaction_input)
        # Shelf v2 inputs (M3): light level, load cells, camera health, restock button.
        self.bus.subscribe_types([EventType.ENVIRONMENT, EventType.WEIGHT, EventType.CAMERA_HEALTH,
                                  EventType.SENSOR], self._shelf_inputs)

        doors = {c.line.beam_door: c.name for c in self.config.cameras
                 if c.line is not None and c.line.beam_door}
        self.beam_check = (BeamCrossCheck(doors, store=self.config.store, node=self.config.node)
                           if doors else None)
        if self.beam_check is not None:
            self.bus.subscribe_types(
                [EventType.BEAM_CROSS, EventType.ENTRY, EventType.EXIT, EventType.CAMERA_HEALTH],
                lambda event: self._emit_all(self.beam_check.on_event(event)))
        self.bus.subscribe_types([EventType.SLOT_STATE, EventType.SENSOR, EventType.ZONE_VISIT],
                                 self._fusion_input)
        self.bus.subscribe_types(EventType.SLOT_STATE, self._on_slot_state)
        # Reorder drafts (M3): a file next to the event DB when live, memory in replays.
        self.reorder = ReorderQueue(":memory:" if self.replay else
                                    Path(self.config.storage.db_path).with_name("reorder.db"))
        self.bus.subscribe_types(EventType.SLOT_STATE, self.reorder.on_event)
        self.bus.subscribe_types(EventType.SHRINK_FLAG, self._on_shrink)
        # Promotions (docs/PROMO.md): a pick at a promo's linked slot = "took the item".
        self.bus.subscribe_types(EventType.PICKUP, self._on_pickup_for_promo)

    def _build_interaction(self) -> ShelfInteractionEngine:
        cell_map = self.config.sensors.cell_map
        slots = [SlotInfo(shelf.name, slot.name, cell_map.get(f"{shelf.name}/{slot.name}"), slot.unit_grams)
                 for cam in self.config.cameras for shelf in cam.shelves for slot in shelf.slots]
        # Slots with a load cell but no camera shelf still get pick/put-back.
        known = {f"{s.shelf}/{s.slot}" for s in slots}
        slots += [SlotInfo(*key.split("/", 1), channel) for key, channel in cell_map.items()
                  if key not in known and "/" in key]

        def person_at_shelf(shelf: str) -> bool:
            for camera in self.cameras:
                if camera.zones is None:
                    continue
                for name, spec in camera.zones.zones.items():
                    if spec.shelf == shelf and camera.zones.occupancy(name) > 0:
                        return True
            return False

        return ShelfInteractionEngine(
            slots, store=self.config.store, node=self.config.node,
            mems_enabled=self.config.sensors.has("mems"),
            open_hours=self.config.alerts.open_hours, person_at_shelf=person_at_shelf)

    def _interaction_input(self, event: Event) -> None:
        if event.type is EventType.ZONE_VISIT:
            for camera in self.cameras:
                spec = camera.zones.zones.get(event.data["zone"]) if camera.zones else None
                if spec is not None and spec.shelf:
                    self.interaction.note_zone_visit(spec.shelf, self.clock.monotonic_s())
            return
        self._emit_all(self.interaction.on_event(event, self.clock))
        for shelf in self.interaction.drain_check_requests():
            for camera in self.cameras:
                if camera.shelf is not None and shelf in camera.shelf.shelves:
                    camera.check_now = True

    def _due(self, camera: CameraPipeline, frame: Frame) -> bool:
        """The FPS budget decides - unless a shelf was just touched (M6)."""
        if camera.check_now:
            camera.check_now = False
            return True
        return camera.scheduler.should_process(frame.video_s)

    def _shelf_inputs(self, event: Event) -> None:
        data = event.data
        shelves = [c for c in self.cameras if c.shelf is not None]
        if event.type is EventType.ENVIRONMENT:
            for camera in shelves:
                camera.shelf.observe_lux(data.get("lux"), data.get("node"))
        elif event.type is EventType.WEIGHT:
            # WEIGHT.slot is the load-cell channel; sensors.cell_map says which slot it is under.
            channel_to_slot = {v: k for k, v in self.config.sensors.cell_map.items()}
            target = channel_to_slot.get(str(data["slot"]), "")
            if "/" in target:
                shelf, slot = target.split("/", 1)
                for camera in shelves:
                    camera.shelf.observe_weight(shelf, slot, data["grams"], data["stable"])
        elif event.type is EventType.CAMERA_HEALTH:
            for camera in shelves:
                if camera.config.name == data["cam"]:
                    camera.shelf.set_camera_ok(data["state"] == "ok")
        elif data.get("sensor") == "restock":       # `$R` restock button -> SENSOR
            shelf = str(data.get("channel"))
            for camera in shelves:
                if shelf in camera.shelf.shelves:
                    self.restock(camera.config.name, shelf)

    def _on_slot_state(self, event: Event) -> None:
        state = event.data["state"]
        if state == "FULL":
            return  # a refilled slot is good news, not an alert
        shelf, slot = event.data["shelf"], event.data["slot"]
        sku = event.data.get("sku") or slot
        if state == "EMPTY":
            key, severity = f"SLOT_EMPTY:{shelf}:{slot}", Severity.CRITICAL
            message = f"{sku} is OUT OF STOCK in {shelf} slot {slot} - refill now"
        elif state == "LOW":
            key, severity = f"SLOT_LOW:{shelf}:{slot}", Severity.WARN
            message = f"{sku} is running low in {shelf} slot {slot}"
        else:
            key, severity = f"WRONG_ITEM:{shelf}:{slot}", Severity.WARN
            message = f"Wrong product in {shelf} slot {slot} (planogram says {sku})"
        self._emit_all(self.alerts.raise_alert(key, message, severity, self.clock, dict(event.data)))

    def _on_pickup_for_promo(self, event: Event) -> None:
        for camera in self.cameras:
            if camera.promo is not None:
                camera.promo.on_pickup(event, self.clock)

    def _on_shrink(self, event: Event) -> None:
        shelf, slot = event.data["shelf"], event.data["slot"]
        self._emit_all(self.alerts.raise_alert(
            f"SHRINK:{shelf}:{slot}",
            f"Unexplained weight drop of {event.data.get('grams')} g at {shelf}/{slot}",
            Severity.WARN, self.clock, dict(event.data)))

    def _fusion_input(self, event: Event) -> None:
        for produced in self.fusion.on_event(event, self.clock):
            self._emit(produced)

    def _build_cameras(self, realtime: bool) -> None:
        for cam in self.config.cameras:
            source = open_source(cam.source, name=cam.name, realtime=realtime, rotate=cam.rotate)
            fps = (1.0 / cam.shelf_period_s) if cam.role == "shelf" else cam.fps
            tracker_config = self.config.tracker.model_copy(update={"frame_rate": max(1, int(cam.fps))})
            self.cameras.append(CameraPipeline(
                config=cam, source=source, tracker=build_tracker(tracker_config),
                scheduler=FpsScheduler(fps), store=self.config.store, node=self.config.node,
                shelf_method=self.config.shelf.method,
                shelf_embed_threshold=self.config.shelf.embed_threshold,
                shelf_detector=self.shelf_detector,
                scales=frozenset(self.config.sensors.cell_map),
                detector=self._per_camera_detector(cam)))
            self.health.camera_ok[cam.name] = True

    # ------------------------------------------------------------------ #
    def _build_shelf_detector(self) -> Detector | None:
        """The product detector behind `shelf.method: detector | hybrid`: the
        `detector` settings with `shelf.detector_model`, class 0 = product."""
        shelf = self.config.shelf
        if shelf.method == "reference" or self.config.detector.backend in ("scripted", "stub"):
            return None
        return build_detector(self.config.detector.model_copy(
            update={"model": shelf.detector_model, "person_class": 0}))

    def _per_camera_detector(self, cam: CameraConfig) -> Detector | None:
        """A camera's own detector, or None to share the pipeline's.

        * scripted replays: each camera replays the detections file beside its clip,
          so a multi-camera config can be exercised without any model;
        * `infer_size` different from `detector.imgsz`: one detector per size,
          shared by the cameras that ask for it."""
        if self.config.detector.backend != "scripted":
            return self._sized_detector(cam)
        if self.config.detector.model:
            return None                      # one explicit file for every camera
        candidate = Path(str(cam.source))
        detections = candidate.with_name(f"{candidate.stem}_detections.json")
        if not detections.is_file():
            log.warning("camera %s: no %s, it will see nothing",
                        cam.name, detections.name)
            return None
        from .inference.scripted import ScriptedDetector

        return ScriptedDetector(detections)

    def _sized_detector(self, cam: CameraConfig) -> Detector | None:
        size = cam.infer_size
        if (size is None or size == self.config.detector.imgsz or self._detector_injected
                or self.config.detector.backend == "stub"):
            return None
        if size not in self._sized_detectors:
            detector = build_detector(self.config.detector.model_copy(update={"imgsz": size}))
            actual = getattr(detector, "input_size", size)
            if actual != size:
                raise SystemExit(
                    f"camera {cam.name}: infer_size {size}, but {self.config.detector.model} has a fixed "
                    f"{actual} px input.  Export the model at {size} or remove infer_size.")
            self._sized_detectors[size] = detector
        return self._sized_detectors[size]

    def _emit(self, event: Event) -> None:
        self.events_emitted += 1
        self.bus.publish(event)

    def _emit_all(self, events: list[Event]) -> None:
        for event in events:
            self._emit(event)

    # ------------------------------------------------------------------ #
    def process_frame(self, camera: CameraPipeline, frame: Frame) -> None:
        width, height = frame.size
        camera.resolve_geometry(width, height)

        if camera.tamper is not None:
            was = camera.tamper.tampered
            now_tampered = camera.tamper.update(frame.image)
            self.health.tamper[camera.config.name] = now_tampered
            if self.beam_check is not None and camera.config.name in self.beam_check.camera_ok:
                self.beam_check.camera_ok[camera.config.name] = not now_tampered
            if now_tampered and not was:
                self.interaction.note_image_tamper(camera.config.name, self.clock.monotonic_s())
            if now_tampered and not was:
                self._emit_all(self.alerts.raise_alert(
                    f"CAMERA_TAMPER:{camera.config.name}",
                    f"Camera {camera.config.name} moved or blocked - counting paused",
                    Severity.CRITICAL, self.clock,
                    {"cam": camera.config.name, "score": round(camera.tamper.last_score, 3)}))
            if now_tampered:
                # Zones no longer point at what they were calibrated on, so
                # publishing counts would be publishing fiction.
                return

        # A scripted detector replays boxes by frame index, so it must be told
        # which frame this is - the FPS scheduler skips frames.
        detector = camera.detector or self.detector
        seek = getattr(detector, "seek", None)
        if seek is not None:
            seek(frame.index)
        started = time.perf_counter()
        detections = detector.detect(frame.image)
        camera.infer_ms.append((time.perf_counter() - started) * 1000.0)
        if camera.det_filter is not None:
            detections = camera.det_filter(detections)
        tracks = camera.tracker.update(detections)
        camera.last_tracks = tracks
        camera.processed += 1
        if self.on_tracks is not None:
            self.on_tracks(camera.config.name, frame, tracks)
        self.health.tick_frame(camera.config.name, time.monotonic())

        # Staff are excluded from every customer metric; the shelf occlusion
        # gate below still sees them (they block the shelf like anyone else).
        staff = (camera.staff.update(frame.image, tracks, self.clock.monotonic_s())
                 if camera.staff is not None else set())
        customers = [t for t in tracks if t.track_id not in staff] if staff else tracks
        if camera.footfall is not None:
            self._emit_all(camera.footfall.update(tracks, self.clock, exclude=staff))
        if camera.zones is not None:
            self._emit_all(camera.zones.update(customers, self.clock))
        if camera.promo is not None:
            self._emit_all(camera.promo.update(customers, self.clock))
        if camera.heatmap is not None:
            camera.heatmap.update(customers, self.clock, (width, height))
        if camera.queue is not None:
            before = len(camera.queue.checkout_arrivals)
            self._emit_all(camera.queue.update(customers, self.clock))
            for stamp in camera.queue.checkout_arrivals[before:]:
                self.forecaster.note_checkout_arrival(stamp)
        if camera.shelf is not None:
            self._maybe_take_shelf_reference(camera, frame)
            self._emit_all(camera.shelf.update(frame.image, tracks, self.clock))

        if self.show:
            self._render(camera, frame, tracks)

    def _maybe_take_shelf_reference(self, camera: CameraPipeline, frame: Frame) -> None:
        """A shelf engine is blind until it has a "restocked" reference crop.

        Live, that comes from the dashboard button or the STM32 `$R` line.  In
        replay there is nobody to press it, so a camera may declare the video
        time at which the shelf is known to be stocked.
        """
        if camera.shelf_reference_taken or camera.shelf is None:
            return
        due = min((s.auto_reference_s for s in camera.config.shelves
                   if s.auto_reference_s is not None), default=None)
        if due is None:
            return
        if self.clock.monotonic_s() + 1e-9 < due:
            return
        taken = camera.shelf.capture_references(frame.image)
        camera.shelf_reference_taken = True
        self._emit_all(camera.shelf.announce(self.clock))
        log.info("camera %s: captured %d shelf reference crops at t=%.1fs",
                 camera.config.name, taken, self.clock.monotonic_s())

    def restock(self, camera_name: str, shelf: str | None = None) -> int:
        """Dashboard / STM32 "Restocked" action."""
        for camera in self.cameras:
            if camera.config.name != camera_name or camera.shelf is None:
                continue
            frame = camera.pending
            image = frame.image if frame is not None else None
            if image is None:
                return 0
            taken = camera.shelf.capture_references(image, shelf)
            self._emit_all(camera.shelf.announce(self.clock, shelf))
            return taken
        return 0

    def _render(self, camera: CameraPipeline, frame: Frame, tracks: list) -> None:
        from .overlay import draw

        hud = [f"{camera.config.name} ({camera.config.role})  t={self.clock.monotonic_s():6.1f}s",
               f"tracks {len(tracks)}   frames {camera.processed}"]
        if camera.footfall is not None:
            hud.append(f"IN {camera.footfall.entries}  OUT {camera.footfall.exits}"
                       f"  occupancy {camera.footfall.occupancy}")
        if camera.queue is not None:
            for name, state in camera.queue.counters.items():
                wait = state.median_wait_s
                hud.append(f"{name}: len {state.queue_len} (smooth {state.queue_len_smooth:.1f})"
                           + (f"  med wait {wait:.0f}s" if wait else ""))
        if self.forecaster.last is not None:
            last = self.forecaster.last
            hud.append(f"forecast: lam {last.lambda_hat_per_min:.2f}/min -> "
                       f"{last.recommended_counters} counters")
        canvas = draw(frame.image, tracks=tracks, line=camera.line,
                      zones=camera.zone_polygons(), lanes=camera.lane_polygons(),
                      billings=camera.billing_polygons(), slots=camera.slot_polygons(), hud=hud)
        cv2.imshow(f"StoreMind - {camera.config.name}", canvas)

    # ------------------------------------------------------------------ #
    def _periodic(self) -> None:
        now = self.clock.monotonic_s()
        self._emit_all(self.interaction.tick(self.clock))
        for key, message, severity, evidence in self.interaction.drain_alerts():
            self._emit_all(self.alerts.raise_alert(key, message, severity, self.clock, evidence))
        for key, message, evidence in (self.beam_check.alerts() if self.beam_check else []):
            self._emit_all(self.alerts.raise_alert(key, message, Severity.WARN, self.clock, evidence))
        mu = None
        open_counters = 0
        for camera in self.cameras:
            if camera.queue is not None:
                mu = camera.queue.mu_per_min() or mu
                open_counters += camera.queue.open_counters()
        open_counters = max(1, open_counters)

        forecast_events = self.forecaster.step(now, self.clock, mu_per_min=mu,
                                               open_counters=open_counters)
        self._emit_all(forecast_events)
        for event in forecast_events:
            recommended = event.data["recommended_counters"]
            if recommended > event.data["open_counters"]:
                eta = event.data.get("eta_min") or 0
                predicted = event.data.get("pred_wait_s")
                message = (f"Open {recommended} counters"
                           + (f" in about {eta:.0f} min" if eta else " now")
                           + (f" (predicted wait {predicted / 60:.1f} min)" if predicted else ""))
                self._emit_all(self.alerts.raise_alert(
                    "QUEUE_FORECAST", message, Severity.WARN, self.clock, dict(event.data)))

        # Congestion that is already happening (the forecast above warns before).
        for camera in self.cameras:
            if camera.queue is None:
                continue
            for name, state in camera.queue.counters.items():
                if state.queue_len_smooth >= state.spec.congestion_len:
                    self._emit_all(self.alerts.raise_alert(
                        f"QUEUE_CONGESTED:{name}",
                        f"{name}: {state.queue_len_smooth:.0f} people waiting now",
                        Severity.WARN, self.clock,
                        {"counter": name, "queue_len": state.queue_len}))
                if state.tail_overflow:
                    self._emit_all(self.alerts.raise_alert(
                        f"QUEUE_OVERFLOW:{name}",
                        f"{name}: the queue has reached the end of its lane - open another counter",
                        Severity.WARN, self.clock,
                        {"counter": name, "queue_len": state.queue_len,
                         "queue_parties": state.parties}))

        self._drain_fusion_findings()
        self._emit_all(self.alerts.tick(self.clock))
        self._emit_all(self.health.step(self.clock))
        self._emit_all(self.fusion.tick(self.clock))

    def _drain_fusion_findings(self) -> None:
        """Fusion rules that need both camera and sensor evidence surface here."""
        while self._fusion_cursor < len(self.fusion.findings):
            finding = self.fusion.findings[self._fusion_cursor]
            self._fusion_cursor += 1
            if finding["type"] == "HIDDEN_DEPLETION":
                slot = finding["slot"]
                self._emit_all(self.alerts.raise_alert(
                    f"HIDDEN_DEPLETION:{slot}",
                    f"{slot} looks full to the camera but has lost "
                    f"{finding['grams_lost']:.0f} g - the back row is empty",
                    Severity.WARN, self.clock, finding))
            elif finding["type"] == "CONFIRMED_OOS":
                slot = finding["slot"]
                self._emit_all(self.alerts.raise_alert(
                    f"SLOT_EMPTY:{slot}",
                    f"{slot} confirmed out of stock by camera AND load cell",
                    Severity.CRITICAL, self.clock, finding))

    # ------------------------------------------------------------------ #
    def run(self, max_seconds: float | None = None, progress: bool = True) -> dict:
        started_wall = time.perf_counter()
        try:
            if self.replay:
                self._run_replay(max_seconds, progress)
            else:
                self._run_live(max_seconds)
        except KeyboardInterrupt:
            print("\ninterrupted", flush=True)
        finally:
            self._finish()
        wall = time.perf_counter() - started_wall
        return self.summary(wall_seconds=wall)

    def _run_replay(self, max_seconds: float | None, progress: bool) -> None:
        for camera in self.cameras:
            camera.pending = camera.source.read()
            if camera.pending is not None:
                camera.decoded += 1
        last_report = 0.0
        while True:
            live = [c for c in self.cameras if c.pending is not None]
            if not live:
                break
            # Merge all cameras onto one timeline.
            camera = min(live, key=lambda c: c.pending.video_s)
            frame = camera.pending
            if max_seconds is not None and frame.video_s > max_seconds:
                break
            assert isinstance(self.clock, VideoClock)
            self.clock.advance_to(frame.video_s)
            if self._due(camera, frame):
                self.process_frame(camera, frame)
            self._periodic()
            camera.pending = camera.source.read()
            if camera.pending is None:
                camera.finished = True
            else:
                camera.decoded += 1
            if self.show and cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
            if progress and frame.video_s - last_report >= 10.0:
                last_report = frame.video_s
                print(f"  ... {frame.video_s:6.1f}s of video, {self.events_emitted} events",
                      flush=True)

    def _run_live(self, max_seconds: float | None) -> None:
        started = time.monotonic()
        while True:
            if max_seconds is not None and time.monotonic() - started > max_seconds:
                break
            idle = True
            for camera in self.cameras:
                frame = camera.source.read()
                if frame is None:
                    self.health.camera_ok[camera.config.name] = getattr(camera.source, "connected", True)
                    continue
                idle = False
                camera.decoded += 1
                self.health.camera_ok[camera.config.name] = True
                if self._due(camera, frame):
                    self.process_frame(camera, frame)
            self._periodic()
            if self.show and cv2.waitKey(1) & 0xFF in (27, ord("q")):
                break
            if idle:
                time.sleep(0.005)

    def _finish(self) -> None:
        for camera in self.cameras:
            if camera.zones is not None:
                self._emit_all(camera.zones.flush(self.clock))
            if camera.promo is not None:
                self._emit_all(camera.promo.flush(self.clock))
            camera.source.close()
        if self.show:
            cv2.destroyAllWindows()
        self.store.flush()

    # ------------------------------------------------------------------ #
    def summary(self, wall_seconds: float | None = None) -> dict:
        out: dict = {
            "store": self.config.store,
            "node": self.config.node,
            "detector": f"{self.config.detector.backend}:{self.config.detector.model}",
            "events": self.events_emitted,
            "video_seconds": round(self.clock.monotonic_s(), 2),
            "wall_seconds": round(wall_seconds, 2) if wall_seconds else None,
            "cameras": {},
            "alerts": len(self.alerts.log),
            "video_bytes_stored": self.health.video_bytes_stored,
        }
        for camera in self.cameras:
            entry: dict = {
                "role": camera.config.role,
                "source": camera.config.source,
                "frames_decoded": camera.decoded,
                "frames_processed": camera.processed,
                "target_fps": camera.scheduler.target_fps,
            }
            if camera.infer_ms:
                ordered = sorted(camera.infer_ms)
                entry["infer_ms_mean"] = round(sum(ordered) / len(ordered), 2)
                entry["infer_ms_p95"] = round(ordered[int(0.95 * (len(ordered) - 1))], 2)
            if camera.footfall is not None:
                entry["entries"] = camera.footfall.entries
                entry["exits"] = camera.footfall.exits
                entry["occupancy"] = camera.footfall.occupancy
            if camera.zones is not None:
                entry["zone_visits"] = len(camera.zones.completed)
            if camera.promo is not None:
                entry["promo"] = {zone: dict(totals) for zone, totals in camera.promo.totals.items()}
            if camera.queue is not None:
                entry["counters"] = camera.queue.summary()
            if camera.shelf is not None:
                entry["shelf"] = camera.shelf.summary()
            if camera.heatmap is not None:
                entry["heatmap"] = camera.heatmap.summary()
            out["cameras"][camera.config.name] = entry
        if self.forecaster.last is not None:
            out["last_forecast"] = self.forecaster.last.model_dump(mode="json")
        if wall_seconds and self.clock.monotonic_s() > 0:
            out["replay_speed_x"] = round(self.clock.monotonic_s() / max(wall_seconds, 1e-6), 2)
        return out

    def close(self) -> None:
        self.store.close()
