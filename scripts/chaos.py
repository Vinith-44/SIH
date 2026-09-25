"""Chaos test: break things on purpose; the box must degrade and alert, not crash (M7b).

    python scripts/chaos.py                    # laptop: fake CCTV + simulator, ~4 minutes
    python scripts/chaos.py --only camera      # one fault

Faults (CLAUDE_CODE_PROMPT_V2 M7 "kill a stream, kill MQTT, unplug serial, cover
lens, fill disk"):

  camera   kill one fake-CCTV stream for --outage-s, then bring it back:
           the pipeline keeps running, the other cameras keep their FPS, the
           killed camera is reported not-ok and recovers by itself.
  serial   stop the STM32 simulator (= unplug the node) for longer than the
           30 s link timeout: NODE_HEALTH link=down + one SENSOR_LINK alert,
           then the bridge reconnects by itself when the node is back.
  disk     make every SQLite write fail ("database or disk is full") for
           --outage-s: the pipeline keeps running (events are dropped, not
           queued without bound), and writing resumes afterwards.
  mqtt     only with --mqtt-host: the broker restart case is run on the Pi
           (docs/HARDWARE_TODO.md "M8"), reported here as not run.
  lens     covering a real lens needs a real camera: not run here; the tamper
           check itself is unit-tested (health/monitor.py TamperDetector).

Writes storemind/storemind/eval/results/platform/chaos_<label>.json (bucket C:
fakes on the laptop).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import REPO, Harness  # noqa: E402

from storemind.core.events import EventType  # noqa: E402

RESULTS = REPO / "storemind" / "storemind" / "eval" / "results" / "platform"


def wait(predicate, timeout_s: float, step: float = 0.5) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def camera_fault(h: Harness, outage_s: float) -> list[dict]:
    cams = {c.config.name: c for c in h.pipeline.cameras}
    victim, other = "counter-1", "entrance"
    ok = wait(lambda: cams[victim].decoded > 20 and cams[other].decoded > 20, 60)
    before_other = cams[other].decoded
    h.cctv.stop_camera(h.camera_index(victim))
    t0 = time.monotonic()
    time.sleep(outage_s)
    during_victim = cams[victim].decoded
    other_kept_going = cams[other].decoded > before_other + 10
    reported_down = not h.pipeline.health.camera_ok.get(victim, True)
    h.cctv.start_camera(h.camera_index(victim))
    recovered = wait(lambda: cams[victim].decoded > during_victim + 10, 90)
    recover_s = round(time.monotonic() - t0 - outage_s, 1)
    return [
        {"metric": "camera: streams running before the fault", "value": "yes" if ok else "no", "target": "yes"},
        {"metric": f"camera: other cameras kept decoding during a {outage_s:g} s outage",
         "value": "yes" if other_kept_going else "no", "target": "yes"},
        {"metric": "camera: killed camera reported not-ok", "value": "yes" if reported_down else "no",
         "target": "yes"},
        {"metric": "camera: frames again after the stream came back",
         "value": f"yes, {recover_s} s" if recovered else "no", "target": "yes"},
        {"metric": "camera: pipeline alive", "value": "yes" if h.alive() else "no", "target": "yes"},
    ]


def serial_fault(h: Harness, outage_s: float) -> list[dict]:
    wait(lambda: h.bridge.link == "up", 30)
    alerts_before = h.events_of(EventType.ALERT)
    downs: list = []
    h.pipeline.bus.subscribe_types(EventType.NODE_HEALTH,
                                   lambda e: downs.append(e) if e.data["link"] == "down" else None)
    port = h.sim_port
    h.stop_sim()
    link_down = wait(lambda: h.bridge.link == "down", outage_s)
    alerted = h.events_of(EventType.ALERT) > alerts_before
    h.sim_port = port
    h.start_sim()
    back = wait(lambda: h.bridge.link == "up" and h.bridge.connected, 60)
    return [
        {"metric": "serial: link reported down (no $H for 30 s)", "value": "yes" if link_down else "no",
         "target": "yes"},
        {"metric": "serial: NODE_HEALTH link=down published", "value": str(len(downs)), "target": ">= 1"},
        {"metric": "serial: SENSOR_LINK alert raised", "value": "yes" if alerted else "no", "target": "yes"},
        {"metric": "serial: bridge reconnected by itself", "value": f"yes ({h.bridge.stats.reconnects} reconnects)"
         if back else "no", "target": "yes"},
        {"metric": "serial: pipeline alive", "value": "yes" if h.alive() else "no", "target": "yes"},
    ]


class _FullDisk:
    """Wraps the store's connection: every write raises 'database or disk is full'."""

    def __init__(self, conn) -> None:
        self._conn = conn

    def executemany(self, *args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)


