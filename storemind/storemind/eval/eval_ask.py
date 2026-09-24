"""M10 acceptance: 20 questions to "Ask your store", 0 invented numbers.

Bucket C.  `build_db()` writes two simulated days of events (visitors, billing,
queue snapshots, shelf states, alerts, lost-sale risk, picks, zone visits)
through the real `EventStore` and event schema, so the views read exactly what
the pipeline would store.  The expected answers are computed in Python from
the generated events, never with SQL, so a wrong query cannot also be the
reference.

Three numbers per backend:
* **correct** - the answer text shows the expected value(s);
* **wrong** - an answer with the wrong value.  The cited rows are real, but
  the query asked the wrong thing: this is the realistic failure of LLM-written SQL;
* **invented numbers** - a number in the answer that is in none of the cited
  rows, the question or the date.  The acceptance target is 0.  It is checked
  here by code independent of `llm/ask.py`'s own verifier.

Three question sets, written one after another (docs/ASK.md section 3):
* A (`questions`) - first run; then used to write the rules and fix the prompt;
* B (`heldout_questions`) - held out for run 2; then used for the next changes;
* C (`final_questions`) - written after those changes; its first run (run 3) is
  the held-out result.  Later runs on C are no longer held out.
The keyword rules were written against set A, so their A score shows coverage,
not generalisation.  The LLM prompt's examples (`FEW_SHOT`) are other questions.
The unit check in `grade()` was added after run 4; `regrade()` applies it to
the saved earlier runs.  Recording extra truth before run 2 reordered the
zone-visit random draws, so run 1's zone-visit data differs from later runs.

    python -m storemind.eval.eval_ask                       # rules, and the LLM if Ollama has it
    python -m storemind.eval.eval_ask --model ''            # rules only
"""

from __future__ import annotations

import argparse
import json
import random
import re
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from ..core.events import EventType, make_event
from ..llm.ask import NO_DATA, OllamaBackend, RuleBackend, ask
from ..llm.summary import LANGS, daily_summary
from ..llm.views import open_readonly
from ..store.db import EventStore

RESULTS = Path(__file__).resolve().parent / "results" / "ask.json"
SETS = ("C_final", "B_heldout_run2", "A_dev", "other_languages")
IST = timezone(timedelta(hours=5, minutes=30))
TODAY, YESTERDAY = date(2026, 3, 15), date(2026, 3, 14)
SKUS = {("s1", "a1"): "Maggi", ("s1", "a2"): "Parle-G", ("s1", "a3"): "Tata Salt",
        ("s2", "b1"): "Surf Excel", ("s2", "b2"): "Amul Butter", ("s2", "b3"): "Dairy Milk"}
PRICE = {"Maggi": 14, "Parle-G": 10, "Tata Salt": 28, "Surf Excel": 110, "Amul Butter": 56, "Dairy Milk": 40}
# Each slot's states through the day: (hour, minute, state).  Parle-G empties and is restocked.
SLOT_PLAN = {
    YESTERDAY: {k: [(9, 0, "FULL")] for k in SKUS},
    TODAY: {("s1", "a1"): [(9, 0, "FULL"), (13, 10, "LOW"), (17, 40, "EMPTY")],
            ("s1", "a2"): [(9, 0, "FULL"), (12, 0, "LOW"), (14, 0, "EMPTY"), (15, 5, "FULL")],
            ("s1", "a3"): [(9, 0, "FULL"), (18, 20, "LOW")],
            ("s2", "b1"): [(9, 0, "FULL")],
            ("s2", "b2"): [(9, 0, "FULL"), (11, 30, "LOW"), (16, 0, "EMPTY")],
            ("s2", "b3"): [(9, 0, "LOW"), (10, 0, "FULL"), (19, 45, "LOW")]},
}


def _ts(day: date, hour: int, minute: int, second: int = 0) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST).isoformat()


