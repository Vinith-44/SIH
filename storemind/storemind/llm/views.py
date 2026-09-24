"""Whitelisted, read-only views over the event store for "Ask your store" (M10).

The event database (`store/db.py`, Ram's) keeps every event as JSON in one
`events` table.  For questions we expose a few flat views instead, created as
TEMP views on a **read-only** connection (so nothing is written to the store),
and a SQLite authorizer allows exactly one thing: SELECT from these views.
Raw tables, writes, ATTACH, PRAGMA and anything else are refused by SQLite
itself - not by parsing the model's SQL and hoping.

Times: `ts` is the event's ISO timestamp with offset; `day` is 'YYYY-MM-DD' and
`hour` 0-23 in the store's local time (as recorded).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

VIEWS: dict[str, tuple[str, str]] = {
    # name: (SQL, one-line description for the model's prompt)
    "entries": ("""SELECT ts, substr(ts,1,10) AS day, CAST(substr(ts,12,2) AS INTEGER) AS hour, cam,
                   json_extract(data,'$.direction') AS direction
                   FROM events WHERE type IN ('ENTRY','EXIT')""",
                "one row per person crossing the door: ts, day, hour, cam, direction ('in' = entered, 'out' = left)"),
    "queue": ("""SELECT ts, substr(ts,1,10) AS day, CAST(substr(ts,12,2) AS INTEGER) AS hour,
                 json_extract(data,'$.counter') AS counter, json_extract(data,'$.queue_len') AS people_waiting,
                 json_extract(data,'$.queue_parties') AS parties_waiting,
                 json_extract(data,'$.median_wait_s') AS median_wait_s,
                 json_extract(data,'$.wait_littles_s') AS littles_wait_s
                 FROM events WHERE type = 'QUEUE_STATE'""",
              ("queue snapshots every few seconds: ts, day, hour, counter, people_waiting, parties_waiting, "
              "median_wait_s, littles_wait_s")),
    "services": ("""SELECT ts, substr(ts,1,10) AS day, CAST(substr(ts,12,2) AS INTEGER) AS hour,
                    json_extract(data,'$.counter') AS counter, json_extract(data,'$.service_s') AS service_s,
                    json_extract(data,'$.wait_s') AS wait_s
                    FROM events WHERE type = 'SERVICE_DONE'""",
                 "one row per customer served at a billing counter: ts, day, hour, counter, service_s, wait_s"),
    "slots": ("""SELECT ts, substr(ts,1,10) AS day, json_extract(data,'$.shelf') AS shelf,
                 json_extract(data,'$.slot') AS slot, json_extract(data,'$.sku') AS sku,
                 json_extract(data,'$.state') AS state, json_extract(data,'$.reason') AS reason
                 FROM events WHERE type = 'SLOT_STATE'""",
              ("shelf slot state changes: ts, day, shelf, slot, sku (product), state (FULL/LOW/EMPTY/WRONG_ITEM/"
              "UNKNOWN), reason. EMPTY = out of stock, LOW = running low. The current state of a slot is its latest "
              "row")),
    "alerts": ("""SELECT ts, substr(ts,1,10) AS day, json_extract(data,'$.severity') AS severity,
                  json_extract(data,'$.message_key') AS alert_key, json_extract(data,'$.message') AS message
                  FROM events WHERE type = 'ALERT'""",
               "alerts raised: ts, day, severity (INFO/WARN/CRITICAL), alert_key, message"),
    "lost_sales": ("""SELECT ts, substr(ts,1,10) AS day, json_extract(data,'$.shelf') AS shelf,
                      json_extract(data,'$.slot') AS slot, json_extract(data,'$.sku') AS sku,
                      json_extract(data,'$.dwell_s') AS dwell_s, json_extract(data,'$.est_value') AS est_value_inr
                      FROM events WHERE type = 'LOST_SALE_RISK'""",
                   ("a shopper looked at an EMPTY/LOW slot: ts, day, shelf, slot, sku, dwell_s, est_value_inr "
                   "(an estimate of rupees at risk, not measured revenue)")),
    "picks": ("""SELECT ts, substr(ts,1,10) AS day, json_extract(data,'$.shelf') AS shelf,
                 json_extract(data,'$.slot') AS slot, json_extract(data,'$.action') AS action,
                 json_extract(data,'$.units') AS units, json_extract(data,'$.grams') AS grams
                 FROM events WHERE type = 'PICKUP'""",
              "load-cell shelf interactions: ts, day, shelf, slot, action (pick/put_back/touch), units, grams"),
    "zone_visits": ("""SELECT ts, substr(ts,1,10) AS day, CAST(substr(ts,12,2) AS INTEGER) AS hour,
                       json_extract(data,'$.zone') AS zone, json_extract(data,'$.dwell_s') AS dwell_s
                       FROM events WHERE type = 'ZONE_VISIT'""",
                    "a shopper stayed in a zone (e.g. promo end-cap): ts, day, hour, zone, dwell_s"),
}

MAX_ROWS = 50
MAX_SECONDS = 2.0

# Plain, side-effect-free SQL functions.  Anything else (load_extension,
# readfile, fts helpers, ...) is refused even where the build provides it.
SAFE_FUNCTIONS = frozenset({
    "count", "sum", "total", "avg", "min", "max", "group_concat", "round", "abs", "coalesce", "ifnull",
    "nullif", "iif", "lower", "upper", "length", "substr", "substring", "instr", "replace", "trim", "ltrim",
    "rtrim", "printf", "format", "date", "time", "datetime", "julianday", "strftime", "unixepoch",
    "json_extract", "cast", "typeof",
})


def schema_text() -> str:
    """What the model is told about the data (names and meanings only)."""
    return "\n".join(f"- {name}: {desc}" for name, (_sql, desc) in VIEWS.items())


class QueryRefused(Exception):
    pass


def open_readonly(db_path: str | Path) -> sqlite3.Connection:
    """A read-only connection with the TEMP views and a strict authorizer."""
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    for name, (sql, _desc) in VIEWS.items():
        conn.execute(f"CREATE TEMP VIEW {name} AS {sql}")

    def authorizer(action, arg1, arg2, db_name, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # A view's own reads of `events` are allowed; a direct read is not.
            if arg1 in VIEWS or (arg1 == "events" and source in VIEWS):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if (arg2 or "").lower() in SAFE_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    return conn


def run_query(conn: sqlite3.Connection, sql: str) -> tuple[list[str], list[tuple]]:
    """Execute one SELECT with a time and row limit.  Raises QueryRefused."""
    statement = sql.strip().rstrip(";").strip()
    if ";" in statement:
        raise QueryRefused("only one statement is allowed")
    if not statement.lower().startswith(("select", "with")):
        raise QueryRefused("only SELECT queries are allowed")
    deadline = time.monotonic() + MAX_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cursor = conn.execute(statement)
        rows = cursor.fetchmany(MAX_ROWS)
    except sqlite3.DatabaseError as error:
        raise QueryRefused(str(error)) from error
    finally:
        conn.set_progress_handler(None, 0)
    columns = [d[0] for d in cursor.description or []]
    return columns, rows
