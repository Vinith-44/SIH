"""Timed photos of a real demo shelf, for the bucket-B shelf test set (M3).

Point a camera at a shelf (hostel store, canteen rack, lab cupboard) and leave
this running through a day: morning, afternoon sun, evening, tube lights, lights
off.  Every `--every` seconds it saves one JPEG plus a line in `captures.csv`
with the time and, if an STM32 with a BH1750 is connected, the lux.  Change the
shelf now and then (take packets out, put a wrong one in, restock) - then label
the photos with `tools/shelf_label.py`.

Shelves only: frame the camera so no people are in the picture.  If someone
walks into frame, delete that photo.  Photos stay on the laptop (videos/ and
snapshots are git-ignored).

    python tools/shelf_capture.py --source 0 --out ../videos/shelf_real/day1 --every 120
    python tools/shelf_capture.py --source rtsp://... --serial COM5 --out ... --every 300
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_lux(port) -> float | None:
    """Latest `$E` lux from the sensor node, if one is connected."""
    if port is None:
        return None
    from storemind.sensors.protocol import ProtocolError, parse_line

    lux = None
    for raw in port.read(port.in_waiting or 1).decode("ascii", "replace").splitlines():
        try:
            message = parse_line(raw.strip())
        except ProtocolError:
            continue
        if message.type == "E" and message.values.get("lux") is not None:
            lux = float(message.values["lux"])
    return lux


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="0 (webcam), a file, or rtsp://...")
    parser.add_argument("--out", required=True)
    parser.add_argument("--every", type=float, default=120.0, help="seconds between photos")
    parser.add_argument("--count", type=int, default=0, help="stop after N photos (0 = forever)")
    parser.add_argument("--serial", default=None, help="STM32 port for BH1750 lux, e.g. COM5")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    source = int(args.source) if args.source.isdigit() else args.source
    port = None
    if args.serial:
        import serial  # pyserial

        port = serial.Serial(args.serial, 115200, timeout=0.2)
    index_path = out / "captures.csv"
    new = not index_path.exists()
    taken = 0
    with index_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["file", "time", "lux"])
        while args.count == 0 or taken < args.count:
            capture = cv2.VideoCapture(source)   # reopen each time: survives RTSP drops
            ok, frame = capture.read()
            capture.release()
            if not ok:
                print("no frame; retrying in 10 s", flush=True)
                time.sleep(10)
                continue
            stamp = datetime.now()
            name = f"shelf_{stamp:%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(str(out / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            lux = read_lux(port)
            writer.writerow([name, stamp.isoformat(timespec="seconds"), "" if lux is None else lux])
            handle.flush()
            taken += 1
            print(f"{name}  lux={lux}", flush=True)
            time.sleep(args.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