def simulate(seed: int = 7) -> tuple[list, dict]:
    """(events, truth).  truth holds the Python-computed answers."""
    events: list = []
    truth: dict = {}

    def add(day, h, m, s, etype, data, cam=None):
        events.append(make_event(ts=_ts(day, h, m, s), store="demo-store", node="pi-1", type=etype,
                                 data=data, cam=cam))

    track = 0
    for index, day in enumerate((YESTERDAY, TODAY)):
        rng = random.Random(seed * 100 + index)
        key = day.isoformat()
        scale = 0.8 if day == YESTERDAY else 1.0
        per_hour = Counter()
        exits = 0
        for hour in range(9, 21):
            base = {12: 14, 13: 16, 17: 22, 18: 30, 19: 21}.get(hour, 8)
            for _ in range(int(base * scale) + rng.randint(0, 4)):
                track += 1
                minute, second = rng.randint(0, 59), rng.randint(0, 59)
                add(day, hour, minute, second, EventType.ENTRY,
                    {"line": "door", "track": track, "direction": "in"}, cam="door")
                per_hour[hour] += 1
                out = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST) + timedelta(
                    minutes=rng.randint(4, 35))
                if out.date() == day and out.hour < 22:
                    add(day, out.hour, out.minute, second, EventType.EXIT,
                        {"line": "door", "track": track, "direction": "out"}, cam="door")
                    exits += 1
        peak = max(sorted(per_hour), key=lambda h: per_hour[h])
        truth[key] = {"in": sum(per_hour.values()), "in_by_hour": dict(per_hour), "out": exits,
                      "peak_h": peak, "peak_n": per_hour[peak],
                      "peak_hours": [h for h in per_hour if per_hour[h] == per_hour[peak]]}

        served: Counter = Counter()
        waits, service_c2, services = [], [], []
        waits_by: dict[str, list] = {}
        for hour in range(9, 21):
            for counter, weight in (("billing-1", 1.0), ("billing-2", 0.6)):
                n = int((per_hour[hour] * 0.7) * weight / 1.6) + rng.randint(0, 2)
                for _ in range(n):
                    track += 1
                    service = round(rng.uniform(40, 180), 1)
                    wait = round(rng.uniform(0, 420), 1) if rng.random() > 0.1 else None
                    add(day, hour, rng.randint(0, 59), rng.randint(0, 59), EventType.SERVICE_DONE,
                        {"counter": counter, "track": track, "service_s": service, "wait_s": wait}, cam="billing")
                    served[counter] += 1
                    services.append(service)
                    if wait is not None:
                        waits.append(wait)
                        waits_by.setdefault(counter, []).append(wait)
                    if counter == "billing-2":
                        service_c2.append(service)
        longest, longest_by = 0, Counter()
        for hour in range(9, 21):
            for minute in range(0, 60, 2):
                for counter in ("billing-1", "billing-2"):
                    length = max(0, int(rng.gauss(per_hour[hour] / 6, 1.5)))
                    longest = max(longest, length)
                    longest_by[counter] = max(longest_by[counter], length)
                    add(day, hour, minute, 0, EventType.QUEUE_STATE,
                        {"counter": counter, "queue_len": length, "queue_len_smooth": float(length),
                         "median_wait_s": round(length * 55.0, 1), "queue_parties": max(0, length - 1)},
                        cam="billing")
        truth[key] |= {"served": sum(served.values()), "top_counter": served.most_common(1)[0][0],
                       "served_by": dict(served), "avg_service_s": mean(services),
                       "avg_wait_s": mean(waits), "avg_service_c2": mean(service_c2), "max_q": longest,
                       "max_q_by": dict(longest_by), "avg_wait_by": {c: mean(v) for c, v in waits_by.items()},
                       "quiet_hours": [h for h in per_hour if per_hour[h] == min(per_hour.values())],
                       "quiet_n": min(per_hour.values())}

        final = {}
        for (shelf, slot), plan in SLOT_PLAN[day].items():
            for hour, minute, state in plan:
                add(day, hour, minute, 0, EventType.SLOT_STATE,
                    {"shelf": shelf, "slot": slot, "sku": SKUS[(shelf, slot)], "state": state, "fill": None,
                     "reason": "reference" if state != "FULL" else "restocked"}, cam="shelf")
                final[(shelf, slot)] = state
        truth[key]["final_state"] = {SKUS[k]: v for k, v in final.items()}
        truth[key] |= {"empty": sorted(SKUS[k] for k, s in final.items() if s == "EMPTY"),
                       "low": sorted(SKUS[k] for k, s in final.items() if s == "LOW")}

        n_alerts = 4 if day == YESTERDAY else 7
        critical = 1 if day == YESTERDAY else 2
        for i in range(n_alerts):
            severity = "CRITICAL" if i < critical else ("WARN" if i % 2 else "INFO")
            add(day, 10 + i, 15, 0, EventType.ALERT,
                {"severity": severity, "message_key": "queue_long" if i % 2 else "slot_empty",
                 "message": "simulated alert", "alert_id": f"{key}-{i}"})
        truth[key] |= {"alerts": n_alerts, "critical": critical,
                       "warn": sum(1 for i in range(critical, n_alerts) if i % 2),
                       "info": sum(1 for i in range(critical, n_alerts) if not i % 2)}

        lost: Counter = Counter()
        lost_n = 0
        for (shelf, slot), plan in SLOT_PLAN[day].items():
            for hour, minute, state in plan:
                if state == "FULL":
                    continue
                sku = SKUS[(shelf, slot)]
                for j in range(rng.randint(2, 5) if state == "EMPTY" else rng.randint(0, 2)):
                    value = round(PRICE[sku] * rng.uniform(1, 3), 2)
                    add(day, min(hour + 1, 21), (minute + 7 * j) % 60, 0, EventType.LOST_SALE_RISK,
                        {"shelf": shelf, "slot": slot, "sku": sku, "price": PRICE[sku],
                         "dwell_s": round(rng.uniform(3, 12), 1), "est_value": value}, cam="shelf")
                    lost[sku] += value
                    lost_n += 1
        truth[key] |= {"lost": sum(lost.values()), "lost_top": lost.most_common(1)[0][0] if lost else None,
                       "lost_n": lost_n}

        units_a1 = put_backs = 0
        units_by_shelf: Counter = Counter()
        for _ in range(rng.randint(40, 60)):
            shelf, slot = rng.choice(list(SKUS))
            action = rng.choices(["pick", "put_back", "touch"], [0.7, 0.15, 0.15])[0]
            units = rng.randint(1, 3) if action != "touch" else None
            add(day, rng.randint(9, 20), rng.randint(0, 59), 0, EventType.PICKUP,
                {"shelf": shelf, "slot": slot, "grams": None if units is None else units * 70.0,
                 "evidence": "weight+mems", "action": action, "units": units})
            if action == "pick" and slot == "a1":
                units_a1 += units
            if action == "pick":
                units_by_shelf[shelf] += units
            put_backs += action == "put_back"
        promo = 0
        zone_n: Counter = Counter()
        zone_dwell: dict[str, list] = {}
        for _ in range(rng.randint(30, 50)):
            zone = rng.choice(["promo-endcap", "entrance", "dairy"])
            track += 1
            dwell = round(rng.uniform(2, 40), 1)
            add(day, rng.randint(9, 20), rng.randint(0, 59), 0, EventType.ZONE_VISIT,
                {"zone": zone, "zone_kind": "promo" if zone.startswith("promo") else "zone", "track": track,
                 "dwell_s": dwell})
            promo += zone == "promo-endcap"
            zone_n[zone] += 1
            zone_dwell.setdefault(zone, []).append(dwell)
        truth[key] |= {"units_a1": units_a1, "put_backs": put_backs, "promo": promo,
                       "units_by_shelf": dict(units_by_shelf), "zone_n": dict(zone_n),
                       "zone_dwell": {z: mean(v) for z, v in zone_dwell.items()}}
    return events, truth


