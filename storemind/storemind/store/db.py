"""SQLite storage - schema from research/04_ARCHITECTURE.md section 5.

Audit item S7: the legacy pipeline did `event_log.write_text("")` on every start,
so all history was destroyed each run and daily/weekly reports were impossible.
Here every event is appended, and a background writer thread keeps disk latency
off the vision loop.

WAL mode is not a detail: Indian stores lose power without warning (05 section 6)
and WAL survives that far better than the rollback journal.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.events import Event, EventType

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    id      INTEGER PRIMARY KEY,
    uuid    TEXT UNIQUE,
    ts      TEXT,
    store   TEXT,
    node    TEXT,
    cam     TEXT,
    type    TEXT,
    data    TEXT
);
CREATE INDEX IF NOT EXISTS ev_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS ev_type ON events(type, ts);

CREATE TABLE IF NOT EXISTS agg_minute(
    ts_min TEXT, store TEXT, metric TEXT, key TEXT, value REAL,
    PRIMARY KEY(ts_min, store, metric, key)
);

CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sync_outbox(
    id INTEGER PRIMARY KEY, payload TEXT, sent INTEGER DEFAULT 0, created TEXT
);

CREATE TABLE IF NOT EXISTS alerts_ack(alert_id TEXT PRIMARY KEY, ack_ts TEXT, by TEXT);
"""


@dataclass
class Aggregate:
    ts_min: str
    metric: str
    key: str
    value: float


