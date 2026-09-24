"""Ask your store (M10): a question in plain language -> a read-only query -> an
answer that cites its rows.

The one rule: **the language model never supplies a number.**

1. A backend turns the question into one SELECT over the whitelisted views in
   `views.py` (a local LLM through Ollama, or the keyword rules below when no
   model is available, e.g. on a Pi without one).
2. SQLite runs it on a read-only connection whose authorizer only lets it read
   those views.
3. The answer is rendered from the returned rows.  For a one-row result the
   LLM may also write a sentence, but only as a template with {column}
   placeholders and no digits of its own; the code fills in the cells
   (`fill_template()`).  Finally `verify_numbers()` checks that every number in
   the answer appears in the rows (or the question); if one does not, the plain
   rendering of the rows is used instead.

Every `Answer` carries its SQL and rows, so the dashboard can show them.

    python -m storemind.llm.ask --db data/storemind.db "How many people came in today?"
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from .views import QueryRefused, open_readonly, run_query, schema_text

CANNOT = "CANNOT_ANSWER"
NO_DATA = "No matching data in the store's records."
NOT_ANSWERABLE = ("I can't answer that from the store's records. I can answer questions about visitors, "
                  "billing queues, shelf stock, lost-sale estimates, shelf picks, zone visits and alerts.")


# --------------------------------------------------------------------------- #
# numbers
# --------------------------------------------------------------------------- #

_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:,\d{2,3})*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    """Every number written in `text` ("1,234.5" and Indian "1,23,456" included)."""
    return [float(m.group().replace(",", "")) for m in _NUMBER.finditer(text)]


def allowed_numbers(rows: list[tuple], *texts: str) -> set[float]:
    """Numbers an answer may contain: the cell values (and their roundings), and
    the numbers inside text cells, the question and the date context."""
    allowed: set[float] = set()
    for row in rows:
        for cell in row:
            if isinstance(cell, bool) or cell is None:
                continue
            if isinstance(cell, (int, float)):
                value = float(cell)
                allowed |= {value, round(value), round(value, 1), round(value, 2)}
            else:
                allowed |= set(numbers_in(str(cell)))
    for text in texts:
        allowed |= set(numbers_in(text))
    return allowed


def verify_numbers(answer: str, rows: list[tuple], *texts: str) -> list[float]:
    """The numbers in `answer` that do not come from the rows or `texts`.
    An empty list means the answer invents nothing."""
    allowed = allowed_numbers(rows, *texts)
    return [n for n in numbers_in(answer) if not any(abs(n - a) < 1e-9 for a in allowed)]


_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def fill_template(template: str, columns: list[str], row: tuple, question: str) -> tuple[str | None, str]:
    """Fill a model-written "{column}" template with the row's cells.

    Returns (sentence, "") or (None, reason).  The model's own text may hold no
    number except those in the question, so every number in the sentence is a
    cell placed by column name: it cannot be attached to the wrong label by a
    copying slip (the M10 run-3 failure "the quietest hour was 8", where 8 was
    the visitor count)."""
    names = _PLACEHOLDER.findall(template or "")
    if not names:
        return None, "no {column} placeholder"
    unknown = sorted(set(names) - set(columns))
    if unknown:
        return None, f"unknown placeholder {unknown}"
    stray = verify_numbers(_PLACEHOLDER.sub("", template), [], question)
    if stray:
        return None, f"numbers {stray} written by the model"
    values = dict(zip(columns, row))
    out, last = [], 0
    for match in _PLACEHOLDER.finditer(template):
        name = match.group(1)
        text = _fmt(values[name])
        unit = next((u for suffix, u in _UNIT_SUFFIX.items() if name.endswith(suffix)), None)
        if unit is not None:
            # The unit comes from the column name, not from the model ("201 s", never
            # "201 minutes" for a seconds column).  A clashing unit word rejects the template.
            after = _UNIT_WORD.match(template, match.end())
            before = template[last:match.start()].rstrip().lower()
            if after is not None and _unit_kind(after.group(1)) != unit:
                return None, f"unit mismatch: {{{name}}} is in {unit}, the sentence says {after.group(1)!r}"
            if unit == "Rs":
                named = after is not None or before.endswith(("rs", "rs.", "inr", "\u20b9"))
                text = text if named else f"Rs {text}"
            elif after is None:
                text = f"{text} {unit}"
        out += [template[last:match.start()], text]
        last = match.end()
    out.append(template[last:])
    return "".join(out).strip(), ""


_UNIT_SUFFIX = {"_s": "s", "_min": "min", "_h": "h", "_inr": "Rs"}
_UNIT_WORD = re.compile(r"\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|rupees?|inr|rs)\b", re.IGNORECASE)


def _unit_kind(word: str) -> str:
    word = word.lower()
    if word.startswith("sec"):
        return "s"
    if word.startswith("min"):
        return "min"
    if word.startswith(("hour", "hr")):
        return "h"
    return "Rs"


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{round(value, 2):g}" if abs(value) < 1e6 else f"{round(value):d}"
    return str(value)


def render_rows(columns: list[str], rows: list[tuple]) -> str:
    """A plain answer made only of the returned cells."""
    if not rows or all(all(c is None for c in row) for row in rows):
        return NO_DATA
    if len(rows) == 1 and len(columns) == 1:
        return f"{columns[0].replace('_', ' ')}: {_fmt(rows[0][0])}"
    if len(rows) == 1:
        return "; ".join(f"{c.replace('_', ' ')}: {_fmt(v)}" for c, v in zip(columns, rows[0]))
    lines = [" | ".join(columns)]
    lines += [" | ".join(_fmt(v) for v in row) for row in rows[:10]]
    if len(rows) > 10:
        lines.append("... more rows in the cited result")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# backends
# --------------------------------------------------------------------------- #

class Backend(Protocol):
    name: str

    def to_sql(self, question: str, context: dict[str, Any]) -> str | None: ...


@dataclass
class OllamaBackend:
    """A local model served by Ollama (http://localhost:11434).  Works on the
    laptop and on a Pi 5 (ARM64 build); nothing leaves the machine."""

    model: str = "qwen2.5-coder:1.5b"
    host: str = "http://localhost:11434"
    timeout_s: float = 60.0
    phrase: bool = True
    name: str = "ollama"

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(self.host + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read())

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=3) as response:
                models = [m["name"] for m in json.loads(response.read()).get("models", [])]
        except (urllib.error.URLError, OSError, ValueError):
            return False
        return any(m == self.model or m.split(":")[0] == self.model for m in models)

    def generate(self, prompt: str, max_tokens: int = 256) -> str:
        reply = self._post("/api/generate", {
            "model": self.model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_predict": max_tokens}})
        return reply.get("response", "")

    def to_sql(self, question: str, context: dict[str, Any]) -> str | None:
        text = self.generate(sql_prompt(question, context))
        return extract_sql(text)

    def phrase_answer(self, question: str, columns: list[str], rows: list[tuple]) -> str:
        """A sentence *template*: the model never sees or writes the values, only
        the column names, which `fill_template()` replaces with the cells."""
        prompt = ("Write one short sentence that answers the shop owner's question from a one-row result. "
                  "Do not write any digits. Where a value from the result belongs, write its column name in "
                  "curly braces, for example: The busiest counter was {counter} with {customers_served} "
                  "customers.\n\n"
                  f"Question: {question}\nResult columns: {', '.join(columns)}\n\nSentence:")
        return self.generate(prompt, max_tokens=60).strip().split("\n")[0]


FEW_SHOT = [
    # Deliberately different from the evaluation questions (eval/eval_ask.py).
    ("How many people left the store on 2026-01-02?",
     "SELECT count(*) AS people_out FROM entries WHERE direction = 'out' AND day = '2026-01-02'"),
    ("Which hour had the fewest visitors yesterday?",
     ("SELECT hour, count(*) AS people_in FROM entries WHERE direction = 'in' AND day = '{yesterday}' "
      "GROUP BY hour ORDER BY people_in ASC LIMIT 1")),
    ("What was the median queue wait at counter billing-1 at its worst today?",
     "SELECT max(median_wait_s) AS worst_median_wait_s FROM queue WHERE counter = 'billing-1' AND day = '{today}'"),
    ("Which slots are full now?",
     ("SELECT shelf, slot, sku FROM slots s WHERE state = 'FULL' AND ts = "
      "(SELECT max(ts) FROM slots t WHERE t.shelf = s.shelf AND t.slot = s.slot)")),
    ("How many warning alerts yesterday?",
     "SELECT count(*) AS warnings FROM alerts WHERE severity = 'WARN' AND day = '{yesterday}'"),
    ("Total grams taken from shelf s2 today?",
     "SELECT sum(grams) AS grams_taken FROM picks WHERE shelf = 's2' AND action = 'pick' AND day = '{today}'"),
    ("What is the weather tomorrow?", CANNOT),
]


def sql_prompt(question: str, context: dict[str, Any]) -> str:
    examples = "\n\n".join(f"Question: {q}\nSQL: {s.format(**context)}" for q, s in FEW_SHOT)
    values = "\n".join(f"- {k}: {', '.join(map(str, v))}" for k, v in context.get("values", {}).items() if v)
    return (
        "You write one SQLite SELECT query that answers a shop owner's question.\n"
        "Only these views exist (no other tables):\n"
        f"{schema_text()}\n\n"
        f"Values present in the data:\n{values}\n\n"
        f"Today is {context['today']}; yesterday was {context['yesterday']}. Dates are 'YYYY-MM-DD' in the "
        "`day` column; `hour` is 0-23. If the question gives no date, use today.\n"
        "`ts` looks like '2026-03-15T18:05:00+05:30': never compare it with a date string; filter with the "
        "`day` and `hour` columns.\n"
        "Give every computed column a clear name with its unit (e.g. avg_wait_s).\n"
        "The data has no money taken at the till, revenue, profit, prices paid, stock counts, staff or customer "
        "identities. Never make up a formula for something the views do not record.\n"
        f"If the question cannot be answered from these views, reply exactly {CANNOT}.\n"
        "Reply with the SQL only.\n\n"
        f"{examples}\n\nQuestion: {question}\nSQL:"
    )


def extract_sql(text: str) -> str | None:
    if CANNOT in text:
        return None
    block = re.search(r"```(?:sql|sqlite)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if block:
        text = block.group(1)
    start = re.search(r"\b(SELECT|WITH)\b", text, re.IGNORECASE)
    if not start:
        return None
    sql = text[start.start():].strip()
    return sql.split(";")[0].strip() or None


# ---- keyword rules: deterministic, no model needed ------------------------- #

_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_HOUR = re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE)
_CURRENT_SLOT = ("ts = (SELECT max(ts) FROM slots t WHERE t.shelf = s.shelf AND t.slot = s.slot)")


def _day(q: str, context: dict[str, Any]) -> str:
    match = _DATE.search(q)
    if match:
        return match.group(1)
    return context["yesterday"] if "yesterday" in q else context["today"]


def _hour(q: str) -> int | None:
    match = _HOUR.search(q)
    if not match:
        return None
    hour = int(match.group(1)) % 12
    return hour + 12 if match.group(2).lower() == "pm" else hour


def _pick(q: str, names: list[str], word: str) -> str | None:
    """Match 'counter 2' / 'slot a1' against the names that exist in the data."""
    match = re.search(rf"\b{word}\s+([\w-]+)", q)
    if not match:
        return None
    wanted = match.group(1).lower()
    for name in names:
        low = str(name).lower()
        if low == wanted or low.endswith((f"-{wanted}", f"_{wanted}")):
            return str(name)
    return None


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


@dataclass
class RuleBackend:
    """Keyword rules for the common questions.  Used when no model is installed
    and as the fallback when the model's query is refused."""

    name: str = "rules"

    def to_sql(self, question: str, context: dict[str, Any]) -> str | None:
        q = question.lower()
        if re.search(r"\b(will|next|tomorrow|predict|forecast|expect|should|recommend)\b", q):
            return None  # the future and advice are not records
        day = _day(q, context)
        values = context.get("values", {})
        on_day = f"day = '{day}'"

        if _has(q, "yesterday") and _has(q, "today") and _has(q, "visit", "people", "customer", "footfall", "came"):
            return (f"SELECT day, count(*) AS people_in FROM entries WHERE direction = 'in' AND day IN "
                    f"('{context['yesterday']}', '{context['today']}') GROUP BY day ORDER BY day")
        if _has(q, "busiest", "peak", "rush"):
            return (f"SELECT hour, count(*) AS people_in FROM entries WHERE direction = 'in' AND {on_day} "
                    "GROUP BY hour ORDER BY people_in DESC LIMIT 1")
        if _has(q, "service time", "serve time", "billing time", "time to bill"):
            counter = _pick(q, values.get("counters", []), "counter")
            where = f" AND counter = '{counter}'" if counter else ""
            return f"SELECT round(avg(service_s), 1) AS avg_service_s FROM services WHERE {on_day}{where}"
        if _has(q, "wait"):
            counter = _pick(q, values.get("counters", []), "counter")
            where = f" AND counter = '{counter}'" if counter else ""
            return (f"SELECT round(avg(wait_s), 1) AS avg_wait_s, round(avg(wait_s) / 60.0, 1) AS avg_wait_min "
                    f"FROM services WHERE {on_day} AND wait_s IS NOT NULL{where}")
        if _has(q, "which counter", "what counter", "busiest counter"):
            return (f"SELECT counter, count(*) AS customers_served FROM services WHERE {on_day} "
                    "GROUP BY counter ORDER BY customers_served DESC LIMIT 1")
        if _has(q, "served", "billed", "bills"):
            return f"SELECT count(*) AS customers_served FROM services WHERE {on_day}"
        if _has(q, "queue", "line"):
            return (f"SELECT max(people_waiting) AS longest_queue_people FROM queue WHERE {on_day}")
        if _has(q, "empty", "out of stock", "stock out", "stockout"):
            return (f"SELECT shelf, slot, sku FROM slots s WHERE state = 'EMPTY' AND {_CURRENT_SLOT} "
                    "ORDER BY shelf, slot")
        if _has(q, "running low", "low stock", "low on", "are low", "is low"):
            return (f"SELECT shelf, slot, sku FROM slots s WHERE state = 'LOW' AND {_CURRENT_SLOT} "
                    "ORDER BY shelf, slot")
        if _has(q, "alert"):
            if _has(q, "critical", "serious", "urgent"):
                return f"SELECT count(*) AS critical_alerts FROM alerts WHERE severity = 'CRITICAL' AND {on_day}"
            return f"SELECT count(*) AS alerts FROM alerts WHERE {on_day}"
        if _has(q, "lost", "missed sale", "at risk"):
            if _has(q, "which", "what product", "most"):
                return (f"SELECT sku, round(sum(est_value_inr), 2) AS est_value_inr FROM lost_sales WHERE {on_day} "
                        "GROUP BY sku ORDER BY est_value_inr DESC LIMIT 1")
            return (f"SELECT round(sum(est_value_inr), 2) AS est_value_inr, count(*) AS looks FROM lost_sales "
                    f"WHERE {on_day}")
        if _has(q, "put-back", "putback", "returned") or re.search(r"\bput\b(\s+\w+){0,2}\s+back\b", q):
            return f"SELECT count(*) AS put_backs FROM picks WHERE action = 'put_back' AND {on_day}"
        if _has(q, "picked", "taken", "pick"):
            slot = _pick(q, values.get("slots", []), "slot")
            where = f" AND slot = '{slot}'" if slot else ""
            return (f"SELECT sum(units) AS units_picked FROM picks WHERE action = 'pick' AND {on_day}{where}")
        if _has(q, "promo", "zone", "end-cap", "endcap"):
            zone = next((z for z in values.get("zones", []) if str(z).lower() in q), None)
            if zone is None and _has(q, "promo"):
                zone = next((z for z in values.get("zones", []) if "promo" in str(z).lower()), None)
            where = f" AND zone = '{zone}'" if zone else ""
            if _has(q, "how long", "time", "dwell", "stay"):
                return f"SELECT round(avg(dwell_s), 1) AS avg_dwell_s FROM zone_visits WHERE {on_day}{where}"
            return f"SELECT count(*) AS visits FROM zone_visits WHERE {on_day}{where}"
        if _has(q, "left", "exit", "went out"):
            return f"SELECT count(*) AS people_out FROM entries WHERE direction = 'out' AND {on_day}"
        if _has(q, "came", "visit", "enter", "footfall", "customers", "people", "walk-in", "walked in"):
            hour = _hour(q)
            where = f" AND hour = {hour}" if hour is not None else ""
            return f"SELECT count(*) AS people_in FROM entries WHERE direction = 'in' AND {on_day}{where}"
        return None


# --------------------------------------------------------------------------- #
# the ask loop
# --------------------------------------------------------------------------- #

@dataclass
class Answer:
    question: str
    text: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    backend: str = ""
    answered: bool = False
    phrased: bool = False          # True: the LLM's sentence passed the number check
    model_query: bool = False      # True: the SQL was written by the LLM - show it, it may ask the wrong thing
    notes: list[str] = field(default_factory=list)

    def cited(self) -> str:
        """Answer plus its evidence, for a terminal or a chat bubble."""
        if not self.sql:
            return self.text
        check = "  (query written by the local model: check it asks what you meant)\n" if self.model_query else ""
        return f"{self.text}\n{check}  [query: {self.sql}]\n  [{len(self.rows)} row(s): {self.rows[:5]}]"

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question, "text": self.text, "sql": self.sql, "columns": self.columns,
                "rows": [list(r) for r in self.rows], "backend": self.backend, "answered": self.answered,
                "phrased": self.phrased, "model_query": self.model_query, "notes": self.notes}