def build_db(path: Path, seed: int = 7) -> dict:
    events, truth = simulate(seed)
    store = EventStore(path)
    for start in range(0, len(events), 2000):   # EventStore's queue holds 10k and drops beyond
        for event in events[start:start + 2000]:
            store.handle(event)
        store.flush(timeout=30)
    store.close()
    truth["n_events"] = len(events)
    return truth


# --------------------------------------------------------------------------- #

def questions(truth: dict) -> list[dict]:
    t, y = truth[TODAY.isoformat()], truth[YESTERDAY.isoformat()]
    return [
        {"q": "How many customers came in today?", "all": [[t["in"]]]},
        {"q": "Show the number of visitors for yesterday and today.", "all": [[y["in"]], [t["in"]]]},
        {"q": "What was the busiest hour today?", "all": [[t["peak_h"]], [t["peak_n"]]]},
        {"q": "How many people entered between 6 pm and 7 pm today?", "all": [[t["in_by_hour"][18]]]},
        {"q": "How many customers were served at the billing counters today?", "all": [[t["served"]]]},
        {"q": "What was the average waiting time at billing today?",
         "all": [[t["avg_wait_s"], t["avg_wait_s"] / 60]], "seconds": True},
        {"q": "What was the longest queue today?", "all": [[t["max_q"]]]},
        {"q": "Which counter served the most customers today?", "names": [t["top_counter"]],
         "universe": ["billing-1", "billing-2"]},
        {"q": "What is the average service time at counter 2 today?", "all": [[t["avg_service_c2"]]], "seconds": True},
        {"q": "Which products are out of stock right now?", "names": t["empty"], "universe": list(SKUS.values())},
        {"q": "Which items are running low?", "names": t["low"], "universe": list(SKUS.values())},
        {"q": "How many alerts were raised today?", "all": [[t["alerts"]]]},
        {"q": "How many critical alerts were there yesterday?", "all": [[y["critical"]]]},
        {"q": "What is the estimated value of lost sales today?", "all": [[t["lost"]]]},
        {"q": "Which product lost the most sales today?", "names": [t["lost_top"]], "universe": list(SKUS.values())},
        {"q": "How many units were picked from slot a1 today?", "all": [[t["units_a1"]]]},
        {"q": "How many times did shoppers put items back today?", "all": [[t["put_backs"]]]},
        {"q": "How many shoppers visited the promo end-cap today?", "all": [[t["promo"]]]},
        {"q": "What is our profit margin this month?", "refuse": True},
        {"q": "Who was the cashier at counter 1 this morning?", "refuse": True},
    ]


