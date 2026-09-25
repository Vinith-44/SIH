"""End-to-end smoke test for the STM32 sensor path (M5).

    python tools/e2e_sensors.py              # ~20 s, no hardware needed

Starts the STM32 simulator on TCP, connects the serial bridge to it, stores
every event in a temporary SQLite database (the same EventStore the pipeline
uses), then checks:
  1. the bridge connects and the node heartbeat (NODE_HEALTH) arrives,
  2. every sensor event type the node can produce reaches SQLite,
  3. a command round-trips (LED on, $K OK),
  4. killing the simulator is survived and the bridge reconnects by itself,
  5. zero checksum / framing errors on a clean link.
Exit code 0 = all passed, 1 = a check failed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "storemind"))

from storemind.core.bus import EventBus  # noqa: E402
from storemind.core.config import MemsNodeConfig, StoreMindConfig  # noqa: E402
from storemind.sensors.bridge import SensorBridge, TcpTransport  # noqa: E402
from storemind.sensors.simulator import SimulatorServer, VirtualNode  # noqa: E402
from storemind.store.db import EventStore  # noqa: E402

WANTED = {"WEIGHT", "SHELF_MOTION", "CAMERA_MOUNT", "BEAM_CROSS", "PRESENCE",
          "ENVIRONMENT", "NODE_HEALTH", "SENSOR"}


def wait_until(pred, timeout_s: float, step_s: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step_s)
    return pred()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speed", type=float, default=20.0, help="simulated MCU seconds per real second")
    args = ap.parse_args(argv)

    config = StoreMindConfig(store="e2e")
    config.sensors.enabled = True
    config.sensors.mems_nodes = [MemsNodeConfig(id="m1", role="shelf", shelf="shelf-a", slot="A1"),
                                 MemsNodeConfig(id="m2", role="camera_mount", cam="entrance")]
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        node = VirtualNode(seed=11)
        server = SimulatorServer(node, port=0, speed=args.speed, scenario="demo").start()
        port = server.port
        store = EventStore(str(Path(tmp) / "e2e.db"), store="e2e")
        bus = EventBus()
        bus.subscribe_all(store.handle)
        bridge = SensorBridge(config, bus, TcpTransport(f"127.0.0.1:{port}")).start()
        try:
            check("bridge connects", wait_until(lambda: bridge.connected, 10))
            check("heartbeat arrives", wait_until(lambda: bridge.link == "up", 10), f"link={bridge.link}")

            def stored() -> set[str]:
                store.flush() if hasattr(store, "flush") else None
                return set(store.counts_by_type())

            got = wait_until(lambda: WANTED <= stored(), 20)
            check("every sensor event type in SQLite", got, f"missing={sorted(WANTED - stored())}")

            result = bridge.send_command("L", ["ON"])
            check("command round-trip", result.status == "OK" and node.led == "ON",
                  f"status={result.status} attempts={result.attempts}")

            server.stop()
            time.sleep(1.0)
            node2 = VirtualNode(seed=12)
            server = SimulatorServer(node2, port=port, speed=args.speed, scenario="rush").start()
            back = wait_until(lambda: bridge.stats.reconnects >= 1 and bridge.connected, 20)
            before = bridge.stats.events
            flowing = wait_until(lambda: bridge.stats.events > before, 10)
            check("reconnects after the node vanishes", back and flowing,
                  f"reconnects={bridge.stats.reconnects}")

            stats = bridge.stats
            check("clean link has zero errors", stats.crc_err == 0 and stats.rx_err == 0,
                  f"crc_err={stats.crc_err} rx_err={stats.rx_err} lost={stats.lost}")
            print(f"events stored: {store.counts_by_type()}")
        finally:
            bridge.stop()
            server.stop()
            close = getattr(store, "close", None)
            if close:
                close()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
