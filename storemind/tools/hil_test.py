"""Hardware-in-the-loop test for the STM32 sensor node (M5).

    python tools/hil_test.py --port COM5 --minutes 60          # the real board (bucket B)
    python tools/hil_test.py --port /dev/storemind-mcu          # on the Pi
    python tools/hil_test.py --simulate --minutes 60            # the simulator, fast (bucket C)

It talks to the node through the same SensorBridge the pipeline uses and checks,
in order:

  1. heartbeat: a `$H` arrives within 15 s (prints reset cause, free heap,
     smallest stack high-water mark);
  2. commands: every command type gets the `$K` PROTOCOL.md says it should
     (LED patterns OK, MODE,BIN -> ERR 3, unknown sensor -> ERR 3, a corrupted
     line -> ERR 1), and the round-trip time (p50 / p95 / max);
  3. sensors: which line types the node sends while you wave at the PIR, walk
     through the beams, press the restock button and touch the shelf;
  4. framing soak: `--minutes` of traffic with zero checksum / framing errors and
     zero lost lines.

Writes `storemind/eval/results/platform/hil_<label>.json` (docs/INTERFACES.md §5),
so RESULTS.md shows exactly what was measured and with which command.
Nothing here is claimed unless it ran: a skipped phase is reported as skipped.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storemind.core.bus import EventBus  # noqa: E402
from storemind.core.config import StoreMindConfig, load_config  # noqa: E402
from storemind.sensors.bridge import SensorBridge, SerialTransport, TcpTransport  # noqa: E402
from storemind.sensors.protocol import encode  # noqa: E402

RESULTS = ROOT / "storemind" / "eval" / "results" / "platform"

# (label, type, fields, expected status, expected code)
COMMAND_CASES = [
    ("time sync", "S", [1790239200000], "OK", 0),
    ("LED ON", "L", ["ON"], "OK", 0),
    ("LED SLOW", "L", ["SLOW"], "OK", 0),
    ("LED ALERT", "L", ["ALERT"], "OK", 0),
    ("LED OFF", "L", ["OFF"], "OK", 0),
    ("buzzer FAST", "Z", ["FAST"], "OK", 0),
    ("buzzer OFF", "Z", ["OFF"], "OK", 0),
    ("text mode", "C", ["MODE", "TXT"], "OK", 0),
    ("binary mode refused (PROTOCOL.md 6 not final)", "C", ["MODE", "BIN"], "ERR", 3),
    ("unknown sensor", "C", ["ENABLE", "toaster", 1], "ERR", 3),
    ("servo (off by default)", "V", [90], "ERR", 4),
]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def wait_for(predicate, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--port", help="serial port of the board (COM5, /dev/storemind-mcu)")
    target.add_argument("--tcp", help="host:port of a running simulator")
    target.add_argument("--simulate", action="store_true", help="start the simulator in-process")
    ap.add_argument("--config", default=None, help="store config (for node id / timeouts)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--minutes", type=float, default=60.0, help="framing soak length")
    ap.add_argument("--rtt-rounds", type=int, default=50, help="LED commands timed for the RTT")
    ap.add_argument("--sensor-seconds", type=float, default=60.0,
                    help="phase 3: seconds to wave / walk / press / touch (0 = skip)")
    ap.add_argument("--speed", type=float, default=30.0, help="--simulate: MCU seconds per real second")
    ap.add_argument("--label", default=None, help="result file name (default: date + target)")
    ap.add_argument("--device", default="STM32F103C8 Blue Pill", help="what was tested, for the report")
    ap.add_argument("--no-write", action="store_true", help="print only, no result file")
    args = ap.parse_args(argv)

    config = load_config(args.config) if args.config else StoreMindConfig()
    config.sensors.enabled = True
    server = None
    if args.simulate:
        from storemind.sensors.simulator import SimulatorServer, VirtualNode

        server = SimulatorServer(VirtualNode(seed=1), port=0, speed=args.speed, scenario="demo").start()
        transport = TcpTransport(f"127.0.0.1:{server.port}")
        bucket, device = "C", "STM32 simulator (storemind.sensors.simulator)"
    elif args.tcp:
        transport, bucket, device = TcpTransport(args.tcp), "C", "STM32 simulator over TCP"
    else:
        transport, bucket, device = SerialTransport(args.port, args.baud), "B", args.device

    bus = EventBus()
    seen_types: dict[str, int] = {}
    health: list[dict] = []

    def on_event(event) -> None:
        seen_types[event.type.value] = seen_types.get(event.type.value, 0) + 1
        if event.type.value == "NODE_HEALTH":
            health.append(dict(event.data))

    bus.subscribe_all(on_event)
    bridge = SensorBridge(config, bus, transport).start()
    rows: list[dict] = []
    failures: list[str] = []
    started = datetime.now().astimezone()

    def row(metric: str, value, target: str = "", ok: bool | None = None) -> None:
        rows.append({"metric": metric, "value": str(value), "target": target})
        mark = "" if ok is None else ("PASS " if ok else "FAIL ")
        print(f"  {mark}{metric}: {value}" + (f"  (target {target})" if target else ""), flush=True)
        if ok is False:
            failures.append(metric)

    try:
        # 1. heartbeat --------------------------------------------------------
        print("1/4 heartbeat", flush=True)
        alive = wait_for(lambda: bool(health), 15.0)
        row("heartbeat ($H) within 15 s", "yes" if alive else "no", "yes", alive)
        if not alive:
            print("no $H: check wiring (TX->RX crossed, common GND), baud, and that the board is flashed",
                  flush=True)
            return 1
        h = health[-1]
        row("reset cause at start", h["reset_cause"])
        row("free heap (bytes)", h["free_heap"])
        row("smallest stack high-water mark (words)", h["min_stack_words"], "> 16", h["min_stack_words"] > 16)

        # 2. commands ---------------------------------------------------------
        print("2/4 commands", flush=True)
        for label, msg_type, fields, want_status, want_code in COMMAND_CASES:
            result = bridge.send_command(msg_type, fields)
            ok = result.status == want_status and result.code == want_code
            row(f"$K for {label}", f"{result.status} {result.code} ({result.attempts} attempt(s))",
                f"{want_status} {want_code}", ok)
        # A corrupted line must still be answered (code 1) so the Pi retries quickly.
        bad = encode("L", 200, 0, ["ON"])
        star = bad.index("*")
        bad = bad[:star + 1] + f"{int(bad[star + 1:star + 3], 16) ^ 0xFF:02X}\r\n"
        with bridge._write_lock:  # noqa: SLF001 - a raw, deliberately broken line
            bridge.transport.write(bad.encode())
        answered = wait_for(lambda: (200, "ERR", 1) in bridge.acks, 1.0)
        row("corrupted command answered with ERR 1", "yes" if answered else "no", "yes", answered)

        rtts: list[float] = []
        for i in range(args.rtt_rounds):
            t0 = time.perf_counter()
            result = bridge.send_command("L", ["ON" if i % 2 else "OFF"])
            if result.status == "OK" and result.attempts == 1:
                rtts.append((time.perf_counter() - t0) * 1000.0)
        if rtts:
            row("command round trip p50 (ms)", f"{statistics.median(rtts):.1f}")
            row("command round trip p95 (ms)", f"{percentile(rtts, 0.95):.1f}", "< 200", percentile(rtts, 0.95) < 200)
            row("commands answered first time", f"{len(rtts)}/{args.rtt_rounds}", "all",
                len(rtts) == args.rtt_rounds)

        # 3. sensors ----------------------------------------------------------
        if args.sensor_seconds > 0:
            print(f"3/4 sensors: for {args.sensor_seconds:.0f} s wave at the PIR, walk through the beams "
                  "both ways, press the restock button, touch the shelf, knock the camera bracket", flush=True)
            base = dict(seen_types)
            time.sleep(args.sensor_seconds)
            for kind in ("WEIGHT", "ENVIRONMENT", "PRESENCE", "BEAM_CROSS", "SENSOR", "SHELF_MOTION",
                         "CAMERA_MOUNT"):
                row(f"{kind} events during phase 3", seen_types.get(kind, 0) - base.get(kind, 0))
        else:
            print("3/4 sensors: skipped", flush=True)

        # 4. framing soak -----------------------------------------------------
        print(f"4/4 framing soak: {args.minutes:g} min", flush=True)
        s0 = bridge.stats.as_dict()
        end = time.monotonic() + args.minutes * 60.0
        while time.monotonic() < end:
            time.sleep(min(5.0, max(0.0, end - time.monotonic())))
            if not bridge.connected:
                row("link stayed up", "no", "yes", False)
                break
        s1 = bridge.stats.as_dict()
        lines = s1["lines"] - s0["lines"]
        crc = s1["crc_err"] - s0["crc_err"]
        rx = s1["rx_err"] - s0["rx_err"]
        lost = s1["lost"] - s0["lost"]
        row("soak lines received", lines)
        row("soak checksum errors", crc, "0", crc == 0)
        row("soak framing/field errors", rx, "0", rx == 0)
        row("soak lost lines (seq gaps)", lost, "0", lost == 0)
        row("reconnects during the run", bridge.stats.reconnects, "0", bridge.stats.reconnects == 0)
        if health:
            row("MCU-side uart_err at the end", health[-1]["uart_err"])
            row("MCU-side i2c_err at the end", health[-1]["i2c_err"])
    finally:
        bridge.stop()
        if server is not None:
            server.stop()

    target_name = args.port or args.tcp or "simulator"
    report = {
        "title": f"HIL test - {device}",
        "bucket": bucket,
        "device": device,
        "note": (f"{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}. "
                 f"Started {started.isoformat(timespec='seconds')} on {target_name}; "
                 f"{args.minutes:g} min framing soak"
                 + (f" (simulator at {args.speed:g}x, so {args.minutes * args.speed:g} MCU minutes)"
                    if args.simulate else "") + "."),
        "rows": rows,
        "commands": ["python tools/hil_test.py " + " ".join(argv if argv is not None else sys.argv[1:])],
    }
    if not args.no_write:
        label = args.label or f"{started:%Y%m%d}_{'sim' if bucket == 'C' else 'board'}"
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"hil_{label}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT.parent)}")
    print("RESULT:", "PASS" if not failures else f"FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