def heldout_questions(truth: dict) -> list[dict]:
    """The acceptance set.  Written after run 1 of `questions()` and before any run
    on these; never used to change the rules or the prompt."""
    t, y = truth[TODAY.isoformat()], truth[YESTERDAY.isoformat()]
    return [
        {"q": "How many visitors did we have yesterday?", "all": [[y["in"]]]},
        {"q": "What was today's footfall?", "all": [[t["in"]]]},
        {"q": "How many people left the shop today?", "all": [[t["out"]]]},
        {"q": "Between 12 pm and 1 pm today, how many people walked in?", "all": [[t["in_by_hour"][12]]]},
        {"q": "At what hour did the most customers arrive yesterday?", "all": [y["peak_hours"], [y["peak_n"]]]},
        {"q": "How many bills were done yesterday?", "all": [[y["served"]]]},
        {"q": "On average, how long did customers wait to be billed yesterday?",
         "all": [[y["avg_wait_s"], y["avg_wait_s"] / 60]], "seconds": True},
        {"q": "How many customers did billing-2 handle today?", "all": [[t["served_by"]["billing-2"]]]},
        {"q": "What was the maximum number of people in the queue at billing-1 today?",
         "all": [[t["max_q_by"]["billing-1"]]]},
        {"q": "How long does it take on average to bill one customer today?", "all": [[t["avg_service_s"]]], "seconds": True},
        {"q": "Is anything out of stock?", "names": t["empty"], "universe": list(SKUS.values())},
        {"q": "Which shelf slots are low right now?", "names": t["low"], "universe": list(SKUS.values())},
        {"q": "How many warning alerts were raised today?", "all": [[t["warn"]]]},
        {"q": "Give me the total alerts for yesterday.", "all": [[y["alerts"]]]},
        {"q": "How much money did we possibly lose from empty shelves yesterday?", "zero_or_no_data": True},
        {"q": "How many units were taken from shelf s2 today?", "all": [[t["units_by_shelf"]["s2"]]]},
        {"q": "What was the average time shoppers spent in the dairy zone today?",
         "all": [[t["zone_dwell"]["dairy"]]], "seconds": True},
        {"q": "How many zone visits were recorded at the entrance today?", "all": [[t["zone_n"]["entrance"]]]},
        {"q": "What will footfall be next Sunday?", "refuse": True},
        {"q": "What is the phone number of the store owner?", "refuse": True},
    ]


