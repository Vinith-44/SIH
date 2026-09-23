"""Ground-truth labelling helper.

Hand-counting a 10-minute clip with a stopwatch is slow and produces timestamps
nobody trusts.  This plays the clip in an OpenCV window with pause, frame-step
and slow motion, and writes a CSV with exact video timestamps in the format the
evaluation scripts expect.

    python tools/label_ground_truth.py --video ../videos/entrance/clip.mp4 --mode entrance
    python tools/label_ground_truth.py --video ../videos/queue/clip.mp4    --mode queue
    python tools/label_ground_truth.py --video ../videos/shelf/clip.mp4    --mode shelf

Common keys
    space   pause / resume
    . / ,   step one frame forward / back (while paused)
    [ / ]   slower / faster
    u       undo the last row
    s       save now
    q / Esc save and quit

Entrance mode
    i   a person crossed INWARD at this instant
    o   a person crossed OUTWARD

Queue mode
    0-9   the queue length right now (a row every time you press)
    j     the customer you are following joined the lane
    b     billing started for them
    e     billing ended for them

Shelf mode
    Type the slot name (letters/digits), then press one of:
    f FULL   l LOW   x EMPTY   w WRONG_ITEM
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2

HELP = {
    "entrance": "i = IN   o = OUT",
    "queue": "0-9 = queue length   j = joined   b = billing start   e = billing end",
    "shelf": "type slot name then  f=FULL l=LOW x=EMPTY w=WRONG_ITEM",
}

HEADERS = {
    "entrance": ["video_time_s", "direction", "note"],
    "queue_length": ["video_time_s", "counter", "queue_length"],
    "queue_waits": ["customer", "counter", "joined_s", "service_start_s", "service_s", "wait_s"],
    "shelf": ["video_time_s", "shelf", "slot", "state"],
}


def draw_hud(frame, lines: list[str]):
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (frame.shape[1], 26 + 22 * len(lines)), (0, 0, 0), -1)
    frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (10, 22 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (250, 250, 250), 1, cv2.LINE_AA)
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True)
    parser.add_argument("--mode", required=True, choices=["entrance", "queue", "shelf"])
    parser.add_argument("--counter", default="counter-1")
    parser.add_argument("--shelf", default="shelf-a")
    parser.add_argument("--out", default=None, help="output CSV (default: next to the video)")
    args = parser.parse_args()

    video = Path(args.video)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"could not open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    rows: list[list] = []
    waits: list[list] = []
    pending: dict[str, float] = {}
    customer = 1
    slot_buffer = ""
    paused = False
    speed = 1.0
    window = f"label: {video.name}"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    frame_index = 0
    frame = None
    while True:
        if not paused or frame is None:
            ok, frame = cap.read()
            if not ok:
                break
            frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        t = frame_index / fps

        hud = [
            f"{video.name}   t={t:7.2f}s   frame {frame_index}/{total}   "
            f"{'PAUSED' if paused else f'x{speed:.2f}'}",
            HELP[args.mode],
            f"rows: {len(rows)}" + (f"   waits: {len(waits)}" if args.mode == "queue" else "")
            + (f"   slot: {slot_buffer}" if args.mode == "shelf" else ""),
        ]
        if rows:
            hud.append("last: " + ", ".join(str(v) for v in rows[-1]))
        cv2.imshow(window, draw_hud(frame.copy(), hud))

        delay = 1 if paused else max(1, int(1000.0 / (fps * speed)))
        key = cv2.waitKey(delay) & 0xFF
        if key == 255:
            continue

        if key in (ord("q"), 27):
            break
        if key == ord(" "):
            paused = not paused
        elif key == ord("."):
            paused = True
            ok, nxt = cap.read()
            if ok:
                frame = nxt
                frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        elif key == ord(","):
            paused = True
            target = max(0, frame_index - 2)
            cap.set(cv2.CAP_PROP_POS_FRAMES, target)
            ok, prev = cap.read()
            if ok:
                frame = prev
                frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        elif key == ord("["):
            speed = max(0.1, speed / 2)
        elif key == ord("]"):
            speed = min(8.0, speed * 2)
        elif key == ord("u"):
            if rows:
                rows.pop()
        elif key == ord("s"):
            pass  # saved below on every loop exit; explicit save happens at the end

        elif args.mode == "entrance" and key in (ord("i"), ord("o")):
            rows.append([f"{t:.2f}", "in" if key == ord("i") else "out", ""])
        elif args.mode == "queue":
            if ord("0") <= key <= ord("9"):
                rows.append([f"{t:.2f}", args.counter, key - ord("0")])
            elif key == ord("j"):
                pending["joined"] = t
            elif key == ord("b"):
                pending["start"] = t
            elif key == ord("e") and "joined" in pending and "start" in pending:
                waits.append([customer, args.counter, f"{pending['joined']:.2f}",
                              f"{pending['start']:.2f}", f"{t - pending['start']:.2f}",
                              f"{pending['start'] - pending['joined']:.2f}"])
                customer += 1
                pending.clear()
        elif args.mode == "shelf":
            if chr(key).isalnum() and key not in (ord("f"), ord("l"), ord("x"), ord("w")):
                slot_buffer += chr(key).upper()
            elif key in (ord("f"), ord("l"), ord("x"), ord("w")):
                state = {ord("f"): "FULL", ord("l"): "LOW",
                         ord("x"): "EMPTY", ord("w"): "WRONG_ITEM"}[key]
                rows.append([f"{t:.2f}", args.shelf, slot_buffer or "A1", state])
                slot_buffer = ""
            elif key == 8:  # backspace
                slot_buffer = slot_buffer[:-1]

    cap.release()
    cv2.destroyAllWindows()

    stem = video.with_suffix("")
    written = []
    if args.mode == "entrance":
        path = Path(args.out) if args.out else Path(f"{stem}_gt_entries.csv")
        _write(path, HEADERS["entrance"], rows)
        written.append(path)
    elif args.mode == "queue":
        path = Path(f"{stem}_gt_queue_length.csv")
        _write(path, HEADERS["queue_length"], rows)
        written.append(path)
        if waits:
            path = Path(f"{stem}_gt_waits.csv")
            _write(path, HEADERS["queue_waits"], waits)
            written.append(path)
    else:
        path = Path(args.out) if args.out else Path(f"{stem}_gt_slots.csv")
        _write(path, HEADERS["shelf"], rows)
        written.append(path)

    for path in written:
        print(f"wrote {path}")
    return 0


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
