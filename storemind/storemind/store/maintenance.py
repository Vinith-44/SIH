"""Nightly database maintenance (M7b): retention, WAL checkpoint, compaction.

    python -m storemind.store.maintenance --config /etc/storemind/store.yaml
    python -m storemind.store.maintenance --db data/storemind.db --retention-days 30 --dry-run

Run by `storemind-maintenance.timer` at 03:30 (deploy/pi5/).  It is safe while
the pipeline is running: SQLite WAL mode lets this process write while the
pipeline writes, and every step is one short transaction.

1. Raw events older than `storage.retention_days` are deleted
   (`EventStore.purge_old_events`, which nothing else calls); the per-minute
   aggregates the reports read are kept.
2. `PRAGMA wal_checkpoint(TRUNCATE)` folds the write-ahead log back into the
   database so the -wal file cannot grow without bound.
3. `PRAGMA optimize`, and `VACUUM` only when more than `--vacuum-free-pct` of
   the file is free pages (VACUUM rewrites the whole file, so not every night).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _size_mb(path: Path) -> float:
    return round(path.stat().st_size / 1e6, 3) if path.exists() else 0.0


def maintain(db_path: str | Path, retention_days: int, *, vacuum_free_pct: float = 25.0,
             dry_run: bool = False, now: datetime | None = None) -> dict:
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    wal = db_path.with_name(db_path.name + "-wal")
    report: dict = {"db": str(db_path), "before_mb": _size_mb(db_path), "wal_before_mb": _size_mb(wal)}
    cutoff = ((now or datetime.now()) - timedelta(days=retention_days)).isoformat()
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        old = conn.execute("SELECT COUNT(*) FROM events WHERE ts < ?", (cutoff,)).fetchone()[0]
        report["events_older_than_retention"] = old
        if not dry_run and old:
            with conn:
                conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        report["deleted"] = 0 if dry_run else old
        if not dry_run:
            busy, log_frames, checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            report["wal_checkpoint"] = {"busy": busy, "log_frames": log_frames, "checkpointed": checkpointed}
            conn.execute("PRAGMA optimize")
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        free_pct = 100.0 * free / pages if pages else 0.0
        report["free_pct"] = round(free_pct, 1)
        report["vacuumed"] = False
        if not dry_run and free_pct > vacuum_free_pct:
            conn.execute("VACUUM")
            report["vacuumed"] = True
    finally:
        conn.close()
    report["after_mb"] = _size_mb(db_path)
    report["wal_after_mb"] = _size_mb(wal)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser("storemind.store.maintenance", description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=None, help="store config: storage.db_path + storage.retention_days")
    ap.add_argument("--db", default=None, help="database path (overrides the config)")
    ap.add_argument("--retention-days", type=int, default=None)
    ap.add_argument("--vacuum-free-pct", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    db, days = args.db, args.retention_days
    if args.config:
        from ..core.config import load_config

        config = load_config(args.config)
        db = db or config.storage.db_path
        days = days if days is not None else config.storage.retention_days
    if db is None:
        ap.error("--db or --config is required")
    try:
        report = maintain(db, days if days is not None else 30, vacuum_free_pct=args.vacuum_free_pct,
                          dry_run=args.dry_run)
    except FileNotFoundError as error:
        print(f"no database yet at {error}; nothing to do")
        return 0
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