def final_questions(truth: dict) -> list[dict]:
    """Set C, the final acceptance set.  Written after run 2 and after the changes
    it prompted (docs/ASK.md section 3), before any run on these questions."""
    t, y = truth[TODAY.isoformat()], truth[YESTERDAY.isoformat()]
    return [
        {"q": "How many customers visited on 2026-03-14?", "all": [[y["in"]]]},
        {"q": "Tell me how many people came into the store this evening between 5 pm and 6 pm.",
         "all": [[t["in_by_hour"][17]]]},
        {"q": "How many exits were counted yesterday?", "all": [[y["out"]]]},
        {"q": "Which hour was the quietest today?", "all": [t["quiet_hours"], [t["quiet_n"]]]},
        {"q": "What's the average wait at billing-1 today?",
         "all": [[t["avg_wait_by"]["billing-1"], t["avg_wait_by"]["billing-1"] / 60]], "seconds": True},
        {"q": "How many customers were billed at counter 1 yesterday?", "all": [[y["served_by"]["billing-1"]]]},
        {"q": "What is the longest queue we had yesterday?", "all": [[y["max_q"]]]},
        {"q": "What was the average service time yesterday?", "all": [[y["avg_service_s"]]], "seconds": True},
        {"q": "List the products that are empty on the shelves now.", "names": t["empty"],
         "universe": list(SKUS.values())},
        {"q": "What is the stock status of Parle-G right now?", "words": [t["final_state"]["Parle-G"]]},
        {"q": "How many info alerts were there today?", "all": [[t["info"]]]},
        {"q": "How many alerts in total over the last two days?", "all": [[t["alerts"] + y["alerts"]]]},
        {"q": "Which SKU had the highest estimated lost sales yesterday?", "zero_or_no_data": True},
        {"q": "How many lost-sale events were recorded today?", "all": [[t["lost_n"]]]},
        {"q": "How many units of stock were picked from shelf s1 today?", "all": [[t["units_by_shelf"]["s1"]]]},
        {"q": "How many put-backs were recorded yesterday?", "all": [[y["put_backs"]]]},
        {"q": "How many people visited the dairy zone yesterday?", "all": [[y["zone_n"]["dairy"]]]},
        {"q": "What is the average dwell time at the promo end-cap today?",
         "all": [[t["zone_dwell"]["promo-endcap"]]], "seconds": True},
        {"q": "Should I hire another cashier?", "refuse": True},
        {"q": "How many customers paid by UPI today?", "refuse": True},
    ]


