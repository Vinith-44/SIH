"""M7b operations: DB maintenance, sd_notify, deploy files, soak maths."""

from __future__ import annotations

import configparser
import importlib.util
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from storemind.core.events import EventType, SensorData, make_event
from storemind.health.systemd import Notifier
from storemind.store.db import EventStore
from storemind.store.maintenance import maintain

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy" / "pi5"


def test_maintenance_purges_old_events_and_truncates_the_wal(tmp_path):
    db = tmp_path / "events.db"
    store = EventStore(db, store="s", retention_days=30)
    now = datetime(2026, 9, 25, 3, 30, tzinfo=timezone.utc)
    for days_ago in (1, 5, 40, 60):
        store.handle(make_event(ts=now - timedelta(days=days_ago), store="s", node="n", type=EventType.SENSOR,
                                data=SensorData(node="n", sensor="restock", channel="a", value=1, unit="press")))
    store.flush()
    store.close()
    dry = maintain(db, 30, dry_run=True, now=now.replace(tzinfo=None))
    assert dry["events_older_than_retention"] == 2 and dry["deleted"] == 0
    report = maintain(db, 30, now=now.replace(tzinfo=None))
    assert report["deleted"] == 2
    assert report["wal_after_mb"] == 0.0
    again = maintain(db, 30, now=now.replace(tzinfo=None))
    assert again["deleted"] == 0


def test_maintenance_cli_without_a_database_is_a_no_op(tmp_path, capsys):
    from storemind.store.maintenance import main

    assert main(["--db", str(tmp_path / "missing.db")]) == 0
    assert "nothing to do" in capsys.readouterr().out


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="AF_UNIX datagram")
def test_notifier_talks_to_systemd_socket(tmp_path):
    path = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    try:
        notifier = Notifier(env={"NOTIFY_SOCKET": path, "WATCHDOG_USEC": "2000000"})
        assert notifier.ready("ok") and server.recv(64).startswith(b"READY=1")
        assert notifier.watchdog(now=100.0) and server.recv(64) == b"WATCHDOG=1"
        assert not notifier.watchdog(now=100.5)            # rate limited to half the period
    finally:
        server.close()


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(DEPLOY / "systemd" / name, encoding="utf-8")
    return parser


@pytest.mark.parametrize("name", ["storemind-pipeline.service", "storemind-bridge.service"])
def test_python_services_use_the_watchdog_and_restart(name):
    unit = _unit(name)
    service = unit["Service"]
    assert service["Type"] == "notify"
    assert int(service["WatchdogSec"].rstrip("s")) >= 30
    assert service["Restart"] == "always"
    assert "NotifyAccess" in service
    assert "--headless" in service["ExecStart"] or "sensors.bridge" in service["ExecStart"]


def test_every_unit_file_parses_and_the_install_script_installs_it():
    units = sorted(p.name for p in (DEPLOY / "systemd").iterdir())
    assert {"storemind-pipeline.service", "storemind-bridge.service", "storemind-go2rtc.service",
            "storemind-maintenance.service", "storemind-maintenance.timer"} <= set(units)
    script = (REPO / "scripts" / "install_pi5.sh").read_text(encoding="utf-8")
    for name in units:
        _unit(name)
        assert name in script or "systemd/*" in script


def test_soak_slope_maths():
    spec = importlib.util.spec_from_file_location("soak_mod", REPO / "scripts" / "soak.py")
    # soak.py imports the harness (which imports the pipeline); only the maths is tested here.
    source = (REPO / "scripts" / "soak.py").read_text(encoding="utf-8")
    namespace: dict = {}
    start = source.index("def slope_per_hour")
    end = source.index("def api_ok")
    exec(source[start:end], namespace)
    slope = namespace["slope_per_hour"]
    assert slope([0, 1800, 3600], [100, 105, 110]) == pytest.approx(10.0)
    assert slope([0], [1]) == 0.0
    assert spec is not None


def test_bridge_reports_its_threads_for_the_watchdog():
    from storemind.core.bus import EventBus
    from storemind.core.config import StoreMindConfig
    from storemind.sensors.bridge import SensorBridge
    from storemind.sensors.simulator import LoopbackTransport, VirtualNode

    config = StoreMindConfig()
    config.sensors.enabled = True
    bridge = SensorBridge(config, EventBus(), LoopbackTransport(VirtualNode()))
    assert not bridge.threads_alive()            # not started: no pings
    bridge.start()
    try:
        assert bridge.threads_alive()
    finally:
        bridge.stop()
    assert not bridge.threads_alive()
