"""M10 "Ask your store": the read-only guard, the number verifier, the ask loop's
fallbacks, and the trilingual daily summary.  No Ollama needed: the LLM is faked."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from storemind.eval.eval_ask import (
    TODAY,
    YESTERDAY,
    build_db,
    grade,
    invented,
    questions,
    simulate,
)
from storemind.llm.ask import (
    NO_DATA,
    RuleBackend,
    ask,
    extract_sql,
    fill_template,
    numbers_in,
    render_rows,
    verify_numbers,
)
from storemind.llm.summary import LANGS, daily_summary
from storemind.llm.views import MAX_ROWS, QueryRefused, open_readonly, run_query


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> tuple[Path, dict]:
    path = tmp_path_factory.mktemp("ask") / "store.db"
    return path, build_db(path)


@pytest.fixture()
def conn(seeded):
    connection = open_readonly(seeded[0])
    yield connection
    connection.close()


# --- the guard -------------------------------------------------------------- #

@pytest.mark.parametrize("sql", [
    "SELECT * FROM events",                                   # the raw table, not a view
    "SELECT name FROM sqlite_master",
    "SELECT sql FROM sqlite_temp_master",
    "DELETE FROM events",
    "DROP VIEW entries",
    "INSERT INTO config VALUES('a','b')",
    "PRAGMA table_info(events)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT load_extension('evil')",
    "SELECT count(*) FROM entries; DROP TABLE events",
    "UPDATE events SET type = 'x'",
    "CREATE TABLE t(x)",
])
def test_guard_refuses_everything_but_reading_views(conn, sql):
    with pytest.raises(QueryRefused):
        run_query(conn, sql)


def test_guard_allows_views_ctes_and_subqueries(conn):
    columns, rows = run_query(conn, "WITH d AS (SELECT day, count(*) AS n FROM entries GROUP BY day) "
                                    "SELECT * FROM d ORDER BY day")
    assert columns == ["day", "n"] and [r[0] for r in rows] == [YESTERDAY.isoformat(), TODAY.isoformat()]
    _cols, many = run_query(conn, "SELECT ts FROM queue")
    assert len(many) == MAX_ROWS


def test_store_file_is_untouched(seeded):
    path = seeded[0]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    connection = open_readonly(path)
    run_query(connection, "SELECT count(*) FROM entries")
    connection.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    plain = sqlite3.connect(path)
    assert plain.execute("SELECT count(*) FROM sqlite_master WHERE type = 'view'").fetchone()[0] == 0
    plain.close()


# --- numbers ---------------------------------------------------------------- #

def test_numbers_in_reads_indian_and_decimal_formats():
    assert numbers_in("Rs 1,23,456 and 1,234.5 and 3.") == [123456.0, 1234.5, 3.0]


def test_verifier_accepts_rows_and_roundings_and_flags_the_rest():
    rows = [(211.2987, "billing-1", 18)]
    assert verify_numbers("about 211.3 s (211) at billing-1, hour 18", rows) == []
    assert verify_numbers("about 3.5 minutes", rows) == [3.5]
    assert verify_numbers("between 6 and 7 pm: 5", [(5,)], "between 6 pm and 7 pm") == []


def test_rendering_only_uses_cells():
    columns, rows = ["counter", "avg_wait_s"], [("billing-1", 211.2987), ("billing-2", 190.0)]
    text = render_rows(columns, rows)
    assert verify_numbers(text, rows) == [] and "211.3" in text
    assert render_rows(["n"], [(None,)]) == NO_DATA


def test_extract_sql():
    assert extract_sql("```sql\nSELECT 1 FROM entries;\n```") == "SELECT 1 FROM entries"
    assert extract_sql("Sure! SELECT count(*) FROM entries") == "SELECT count(*) FROM entries"
    assert extract_sql("CANNOT_ANSWER") is None


# --- the ask loop, with a fake model ----------------------------------------- #

class FakeLLM:
    name = "ollama"
    phrase = True

    def __init__(self, sql: str | None, sentence: str = "") -> None:
        self.sql, self.sentence = sql, sentence

    def to_sql(self, question, context):
        return self.sql

    def phrase_answer(self, question, columns, rows):
        return self.sentence


def test_refused_model_query_falls_back_to_rules(conn):
    answer = ask("How many customers came in today?", conn, [FakeLLM("SELECT * FROM events"), RuleBackend()],
                 today=TODAY)
    assert answer.backend == "rules" and answer.answered and not answer.model_query
    assert any("query refused" in n for n in answer.notes)


def test_model_written_digits_are_refused_and_templates_are_filled(conn):
    sql = "SELECT count(*) AS people_in FROM entries WHERE direction = 'in' AND day = '2026-03-15'"
    n = run_query(conn, sql)[1][0][0]
    answer = ask("How many came in today?", conn, [FakeLLM(sql, f"About {n} people came in.")], today=TODAY)
    assert not answer.phrased and answer.text == f"people in: {n}" and answer.model_query
    good = ask("How many came in today?", conn, [FakeLLM(sql, "{people_in} people came in today.")], today=TODAY)
    assert good.phrased and good.text == f"{n} people came in today."


def test_template_needs_known_placeholders(conn):
    sql = "SELECT count(*) AS n FROM entries WHERE day = '2026-03-15' AND hour = 18 AND direction = 'in'"
    for template in ("No people came between 6 pm and 7 pm.", "{visitors} people came."):
        answer = ask("How many between 6 pm and 7 pm?", conn, [FakeLLM(sql, template)], today=TODAY)
        assert not answer.phrased and answer.text.startswith("n: ")


def test_values_follow_their_column_names():
    """Run 3's failure: "the quietest hour was 8" when 8 was the count.  A template
    cannot swap them: {hour} is always the hour cell."""
    sentence, _ = fill_template("The quietest hour was {hour} ({people_in} people).", ["hour", "people_in"],
                                (11, 8), "Which hour was the quietest?")
    assert sentence == "The quietest hour was 11 (8 people)."


def test_units_come_from_the_column_name():
    """Run 4's failure: "the average wait is 201.31 minutes" for a seconds column."""
    q = "What's the average wait?"
    bad, reason = fill_template("The average wait is {avg_wait_s} minutes.", ["avg_wait_s"], (201.31,), q)
    assert bad is None and "unit mismatch" in reason
    assert fill_template("The average wait is {avg_wait_s}.", ["avg_wait_s"], (201.31,), q)[0] == \
        "The average wait is 201.31 s."
    assert fill_template("It was {avg_wait_s} seconds.", ["avg_wait_s"], (201.31,), q)[0] == "It was 201.31 seconds."
    assert fill_template("About {est_value_inr} at risk.", ["est_value_inr"], (770.5,), q)[0] == \
        "About Rs 770.5 at risk."
    assert fill_template("About {est_value_inr} rupees.", ["est_value_inr"], (770.5,), q)[0] == "About 770.5 rupees."


