"""Daily summary in English, Telugu and Hindi (M10).

No language model is involved.  The numbers come from fixed queries over the
same read-only views as "Ask your store"; the sentences are fixed templates per
language.  A section with no events says "no data", never a made-up zero.

    python -m storemind.llm.summary --db data/storemind.db --day 2026-03-15 --lang te

The Telugu and Hindi templates need a native speaker's read before a demo
(docs/ASK.md section 4).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from typing import Any

from .ask import verify_numbers
from .views import open_readonly, run_query

LANGS = ("en", "te", "hi")

TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "title": "StoreMind daily summary - {day}",
        "visitors": "{visitors} people came in. Busiest hour: {peak_h}:00-{peak_h1}:00 ({peak_n} people).",
        "billing": "{served} customers were billed. Average wait {wait_min} min; longest queue {max_q} people.",
        "empty": "{n_empty} shelf slots were empty at the end of the day: {empty_list}.",
        "no_empty": "No shelf slot was empty at the end of the day.",
        "low": "{n_low} items are running low: {low_list}.",
        "lost": "Estimated sales at risk from empty shelves: Rs {lost} ({lost_n} times a shopper looked at an "
                "empty or low shelf). This is an estimate.",
        "alerts": "{alerts} alerts ({critical} critical).",
        "no_data": "No {what} data for this day.",
        "what_visitors": "visitor count", "what_billing": "billing queue", "what_shelves": "shelf",
        "what_lost": "lost-sale", "what_alerts": "alert",
    },
    "te": {
        "title": "StoreMind రోజువారీ సారాంశం - {day}",
        "visitors": "{visitors} మంది దుకాణానికి వచ్చారు. అత్యంత రద్దీ సమయం: {peak_h}:00-{peak_h1}:00 "
                    "({peak_n} మంది).",
        "billing": "{served} మంది కస్టమర్లకు బిల్లింగ్ జరిగింది. సగటు నిరీక్షణ {wait_min} నిమిషాలు; "
                   "అతి పొడవైన క్యూ {max_q} మంది.",
        "empty": "రోజు చివరికి {n_empty} షెల్ఫ్ స్లాట్లు ఖాళీగా ఉన్నాయి: {empty_list}.",
        "no_empty": "రోజు చివరికి ఏ షెల్ఫ్ స్లాట్ ఖాళీగా లేదు.",
        "low": "{n_low} వస్తువులు తక్కువగా ఉన్నాయి: {low_list}.",
        "lost": "ఖాళీ షెల్ఫ్‌ల వల్ల అంచనా అమ్మకాల నష్టం: రూ. {lost} ({lost_n} సార్లు కస్టమర్లు ఖాళీ లేదా "
                "తక్కువ షెల్ఫ్‌ను చూశారు). ఇది అంచనా మాత్రమే.",
        "alerts": "{alerts} హెచ్చరికలు ({critical} తీవ్రమైనవి).",
        "no_data": "ఈ రోజుకు {what} డేటా లేదు.",
        "what_visitors": "కస్టమర్ల లెక్క", "what_billing": "బిల్లింగ్ క్యూ", "what_shelves": "షెల్ఫ్",
        "what_lost": "అమ్మకాల నష్టం", "what_alerts": "హెచ్చరికల",
    },
    "hi": {
        "title": "StoreMind दैनिक सारांश - {day}",
        "visitors": "{visitors} लोग दुकान में आए। सबसे व्यस्त समय: {peak_h}:00-{peak_h1}:00 ({peak_n} लोग)।",
        "billing": "{served} ग्राहकों का बिल बना। औसत इंतज़ार {wait_min} मिनट; सबसे लंबी कतार {max_q} लोग।",
        "empty": "दिन के अंत में {n_empty} शेल्फ़ स्लॉट खाली थे: {empty_list}।",
        "no_empty": "दिन के अंत में कोई शेल्फ़ स्लॉट खाली नहीं था।",
        "low": "{n_low} सामान कम बचे हैं: {low_list}।",
        "lost": "खाली शेल्फ़ के कारण अनुमानित बिक्री नुकसान: रु. {lost} ({lost_n} बार ग्राहकों ने खाली या कम "
                "शेल्फ़ देखी)। यह केवल एक अनुमान है।",
        "alerts": "{alerts} अलर्ट ({critical} गंभीर)।",
        "no_data": "इस दिन का {what} डेटा उपलब्ध नहीं है।",
        "what_visitors": "ग्राहक गिनती", "what_billing": "बिलिंग कतार", "what_shelves": "शेल्फ़",
        "what_lost": "बिक्री नुकसान", "what_alerts": "अलर्ट",
    },
}

_CURRENT = "ts = (SELECT max(ts) FROM slots t WHERE t.shelf = s.shelf AND t.slot = s.slot AND t.day <= '{day}')"

QUERIES: dict[str, str] = {
    "visitors": "SELECT count(*) FROM entries WHERE direction = 'in' AND day = '{day}'",
    "peak": ("SELECT hour, count(*) AS n FROM entries WHERE direction = 'in' AND day = '{day}' "
             "GROUP BY hour ORDER BY n DESC, hour LIMIT 1"),
    "billing": ("SELECT count(*), round(avg(wait_s) / 60.0, 1) FROM services WHERE day = '{day}'"),
    "max_q": "SELECT max(people_waiting) FROM queue WHERE day = '{day}'",
    "empty": ("SELECT coalesce(sku, shelf || '/' || slot) FROM slots s WHERE day <= '{day}' AND state = 'EMPTY' AND "
              + _CURRENT + " ORDER BY shelf, slot"),
    "low": ("SELECT coalesce(sku, shelf || '/' || slot) FROM slots s WHERE day <= '{day}' AND state = 'LOW' AND "
            + _CURRENT + " ORDER BY shelf, slot"),
    "any_slots": "SELECT count(*) FROM slots WHERE day <= '{day}'",
    "lost": "SELECT round(sum(est_value_inr)), count(*) FROM lost_sales WHERE day = '{day}'",
    "alerts": ("SELECT count(*), sum(severity = 'CRITICAL') FROM alerts WHERE day = '{day}'"),
}


def compute(conn: sqlite3.Connection, day: str) -> tuple[dict[str, Any], list[tuple]]:
    """The day's numbers (None = no data) and every row they came from."""
    date.fromisoformat(day)  # only a real date reaches the SQL
    evidence: list[tuple] = []

    def rows(key: str) -> list[tuple]:
        result = run_query(conn, QUERIES[key].format(day=day))[1]
        evidence.extend(result)
        return result

    m: dict[str, Any] = {"day": day}
    m["visitors"] = rows("visitors")[0][0] or None
    peak = rows("peak")
    m["peak_h"], m["peak_n"] = (peak[0][0], peak[0][1]) if peak else (None, None)
    served, wait_min = rows("billing")[0]
    m["served"], m["wait_min"] = served or None, wait_min
    m["max_q"] = rows("max_q")[0][0]
    m["has_slots"] = rows("any_slots")[0][0] > 0
    m["empty"] = [r[0] for r in rows("empty")]
    m["low"] = [r[0] for r in rows("low")]
    lost, lost_n = rows("lost")[0]
    m["lost"], m["lost_n"] = (int(lost), lost_n) if lost_n else (None, 0)
    alerts, critical = rows("alerts")[0]
    m["alerts"], m["critical"] = alerts, critical or 0
    return m, evidence