def extra_language_questions(truth: dict) -> list[dict]:
    """Not part of the 20: the same visitor question in Hindi and Telugu."""
    n = truth[TODAY.isoformat()]["in"]
    return [{"q": q, "all": [[n]]} for q in (
        "आज कितने ग्राहक आए?", "ఈ రోజు ఎంత మంది కస్టమర్లు వచ్చారు?", "aaj kitne log aaye?",
        "ee roju entha mandi vacharu?")]


_NUM = re.compile(r"(?<![\w.])-?\d+(?:,\d{2,3})*(?:\.\d+)?")


def _nums(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _NUM.findall(text)]


def invented(answer) -> list[float]:
    """Independent re-check: numbers in the text not traceable to rows/question/date/columns."""
    pool: list[float] = []
    for row in answer.rows:
        for cell in row:
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                pool += [float(cell), round(cell), round(cell, 1), round(cell, 2)]
            elif cell is not None:
                pool += _nums(str(cell))
    pool += _nums(answer.question) + _nums(TODAY.isoformat()) + _nums(" ".join(answer.columns))
    return [n for n in _nums(answer.text) if all(abs(n - p) > 1e-9 for p in pool)]


def grade(item: dict, answer) -> str:
    if item.get("refuse"):
        return "correct" if not answer.answered else "wrong"
    if not answer.answered:
        return "refused"
    text = answer.text
    if item.get("zero_or_no_data"):
        return "correct" if text == NO_DATA or all(v == 0 for v in _nums(text)) and _nums(text) else "wrong"
    if "words" in item:
        return "correct" if all(w.lower() in text.lower() for w in item["words"]) else "wrong"
    if "names" in item:
        low = text.lower()
        shown = {n for n in item["universe"] if n.lower() in low}
        return "correct" if shown == set(item["names"]) else "wrong"
    values = _nums(text)
    if item.get("seconds"):
        # The first value is in seconds, an optional second one in minutes.  A seconds
        # value called "minutes" (run 4: "201.31 minutes") is wrong, whatever the digits.
        secs, *mins = item["all"][0]
        low = text.lower()
        says_min, says_s = "minute" in low or " min" in low, bool(re.search(r"\bs\b|second|avg[ _]\w*[ _]s\b", low))
        if any(abs(v - secs) <= 0.06 for v in values) and says_min and not says_s:
            return "wrong"
        if mins and any(abs(v - mins[0]) <= 0.06 for v in values) and says_s and not says_min:
            return "wrong"
    for alternatives in item["all"]:
        if not any(abs(v - a) <= max(0.06, 0.001 * abs(a)) for v in values for a in alternatives):
            return "wrong"
    return "correct"


