# Shelf engine under changing light (synthetic, bucket C) - seeds 11-40

| engine | state acc (lit) | EMPTY P/R/F1 | LOW F1 | WRONG_ITEM F1 | false EMPTY when dark | UNKNOWN when dark |
|---|---|---|---|---|---|---|
| v1 | 54.7% | 0.98/0.59/0.74 | 0.55 | 0.21 | 0 of 714 | 0 of 714 |
| v2-no-lux | 91.7% | 0.97/0.89/0.93 | 0.88 | 0.62 | 0 of 714 | 714 of 714 |
| v2 | 91.7% | 0.97/0.88/0.92 | 0.87 | 0.63 | 0 of 714 | 714 of 714 |

EMPTY F1 by lighting:

| engine | day | dim | evening | glare | tube |
|---|---|---|---|---|---|
| v1 | 0.91 | 0.33 | 0.15 | 0.90 | 0.90 |
| v2-no-lux | 0.91 | 0.89 | 0.95 | 0.89 | 0.94 |
| v2 | 0.91 | 0.89 | 0.93 | 0.89 | 0.94 |

`v1` = the engine as shipped before M3 (single reference, fixed Canny, no CLAHE). `v2-no-lux` = M3 engine without the BH1750 (brightness fallback). `v2` = with lux.

Command: `python -m storemind.eval.eval_shelf_lighting`
