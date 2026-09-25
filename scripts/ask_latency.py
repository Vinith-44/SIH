"""Time "Ask your store" through the dashboard API (M10 on the box).

    python scripts/ask_latency.py --url http://127.0.0.1:8000 --label pi5_qwen1.5b

Asks each question in Vinith's evaluation set (storemind/eval/eval_ask.py) through
`GET /api/ask`, records the wall time per question and which backend answered,
and writes storemind/storemind/eval/results/platform/ask_latency_<label>.json
(bucket S: speed only; accuracy is Vinith's docs/ASK.md).  docs/ASK.md says
"Pi 5 latency: not measured" - this is the script that measures it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "storemind"))
RESULTS = REPO / "storemind" / "storemind" / "eval" / "results" / "platform"


def load_questions(limit: int) -> list[str]:
    from storemind.eval.eval_ask import build_db, questions

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        truth = build_db(Path(tmp) / "q.db")
    return [q["q"] for q in questions(truth)][:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--device", default="Raspberry Pi 5")
    ap.add_argument("--label", default="pi5")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    times, backends, answered = [], {}, 0
    for question in load_questions(args.limit):
        started = time.perf_counter()
        with urllib.request.urlopen(f"{args.url}/api/ask?q={urllib.parse.quote(question)}", timeout=300) as r:
            body = json.load(r)
        elapsed = (time.perf_counter() - started) * 1000.0
        times.append(elapsed)
        backends[body.get("backend") or "none"] = backends.get(body.get("backend") or "none", 0) + 1
        answered += bool(body.get("answered"))
        print(f"{elapsed:8.0f} ms  {body.get('backend') or '-':>10}  {question}", flush=True)
    if not times:
        print("no questions")
        return 1
    ordered = sorted(times)
    rows = [
        {"metric": "questions", "value": str(len(times)), "target": ""},
        {"metric": "time per question p50 (ms)", "value": f"{statistics.median(times):.0f}", "target": "report"},
        {"metric": "time per question p95 (ms)", "value": f"{ordered[int(0.95 * (len(ordered) - 1))]:.0f}",
         "target": "report"},
        {"metric": "answered by", "value": ", ".join(f"{k} {v}" for k, v in backends.items()), "target": ""},
        {"metric": "answered (any backend)", "value": f"{answered}/{len(times)}",
         "target": "accuracy: docs/ASK.md, not here"},
    ]
    for r in rows:
        print(f"  {r['metric']}: {r['value']}")
    report = {"title": f"Ask your store latency - {args.device}", "bucket": "S", "device": args.device,
              "note": "Wall time of GET /api/ask per question from Vinith's question set, via the running dashboard "
                      "(includes the HTTP round trip). Correctness is graded in docs/ASK.md, not here.",
              "rows": rows,
              "commands": ["python scripts/ask_latency.py " + " ".join(argv if argv is not None else sys.argv[1:])]}
    if not args.no_write:
        out = RESULTS / f"ask_latency_{args.label}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
