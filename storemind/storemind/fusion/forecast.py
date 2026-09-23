"""Door-to-counter queue forecasting (research/03 pillar N1).

The idea (research/03 N1): the entrance camera is a *leading indicator* of the
billing counter.  People entering now reach a counter roughly one shopping trip
later, so counting the door lets us recommend opening a counter **before** the
queue exists, which is exactly what the problem statement asks for ("predict
congestion before queues become excessive").

Three pieces:

1.  `best_lag` - cross-correlate the per-minute entrance series against the
    per-minute checkout-arrival series to learn the shopping-trip lag L.  No
    person is matched across cameras, so this is privacy-safe by construction:
    it is a correlation between two *counts*, not a re-identification.
2.  `lambda_hat` - predicted checkout arrival rate = conversion x entrances
    observed L minutes ago (falling back to the recent checkout rate when we have
    not yet observed a whole lag).
3.  `erlang_c` / `recommend_counters` - M/M/c queueing.  Smallest number of
    counters that keeps P(wait > target) under a threshold.

Prior art matters here and research/22 checked it: predictive checkout
staffing from entrance counts is NOT new.  Irisys patent US7778855B2 (2010)
does exactly this with dedicated overhead sensors plus POS data, and both
Irisys and Xovis sell it to big-box retailers.  So this is never described as
"first" or "novel".  What is ours: the same proven idea on a shop's EXISTING
CCTV and a ~Rs 15-25k box, fully offline, with the door-to-counter lag learned
automatically by cross-correlation rather than configured, and no
re-identification anywhere.

Honesty note for the viva: Erlang-C assumes Poisson arrivals, exponential service
times, no balking and a shared queue.  Indian kirana checkout is not exactly any
of those.  We report it as a *staffing recommendation*, validate the predicted
wait against measured waits in `eval/eval_forecast.py`, and say so on the slide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..core.clock import Clock
from ..core.events import Event, EventType, ForecastData, make_event


# --------------------------------------------------------------------------- #
# Pure queueing maths (unit-tested against textbook values)
# --------------------------------------------------------------------------- #

def erlang_c(lam: float, mu: float, c: int) -> float:
    """Probability that an arriving customer has to wait at all (M/M/c).

    `lam` and `mu` are rates in the same unit (per minute here).
    """
    if c <= 0 or mu <= 0 or lam < 0:
        return 1.0
    a = lam / mu
    if a >= c:
        return 1.0  # offered load exceeds capacity: the queue grows without bound
    top = (a ** c / math.factorial(c)) * (c / (c - a))
    bottom = sum(a ** k / math.factorial(k) for k in range(c)) + top
    return top / bottom


def prob_wait_over(lam: float, mu: float, c: int, target_min: float) -> float:
    """P(wait > target).  Waiting time in M/M/c is exponential with rate c*mu-lam."""
    if c <= 0 or mu <= 0 or lam / mu >= c:
        return 1.0
    return erlang_c(lam, mu, c) * math.exp(-(c * mu - lam) * target_min)


def mean_wait_min(lam: float, mu: float, c: int) -> float | None:
    """Expected waiting time in queue (excluding service), minutes."""
    if c <= 0 or mu <= 0 or lam / mu >= c:
        return None
    return erlang_c(lam, mu, c) / (c * mu - lam)


@dataclass
class Recommendation:
    counters: int
    mean_wait_min: float | None
    prob_over_target: float | None
    feasible: bool = True


def recommend_counters(lam: float, mu: float, target_wait_min: float = 3.0,
                       max_prob: float = 0.2, c_max: int = 8) -> Recommendation:
    """Smallest c with P(wait > target) <= max_prob."""
    for c in range(1, c_max + 1):
        if mu <= 0 or lam / mu >= c:
            continue
        risk = prob_wait_over(lam, mu, c, target_wait_min)
        if risk <= max_prob:
            return Recommendation(c, mean_wait_min(lam, mu, c), risk, True)
    return Recommendation(c_max, mean_wait_min(lam, mu, c_max),
                          prob_wait_over(lam, mu, c_max, target_wait_min), False)


def best_lag(entries: np.ndarray, checkout: np.ndarray, min_lag: int = 1,
             max_lag: int = 40) -> int | None:
    """Shopping-trip lag in minutes, by normalised cross-correlation.

    Returns None when either series is flat (no information to correlate).
    """
    entries = np.asarray(entries, dtype=np.float64)
    checkout = np.asarray(checkout, dtype=np.float64)
    n = min(len(entries), len(checkout))
    if n < max(4, min_lag + 2):
        return None
    entries, checkout = entries[:n], checkout[:n]
    if entries.std() < 1e-9 or checkout.std() < 1e-9:
        return None
    x = (entries - entries.mean()) / entries.std()
    y = (checkout - checkout.mean()) / checkout.std()
    best_score, best_l = -np.inf, None
    for lag in range(min_lag, min(max_lag, n - 2) + 1):
        score = float(np.mean(x[:n - lag] * y[lag:]))
        if score > best_score:
            best_score, best_l = score, lag
    return best_l


# --------------------------------------------------------------------------- #
# Online forecaster
# --------------------------------------------------------------------------- #

@dataclass
class Forecaster:
    """Keeps per-minute count series and emits FORECAST events."""

    target_wait_min: float = 3.0
    max_prob_over_target: float = 0.2
    max_counters: int = 8
    min_lag_min: int = 1
    max_lag_min: int = 40
    conversion: float = 0.6
    default_service_s: float = 90.0
    period_s: float = 60.0
    # Minutes of door history required before we are willing to tell a shopkeeper
    # to open another till.  research/01 Q2: the legacy predictor had no minimum
    # window and turned a queue of 1 into a predicted 78.  One minute of counting
    # is not evidence of a rush, so during warm-up we publish the forecast but
    # never recommend adding staff.
    warmup_min: int = 3
    store: str = "demo-store"
    node: str = "pi5-01"

    entries_per_min: dict[int, int] = field(default_factory=dict)
    checkout_per_min: dict[int, int] = field(default_factory=dict)
    _next_emit_s: float = 0.0
    last: ForecastData | None = None
    history: list[tuple[float, ForecastData]] = field(default_factory=list)

    # -- ingestion ------------------------------------------------------- #
    def note_entry(self, now_s: float) -> None:
        minute = int(now_s // 60)
        self.entries_per_min[minute] = self.entries_per_min.get(minute, 0) + 1

    def note_checkout_arrival(self, now_s: float) -> None:
        minute = int(now_s // 60)
        self.checkout_per_min[minute] = self.checkout_per_min.get(minute, 0) + 1

    def _series(self, source: dict[int, int], upto_minute: int) -> np.ndarray:
        if not source:
            return np.zeros(max(1, upto_minute + 1), dtype=np.float64)
        return np.array([source.get(m, 0) for m in range(0, upto_minute + 1)], dtype=np.float64)

    # -- forecasting ----------------------------------------------------- #
    def estimate_lag(self, now_s: float) -> int | None:
        minute = int(now_s // 60)
        return best_lag(self._series(self.entries_per_min, minute),
                        self._series(self.checkout_per_min, minute),
                        min_lag=self.min_lag_min, max_lag=self.max_lag_min)

    def lambda_hat(self, now_s: float, lag_min: int | None) -> float:
        """Predicted checkout arrivals per minute for the near future.

        With a known lag L, people who entered L minutes ago are about to reach
        the counter, so lambda-hat = conversion x entries(t - L + 1 .. t).  Before
        we have enough history, fall back to the recent measured checkout rate.
        """
        minute = int(now_s // 60)
        if lag_min:
            window = [self.entries_per_min.get(m, 0)
                      for m in range(max(0, minute - lag_min + 1), minute + 1)]
            if window:
                return self.conversion * (sum(window) / len(window))
        if self.checkout_per_min:
            recent = [self.checkout_per_min.get(m, 0) for m in range(max(0, minute - 4), minute + 1)]
            if recent:
                return float(sum(recent)) / len(recent)
        # No counter camera (or nobody has reached a counter yet): the door is
        # the only signal we have, so scale it by the conversion rate.
        entries_recent = [self.entries_per_min.get(m, 0) for m in range(max(0, minute - 4), minute + 1)]
        return self.conversion * (sum(entries_recent) / max(1, len(entries_recent)))

    def due(self, now_s: float) -> bool:
        return now_s >= self._next_emit_s

    def step(self, now_s: float, clock: Clock, *, mu_per_min: float | None,
             open_counters: int) -> list[Event]:
        if not self.due(now_s):
            return []
        self._next_emit_s = now_s + self.period_s

        mu = mu_per_min if mu_per_min and mu_per_min > 0 else 60.0 / self.default_service_s
        lag = self.estimate_lag(now_s)
        lam = self.lambda_hat(now_s, lag)
        recommendation = recommend_counters(lam, mu, self.target_wait_min,
                                            self.max_prob_over_target, self.max_counters)
        pred_wait_min = mean_wait_min(lam, mu, max(1, open_counters))

        observed_minutes = int(now_s // 60) + 1
        warming_up = observed_minutes < self.warmup_min
        if warming_up:
            recommendation = Recommendation(counters=max(1, open_counters),
                                            mean_wait_min=pred_wait_min,
                                            prob_over_target=None, feasible=True)
        # "Open counter 3 in ~6 minutes": if extra staff are needed and we know
        # the lag, the pressure lands roughly one shopping trip after the door
        # count that produced it.
        eta = float(lag) if (lag and recommendation.counters > open_counters) else 0.0

        data = ForecastData(
            lambda_hat_per_min=round(lam, 3),
            mu_per_min_per_counter=round(mu, 3),
            open_counters=open_counters,
            recommended_counters=recommendation.counters,
            eta_min=round(eta, 1),
            pred_wait_s=(round(pred_wait_min * 60.0, 1) if pred_wait_min is not None else None),
            lag_min=lag,
            basis=("warmup" if warming_up
                   else ("erlang_c" if recommendation.feasible else "erlang_c_saturated")),
        )
        self.last = data
        self.history.append((now_s, data))
        return [make_event(ts=clock.now(), store=self.store, node=self.node,
                           type=EventType.FORECAST, data=data)]