def disk_fault(h: Harness, outage_s: float) -> list[dict]:
    store = h.pipeline.store
    store.flush()
    written_before = store.written
    real = store._conn                                    # noqa: SLF001 - fault injection
    store._conn = _FullDisk(real)                         # noqa: SLF001
    time.sleep(outage_s)
    written_during = store.written - written_before
    store._conn = real                                    # noqa: SLF001
    after = store.written
    resumed = wait(lambda: store.written > after, 60)
    return [
        {"metric": f"disk: writes during a {outage_s:g} s 'disk full'", "value": str(written_during),
         "target": "0 (dropped, not crashing)"},
        {"metric": "disk: pipeline alive while the disk was full", "value": "yes" if h.alive() else "no",
         "target": "yes"},
        {"metric": "disk: writing resumed afterwards", "value": "yes" if resumed else "no", "target": "yes"},
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["camera", "serial", "disk"], action="append", default=None)
    ap.add_argument("--outage-s", type=float, default=40.0)
    ap.add_argument("--rtsp-port", type=int, default=8556)
    ap.add_argument("--api-port", type=int, default=8767)
    ap.add_argument("--mqtt-host", default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.ERROR)

    faults = args.only or ["camera", "serial", "disk"]
    tmp = tempfile.TemporaryDirectory(prefix="storemind_chaos_", ignore_cleanup_errors=True)
    h = Harness(db_path=Path(tmp.name) / "chaos.db", rtsp_port=args.rtsp_port, api_port=args.api_port,
                sim_speed=1.0)
    h.start(run_seconds=3600)
    rows: list[dict] = []
    try:
        wait(lambda: h.bridge.connected, 30)
        for fault in faults:
            print(f"--- {fault}", flush=True)
            result = {"camera": camera_fault, "serial": serial_fault, "disk": disk_fault}[fault](h, args.outage_s)
            for r in result:
                print(f"  {r['metric']}: {r['value']}  (target {r['target']})", flush=True)
            rows += result
    finally:
        h.stop()
    rows.append({"metric": "mqtt: broker restart", "value": "not run (no broker on the laptop)"
                 if not args.mqtt_host else "not implemented here", "target": "run on the Pi"})
    rows.append({"metric": "lens: camera covered", "value": "not run (needs a real camera)",
                 "target": "run on the Pi"})

    def failed(r: dict) -> bool:
        target, value = r["target"], r["value"]
        if target == "yes":
            return not value.startswith("yes")
        if target == ">= 1":
            return int(value) < 1
        if target.startswith("0 "):
            return value != "0"
        return False

    failures = [r["metric"] for r in rows if failed(r)]
    report = {
        "title": "Chaos test - laptop with fake CCTV + STM32 simulator",
        "bucket": "C",
        "device": "ASUS Vivobook 15 (Ram's laptop)",
        "note": ("PASS" if not failures else "FAIL: " + "; ".join(failures))
                + f". {args.outage_s:g} s outages; pipeline + bridge + API in one process (scripts/harness.py).",
        "rows": rows,
        "commands": ["python scripts/chaos.py " + " ".join(argv if argv is not None else sys.argv[1:])],
    }
    if not args.no_write:
        label = args.label or f"{datetime.now():%Y%m%d}_laptop"
        out = RESULTS / f"chaos_{label}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out.relative_to(REPO)}")
    print("RESULT:", "PASS" if not failures else f"FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