def data_context(conn: sqlite3.Connection, today: date | None = None) -> dict[str, Any]:
    """Dates plus the names present in the data (counters, slots, SKUs, zones), so
    that the model and the rules use real values."""
    today = today or datetime.now().astimezone().date()  # the store's local date

    def distinct(sql: str) -> list:
        try:
            return [r[0] for r in run_query(conn, sql)[1] if r[0] is not None]
        except QueryRefused:
            return []

    return {
        "today": today.isoformat(),
        "yesterday": (today - timedelta(days=1)).isoformat(),
        "values": {
            "counters": distinct("SELECT DISTINCT counter FROM services UNION SELECT DISTINCT counter FROM queue"),
            "shelves": distinct("SELECT DISTINCT shelf FROM slots"),
            "slots": distinct("SELECT DISTINCT slot FROM slots UNION SELECT DISTINCT slot FROM picks"),
            "skus": distinct("SELECT DISTINCT sku FROM slots"),
            "zones": distinct("SELECT DISTINCT zone FROM zone_visits"),
            "severity": ["INFO", "WARN", "CRITICAL"],
            "slot states": ["FULL", "LOW", "EMPTY", "WRONG_ITEM", "UNKNOWN"],
        },
    }


def ask(question: str, conn: sqlite3.Connection, backends: list[Backend],
        today: date | None = None, context: dict[str, Any] | None = None) -> Answer:
    """Try each backend in order until one produces a query SQLite accepts."""
    context = context or data_context(conn, today)
    answer = Answer(question=question, text=NOT_ANSWERABLE)
    for backend in backends:
        try:
            sql = backend.to_sql(question, context)
        except (urllib.error.URLError, OSError, ValueError) as error:
            answer.notes.append(f"{backend.name}: unavailable ({error})")
            continue
        if sql is None:
            answer.notes.append(f"{backend.name}: cannot answer")
            continue
        try:
            columns, rows = run_query(conn, sql)
        except QueryRefused as error:
            answer.notes.append(f"{backend.name}: query refused ({error})")
            continue
        answer.sql, answer.columns, answer.rows, answer.backend = sql, columns, rows, backend.name
        answer.answered = True
        answer.model_query = backend.name != "rules"
        answer.text = render_rows(columns, rows)
        phrase = getattr(backend, "phrase_answer", None)
        # Only a complete single-row result is phrased (a one-line summary of a list
        # drops rows), and only through a {column} template the code fills in.
        if (phrase is not None and getattr(backend, "phrase", False) and len(rows) == 1
                and all(v is not None for v in rows[0])):
            try:
                template = phrase(question, columns, rows)
            except (urllib.error.URLError, OSError, ValueError):
                template = ""
            sentence, reason = fill_template(template, columns, rows[0], question)
            if sentence:
                answer.text, answer.phrased = sentence, True
            elif template:
                answer.notes.append(f"phrasing dropped: {reason}")
        # The guarantee, checked on whatever text is returned.  Column names may
        # carry digits of their own (e.g. "hour_18"): they are visible in the cited query.
        invented = verify_numbers(answer.text, rows, question, context["today"], " ".join(columns))
        if invented:
            answer.notes.append(f"rendering replaced: {invented} not in the rows")
            answer.text, answer.phrased = json.dumps([list(r) for r in rows], ensure_ascii=False), False
        return answer
    return answer


def default_backends(model: str | None = "qwen2.5-coder:1.5b", host: str = "http://localhost:11434",
                     phrase: bool = True) -> list[Backend]:
    """The LLM first, the keyword rules as the fallback (no model installed, or
    the model refuses).  Measured on held-out questions the rules generalise
    worse than the model: docs/ASK.md section 3."""
    backends: list[Backend] = []
    if model:
        llm = OllamaBackend(model=model, host=host, phrase=phrase)
        if llm.available():
            backends.append(llm)
    backends.append(RuleBackend())
    return backends


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", nargs="+")
    parser.add_argument("--db", default="data/storemind.db")
    parser.add_argument("--model", default="qwen2.5-coder:1.5b", help="Ollama model; '' = rules only")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--today", help="YYYY-MM-DD (default: the real date)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    conn = open_readonly(args.db)
    today = date.fromisoformat(args.today) if args.today else None
    result = ask(" ".join(args.question), conn, default_backends(args.model or None, args.host), today)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=1) if args.json else result.cited())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
