# Ground truth templates

Nothing goes on a slide unless it has a measured number, and a measured number
needs something to measure against. These four CSVs are that something.

Copy the template next to the clip it describes and rename it to match the video
file, e.g. for `videos/entrance/entrance_canteen_2026-09-24_1300.mp4`:

```
videos/entrance/entrance_canteen_2026-09-24_1300_gt_entries.csv
```

The evaluation scripts find ground truth by that `<video stem>_gt_*.csv` naming,
so the names matter.

## The fastest way to fill them in

Use the labelling helper instead of a stopwatch and a notebook:

```bash
cd storemind
.venv/Scripts/python tools/label_ground_truth.py --video ../videos/entrance/<clip>.mp4 --mode entrance
.venv/Scripts/python tools/label_ground_truth.py --video ../videos/queue/<clip>.mp4    --mode queue
.venv/Scripts/python tools/label_ground_truth.py --video ../videos/shelf/<clip>.mp4    --mode shelf
```

It plays the clip, lets you pause, step frame by frame and slow down, and writes
the CSV with the correct video timestamps. Keys are shown on screen.

## What each file means

### `*_gt_entries.csv` - entrance
One row per person crossing the counting line.

| column | meaning |
|---|---|
| `video_time_s` | seconds from the start of the clip, when the person's **feet** cross the line |
| `direction` | `in` or `out` |
| `note` | free text, e.g. "carrying a box", "two people together" |

Mark the crossing when the feet cross, not when the head does - that is what the
system measures, and disagreeing on the definition would make the error look
larger than it is.

### `*_gt_queue_length.csv` - queue
One row every 10 seconds per counter.

`queue_length` = people **waiting** in the lane, **not** counting whoever is
currently being billed.

### `*_gt_waits.csv` - queue
One row per customer you follow (10-20 is plenty).

| column | meaning |
|---|---|
| `joined_s` | when their feet enter the queue lane |
| `service_start_s` | when they reach the billing spot and billing begins |
| `service_s` | how long billing took |
| `wait_s` | `service_start_s - joined_s` |

### `*_gt_slots.csv` - shelf
One row per **state change**, not per frame.

`state` is one of `FULL`, `LOW`, `EMPTY`, `WRONG_ITEM`. Start each slot with a
row at `video_time_s = 0` giving its state at the beginning of the clip.

`LOW` needs a house rule so two people label it the same way. Ours: **LOW = less
than half the facings the slot has when freshly restocked.**

## Rules that keep the numbers honest

1. Label **before** you look at what StoreMind produced. Deciding "the system
   was probably right" afterwards is not ground truth.
2. Two people should label the first clip independently. If you disagree on more
   than ~5% of events, the definition is unclear - fix the definition, not the
   data.
3. Note anything genuinely ambiguous in the `note` column instead of silently
   guessing. Failure cases are worth slide space.
4. These CSVs may be committed (they are small text and contain no images). The
   **videos must not be** - they stay on the laptop and are deleted after
   testing.
