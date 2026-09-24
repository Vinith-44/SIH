# Ask your store + daily summary

**Owner:** Vinith (Person A) · **Milestone:** M10 (stretch) · **Code:** `storemind/storemind/llm/`
(`views.py`, `ask.py`, `summary.py`) · **Eval:** `storemind/storemind/eval/eval_ask.py` ·
**Results:** `eval/results/ask*.json`, section "Ask your store" in RESULTS.md

## 1. The rule: the language model never supplies a number

A shopkeeper types *"How many customers came in today?"*. Every number in the answer comes from
the store's own event database, and the answer shows the query and the rows it came from.

```
question ──> SQL writer ──> read-only guard ──> SQLite rows ──> answer (+ query + rows)
             (local LLM, or        │                              │
              keyword rules)       │                              └─ number check
                          only SELECT on 8 views
```

1. **Views, not tables.** `views.py` defines eight flat views over the `events` table: `entries`,
   `queue`, `services`, `slots`, `alerts`, `lost_sales`, `picks` and `zone_visits`. They are
   created as TEMP views on a connection opened with `mode=ro`, so nothing is ever written to
   the store file (a test checks the file's hash).
2. **The guard is SQLite's, not ours.** A `set_authorizer` callback allows SELECT, reads of the
   eight views (the views' own reads of `events` are allowed), and a whitelist of plain functions.
   Everything else is refused by SQLite itself: the raw table, `sqlite_master`, PRAGMA, ATTACH,
   writes, DDL and `load_extension`. It also allows one statement only, at most 50 rows, and a
   2-second limit. We never try to decide from the SQL text whether a query is safe.
3. **Who writes the SQL.** A local model through [Ollama](https://ollama.com) (default
   `qwen2.5-coder:1.5b`, 1 GB, small enough for the Pi 5 CPU; its speed there is not measured yet). Keyword rules (`RuleBackend`) are the
   fallback when no model is installed or the model declines. The model sees the view
   descriptions, the names present in the data (counters, slots, SKUs, zones), today's date and
   the question. It never sees a row when it writes SQL.
4. **The answer.**
   - The answer is rendered from the rows.
   - For a one-row result, the model may write a sentence, but only as a *template*:
     `The busiest counter was {counter} with {customers_served} customers.` It writes no
     digits of its own, and the code fills each `{column}` with that column's cell.
   - `verify_numbers()` then checks the final text: every number must be a cell (or a rounding of
     one), or appear in the question. If not, the plain rows are shown instead.
5. **Citation.**
   - `Answer` carries `sql`, `columns` and `rows`, and `model_query=True` when the model wrote the
     query.
   - `Answer.cited()` prints "query written by the local model: check it asks what you meant".

The daily summary (section 4) uses no model at all.

## 2. What the number check does and does not prove

- **Proves:** no number was made up. Across 60 questions × every backend in every run: **0
  invented numbers.** The eval checks this with its own code, separate from the verifier in
  `ask.py`.
- **Does not prove the query asked the right thing.** A real, cited number from the wrong
  query is the main way this fails. Examples from the current code (run 4, set C):
  - "How many customers paid by UPI today?" → the model filtered `service_s = 'UPI'` and
    answered "0 customers". The data has no payment method.
  - "How many customers visited on 2026-03-14?" → it counted `zone_visits` instead of `entries`.
  - "What is the longest queue we had yesterday?" → `max(counter)`, i.e. the alphabetically last
    counter name.
  - "How many alerts in total over the last two days?" → `sum(severity)` = 0.

  **This is why the dashboard must always show the query next to an LLM answer.**
- **Units.** Until run 4 the model often called seconds "minutes": "the average service time
  was 109.45 minutes" for a 109 s average. Now a column's unit comes from its name (`_s`,
  `_min`, `_h`, `_inr`): the code writes "201.31 s", and a template that says "minutes" after a
  seconds column is rejected. In run 4 this rejected 3 of 17 model sentences.
- Not checked:
  - Numbers written as words ("two", "no one"). This is why model sentences are templates
    with no digits of their own.
  - A model can still put the right cell in the wrong sentence position. The template makes
    this much less likely than copying digits: run 3's "the quietest hour was **8**" (8 was the
    visitor count) can no longer happen.
  - Digits inside a column name the model chose (e.g. `hour_18`) are allowed; they are visible in
    the cited query.

