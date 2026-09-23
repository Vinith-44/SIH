"""Typed configuration loaded from YAML.

One file per deployment describes cameras, what each camera is for, and the
geometry the calibration tool (`tools/calibrate.py`) drew on a snapshot.  Nothing
in the pipeline reads magic numbers from code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Point = tuple[float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LineConfig(_Model):
    name: str = "door"
    a: Point
    b: Point
    margin_px: float = 12.0
    # Which sign of the crossing counts as entering the store.
    entry_direction: Literal["pos", "neg"] = "pos"
    cooldown_s: float = 3.0


class ZoneConfig(_Model):
    name: str
    points: list[Point]
    kind: Literal["zone", "promo", "shelf_front"] = "zone"
    min_dwell_s: float = 3.0
    # Shelf slot this zone sits in front of (drives LOST_SALE_RISK in fusion).
    shelf: str | None = None
    slot: str | None = None


class CounterConfig(_Model):
    name: str
    lane: list[Point]
    billing: list[Point]
    # A shopper must stand in the billing polygon this long before we believe
    # service started (audit Q3: ID flicker produced 0.4 s "services").
    min_service_s: float = 3.0
    gap_tolerance_s: float = 2.0
    open: bool = True
    # Smoothed queue length that counts as congestion right now (as opposed to
    # the forecast, which warns before this happens).
    congestion_len: int = 5


class SlotConfig(_Model):
    name: str
    points: list[Point]
    sku: str | None = None
    price: float | None = None
    # Facings visible when the slot is freshly restocked; used for fill ratio.
    reference_facings: int | None = None


class ShelfConfig(_Model):
    name: str
    slots: list[SlotConfig] = Field(default_factory=list)
    low_threshold: float = 0.4
    empty_threshold: float = 0.15
    vote_k: int = 3
    vote_n: int = 5
    # Skip a frame entirely when a person box overlaps the shelf by this much.
    occlusion_iou: float = 0.05
    # Replay convenience: capture "restocked" reference crops automatically at
    # this video time (seconds).  On a live shelf the reference comes from the
    # dashboard/STM32 "Restocked" button instead; set to null to require that.
    auto_reference_s: float | None = 0.0
    # Where reference crops are kept between runs.
    reference_dir: str | None = None


class FloorPlanConfig(_Model):
    """4 image points -> 4 floor-plan points (04 section 7)."""

    image_points: list[Point]
    plan_points: list[Point]
    plan_width: float = 10.0
    plan_height: float = 10.0
    cell_size: float = 0.5  # metres per heatmap cell


class CameraConfig(_Model):
    name: str
    source: str
    role: Literal["entrance", "counter", "shelf", "zone", "generic"] = "generic"
    fps: float = 8.0
    infer_size: int = 640
    rotate: Literal[0, 90, 180, 270] = 0
    line: LineConfig | None = None
    zones: list[ZoneConfig] = Field(default_factory=list)
    counters: list[CounterConfig] = Field(default_factory=list)
    shelves: list[ShelfConfig] = Field(default_factory=list)
    floor_plan: FloorPlanConfig | None = None
    reference_frame: str | None = None  # for camera-tamper detection
    shelf_period_s: float = 30.0  # shelf cameras: one frame every N seconds


class DetectorConfig(_Model):
    backend: Literal["ultralytics", "litert", "onnx", "scripted", "stub"] = "ultralytics"
    model: str = "yolo11n.pt"
    conf: float = 0.35
    iou: float = 0.5
    person_class: int = 0
    num_threads: int = 4
    imgsz: int = 640


class TrackerConfig(_Model):
    track_activation_threshold: float = 0.25
    lost_track_buffer: int = 30
    minimum_matching_threshold: float = 0.8
    frame_rate: int = 8


class ForecastConfig(_Model):
    target_wait_min: float = 3.0
    max_prob_over_target: float = 0.2
    max_counters: int = 8
    horizon_min: int = 10
    min_lag_min: int = 1
    max_lag_min: int = 40
    # Fraction of people entering the store who reach a billing counter.
    conversion: float = 0.6
    default_service_s: float = 90.0
    period_s: float = 60.0
    warmup_min: int = 3


class ShelfEngineConfig(_Model):
    method: Literal["reference", "detector", "hybrid"] = "reference"
    detector_model: str | None = None
    embed_threshold: float = 0.75


class AlertsConfig(_Model):
    cooldown_s: float = 120.0
    escalate_after_s: float = 300.0
    voice_enabled: bool = False
    voice_lang: str = "en"
    console: bool = True
    sound: bool = False


class StorageConfig(_Model):
    db_path: str = "data/storemind.db"
    retention_days: int = 30


class ApiConfig(_Model):
    host: str = "0.0.0.0"
    port: int = 8000


class SensorConfig(_Model):
    enabled: bool = False
    port: str = "COM5"
    baud: int = 115200
    tcp: str | None = None  # "host:port" to talk to tools/stm32_simulator.py
    # slot -> load cell channel mapping for fusion
    cell_map: dict[str, str] = Field(default_factory=dict)


class MqttConfig(_Model):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None


class StoreMindConfig(_Model):
    store: str = "demo-store"
    node: str = "pi5-01"
    cameras: list[CameraConfig] = Field(default_factory=list)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    shelf: ShelfEngineConfig = Field(default_factory=ShelfEngineConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    sensors: SensorConfig = Field(default_factory=SensorConfig)
    mqtt: MqttConfig = Field(default_factory=MqttConfig)

    def camera(self, name: str) -> CameraConfig:
        for cam in self.cameras:
            if cam.name == name:
                return cam
        raise KeyError(name)


def load_config(path: str | Path) -> StoreMindConfig:
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"config not found: {path}")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StoreMindConfig(**raw)


def save_config(config: StoreMindConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json", exclude_none=False), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
