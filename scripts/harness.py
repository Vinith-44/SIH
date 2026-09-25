"""Shared harness for the soak and chaos tests (M7b).

Starts, in one process, what `run.py --live --api --sensors` runs on a box:
the pipeline on live RTSP cameras, the serial bridge, and the dashboard API,
plus the fakes that stand in for the store: fake CCTV (MediaMTX + ffmpeg
looping our synthetic clips, M2) and the STM32 simulator (M5).  Everything can
be stopped and restarted from outside, which is what the chaos test does.

Used by scripts/soak.py and scripts/chaos.py; not a product entry point.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STOREMIND = REPO / "storemind"
sys.path.insert(0, str(STOREMIND))

from storemind.alerts.manager import TowerLightSink  # noqa: E402
from storemind.core.config import CameraConfig, MemsNodeConfig, StoreMindConfig, load_config  # noqa: E402
from storemind.core.events import EventType  # noqa: E402
from storemind.pipeline import Pipeline  # noqa: E402
from storemind.sensors.bridge import SensorBridge, TcpTransport  # noqa: E402
from storemind.sensors.simulator import SimulatorServer, VirtualNode  # noqa: E402

log = logging.getLogger("harness")

CLIPS = {
    "entrance": REPO / "videos" / "entrance" / "synthetic_entrance.mp4",
    "counter-1": REPO / "videos" / "queue" / "synthetic_queue.mp4",
    "shelf-a": REPO / "videos" / "shelf" / "synthetic_shelf.mp4",
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def load_fake_cctv():
    path = STOREMIND / "tools" / "fake_cctv.py"
    spec = importlib.util.spec_from_file_location("fake_cctv", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fake_cctv"] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class Harness:
    db_path: Path
    rtsp_port: int = 8554
    api_port: int = 8766
    sim_speed: float = 1.0
    use_cctv: bool = True
    detector: str = "stub"
    config_path: Path | None = None
    cctv: object | None = None
    sim: SimulatorServer | None = None
    sim_port: int = 0
    pipeline: Pipeline | None = None
    bridge: SensorBridge | None = None
    counts: dict[str, int] = field(default_factory=dict)
    _thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    def build_config(self) -> StoreMindConfig:
        config = (load_config(self.config_path) if self.config_path
                  else load_config(STOREMIND / "configs" / "demo.yaml"))
        config.storage.db_path = str(self.db_path)
        config.detector.backend = self.detector
        config.detector.model = ""
        config.api.port = self.api_port
        config.sensors.enabled = True
        config.sensors.tcp = f"127.0.0.1:{self.sim_port}"
        config.sensors.cell_map = {"shelf-a/A1": "1", "shelf-a/A2": "2"}
        config.sensors.mems_nodes = [MemsNodeConfig(id="m1", role="shelf", shelf="shelf-a", slot="A1"),
                                     MemsNodeConfig(id="m2", role="camera_mount", cam="entrance")]
        if self.use_cctv:
            order = list(CLIPS)
            for cam in config.cameras:
                if cam.name in order:           # fake CCTV names its streams cam1..camN
                    cam.source = f"rtsp://127.0.0.1:{self.rtsp_port}/cam{order.index(cam.name) + 1}"
        return config

    def start_cctv(self) -> None:
        fake = load_fake_cctv()
        missing = [str(p) for p in CLIPS.values() if not p.is_file()]
        if missing:
            raise SystemExit(f"synthetic clips missing: {missing} - run storemind/tools/make_synthetic_video.py")
        self.cctv = fake.FakeCCTV(videos=list(CLIPS.values()), port=self.rtsp_port, fps=10)
        self.cctv.start()

    def camera_index(self, name: str) -> int:
        return list(CLIPS).index(name) + 1

    def start_sim(self) -> None:
        node = VirtualNode(seed=int(time.time()) % 1000)
        self.sim = SimulatorServer(node, port=self.sim_port, speed=self.sim_speed, scenario="demo").start()
        self.sim_port = self.sim.port

    def stop_sim(self) -> None:
        if self.sim is not None:
            self.sim.stop()
            self.sim = None

    def start(self, run_seconds: float) -> None:
        if self.use_cctv:
            self.start_cctv()
        self.start_sim()
        config = self.build_config()
        self.pipeline = Pipeline(config, replay=False, realtime=True)
        self.bridge = SensorBridge(config, self.pipeline.bus, TcpTransport(config.sensors.tcp)).start()
        self.pipeline.sensor_bridge = self.bridge
        self.pipeline.alerts.add_sink(TowerLightSink(self.bridge))

        def count(event) -> None:
            self.counts[event.type.value] = self.counts.get(event.type.value, 0) + 1

        self.pipeline.bus.subscribe_all(count)
        from storemind.api.server import serve_in_thread

        serve_in_thread(self.pipeline, host="127.0.0.1", port=self.api_port)
        self._thread = threading.Thread(
            target=lambda: self.pipeline.run(max_seconds=run_seconds, progress=False),
            name="pipeline", daemon=True)
        self._thread.start()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        if self.bridge is not None:
            self.bridge.stop()
        self.stop_sim()
        if self._thread is not None:
            self._thread.join(timeout=15)
        if self.cctv is not None:
            try:
                self.cctv.stop()
            except Exception:
                log.exception("fake CCTV stop failed")
        if self.pipeline is not None:
            self.pipeline.close()

    # ------------------------------------------------------------------ #
    def events_of(self, event_type: EventType) -> int:
        return self.counts.get(event_type.value, 0)


_PROC = None


def process_stats() -> dict:
    """RSS, threads, handles and CPU % of this process.  One psutil.Process is
    kept, because cpu_percent() measures since the previous call on the same object."""
    global _PROC
    import psutil

    if _PROC is None:
        _PROC = psutil.Process(os.getpid())
        _PROC.cpu_percent(interval=None)
    proc = _PROC
    with proc.oneshot():
        mem = proc.memory_info()
        stats = {"rss_mb": round(mem.rss / 1e6, 1), "threads": proc.num_threads(),
                 "cpu_percent": proc.cpu_percent(interval=None)}
        try:
            stats["handles"] = proc.num_handles() if hasattr(proc, "num_handles") else proc.num_fds()
        except Exception:
            stats["handles"] = None
    return stats


def db_sizes(db_path: Path) -> dict:
    def size(p: Path) -> float:
        return round(p.stat().st_size / 1e6, 2) if p.exists() else 0.0
    return {"db_mb": size(db_path), "wal_mb": size(db_path.with_name(db_path.name + "-wal"))}


__all__ = ["Harness", "CameraConfig", "process_stats", "db_sizes", "free_port"]
