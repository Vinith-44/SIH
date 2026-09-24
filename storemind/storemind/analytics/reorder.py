"""Reorder drafts from shelf states (M3): offline, SQLite, WhatsApp-ready text.

When a slot goes EMPTY or LOW, a draft line is opened ("Atta 5kg - OUT since
14:05").  When it is FULL again (restocked), the line is closed.  The owner
copies the open list into WhatsApp for the distributor, which is how most kirana
reordering already works; an ONDC order is a later step.

Drafts are suggestions, never orders: nothing is sent anywhere automatically.
The table lives in its own small SQLite file next to the event database, so it
does not touch the event store's schema (Person B's `store/`).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ..core.events import Event, EventType

SCHEMA = """
CREATE TABLE IF NOT EXISTS reorder_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shelf TEXT NOT NULL,
    slot TEXT NOT NULL,
    sku TEXT,
    state TEXT NOT NULL,            -- EMPTY | LOW at the time it was last seen
    opened_ts TEXT NOT NULL,
    updated_ts TEXT NOT NULL,
    closed_ts TEXT,                 -- set when the slot is FULL again
    suggested_qty INTEGER
);
CREATE INDEX IF NOT EXISTS open_drafts ON reorder_drafts(shelf, slot) WHERE closed_ts IS NULL;
"""


class ReorderQueue:
    def __init__(self, path: str | Path = ":memory:", default_qty: int | None = None) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.default_qty = default_qty
        self.facings: dict[tuple[str, str], int] = {}

    def on_event(self, event: Event) -> None:
        if event.type is not EventType.SLOT_STATE:
            return
        data = event.data
        key = (data["shelf"], data["slot"])
        state = data["state"]
        row = self.db.execute("SELECT id FROM reorder_drafts WHERE shelf=? AND slot=? AND closed_ts IS NULL",
                              key).fetchone()
        if state in ("EMPTY", "LOW"):
            qty = self.facings.get(key, self.default_qty)
            if row:
                self.db.execute("UPDATE reorder_drafts SET state=?, updated_ts=? WHERE id=?",
                                (state, event.ts, row[0]))
            else:
                self.db.execute("INSERT INTO reorder_drafts (shelf, slot, sku, state, opened_ts, updated_ts,"
                                " suggested_qty) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (*key, data.get("sku"), state, event.ts, event.ts, qty))
        elif state == "FULL" and row:
            self.db.execute("UPDATE reorder_drafts SET closed_ts=? WHERE id=?", (event.ts, row[0]))
        # UNKNOWN / WRONG_ITEM change nothing: we don't know, or it isn't a stock problem.
        self.db.commit()

    def open_drafts(self) -> list[dict]:
        rows = self.db.execute("SELECT shelf, slot, sku, state, opened_ts, suggested_qty FROM reorder_drafts "
                               "WHERE closed_ts IS NULL ORDER BY state = 'LOW', opened_ts").fetchall()
        return [dict(zip(("shelf", "slot", "sku", "state", "opened_ts", "suggested_qty"), r)) for r in rows]

    def whatsapp_text(self, store_name: str = "our store", now: datetime | None = None) -> str:
        drafts = self.open_drafts()
        now = now or datetime.now()
        if not drafts:
            return f"StoreMind - {store_name} - {now:%d %b %H:%M}: nothing to reorder."
        lines = [f"StoreMind reorder list - {store_name} - {now:%d %b %H:%M}"]
        for d in drafts:
            since = datetime.fromisoformat(d["opened_ts"]).strftime("%H:%M")
            what = d["sku"] or f"{d['shelf']}/{d['slot']}"
            status = "OUT" if d["state"] == "EMPTY" else "low"
            qty = f" - qty {d['suggested_qty']}" if d["suggested_qty"] else ""
            lines.append(f"- {what}: {status} since {since}{qty}")
        lines.append("(draft - please check before sending)")
        return "\n".join(lines)
