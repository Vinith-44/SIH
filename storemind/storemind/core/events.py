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
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1


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