def test_multi_row_results_are_not_phrased(conn):
    sql = "SELECT DISTINCT sku FROM slots ORDER BY sku"
    answer = ask("Which products?", conn, [FakeLLM(sql, "Maggi.")], today=TODAY)
    assert not answer.phrased and "Parle-G" in answer.text


def test_rules_refuse_the_future_and_the_unrecorded(conn):
    for question in ("What will footfall be next Sunday?", "Should I hire another cashier?",
                     "What is our profit margin this month?"):
        assert not ask(question, conn, [RuleBackend()], today=TODAY).answered


def test_rules_answer_the_dev_set_with_no_invented_numbers(conn, seeded):
    """Regression for the rule set: it was written against these questions (set A)."""
    for item in questions(seeded[1]):
        answer = ask(item["q"], conn, [RuleBackend()], today=TODAY)
        assert grade(item, answer) == "correct", (item["q"], answer.text)
        assert invented(answer) == []


def test_simulation_is_deterministic():
    assert simulate(7)[1] == simulate(7)[1]


# --- daily summary ------------------------------------------------------------ #

def test_summary_in_three_languages_carries_the_same_numbers(conn, seeded):
    truth = seeded[1][TODAY.isoformat()]
    texts = {lang: daily_summary(conn, TODAY.isoformat(), lang) for lang in LANGS}
    assert str(truth["in"]) in texts["en"] and "Maggi" in texts["en"]
    reference = sorted(numbers_in(texts["en"]))
    for lang in ("te", "hi"):
        assert sorted(numbers_in(texts[lang])) == reference, lang
    assert "దుకాణానికి" in texts["te"] and "दुकान" in texts["hi"]


def test_summary_says_no_data_rather_than_zero(conn):
    text = daily_summary(conn, "2026-01-01", "en")
    assert "No visitor count data" in text and "No alert data" in text
    assert numbers_in(text.split("\n", 1)[1]) == []


def test_summary_rejects_a_non_date(conn):
    with pytest.raises(ValueError):
        daily_summary(conn, "2026-03-15' OR 1=1 --", "en")
