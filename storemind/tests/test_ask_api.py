"""M10 wiring: /api/ask and /api/summary on the dashboard (Vinith's llm/ package)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from storemind.api.server import create_app
from storemind.core.config import StoreMindConfig
from storemind.eval.eval_ask import TODAY, build_db
from storemind.pipeline import Pipeline


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOREMIND_LLM_MODEL", "off")          # rules only: no Ollama needed in CI
    db = tmp_path / "store.db"
    build_db(db)                                                # Vinith's simulated day of events
    config = StoreMindConfig(store="bvrit-demo")
    config.storage.db_path = str(db)
    config.detector.backend = "stub"
    pipeline = Pipeline(config, replay=True)
    yield TestClient(create_app(pipeline))
    pipeline.close()


def test_ask_answers_with_its_query_and_rows(client):
    response = client.get("/api/ask", params={"q": "How many people came in today?", "today": TODAY.isoformat()})
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] and body["backend"] == "rules" and body["backends"] == ["rules"]
    assert body["sql"].lower().startswith("select") and body["rows"]
    assert body["model_query"] is False and body["elapsed_ms"] >= 0


def test_unanswerable_question_says_so_instead_of_inventing(client):
    body = client.get("/api/ask", params={"q": "what is the meaning of life", "today": TODAY.isoformat()}).json()
    assert body["answered"] is False and body["sql"] is None


@pytest.mark.parametrize("lang", ["en", "te", "hi"])
def test_daily_summary_in_three_languages(client, lang):
    body = client.get("/api/summary", params={"day": TODAY.isoformat(), "lang": lang}).json()
    assert body["lang"] == lang and body["text"]


def test_bad_inputs_are_400_and_a_missing_db_is_503(client, tmp_path):
    assert client.get("/api/ask", params={"q": "  "}).status_code == 400
    assert client.get("/api/summary", params={"day": "yesterday"}).status_code == 400
    assert client.get("/api/summary", params={"day": TODAY.isoformat(), "lang": "fr"}).status_code == 400
    client.app.state.asker.db_path = tmp_path / "nothing-yet.db"
    client.app.state.asker._local.__dict__.clear()
    assert client.get("/api/ask", params={"q": "how many people came"}).status_code == 503


def test_ask_latency_script_against_a_live_server(tmp_path, monkeypatch, capsys):
    import importlib.util
    import socket
    import time
    from pathlib import Path

    from storemind.api.server import serve_in_thread

    monkeypatch.setenv("STOREMIND_LLM_MODEL", "off")
    db = tmp_path / "store.db"
    build_db(db)
    config = StoreMindConfig(store="bvrit-demo")
    config.storage.db_path = str(db)
    config.detector.backend = "stub"
    pipeline = Pipeline(config, replay=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    serve_in_thread(pipeline, host="127.0.0.1", port=port)
    script = Path(__file__).resolve().parents[2] / "scripts" / "ask_latency.py"
    spec = importlib.util.spec_from_file_location("ask_latency", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(50):                          # wait for uvicorn
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    assert module.main(["--url", f"http://127.0.0.1:{port}", "--limit", "3", "--no-write"]) == 0
    out = capsys.readouterr().out
    assert "questions: 3" in out and "rules" in out
    pipeline.close()