def run_backend(conn, items: list[dict], backends: list, label: str) -> dict:
    rows, counts, times = [], Counter(), []
    for item in items:
        start = time.perf_counter()
        answer = ask(item["q"], conn, backends, today=TODAY)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        verdict = grade(item, answer)
        bad = invented(answer)
        counts[verdict] += 1
        counts["invented_numbers"] += len(bad)
        counts["phrased"] += answer.phrased
        counts["phrasing_dropped"] += any(n.startswith("phrasing dropped") for n in answer.notes)
        counts["answered_by_llm"] += answer.backend == "ollama"
        rows.append({"q": item["q"], "verdict": verdict, "backend": answer.backend, "text": answer.text,
                     "sql": answer.sql, "rows": [list(r) for r in answer.rows[:5]], "notes": answer.notes,
                     "invented": bad, "seconds": round(elapsed, 2)})
        print(f"  [{verdict:7}] {item['q']}\n            -> {answer.text.splitlines()[0] if answer.text else ''}"
              + (f"   INVENTED {bad}" if bad else ""), flush=True)
    return {"label": label, "n": len(items), "correct": counts["correct"], "wrong": counts["wrong"],
            "refused": counts["refused"], "invented_numbers": counts["invented_numbers"],
            "phrased": counts["phrased"], "phrasing_dropped": counts["phrasing_dropped"],
            "answered_by_llm": counts["answered_by_llm"],
            "median_s": sorted(times)[len(times) // 2], "rows": rows}


def regrade(path: Path) -> dict:
    """Re-grade a saved run with the current `grade()` (the unit check was added
    after run 4).  The original verdicts are kept; the new counts are stored
    beside them under "regraded"."""
    from types import SimpleNamespace

    data = json.loads(path.read_text(encoding="utf-8"))
    truth = simulate(data.get("seed", 7))[1]
    items = {i["q"]: i for f in (questions, heldout_questions, final_questions, extra_language_questions)
             for i in f(truth)}
    for report in data["report"]:
        sets = {k: v for k, v in report.items() if isinstance(v, dict) and "rows" in v}
        if "rows" in report:  # run 1's layout: set A at the top level
            sets["A_dev"] = report
        for result in sets.values():
            counts = Counter()
            for row in result["rows"]:
                item = items[row["q"]]
                if not item.get("seconds"):
                    # Only the unit check is new.  Other questions keep their verdict: run 1's
                    # zone-visit data differs from today's generator (docs/ASK.md section 3).
                    counts[row["verdict"]] += 1
                    continue
                answer = SimpleNamespace(text=row["text"], answered=row["sql"] is not None)
                counts[grade(item, answer)] += 1
            result["regraded"] = {"correct": counts["correct"], "wrong": counts["wrong"],
                                  "refused": counts["refused"], "note": "grade() with the unit check"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="qwen2.5-coder:1.5b", help="Ollama model; '' = rules only")
    parser.add_argument("--rules", action=argparse.BooleanOptionalAction, default=True,
                        help="include the rules-only row")
    parser.add_argument("--host", default="http://localhost:11434")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(RESULTS))
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "ask_eval.db"
        truth = build_db(db, args.seed)
        conn = open_readonly(db)
        try:
            report, summaries, empty_day = evaluate(conn, truth, args)
        finally:
            conn.close()

    out = {"bucket": "C", "today": TODAY.isoformat(), "seed": args.seed, "n_events": truth["n_events"],
           "report": report, "summary_sample": summaries, "summary_no_data_sample": empty_day}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print()
    for r in report:
        for name in SETS:
            x = r[name]
            print(f"{r['label']:45} {name:15}: correct {x['correct']}/{x['n']}, wrong {x['wrong']}, "
                  f"refused {x['refused']}, invented numbers {x['invented_numbers']}, median {x['median_s']:.2f} s")
    print(f"wrote {args.out}")
    return 0 if all(r[n]["invented_numbers"] == 0 for r in report for n in SETS) else 1


def evaluate(conn, truth: dict, args) -> tuple[list, dict, dict]:
    sets = {"C_final": final_questions(truth), "B_heldout_run2": heldout_questions(truth),
            "A_dev": questions(truth), "other_languages": extra_language_questions(truth)}
    configs: list[tuple[str, list]] = [("rules", [RuleBackend()])] if args.rules else []
    llm = OllamaBackend(model=args.model, host=args.host) if args.model else None
    if llm is not None and llm.available():
        configs += [(f"llm ({args.model})", [llm]),
                    (f"llm ({args.model}), then rules - deployed", [llm, RuleBackend()])]
    elif llm is not None:
        print(f"Ollama model {args.model} not available: LLM rows skipped")
    report = []
    for label, backends in configs:
        result = {"label": label}
        for name, items in sets.items():
            print(f"--- {label}: {name} ---")
            result[name] = run_backend(conn, items, backends, label)
        report.append(result)

    summaries = {lang: daily_summary(conn, TODAY.isoformat(), lang) for lang in LANGS}
    empty_day = {lang: daily_summary(conn, "2026-03-01", lang) for lang in ("en",)}
    return report, summaries, empty_day


if __name__ == "__main__":
    raise SystemExit(main())
