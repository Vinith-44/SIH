"""Soak test: run the whole stack for hours and report what grows (M7b).

    python scripts/soak.py --hours 1 --label laptop_1h          # laptop: fake CCTV + simulator
    python scripts/soak.py --hours 24 --label pi5_24h           # the Pi (docs/HARDWARE_TODO.md "M8")

Runs, in one process: the pipeline on three live RTSP cameras (fake CCTV =
MediaMTX + ffmpeg looping the synthetic clips), the serial bridge to the STM32
simulator, and the dashboard API.  Every `--every` seconds it records memory
(RSS), threads, OS handles, SQLite DB + WAL size, events per type, the bridge's
error counters and whether the pipeline thread is alive.

Pass criteria (research/23 section 6, "the box must not degrade"):
  * pipeline, bridge and API alive for the whole run;
  * RSS growth after the first 10 minutes (warm-up) below --max-mb-per-hour;
  * thread count stable (no thread leak);
  * 0 checksum / framing errors and 0 lost lines on the serial link;
  * events keep arriving in every sample (no silent stall).

Writes a per-sample CSV and `storemind/storemind/eval/results/platform/soak_<label>.json`.
The detector is `stub` by default (no model on the laptop): this soaks the
platform (ingest, bus, SQLite, API, bridge), not the vision model. `--detector
ultralytics` on the Pi soaks both.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import REPO, Harness, db_sizes, process_stats  # noqa: E402

RESULTS = REPO / "storemind" / "storemind" / "eval" / "results" / "platform"


def slope_per_hour(times_s: list[float], values: list[float]) -> float:
    """Least-squares slope, units per hour."""
    n = len(times_s)
    if n < 2:
        return 0.0
    mt, mv = sum(times_s) / n, sum(values) / n
    den = sum((t - mt) ** 2 for t in times_s)
    if den == 0:
        return 0.0
    return sum((t - mt) * (v - mv) for t, v in zip(times_s, values)) / den * 3600.0


def api_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--every", type=float, default=60.0, help="seconds between samples")
    ap.add_argument("--warmup-min", type=float, default=10.0)
    ap.add_argument("--max-mb-per-hour", type=float, default=20.0)
    ap.add_argument("--detector", default="stub")
    ap.add_argument("--config", type=Path, default=None, help="store config (default configs/demo.yaml)")
    ap.add_argument("--no-cctv", action="store_true", help="use the config's sources instead of fake CCTV")
    ap.add_argument("--rtsp-port", type=int, default=8554)
    ap.add_argument("--api-port", type=int, default=8766)
    ap.add_argument("--device", default="ASUS Vivobook 15 (Ram's laptop)")
    ap.add_argument("--label", default=None)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    duration = args.hours * 3600.0
    # ignore_cleanup_errors: on Windows the reorder DB (Vinith's ReorderQueue) stays open
    tmp = tempfile.TemporaryDirectory(prefix="storemind_soak_", ignore_cleanup_errors=True)
    db = Path(tmp.name) / "soak.db"
    harness = Harness(db_path=db, rtsp_port=args.rtsp_port, api_port=args.api_port,
                      use_cctv=not args.no_cctv, detector=args.detector, config_path=args.config)
    label = args.label or f"{datetime.now():%Y%m%d_%H%M}"
    csv_path = (RESULTS if not args.no_write else Path(tmp.name)) / f"soak_{label}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []
    started = time.monotonic()
    started_at = datetime.now().astimezone()
    harness.start(run_seconds=duration + 60)
    print(f"soak: {args.hours:g} h, sample every {args.every:g} s, dashboard http://127.0.0.1:{args.api_port}/",
          flush=True)
    last_events = 0
    try:
        while time.monotonic() - started < duration:
            time.sleep(min(args.every, max(0.0, duration - (time.monotonic() - started))))
            harness.pipeline.store.flush()
            total_events = sum(harness.counts.values())
            stats = harness.bridge.stats
            sample = {
                "t_s": round(time.monotonic() - started, 1),
                **process_stats(),
                **db_sizes(db),
                "events_total": total_events,
                "events_since_last": total_events - last_events,
                "pipeline_alive": harness.alive(),
                "api_ok": api_ok(args.api_port),
                "bridge_connected": harness.bridge.connected,
                "crc_err": stats.crc_err, "rx_err": stats.rx_err, "lost": stats.lost,
                "reconnects": stats.reconnects,
                "camera_fps": json.dumps(harness.pipeline.health.fps()),
            }
            last_events = total_events
            samples.append(sample)
            print(f"  {sample['t_s'] / 60:6.1f} min  rss {sample['rss_mb']} MB  threads {sample['threads']}  "
                  f"db {sample['db_mb']} MB  events +{sample['events_since_last']}  "
                  f"alive {sample['pipeline_alive']}  api {sample['api_ok']}", flush=True)
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(samples[0]))
                writer.writeheader()
                writer.writerows(samples)
    except KeyboardInterrupt:
        print("interrupted: reporting what was measured", flush=True)
    finally:
        harness.stop()

    if not samples:
        print("no samples")
        return 1
    warm = [s for s in samples if s["t_s"] >= args.warmup_min * 60.0] or samples
    rss_slope = slope_per_hour([s["t_s"] for s in warm], [s["rss_mb"] for s in warm])
    db_slope = slope_per_hour([s["t_s"] for s in samples], [s["db_mb"] + s["wal_mb"] for s in samples])
    threads = [s["threads"] for s in warm]
    last = samples[-1]
    checks = {
        "pipeline alive all run": all(s["pipeline_alive"] for s in samples),
        "API answered every sample": all(s["api_ok"] for s in samples),
        "bridge connected every sample": all(s["bridge_connected"] for s in samples),
        f"RSS growth after warm-up < {args.max_mb_per_hour:g} MB/h": rss_slope < args.max_mb_per_hour,
        "thread count stable (max - min <= 3)": max(threads) - min(threads) <= 3,
        "serial link: 0 crc / 0 framing / 0 lost": last["crc_err"] == 0 and last["rx_err"] == 0 and last["lost"] == 0,
        "events every sample": all(s["events_since_last"] > 0 for s in samples),
    }
    ran_h = last["t_s"] / 3600.0
    rows = [
        {"metric": "duration", "value": f"{ran_h:.2f} h", "target": f"{args.hours:g} h"},
        {"metric": "RSS start / end", "value": f"{samples[0]['rss_mb']} / {last['rss_mb']} MB", "target": ""},
        {"metric": "RSS growth after warm-up", "value": f"{rss_slope:+.1f} MB/h",
         "target": f"< {args.max_mb_per_hour:g} MB/h"},
        {"metric": "threads (min / max after warm-up)", "value": f"{min(threads)} / {max(threads)}", "target": "stable"},
        {"metric": "OS handles start / end", "value": f"{samples[0]['handles']} / {last['handles']}", "target": ""},
        {"metric": "DB + WAL growth", "value": f"{db_slope:+.2f} MB/h", "target": "report"},
        {"metric": "events stored", "value": str(last["events_total"]), "target": ""},
        {"metric": "serial link crc / framing / lost / reconnects",
         "value": f"{last['crc_err']} / {last['rx_err']} / {last['lost']} / {last['reconnects']}", "target": "0 / 0 / 0"},
    ]
    rows += [{"metric": name, "value": "PASS" if ok else "FAIL", "target": "PASS"} for name, ok in checks.items()]
    passed = all(checks.values())
    for r in rows:
        print(f"  {r['metric']}: {r['value']}" + (f"  (target {r['target']})" if r["target"] else ""))
    report = {
        "title": f"Soak test {ran_h:.1f} h - {args.device}",
        "bucket": "S",
        "device": args.device,
        "note": (("PASS" if passed else "FAIL") + f". Started {started_at.isoformat(timespec='seconds')}. "
                 f"Pipeline live on 3 RTSP cameras from fake CCTV (MediaMTX + ffmpeg looping the synthetic clips) "
                 f"+ STM32 simulator + bridge + API in one process; detector `{args.detector}`"
                 + (" (no inference: a platform soak, not a vision soak)" if args.detector == "stub" else "")
                 + f". Per-sample CSV: eval/results/platform/{csv_path.name}."),
        "rows": rows,
        "commands": ["python scripts/soak.py " + " ".join(argv if argv is not None else sys.argv[1:])],
    }
    if not args.no_write:
        out = RESULTS / f"soak_{label}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out.relative_to(REPO)}")
    print("RESULT:", "PASS" if passed else "FAIL")
    tmp.cleanup()
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
