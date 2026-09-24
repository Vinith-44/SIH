# Pick / put-back fusion on simulated shelf sensors (bucket C) - seeds 11-40

| engine | pick P / R / F1 | pick units correct | put-back P / R / F1 | touches (true) | shrink flags | fallen-stock alerts (true) |
|---|---|---|---|---|---|---|
| v1 | 0.05 / 1.00 / 0.09 | 0.0% | not measured yet / 0.00 / not measured yet | 0 (399) | 243 | 0 (15) |
| weight-only | 0.66 / 0.66 / 0.66 | 92.2% | 0.54 / 0.61 / 0.57 | 0 (399) | 0 | 0 (15) |
| M6 | 0.98 / 0.98 / 0.98 | 99.7% | 0.90 / 0.96 / 0.93 | 375 (399) | 0 | 15 (15) |

v1 has no put-back, touch or fallen-stock logic, and no units.

Command: `python -m storemind.eval.eval_fusion`
