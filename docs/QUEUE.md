# Queue intelligence

**Owner:** Person A (Vinith) · **Milestone:** M4 (queue v2) · **Code:** `storemind/storemind/analytics/queue.py` ·
**Evaluation:** `eval/queue_sim.py` + `eval/eval_queue_v2.py` (simulated tracks, bucket C), `eval/eval_queue.py`
(synthetic video, bucket C) · **Real clip:** not recorded yet (bucket B)

## 1. What it measures

Each billing counter has a **lane** (where people wait) and a small **billing** area (where they pay), drawn once
with `tools/calibrate.py`.

| Output | Meaning |
|---|---|
| `queue_len` / `queue_len_smooth` | people waiting (not yet being served); smoothed = median of recent looks |
| `queue_parties` | groups: a family that stands and pays together counts once |
| `median_wait_s` | median time from entering the lane to service starting (per-person timers) |
| `wait_littles_s` | the same wait from **Little's law**, W = L / λ (average queue ÷ arrival rate) |
| `service_rate_per_min` (μ) | services per minute at this counter; feeds the Erlang-C forecast |
| `arrivals_per_min` (λ) | people joining per minute |
| `balks`, `reneges` | stopped at the queue and left / joined and left before being served |
| `tail_overflow` | the queue has reached the end of its lane for ≥ 5 s → alert "open another counter" |

## 2. v1 → v2 (`membership: dwell`)

| Problem | v1 | v2 |
|---|---|---|
| Someone walks *through* the queue area | counted as joining the queue (a false arrival that inflates λ and the forecast) | joins only after **3 s** in the lane while moving slower than **0.10 frame heights/s**; walkers never join. Their wait is back-dated to when they entered |
| A family queues together | 3 "customers" | 1 party of 3 (joined within 4 s, stay within 0.06 frame heights of each other) |
| The queue bends round a shelf | a polygon has to cover the bend (and the aisle beside it) | `lane_polyline` + `lane_width` follows the bend; the last 10% of the line is the tail |
| The tracker swaps someone's ID mid-queue | they "leave", and a stranger "arrives" with a zero timer | **stitching**: a new track within 0.1 frame heights of someone who vanished < 4 s ago takes over their place and timer |
| Wait timers break under ID switches | nothing to cross-check | **Little's law** needs no identities. It is shown next to the median wait; a big gap between the two means tracking is struggling |
| People giving up | invisible | `balks` and `reneges` (read §3 before using them separately) |

Service still ends when the person leaves the billing area (the default). Erlang-C staffing advice
(`fusion/forecast.py`) is unchanged and now gets a λ without passers-by in it.

## 3. Evaluation

### Simulated tracks (bucket C: logic, not accuracy)

`eval/queue_sim.py` simulates tracker output for an L-shaped queue at 5 FPS:
- arrivals are Poisson, with a rush in the middle of the run;
- 25% of customers come as parties of 2; 8% renege, 10% balk;
- passers-by walk along the lane;
- service takes about 40 s.

Truth is exact. "Noisy" adds box jitter, 3% missed detections and 0.3 tracker ID switches per person-minute.
Defaults were tuned on **seeds 1–10** (`--grid`, 27 settings); **seeds 11–40** are the report:

| engine | queue MAE | party MAE | joins (truth 833) | Little's W err | median-wait err | walked away (truth 95) |
|---|---|---|---|---|---|---|
| v1 | 0.38 | 0.85 | **2,598** | 53% | 2.7% | — |
| **v2** | **0.19** | **0.15** | **825** | 20% | 2.8% | 87 |
| v1 noisy | 0.41 | 0.89 | 3,081 | 61% | 52% | — |
| **v2 noisy** | **0.21** | **0.18** | **969** | **12%** | **33%** | 118 |

What this says, honestly:
- **The big fix is arrivals.** v1 counts every passer-by as a queue arrival (3× the truth). That is the λ the
  queue forecast uses. v2 is within 1% on clean tracks and 16% high with ID switches.
- **Queue length was already fine** (both engines are well under the MAE ≤ 1 target), because v1's median smoothing
  hides short passes. The M4 acceptance is really about waits and λ.
- **ID switches still hurt per-person waits** (33% error even with stitching, above the 20% target). **Little's
  law holds up (12%)**, which is why it is shown next to the median.
- **The balk / renege split does not work.** With a 3 s join time, people who stop 3–6 s and leave are counted
  as joining and then reneging (0 of 65 balks found; 87 reneges against 30 true). Their **sum** ("walked away
  unserved", 87 vs 95) is the usable KPI. A longer join time separates them better but makes queue and wait
  numbers worse; the tuning objective ranked those higher.
- **Tail overflow is barely exercised** by the simulator (1 true sample in the test seeds). It is covered by
  unit tests only.
- **Protocol notes.** The truth definition of "tail" was aligned to the engine's (last 10% of the lane) on the
  tuning seeds, where it had been off by one queue position. After a unit test found a small balk-timing bias,
  the test seeds were scored a second time; the numbers were identical.

The synthetic queue **video** (bucket C, `eval/eval_queue.py`, 7 customers) stays at 7/7 customers and 1.1% wait
error with v2. Queue MAE goes from 0.00 to 0.06, the cost of the 3 s join time.

### Our own canteen clip (bucket B): the real acceptance test, **not measured yet**

M4 accepts on **queue MAE ≤ 1 and wait error ≤ 20% on our own clip**. Steps: `docs/HARDWARE_TODO.md`
("M4 - canteen queue clip").

## 4. Configuration

```yaml
counters:
  - name: counter-1
    billing: [[0.45,0.20],[0.62,0.20],[0.62,0.36],[0.45,0.36]]
    membership: dwell                 # v2
    lane_polyline: [[0.535,0.40],[0.535,0.80],[0.30,0.80]]   # billing end first; or lane: <polygon>
    lane_width: 0.12                  # fraction of frame height
    tail_zone: []                     # optional polygon; otherwise the last 10% of the polyline
    min_service_s: 3.0
    congestion_len: 5
```

## 5. Not done in M4, and why
- **LD2450 radar fusion.** The radar is optional and not bought. Using `$Q` targets would need a new radar event
  type in the contract, which is not worth adding for hardware we don't have. The firmware side already parses `$Q`.
- **Occupancy grid.** The lane polyline covers bent queues more simply. A grid is only worth it if a real store
  shows a queue with no single line.
- **Staff at the counter.** The cashier is excluded by M1's staff zones and badges (`cameras[].staff`), not by
  the queue engine.

## Sources
- research/23 §3.2 (queue problems and fixes); CLAUDE_CODE_PROMPT_V2 M4.
- Little's law: J. D. C. Little, "A proof for the queuing formula L = λW", Operations Research 9(3), 1961;
  https://web.eng.ucsd.edu/~massimo/ECE158A/Handouts_files/Little.pdf
- Balk / renege as retail KPIs; velocity-threshold membership: Zone24x7 queue white paper,
  https://zone24x7.com/wp-content/uploads/2022/01/white-paper-queue-detection-white-paperd-2.pdf
