"""Guided MEMS + load-cell acceptance test (M6, docs/HARDWARE_TODO.md "M6").

    python tools/mems_test.py --port COM5 --config configs/myshelf.yaml     # the real shelf (bucket B)
    python tools/mems_test.py --simulate                                    # dry run on the simulator (bucket C)

A teammate follows the prompts: 30 picks, 10 put-backs, 10 touches without
taking anything, 10 accidental bumps, 10 knocks on the camera bracket, then
10 minutes with nobody near.  For every trial the tool opens a window, the
teammate does it and presses Enter, and the tool records what the node and
Vinith's fusion engine (fusion/interaction.py, the same code the pipeline runs)
produced inside that window.

Acceptance (CLAUDE_CODE_PROMPT_V2 M6):
  * picks and put-backs detected >= 90% (with weight gating),
  * fewer than 1 false TOUCH per 10 minutes idle,
  * at least 9 of 10 camera knocks detected.

With --simulate the simulator plays each trial itself, so the tool can be
checked end to end without hardware; that result is bucket C and says so.
Writes storemind/eval/results/platform/mems_<label>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storemind.core.bus import EventBus  # noqa: E402
from storemind.core.clock import WallClock  # noqa: E402
from storemind.core.config import MemsNodeConfig, StoreMindConfig, load_config  # noqa: E402
from storemind.core.events import EventType  # noqa: E402
from storemind.fusion.interaction import ShelfInteractionEngine, SlotInfo  # noqa: E402
from storemind.sensors.bridge import SensorBridge, SerialTransport, TcpTransport  # noqa: E402

RESULTS = ROOT / "storemind" / "eval" / "results" / "platform"

# (kind, count, prompt, simulator scenario, expected evidence)
TRIALS = [
    ("pick", 30, "Take ONE pack from slot {slot} and walk away", "pick"),
    ("put_back", 10, "Put ONE pack back on slot {slot}", "put_back"),
    ("touch", 10, "Touch / handle a pack on {slot} and leave it there", "lean"),
    ("bump", 10, "Bump the shelf with your hip or a trolley (don't take anything)", "trolley_knock"),
    ("camera_knock", 10, "Knock the camera bracket once with your knuckle", "camera_knock"),
]


@dataclass
class Window:
    kind: str
    start: float
    end: float = 0.0
    events: list[dict] = field(default_factory=list)


def detected(window: Window) -> bool:
    kinds = [(e["type"], e.get("kind"), e.get("action")) for e in window.events]
    if window.kind == "pick":
        return any(t == "PICKUP" and a == "pick" for t, _, a in kinds)
    if window.kind == "put_back":
        return any(t == "PICKUP" and a == "put_back" for t, _, a in kinds)
    if window.kind == "touch":
        return any(t == "SHELF_MOTION" and k == "TOUCH" for t, k, _ in kinds)
    if window.kind == "bump":
        return any(t == "SHELF_MOTION" and k == "KNOCK" for t, k, _ in kinds)
    if window.kind == "camera_knock":
        return any(t == "CAMERA_MOUNT" and k == "KNOCK" for t, k, _ in kinds)
    return False


def false_positive(window: Window) -> bool:
    """Things that must NOT happen in a trial of this kind."""
    actions = [e.get("action") for e in window.events if e["type"] == "PICKUP"]
    if window.kind in ("touch", "bump"):
        return "pick" in actions or "put_back" in actions        # no stock change happened
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--port", help="serial port of the board")
    target.add_argument("--tcp", help="host:port of a running simulator (you still press Enter)")
    target.add_argument("--simulate", action="store_true", help="the simulator plays every trial")
    ap.add_argument("--config", default=None, help="store config: sensors.mems_nodes, cell_map, unit_grams")
    ap.add_argument("--slot", default="A1", help="slot the trials use")
    ap.add_argument("--unit-grams", type=float, default=218.0, help="pack weight if the config has none")
    ap.add_argument("--idle-minutes", type=float, default=10.0)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply trial counts (0.1 = a quick rehearsal: 3 picks, 1 of the rest)")
    ap.add_argument("--speed", type=float, default=20.0, help="--simulate: MCU seconds per real second")
    ap.add_argument("--label", default=None)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)

    config = load_config(args.config) if args.config else StoreMindConfig()
    config.sensors.enabled = True
    if not config.sensors.mems_nodes:
        config.sensors.mems_nodes = [MemsNodeConfig(id="m1", role="shelf", shelf="shelf-a", slot=args.slot),
                                     MemsNodeConfig(id="m2", role="camera_mount", cam="entrance")]
    shelf_node = next(m for m in config.sensors.mems_nodes if m.role == "shelf")
    channel = config.sensors.cell_map.get(f"{shelf_node.shelf}/{args.slot}", "1")

    node = None
    if args.simulate:
        from storemind.sensors.simulator import LoopbackTransport, VirtualNode

        node = VirtualNode(seed=7, slots={channel: (20 * args.unit_grams, args.unit_grams)})
        # Each read runs 100 ms of MCU time; 100 ms / speed of real time per read.
        transport = LoopbackTransport(node, step_ms=100, sleep_s=0.1 / args.speed)
        bucket, device = "C", "STM32 simulator"
    elif args.tcp:
        transport, bucket, device = TcpTransport(args.tcp), "C", "STM32 simulator (manual)"
    else:
        transport, bucket, device = SerialTransport(args.port, config.sensors.baud), "B", "STM32 + MPU6050 + HX711"

    bus = EventBus()
    engine = ShelfInteractionEngine(
        [SlotInfo(shelf_node.shelf, args.slot, channel, args.unit_grams)], store=config.store,
        node=config.node, mems_enabled=True, person_at_shelf=lambda shelf: True)
    clock = WallClock()
    current: list[Window] = []
    idle_touches: list[dict] = []
    in_idle = [False]

    def record(event_type: str, data: dict) -> None:
        entry = {"type": event_type, **{k: data.get(k) for k in ("kind", "action", "units", "grams")}}
        if current:
            current[-1].events.append(entry)
        if in_idle[0] and event_type == "SHELF_MOTION" and data.get("kind") == "TOUCH":
            idle_touches.append(entry)

    def on_event(event) -> None:
        record(event.type.value, event.data)
        for produced in engine.on_event(event, clock):
            record(produced.type.value, produced.data)

    bus.subscribe_types([EventType.SHELF_MOTION, EventType.WEIGHT, EventType.CAMERA_MOUNT], on_event)
    bridge = SensorBridge(config, bus, transport).start()

    def mcu_wait(ms: int) -> None:
        """--simulate: wait until the simulated MCU clock has moved on by `ms`."""
        target = node.ms + ms
        wait(lambda: node.ms >= target, 60.0)

    def do_trial(kind: str, scenario: str, prompt: str, i: int, total: int) -> None:
        window = Window(kind, time.monotonic())
        current.append(window)
        if args.simulate:
            with transport._lock:                    # noqa: SLF001 - the reader thread runs the node
                duration = node.scenario(scenario)
            mcu_wait(duration + 3000)                # SETTLED + the stable weight, then a gap
        else:
            input(f"[{kind} {i}/{total}] {prompt.format(slot=args.slot)} - press Enter when done ")
            time.sleep(2.0)                          # let SETTLED and the stable weight arrive
        window.end = time.monotonic()

    try:
        if not wait(lambda: bridge.link == "up", 20.0):
            print("no heartbeat from the node - check the connection (tools/hil_test.py first)")
            return 1
        if args.simulate:
            mcu_wait(12000)                          # a stable baseline weight first
        else:
            time.sleep(3.0)
        windows_by_kind: dict[str, list[Window]] = {}
        for kind, count, prompt, scenario in TRIALS:
            total = max(1, round(count * args.scale))
            for i in range(1, total + 1):
                do_trial(kind, scenario, prompt, i, total)
            windows_by_kind[kind] = [w for w in current if w.kind == kind]

        if not args.simulate:
            input(f"Idle test: step away from the shelf for {args.idle_minutes:g} min. Press Enter to start ")
        current.append(Window("idle", time.monotonic()))
        in_idle[0] = True
        if args.simulate:
            mcu_wait(int(args.idle_minutes * 60_000))
        else:
            time.sleep(args.idle_minutes * 60.0)
        in_idle[0] = False
    finally:
        bridge.stop()

    rows = []
    passed = True
    for kind, *_ in TRIALS:
        ws = windows_by_kind.get(kind, [])
        hits = sum(detected(w) for w in ws)
        wrong = sum(false_positive(w) for w in ws)
        rate = hits / len(ws) if ws else 0.0
        target = {"pick": 0.9, "put_back": 0.9, "camera_knock": 0.9}.get(kind)
        ok = target is None or rate >= target
        passed &= ok
        rows.append({"metric": f"{kind}: detected", "value": f"{hits}/{len(ws)} ({rate:.0%})",
                     "target": f">= {target:.0%}" if target else "report"})
        if kind in ("touch", "bump"):
            rows.append({"metric": f"{kind}: wrongly counted as pick/put-back", "value": str(wrong),
                         "target": "0"})
    per_10 = len(idle_touches) / max(args.idle_minutes, 1e-9) * 10.0
    passed &= per_10 < 1.0
    rows.append({"metric": f"false TOUCH while idle ({args.idle_minutes:g} min)", "value": str(len(idle_touches)),
                 "target": "< 1 per 10 min"})
    for r in rows:
        print(f"  {r['metric']}: {r['value']}  (target {r['target']})")

    report = {
        "title": f"MEMS + load cell acceptance - {device}",
        "bucket": bucket,
        "device": device,
        "note": ("PASS" if passed else "FAIL") + f". Slot {args.slot}, pack {args.unit_grams:g} g, "
                f"trial counts x{args.scale:g}. Pick/put-back come from Vinith's ShelfInteractionEngine "
                "fed by the bridge." + (" Simulated trials: checks the tool and the logic, not the sensor."
                                       if bucket == "C" else ""),
        "rows": rows,
        "commands": ["python tools/mems_test.py " + " ".join(argv if argv is not None else sys.argv[1:])],
    }
    if not args.no_write:
        label = args.label or f"{datetime.now():%Y%m%d}_{'sim' if bucket == 'C' else 'board'}"
        RESULTS.mkdir(parents=True, exist_ok=True)
        out = RESULTS / f"mems_{label}.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    print("RESULT:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


def wait(predicate, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


if __name__ == "__main__":
    sys.exit(main())