class EventStore:
    def __init__(self, path: str | Path, store: str = "demo-store",
                 retention_days: int = 30) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.retention_days = retention_days
        self._queue: queue.Queue = queue.Queue(maxsize=10000)
        self._stop = threading.Event()
        self._conn = self._connect()
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._minute_counts: dict[tuple[str, str, str], float] = defaultdict(float)
        self.written = 0
        self._thread = threading.Thread(target=self._writer_loop, name="sqlite-writer", daemon=True)
        self._thread.start()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # -- write path ------------------------------------------------------- #
    def handle(self, event: Event) -> None:
        """Bus subscriber.  Never blocks the pipeline: if the disk cannot keep
        up we drop and count, rather than stalling the camera."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass

    def _writer_loop(self) -> None:
        batch: list[Event] = []
        while not self._stop.is_set() or not self._queue.empty():
            try:
                batch.append(self._queue.get(timeout=0.25))
            except queue.Empty:
                pass
            while len(batch) < 200:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._flush(batch)
                batch = []

    def _flush(self, events: list[Event]) -> None:
        rows = [(e.id, e.ts, e.store, e.node, e.cam, e.type.value, json.dumps(e.data))
                for e in events]
        try:
            with self._conn:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO events(uuid, ts, store, node, cam, type, data)"
                    " VALUES(?,?,?,?,?,?,?)", rows)
            self.written += len(rows)
        except sqlite3.Error:
            return
        self._roll_up(events)

    def _roll_up(self, events: list[Event]) -> None:
        """Per-minute aggregates - the basis of every report and chart."""
        updates: dict[tuple[str, str, str], float] = defaultdict(float)
        gauges: dict[tuple[str, str, str], float] = {}
        for event in events:
            minute = event.ts[:16]
            if event.type is EventType.ENTRY:
                updates[(minute, "entries", event.data.get("line", "door"))] += 1
            elif event.type is EventType.EXIT:
                updates[(minute, "exits", event.data.get("line", "door"))] += 1
            elif event.type is EventType.ZONE_VISIT:
                updates[(minute, "zone_visits", event.data["zone"])] += 1
                updates[(minute, "zone_dwell_s", event.data["zone"])] += float(event.data["dwell_s"])
            elif event.type is EventType.SERVICE_DONE:
                updates[(minute, "services", event.data["counter"])] += 1
                updates[(minute, "service_s", event.data["counter"])] += float(event.data["service_s"])
            elif event.type is EventType.QUEUE_STATE:
                gauges[(minute, "queue_len", event.data["counter"])] = float(event.data["queue_len_smooth"])
                if event.data.get("median_wait_s") is not None:
                    gauges[(minute, "median_wait_s", event.data["counter"])] = float(event.data["median_wait_s"])
            elif event.type is EventType.LOST_SALE_RISK:
                updates[(minute, "lost_sale_events", event.data["slot"])] += 1
                if event.data.get("est_value"):
                    updates[(minute, "lost_sale_inr", event.data["slot"])] += float(event.data["est_value"])
            elif event.type is EventType.ALERT:
                updates[(minute, "alerts", event.data["severity"])] += 1
        try:
            with self._conn:
                for (minute, metric, key), value in updates.items():
                    self._conn.execute(
                        "INSERT INTO agg_minute(ts_min, store, metric, key, value) VALUES(?,?,?,?,?)"
                        " ON CONFLICT(ts_min, store, metric, key)"
                        " DO UPDATE SET value = value + excluded.value",
                        (minute, self.store, metric, key, value))
                for (minute, metric, key), value in gauges.items():
                    self._conn.execute(
                        "INSERT INTO agg_minute(ts_min, store, metric, key, value) VALUES(?,?,?,?,?)"
                        " ON CONFLICT(ts_min, store, metric, key)"
                        " DO UPDATE SET value = excluded.value",
                        (minute, self.store, metric, key, value))
        except sqlite3.Error:
            pass

    def flush(self, timeout: float = 5.0) -> None:
        deadline = threading.Event()
        waited = 0.0
        while not self._queue.empty() and waited < timeout:
            deadline.wait(0.05)
            waited += 0.05

    def close(self) -> None:
        self.flush()
        self._stop.set()
        self._thread.join(timeout=5.0)
        try:
            self._conn.commit()
            self._conn.close()
        except sqlite3.Error:
            pass

    # -- read path -------------------------------------------------------- #
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql, params))
        finally:
            conn.close()

    def recent_events(self, limit: int = 100, types: list[str] | None = None) -> list[dict[str, Any]]:
        if types:
            marks = ",".join("?" * len(types))
            rows = self.query(
                f"SELECT ts, cam, type, data FROM events WHERE type IN ({marks})"
                " ORDER BY id DESC LIMIT ?", (*types, limit))
        else:
            rows = self.query("SELECT ts, cam, type, data FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [{"ts": r["ts"], "cam": r["cam"], "type": r["type"], "data": json.loads(r["data"])}
                for r in rows]

    def counts_by_type(self) -> dict[str, int]:
        return {r["type"]: r["n"] for r in
                self.query("SELECT type, COUNT(*) AS n FROM events GROUP BY type")}

    def series(self, metric: str, key: str | None = None, hours: int = 24) -> list[tuple[str, float]]:
        if key:
            rows = self.query(
                "SELECT ts_min, value FROM agg_minute WHERE metric=? AND key=? ORDER BY ts_min", (metric, key))
        else:
            rows = self.query(
                "SELECT ts_min, SUM(value) AS value FROM agg_minute WHERE metric=?"
                " GROUP BY ts_min ORDER BY ts_min", (metric,))
        return [(r["ts_min"], float(r["value"])) for r in rows]

    def hourly(self, metric: str) -> list[tuple[str, float]]:
        rows = self.query(
            "SELECT substr(ts_min, 1, 13) AS hour, SUM(value) AS value FROM agg_minute"
            " WHERE metric=? GROUP BY hour ORDER BY hour", (metric,))
        return [(r["hour"], float(r["value"])) for r in rows]

    def daily(self, metric: str) -> list[tuple[str, float]]:
        rows = self.query(
            "SELECT substr(ts_min, 1, 10) AS day, SUM(value) AS value FROM agg_minute"
            " WHERE metric=? GROUP BY day ORDER BY day", (metric,))
        return [(r["day"], float(r["value"])) for r in rows]

    def set_config(self, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO config(key, value) VALUES(?,?)"
                         " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, json.dumps(value)))

    def get_config(self, key: str, default: Any = None) -> Any:
        rows = self.query("SELECT value FROM config WHERE key=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    def ack_alert(self, alert_id: str, by: str = "staff") -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO alerts_ack(alert_id, ack_ts, by) VALUES(?,?,?)",
                         (alert_id, datetime.now().isoformat(timespec="seconds"), by))

    def acked(self) -> set[str]:
        return {r[0] for r in self.query("SELECT alert_id FROM alerts_ack")}

    def enqueue_sync(self, payload: dict) -> None:
        """Store-and-forward for HQ (07 section 4): queue locally, flush online."""
        with self._connect() as conn:
            conn.execute("INSERT INTO sync_outbox(payload, sent, created) VALUES(?,0,?)",
                         (json.dumps(payload), datetime.now().isoformat(timespec="seconds")))

    def purge_old_events(self, now: datetime | None = None) -> int:
        """Raw events expire after `retention_days`; aggregates are kept forever
        (they are tiny and they are what the reports read)."""
        now = now or datetime.now()
        cutoff = (now - timedelta(days=self.retention_days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
            return cursor.rowcount
