"""End-to-end smoke test for M2 camera ingest.

    python tools/e2e_smoke.py                 # test-pattern camera, ~1 minute
    python tools/e2e_smoke.py --video storemind/data/test.mp4

Starts fake CCTV (MediaMTX + ffmpeg) -> go2rtc -> RtspReader, then checks:
  1. the camera comes online and delivers frames at a sane FPS,
  2. killing the camera is detected (stale/offline),
  3. restarting it recovers automatically,
  4. CAMERA_HEALTH payloads were produced and never contain a password.
Exit code 0 = all passed, 1 = a check failed, 2 = setup problem (missing binary).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STOREMIND = REPO_ROOT / "storemind"
sys.path.insert(0, str(STOREMIND))

from storemind.ingest.go2rtc import Go2rtc  # noqa: E402
from storemind.ingest.rtsp_reader import RtspReader  # noqa: E402
from storemind.ingest.urls import CameraEndpoint  # noqa: E402


def _load_fake_cctv():
    path = STOREMIND / "tools" / "fake_cctv.py"
    spec = importlib.util.spec_from_file_location("fake_cctv", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fake_cctv"] = mod
    spec.loader.exec_module(mod)
    return mod


def wait_until(pred, timeout_s: float, step_s: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(step_s)
    return pred()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, default=None, help="video file (default: test pattern)")
    ap.add_argument("--bin-dir", type=Path, default=REPO_ROOT / "tools" / "bin")
    ap.add_argument("--min-fps", type=float, default=5.0)
    ap.add_argument("--max-fps", type=float, default=60.0,
                    help="no camera we configure exceeds this; a higher reported rate "
                         "means the measurement is wrong, not the camera")
    ap.add_argument("--password", default="smoke-secret-123",
                    help="dummy password put in the camera URL to test masking")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    fake_cctv = _load_fake_cctv()
    try:
        go2rtc_bin = fake_cctv.find_binary("go2rtc", args.bin_dir)
        fake_cctv.find_binary("mediamtx", args.bin_dir)
        fake_cctv.find_binary("ffmpeg", args.bin_dir)
    except FileNotFoundError as e:
        print(f"SETUP  {e}")
        return 2

    health: list[dict] = []
    frames = [0]
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    # A dummy credential goes into the URL purely so the last check can prove it
    # never reaches a health payload.
    cam = CameraEndpoint(name="cam1", brand="mediamtx", ip="127.0.0.1", channel=1)
    secrets = {"cam1": {"username": "smoke", "password": args.password}}
    cctv = fake_cctv.FakeCCTV([args.video], bin_dir=args.bin_dir)
    g2 = Go2rtc([cam], secrets, binary=go2rtc_bin, workdir=REPO_ROOT / "runtime" / "e2e")
    reader = None
    try:
        cctv.start()
        g2.start()
        reader = RtspReader("cam1", g2.stream_url("cam1"),
                            on_frame=lambda c, f, ts: frames.__setitem__(0, frames[0] + 1),
                            on_health=health.append, stale_after_s=3, health_every_s=2)
        reader.start()
        wd = reader.watchdog

        online = wait_until(lambda: wd.state == "online", 20)
        check("camera online", online, f"state={wd.state}")
        time.sleep(5)
        fps = wd.fps()
        check("frames flowing", frames[0] > 0 and fps >= args.min_fps,
              f"frames={frames[0]} fps={fps}")

        cctv.stop_camera(1)
        down = wait_until(lambda: wd.health()["state"] in ("stale", "offline"), 25)
        check("outage detected", down, f"state={wd.state}")

        cctv.start_camera(1)
        wait_until(lambda: wd.state == "online", 45)
        time.sleep(3)
        # The state *now*, not "was online at some point": an earlier version of
        # this check used the wait_until result and reported PASS on a run that
        # ended stale, which is the one outcome it exists to catch.
        recovered_fps = wd.fps()
        check("auto recovery", wd.state == "online" and recovered_fps > 0,
              f"state={wd.state} fps={recovered_fps} reconnects={wd.reconnects}")

        states = {h["state"] for h in health}
        check("CAMERA_HEALTH produced", {"online"} <= states and len(health) >= 3,
              f"{len(health)} payloads, states={sorted(states)}")
        # A reconnect used to publish the buffered-frame burst as the rate -
        # 1532 FPS on a 15 FPS stream - straight onto the health panel.
        reported = [h["fps"] for h in health]
        worst = max(reported, default=0.0)
        check("fps stays plausible", worst <= args.max_fps,
              f"highest reported {worst} fps (ceiling {args.max_fps})")

        blob = json.dumps(health)
        check("no password in health", args.password not in blob)
    finally:
        if reader:
            reader.stop()
        g2.stop()
        cctv.stop()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