## 3. Results and how we got there (bucket C, simulated store)

Two simulated days: visitors, billing, queue snapshots, shelf states, alerts, lost-sale risk,
picks and zone visits, 2,598 events written through the real `EventStore` and event schema.
The expected answers are computed in Python from the generated events, not with SQL.
"Correct" = the answer shows the expected value(s), with the right unit for times; "wrong" = a
cited answer with the wrong value or unit; "refused" = no answer. The two "cannot be answered"
questions in each set count as correct only if refused.

We wrote three sets of 20 questions, one after another, so that a set used to fix the system is
never the set that scores it:

| run | set | what changed before it | rules | LLM 1.5B | deployed |
|---|---|---|---|---|---|
| 1 | A (first run, untuned) | - | 18 / 1 / 1 | 8 / 8 / 4 | LLM then rules: 12 / 8 / 0 |
| 2 | **B (held out)** | from A's failures: prompt says how `ts` looks and what the data does not hold; a model sentence must repeat a row number; rules extended; rules placed first | 13 / 6 / 1 | 13 / 4 / 3 | rules then LLM: 13 / 7 / 0 |
| 3 | **C (held out, final)** | from B: the rules overfit (A 20/20, B 13/20), so LLM first again; lists not phrased; rules refuse "will / next / should / predict" questions | 12 / 6 / 2 | 11 / 7 / 2 | **LLM then rules: 13 / 7 / 0** |
| 4 | C again (no longer held out) | from C: sentences become `{column}` templates; units from column names | 12 / 6 / 2 | 13 / 5 / 2 | LLM then rules: 15 / 5 / 0 |

(correct / wrong / refused out of 20; invented numbers 0 in every cell.)

**Corrections to our own grading, disclosed:**
- The unit check was added to the grader after run 4. It found seconds called "minutes" in
  answers the old grader had marked correct.
- Runs 1–3 were re-graded with it (`regrade()` in eval_ask.py). The table shows the re-graded
  numbers; the original verdicts are kept in the JSON files next to a `regraded` block. Only
  the time questions were re-judged. Re-grading lowered LLM scores by 1–2 per set; the rules
  were unaffected.
- Recording extra ground truth before run 2 changed the order of the random draws for zone
  visits. Run 1's zone-visit data therefore differs slightly from later runs. Nothing else
  changed, because each simulated day has its own seeded generator and zone visits are drawn
  last. Run 1's zone answers keep the verdicts they got on run 1's data.

**Headline, honestly:**
- On questions nobody had seen (set C, first run), the deployed setup answered **13/20
  correctly, 7 wrong, 0 invented numbers**.
- The current code scores 15/20 on the same questions, but those are no longer unseen.
- **The M10 acceptance "20 test questions, 0 invented numbers" is met.**
- Answer *accuracy* is 65–75%, so the query must be visible (section 2).

Lessons:
- **Hand-written keyword rules overfit.** They scored 20/20 on the questions they were written
  against and 12–13/20 on new wording, and they fail by answering a *different* question
  confidently ("How many customers did billing-2 handle?" → total visitors).
- **The small LLM generalises a little better but writes wrong SQL about a quarter to a third of
  the time**, and left alone it mislabels units.
- **An automatic grader has blind spots too.** Ours missed wrong units until we read the
  answers.

Model size (same sets, `ask_3b.json`), laptop RTX 4050, median ≈5 s per question for both. The
Pi 5 time is not measured yet:

| LLM alone, then with rules | set C first run (held out) | set C, run 4 |
|---|---|---|
| qwen2.5-coder:1.5b (986 MB) | 11 / 7 / 2 → 13 / 7 / 0 | 13 / 5 / 2 → 15 / 5 / 0 |
| qwen2.5-coder:3b (1.9 GB) | 13 / 7 / 0 → 12 / 8 / 0 | 15 / 5 / 0 → 14 / 6 / 0 |

The 3B model is no better here, at twice the memory, so the default stays 1.5B.

