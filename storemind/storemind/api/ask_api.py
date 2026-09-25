"""M10 "Ask your store" + daily summary, served by the dashboard (Person B's
wiring of Vinith's `storemind/llm`, docs/ASK.md section 6).

*   Every request gets a **read-only** connection (`llm.views.open_readonly`:
    TEMP views + a strict authorizer), one per worker thread, beside the
    pipeline's writer (WAL mode, so readers never block it).
*   FastAPI runs the plain `def` endpoints in its thread pool, so a slow model
    (seconds on a Pi CPU) never blocks the event loop or the WebSocket.
*   The backend list (local model via Ollama if it has the model, then the
    keyword rules) is probed once and re-probed every `refresh_s`, not per
    question: the probe itself has a 3 s timeout.

Environment (no config contract change): `STOREMIND_LLM_MODEL` (default
`qwen2.5-coder:1.5b`; `off` = rules only), `STOREMIND_LLM_HOST`
(default `http://localhost:11434`).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

MAX_QUESTION_CHARS = 300
LANGS = ("en", "te", "hi")


class AskService:
    def __init__(self, db_path: str | Path, *, model: str | None = None, host: str | None = None,
                 refresh_s: float = 300.0) -> None:
        self.db_path = Path(db_path)
        env_model = os.environ.get("STOREMIND_LLM_MODEL", "qwen2.5-coder:1.5b")
        self.model = model if model is not None else (None if env_model.lower() in ("", "off", "none")
                                                      else env_model)
        self.host = host or os.environ.get("STOREMIND_LLM_HOST", "http://localhost:11434")
        self.refresh_s = refresh_s
        self._local = threading.local()
        self._lock = threading.Lock()
        self._backends: list | None = None
        self._probed_at = 0.0

    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            from ..llm.views import open_readonly

            conn = open_readonly(self.db_path)          # FileNotFoundError before the first event
            self._local.conn = conn
        return conn

    def backends(self) -> list:
        with self._lock:
            if self._backends is None or time.monotonic() - self._probed_at > self.refresh_s:
                from ..llm.ask import default_backends

                self._backends = default_backends(self.model, self.host)
                self._probed_at = time.monotonic()
            return self._backends

    # ------------------------------------------------------------------ #
    def ask(self, question: str, today: date | None = None) -> dict[str, Any]:
        from ..llm.ask import ask

        question = question.strip()[:MAX_QUESTION_CHARS]
        started = time.perf_counter()
        answer = ask(question, self._conn(), self.backends(), today)
        result = answer.to_dict()
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
        result["backends"] = [b.name for b in self.backends()]
        return result

    def summary(self, day: str, lang: str = "en") -> dict[str, Any]:
        from ..llm.summary import daily_summary

        if lang not in LANGS:
            raise ValueError(f"lang must be one of {LANGS}")
        date.fromisoformat(day)                         # ValueError on junk
        return {"day": day, "lang": lang, "text": daily_summary(self._conn(), day, lang)}