def render(m: dict[str, Any], lang: str = "en") -> str:
    t = TEMPLATES[lang]

    def no(what: str) -> str:
        return t["no_data"].format(what=t[f"what_{what}"])

    lines = [t["title"].format(day=m["day"])]
    if m["visitors"]:
        lines.append(t["visitors"].format(visitors=m["visitors"], peak_h=m["peak_h"], peak_h1=m["peak_h"] + 1,
                                          peak_n=m["peak_n"]))
    else:
        lines.append(no("visitors"))
    if m["served"]:
        lines.append(t["billing"].format(served=m["served"], wait_min="-" if m["wait_min"] is None else m["wait_min"],
                                         max_q="-" if m["max_q"] is None else m["max_q"]))
    else:
        lines.append(no("billing"))
    if m["has_slots"]:
        lines.append(t["empty"].format(n_empty=len(m["empty"]), empty_list=", ".join(m["empty"]))
                     if m["empty"] else t["no_empty"])
        if m["low"]:
            lines.append(t["low"].format(n_low=len(m["low"]), low_list=", ".join(m["low"])))
    else:
        lines.append(no("shelves"))
    if m["lost"] is not None:
        lines.append(t["lost"].format(lost=m["lost"], lost_n=m["lost_n"]))
    if m["alerts"] or m["visitors"] or m["served"]:
        lines.append(t["alerts"].format(alerts=m["alerts"], critical=m["critical"]))
    else:  # nothing at all recorded: the system was probably off, so "0 alerts" would mislead
        lines.append(no("alerts"))
    return "\n".join(lines)


def daily_summary(conn: sqlite3.Connection, day: str, lang: str = "en") -> str:
    """Render one language, and refuse to return text with a number that did not
    come from the day's query rows (the counts of listed items are derived, and allowed)."""
    m, evidence = compute(conn, day)
    text = render(m, lang)
    derived = [(len(m["empty"]),), (len(m["low"]),), (m["peak_h"] + 1 if m["peak_h"] is not None else None,)]
    # "18:00" is a clock format: its "00" is not a measured number.
    invented = verify_numbers(text.replace(":00", ""), evidence + derived, day)
    if invented:
        raise ValueError(f"summary contains numbers not in the data: {invented}")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default="data/storemind.db")
    parser.add_argument("--day", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--lang", choices=[*LANGS, "all"], default="all")
    args = parser.parse_args(argv)
    conn = open_readonly(args.db)
    for lang in LANGS if args.lang == "all" else (args.lang,):
        print(daily_summary(conn, args.day, lang), end="\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