**Hindi and Telugu questions** (not in the 20): the same visitor question in four phrasings:
- The rules answer none; they are English-only.
- The 1.5B model answers 2/4 (the Devanagari and the romanised Hindi); the 3B answered 3/4 in
  run 3.
- Neither invented a number.

The daily summary (below) is the reliable trilingual path.

## 4. Daily summary (English, Telugu, Hindi)

`summary.py`: fixed queries over the same views, fixed sentence templates per language, no model.
A section with no events says "no data", not 0. `daily_summary()` raises if any number in the text
is not in the query rows (the "18:00" clock format is exempt). Sample (simulated day):

```
StoreMind daily summary - 2026-03-15
178 people came in. Busiest hour: 18:00-19:00 (34 people).
131 customers were billed. Average wait 3.5 min; longest queue 8 people.
2 shelf slots were empty at the end of the day: Maggi, Amul Butter.
2 items are running low: Tata Salt, Dairy Milk.
Estimated sales at risk from empty shelves: Rs 771 (14 times a shopper looked at an empty or low shelf). This is an estimate.
7 alerts (2 critical).

StoreMind రోజువారీ సారాంశం - 2026-03-15
178 మంది దుకాణానికి వచ్చారు. అత్యంత రద్దీ సమయం: 18:00-19:00 (34 మంది).
...
StoreMind दैनिक सारांश - 2026-03-15
178 लोग दुकान में आए। सबसे व्यस्त समय: 18:00-19:00 (34 लोग)।
...
```

**Before a demo:** a native speaker must read the Telugu and Hindi templates (`TEMPLATES` in
`summary.py`). They were written by the assistant, not by a translator.

## 5. Running it

```bash
# once, on the laptop or the Pi 5 (ARM64 build exists): https://ollama.com/download
ollama pull qwen2.5-coder:1.5b

python -m storemind.llm.ask --db data/storemind.db "How many people came in today?"
python -m storemind.llm.ask --db data/storemind.db --model '' "..."    # rules only, no model
python -m storemind.llm.summary --db data/storemind.db --day 2026-03-15 --lang te
python -m storemind.eval.eval_ask                                       # the evaluation
```

Nothing leaves the machine. Ollama listens on localhost. The model sees view descriptions, the
names of counters/slots/SKUs/zones, and the question; the events hold no personal data anyway
(docs/PRIVACY_DPDP.md).

## 6. For Ram: wiring it into the dashboard (Person B's `api/`)

The two calls, both read-only and safe to run beside the writer (WAL):

```python
from storemind.llm.views import open_readonly
from storemind.llm.ask import ask, default_backends
from storemind.llm.summary import daily_summary

conn = open_readonly(db_path)                  # one per worker thread; check_same_thread=False
backends = default_backends()                  # LLM if Ollama has the model, then the rules
answer = ask(question, conn, backends)         # .to_dict(): text, sql, columns, rows, model_query, notes
text = daily_summary(conn, "2026-03-15", "te") # "en" | "te" | "hi"
```

Suggested endpoints:
- `GET /api/ask?q=...` returns `answer.to_dict()`.
- `GET /api/summary?day=YYYY-MM-DD&lang=te` returns the text.

UI rules:
1. Always show `sql` and the rows under an answer.
2. When `model_query` is true, add "written by the local model: check it".
3. Show `notes` if the answer fell back.

Two deploy items for Ram, and one optional contract:
- On the Pi: install Ollama and pull the model (`deploy/pi5/`), and measure the per-question time.
- `ask()` takes ≈5 s on the laptop GPU and will be slower on the Pi CPU, so call it off the
  event loop.
- Optional contract PR, if we want it configurable: an `llm:` config section
  (`enabled`, `model`, `host`).

## 7. Limits (read before quoting any number)

- Bucket C: questions on a simulated store written by us. Real shopkeepers phrase things we did
  not think of; expect lower accuracy.
- 20 questions per set is small: one question is 5 points.
- The grading is automatic, strict in some places and blind in others:
  - "The quietest hour was 11 AM" was marked wrong because the count was missing.
  - "Parle-G is in stock" was marked wrong because the check looks for the word FULL.
  - It only checks units on the time questions (section 3).
- The rules only understand English and the phrasings they were written for.
- Pi 5 latency: not measured.
