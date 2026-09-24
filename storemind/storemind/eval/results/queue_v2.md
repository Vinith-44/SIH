# Queue v1 vs v2 on simulated tracks (bucket C) - seeds 11-40

| engine | queue MAE (people) | party MAE | median-wait err | Little's-law W err | joins (truth) | balks (truth) | reneges (truth) | walked away unserved (truth) | tail overflow recall / false alarm |
|---|---|---|---|---|---|---|---|---|---|
| v1 | 0.38 | 0.85 | 2.7% | 53.1% | 2598 (833) | 0 (65) | 0 (30) | 0 (95) | 0.0% / 0.0% (1 true-overflow samples) |
| v2 | 0.19 | 0.15 | 2.8% | 20.4% | 825 (833) | 0 (65) | 87 (30) | 87 (95) | 100.0% / 0.1% (1 true-overflow samples) |
| v1 noisy | 0.41 | 0.89 | 52.5% | 61.2% | 3081 (833) | 0 (65) | 0 (30) | 0 (95) | 0.0% / 0.0% (1 true-overflow samples) |
| v2 noisy | 0.21 | 0.18 | 32.9% | 12.4% | 969 (833) | 0 (65) | 118 (30) | 118 (95) | 100.0% / 0.4% (1 true-overflow samples) |

v1 has no party, balk or renege logic: its party MAE uses its people count, and its balk/renege counts are 0 by construction.

**The balk / renege split is not reliable:** with the tuned 3 s join time, people who stop 3-6 s and leave are counted as joining and then reneging. Their sum (walked away unserved) is the usable number. Tail overflow is barely exercised by this simulator (see the true-overflow sample count); it is covered by unit tests only.

Command: `python -m storemind.eval.eval_queue_v2`
