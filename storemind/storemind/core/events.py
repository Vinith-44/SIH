"""Event schema — the only contract between StoreMind modules.

Implements `research/04_ARCHITECTURE.md` section 4.  Every module publishes small
JSON events onto the bus; nothing else crosses a module boundary.

Design notes
------------
*   `Event` carries an envelope (`v`, `ts`, `store`, `node`, `cam`, `type`) plus a
    free-form `data` dict.  The dict is *validated* against a per-type payload
    model at construction time, so a typo in a producer fails loudly instead of
    silently writing junk into SQLite.
*   `ts` is an ISO-8601 string with offset.  In replay mode it is derived from the
    *video* timeline (see `storemind.core.clock`), not the wall clock, so replaying
    a 10-minute video produces a 10-minute event timeline no matter how fast the
    machine is.
*   Every event gets a UUID so HQ sync can be idempotent (07 section 4 rule 3).

Schema v2 (research/26 section 3.1) adds the sensor-node and camera-health
types.  It is *additive*: every v1 type and field is unchanged and v1 events
still validate.  A producer may later gain an optional field; renaming or
removing a field needs a contract PR.  `docs/INTERFACES.md` is the reference.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 2


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_VISIT = "ZONE_VISIT"
    QUEUE_STATE = "QUEUE_STATE"
    SERVICE_DONE = "SERVICE_DONE"
    SLOT_STATE = "SLOT_STATE"
    SENSOR = "SENSOR"
    PICKUP = "PICKUP"
    SHRINK_FLAG = "SHRINK_FLAG"
    LOST_SALE_RISK = "LOST_SALE_RISK"
    FORECAST = "FORECAST"
    ALERT = "ALERT"
    HEALTH = "HEALTH"
    # --- schema v2: STM32 sensor node (via the serial bridge) ------------- #
    SHELF_MOTION = "SHELF_MOTION"
    CAMERA_MOUNT = "CAMERA_MOUNT"
    BEAM_CROSS = "BEAM_CROSS"
    PRESENCE = "PRESENCE"
    ENVIRONMENT = "ENVIRONMENT"
    WEIGHT = "WEIGHT"
    NODE_HEALTH = "NODE_HEALTH"
    # --- schema v2: camera ingest ------------------------------------------ #
    CAMERA_HEALTH = "CAMERA_HEALTH"


class SlotState(str, Enum):
    FULL = "FULL"
    LOW = "LOW"
    EMPTY = "EMPTY"
    WRONG_ITEM = "WRONG_ITEM"
    UNKNOWN = "UNKNOWN"


class Severity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntryExitData(_Payload):
    line: str
    track: int
    direction: str  # "in" | "out"


class ZoneVisitData(_Payload):
    zone: str
    zone_kind: str = "zone"  # "zone" | "promo" | "shelf_front"
    track: int
    dwell_s: float


class QueueStateData(_Payload):
    counter: str
    queue_len: int
    queue_len_smooth: float
    median_wait_s: float | None = None
    service_rate_per_min: float | None = None
    in_service: bool = False


class ServiceDoneData(_Payload):
    counter: str
    track: int
    service_s: float
    wait_s: float | None = None


class SlotStateData(_Payload):
    shelf: str
    slot: str
    sku: str | None = None
    state: SlotState
    fill: float | None = None
    confidence: float | None = None
    reason: str | None = None


class SensorData(_Payload):
    node: str
    sensor: str
    channel: str | int | None = None
    value: float
    unit: str


class PickupData(_Payload):
    shelf: str
    slot: str
    grams: float | None = None
    evidence: str


class ShrinkFlagData(_Payload):
    shelf: str
    slot: str
    grams: float | None = None
    evidence: str


class LostSaleRiskData(_Payload):
    shelf: str
    slot: str
    sku: str | None = None
    price: float | None = None
    dwell_s: float
    est_value: float | None = None


class ForecastData(_Payload):
    lambda_hat_per_min: float
    mu_per_min_per_counter: float
    open_counters: int
    recommended_counters: int
    eta_min: float | None = None
    pred_wait_s: float | None = None
    lag_min: int | None = None
    basis: str = "erlang_c"


class AlertData(_Payload):
    severity: Severity
    message_key: str
    message: str
    lang: str = "en"
    ack: bool = False
    alert_id: str
    context: dict[str, Any] = Field(default_factory=dict)


class HealthData(_Payload):
    fps: dict[str, float] = Field(default_factory=dict)
    cpu_percent: float | None = None
    cpu_temp_c: float | None = None
    mem_percent: float | None = None
    camera_ok: dict[str, bool] = Field(default_factory=dict)
    tamper: dict[str, bool] = Field(default_factory=dict)
    uptime_s: float | None = None
    video_bytes_stored: int = 0
    # v2: hardware panel (research/23 section 4.5).  Pi 5 power is the PMIC
    # reading with the published correction applied; None when not measured.
    power_w: float | None = None
    mj_per_frame: float | None = None
    throttled: bool | None = None


# --------------------------------------------------------------------------- #
# Schema v2 payloads.  `node` inside a payload is the physical sensor node the
# reading came from (the MCU id, or for `$M` the MEMS node id on that MCU);
# the envelope `node` is whoever published it.  Integers on the wire are
# scaled back to real units by the bridge (docs/PROTOCOL.md).
# --------------------------------------------------------------------------- #

ShelfMotionKind = Literal["TOUCH", "SETTLED", "TILT", "KNOCK"]


class ShelfMotionData(_Payload):
    """MEMS accelerometer under a shelf: someone touched / bumped it."""

    node: str
    shelf: str
    slot: str | None = None
    kind: ShelfMotionKind
    peak_mg: float
    rms_mg: float
    dur_ms: int


class CameraMountData(_Payload):
    """MEMS accelerometer on a camera bracket: the camera was knocked or tilted."""

    node: str
    cam: str
    kind: Literal["KNOCK", "TILT"]
    peak_mg: float
    tilt_deg: float | None = None


class BeamCrossData(_Payload):
    """Two IR beams at a door; direction is decided on the MCU (µs timing)."""

    node: str
    door: str
    direction: Literal["in", "out"]
    t_ms_mcu: int


class PresenceData(_Payload):
    """PIR motion in a zone (camera wake-up, after-hours intrusion)."""

    node: str
    zone: str
    active: bool


class EnvironmentData(_Payload):
    node: str
    lux: float | None = None
    temp_c: float | None = None
    rh_pct: float | None = None
    pressure_hpa: float | None = None


class WeightData(_Payload):
    """HX711 load cell.  `stable` is False while the shelf is being handled."""

    node: str
    slot: str
    grams: float
    stable: bool


class NodeHealthData(_Payload):
    node: str
    uptime_s: int
    free_heap: int
    min_stack_words: int
    i2c_err: int = 0
    uart_err: int = 0
    crc_err: int = 0
    reset_cause: str = "unknown"
    link: Literal["up", "down"] = "up"


class CameraHealthData(_Payload):
    cam: str
    state: Literal["ok", "stale", "reconnecting", "tampered", "dark"]
    fps: float
    lag_ms: float | None = None
    reconnects: int = 0


PAYLOADS: dict[EventType, type[_Payload]] = {
    EventType.ENTRY: EntryExitData,
    EventType.EXIT: EntryExitData,
    EventType.ZONE_VISIT: ZoneVisitData,
    EventType.QUEUE_STATE: QueueStateData,
    EventType.SERVICE_DONE: ServiceDoneData,
    EventType.SLOT_STATE: SlotStateData,
    EventType.SENSOR: SensorData,
    EventType.PICKUP: PickupData,
    EventType.SHRINK_FLAG: ShrinkFlagData,
    EventType.LOST_SALE_RISK: LostSaleRiskData,
    EventType.FORECAST: ForecastData,
    EventType.ALERT: AlertData,
    EventType.HEALTH: HealthData,
    EventType.SHELF_MOTION: ShelfMotionData,
    EventType.CAMERA_MOUNT: CameraMountData,
    EventType.BEAM_CROSS: BeamCrossData,
    EventType.PRESENCE: PresenceData,
    EventType.ENVIRONMENT: EnvironmentData,
    EventType.WEIGHT: WeightData,
    EventType.NODE_HEALTH: NodeHealthData,
    EventType.CAMERA_HEALTH: CameraHealthData,
}


# --------------------------------------------------------------------------- #
# Envelope
# --------------------------------------------------------------------------- #


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str
    store: str
    node: str
    cam: str | None = None
    type: EventType
    data: dict[str, Any]

    ISO_FMT: ClassVar[str] = "%Y-%m-%dT%H:%M:%S.%f%z"

    @field_validator("ts")
    @classmethod
    def _ts_must_parse(cls, value: str) -> str:
        datetime.fromisoformat(value)  # raises on junk
        return value

    def model_post_init(self, _context: Any) -> None:
        model = PAYLOADS.get(self.type)
        if model is not None:
            # Validate, then write the normalised dict back so enums become plain
            # strings and defaults are filled in before storage.
            object.__setattr__(self, "data", model(**self.data).model_dump(mode="json"))

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.ts)

    def to_json(self) -> str:
        return self.model_dump_json()


def make_event(
    *,
    ts: datetime | str,
    store: str,
    node: str,
    type: EventType,
    data: _Payload | dict[str, Any],
    cam: str | None = None,
) -> Event:
    """Build a validated `Event`.  `data` may be a payload model or a plain dict."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts = ts.isoformat()
    if isinstance(data, _Payload):
        data = data.model_dump(mode="json")
    return Event(ts=ts, store=store, node=node, cam=cam, type=type, data=data)
