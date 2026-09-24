"""Typed configuration loaded from YAML.

One file per deployment describes cameras, what each camera is for, and the
geometry the calibration tool (`tools/calibrate.py`) drew on a snapshot.  Nothing
in the pipeline reads magic numbers from code.

Secrets (camera and MQTT passwords) never live in these files: they come from
`configs/secrets.yaml` (git-ignored; `configs/secrets.example.yaml` shows the
shape) or from environment variables, via `load_secrets`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    # --- counting v2 (M1, docs/COUNTING.md).  mode "single" = v1 behaviour. --- #
    mode: Literal["single", "gate"] = "single"
    gate_px: float = 16.0            # width of the A->B band centred on the line
    min_track_age_s: float = 0.5     # a track younger than this cannot count
    min_displacement_px: float = 0.0  # net movement across the line in the window
    direction_mode: Literal["off", "balanced", "strict"] = "balanced"
    direction_window_s: float = 1.0
    confirm_s: float = 0.0           # stay on the far side this long before counting
    beam_door: str | None = None     # IR break-beam door that watches this line


class DetectionFilterConfig(_Model):
    """Frigate-style per-zone filter applied before tracking.  A detection whose
    foot point is inside `points` (or anywhere, if `points` is empty) must pass
    every set threshold."""

    points: list[Point] = Field(default_factory=list)
    min_score: float | None = None
    min_area_px: float = 0.0
    max_area_frac: float = 1.0       # of the frame area: drops "whole-frame" boxes
    classes: list[int] | None = None


class StaffConfig(_Model):
    """Staff exclusion: never identified, only excluded from customer counts."""

    zones: list[list[Point]] = Field(default_factory=list)  # cashier / back door
    zone_dwell_s: float = 2.0        # this long inside a staff zone = staff
    badge: bool = False              # printed ArUco badge
    aruco_dict: str = "DICT_4X4_50"
    badge_ids: list[int] = Field(default_factory=list)  # empty = any marker id
    badge_every_n: int = 2           # look for badges every N processed frames


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
    filters: list[DetectionFilterConfig] = Field(default_factory=list)
    staff: StaffConfig | None = None


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
    # M1 tracker bake-off: bytetrack | ocsort | botsort (no ReID) | sort | simple
    type: Literal["bytetrack", "ocsort", "botsort", "sort", "simple"] = "bytetrack"
    high_conf_det_threshold: float | None = None   # None = the library default
    minimum_consecutive_frames: int = 1


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


SensorName = Literal["hx711", "mems", "ir_beam", "pir", "bh1750", "bme280",
                     "buzzer", "led", "servo", "restock_button", "ld2450"]

# What a fresh node has fitted (CLAUDE_CODE_PROMPT_V2 section 0): the servo is
# demo-only and the LD2450 radar was not bought, so both are off by default.
DEFAULT_SENSORS: list[str] = ["hx711", "mems", "ir_beam", "pir", "bh1750", "bme280",
                              "buzzer", "led", "restock_button"]


class SensorNodeConfig(_Model):
    """One STM32 node.  A sensor that is not listed simply does not exist."""

    id: str = "stm32-01"
    enabled_sensors: list[SensorName] = Field(default_factory=lambda: list(DEFAULT_SENSORS))
    protocol_mode: Literal["txt", "bin"] = "txt"   # docs/PROTOCOL.md: demo text / COBS+CRC16
    time_sync_s: float = 60.0                      # `$S` period
    cmd_timeout_ms: int = 200                      # retry a command if no `$K` in time
    cmd_retries: int = 3


class MemsNodeConfig(_Model):
    """Where a MEMS accelerometer is mounted: under a shelf or on a camera bracket."""

    id: str
    role: Literal["shelf", "camera_mount"]
    shelf: str | None = None
    slot: str | None = None
    cam: str | None = None

    @model_validator(mode="after")
    def _role_needs_target(self) -> MemsNodeConfig:
        if self.role == "shelf" and not self.shelf:
            raise ValueError(f"MEMS node {self.id!r} has role 'shelf' but no 'shelf:'")
        if self.role == "camera_mount" and not self.cam:
            raise ValueError(f"MEMS node {self.id!r} has role 'camera_mount' but no 'cam:'")
        return self


class SensorConfig(_Model):
    enabled: bool = False
    port: str = "COM5"
    baud: int = 115200
    tcp: str | None = None  # "host:port" to talk to the sensor simulator
    # slot -> load cell channel mapping for fusion
    cell_map: dict[str, str] = Field(default_factory=dict)
    # v2 (research/26 section 3.4)
    node: SensorNodeConfig = Field(default_factory=SensorNodeConfig)
    mems_nodes: list[MemsNodeConfig] = Field(default_factory=list)

    def has(self, sensor: str) -> bool:
        return self.enabled and sensor in self.node.enabled_sensors


class MqttConfig(_Model):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    # Also receive events published by other processes (serial bridge, ingest).
    listen: bool = False


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


def _readable(error: ValidationError, path: Path) -> str:
    lines = [f"invalid config {path}:"]
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"]) or "(top level)"
        lines.append(f"  - {where}: {item['msg']}")
    return "\n".join(lines)


def load_config(path: str | Path, secrets: str | Path | None = None) -> StoreMindConfig:
    """Load and validate a config.  Errors name the exact key, e.g.
    `cameras.0.fps: Input should be a valid number`.

    If `secrets` (or `configs/secrets.yaml` beside the config) exists, MQTT
    credentials missing from the config are filled from it.
    """
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"config not found: {path}")
    try:
        raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise SystemExit(f"config {path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise SystemExit(f"config {path} must be a mapping at the top level")
    try:
        config = StoreMindConfig(**raw)
    except ValidationError as error:
        raise SystemExit(_readable(error, path)) from error

    found = load_secrets(secrets if secrets is not None else path.parent / "secrets.yaml")
    mqtt = found.get("mqtt") or {}
    if config.mqtt.username is None and mqtt.get("username"):
        config.mqtt.username = mqtt["username"]
    if config.mqtt.password is None and mqtt.get("password"):
        config.mqtt.password = mqtt["password"]
    return config


def load_secrets(path: str | Path | None = None) -> dict[str, Any]:
    """Secrets from a git-ignored YAML file, overridden by environment variables.

    Environment variables: `STOREMIND_MQTT_USERNAME`, `STOREMIND_MQTT_PASSWORD`,
    `STOREMIND_CAM_<NAME>_USERNAME` / `_PASSWORD` (camera name upper-cased,
    `-` -> `_`).  The Qualcomm AI Hub token is *not* handled here: `qai-hub
    configure` or `QAI_HUB_API_TOKEN` only.
    """
    data: dict[str, Any] = {}
    if path is not None and Path(path).is_file():
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    mqtt = dict(data.get("mqtt") or {})
    for key in ("username", "password"):
        value = os.environ.get(f"STOREMIND_MQTT_{key.upper()}")
        if value:
            mqtt[key] = value
    data["mqtt"] = mqtt
    cameras = {name: dict(creds or {}) for name, creds in (data.get("cameras") or {}).items()}
    for name, creds in cameras.items():
        env = name.upper().replace("-", "_")
        for key in ("username", "password"):
            value = os.environ.get(f"STOREMIND_CAM_{env}_{key.upper()}")
            if value:
                creds[key] = value
    data["cameras"] = cameras
    return data


def save_config(config: StoreMindConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json", exclude_none=False), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
