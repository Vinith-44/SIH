"""Erlang-C and the door-to-counter lag estimator (novelty N1).

Erlang-C values are checked against numbers that can be verified by hand from the
M/M/c formula, so a refactor that silently breaks the staffing recommendation
fails here rather than on a slide.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from storemind.core.clock import ManualClock
from storemind.fusion.forecast import (
    Forecaster,
    best_lag,
    erlang_c,
    mean_wait_min,
    prob_wait_over,
    recommend_counters,
)


def erlang_c_reference(lam: float, mu: float, c: int) -> float:
    """Independent textbook implementation, written differently on purpose."""
    a = lam / mu
    numerator = (a ** c) / (math.factorial(c) * (1 - a / c))
    denominator = sum((a ** k) / math.factorial(k) for k in range(c)) + numerator
    return numerator / denominator


@pytest.mark.parametrize("lam,mu,c", [(2.0, 1.0, 3), (2.4, 0.667, 5), (0.5, 1.0, 1),
                                      (8.0, 1.0, 10), (1.0, 3.0, 1)])
def test_erlang_c_matches_the_textbook_formula(lam, mu, c):
    assert erlang_c(lam, mu, c) == pytest.approx(erlang_c_reference(lam, mu, c), rel=1e-9)


def test_erlang_c_known_single_server_case():
    """For c = 1, M/M/c reduces to M/M/1 where P(wait) = rho."""
    assert erlang_c(0.5, 1.0, 1) == pytest.approx(0.5, rel=1e-9)
    assert erlang_c(0.9, 1.0, 1) == pytest.approx(0.9, rel=1e-9)


def test_erlang_c_saturates_when_demand_exceeds_capacity():
    assert erlang_c(5.0, 1.0, 4) == 1.0      # offered load 5 > 4 servers
    assert erlang_c(4.0, 1.0, 4) == 1.0      # exactly at capacity is still unstable


def test_erlang_c_falls_as_servers_are_added():
    values = [erlang_c(2.0, 1.0, c) for c in range(3, 9)]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_probability_of_waiting_longer_than_target_decays():
    lam, mu, c = 2.0, 1.0, 3
    near = prob_wait_over(lam, mu, c, 1.0)
    far = prob_wait_over(lam, mu, c, 5.0)
    assert 0.0 < far < near < 1.0


def test_mean_wait_is_none_when_the_system_is_unstable():
    assert mean_wait_min(5.0, 1.0, 4) is None
    assert mean_wait_min(2.0, 1.0, 3) == pytest.approx(erlang_c(2.0, 1.0, 3) / (3 * 1.0 - 2.0))


def test_recommendation_from_the_research_note():
    """research/03 N1: 2.4 shoppers/min reaching billing, 1.5 min per bill,
    P(wait > 3 min) <= 20% -> 5 counters."""
    recommendation = recommend_counters(lam=2.4, mu=1 / 1.5, target_wait_min=3.0, max_prob=0.2)
    assert recommendation.counters == 5
    assert recommendation.feasible
    assert recommendation.prob_over_target <= 0.2


def test_recommendation_is_the_smallest_sufficient_number():
    recommendation = recommend_counters(lam=2.4, mu=1 / 1.5, target_wait_min=3.0, max_prob=0.2)
    below = recommendation.counters - 1
    assert prob_wait_over(2.4, 1 / 1.5, below, 3.0) > 0.2


def test_quiet_store_needs_one_counter():
    assert recommend_counters(lam=0.2, mu=1.0, target_wait_min=3.0).counters == 1


def test_recommendation_reports_infeasible_rather_than_lying():
    recommendation = recommend_counters(lam=50.0, mu=0.5, target_wait_min=3.0,
                                        max_prob=0.2, c_max=4)
    assert recommendation.counters == 4
    assert recommendation.feasible is False


def test_lag_estimator_recovers_a_known_shift():
    rng = np.random.default_rng(3)
    base = np.abs(rng.normal(6, 3, 300))
    lag = 12
    checkout = np.roll(base, lag) * 0.6
    assert best_lag(base, checkout, max_lag=40) == lag


def test_lag_estimator_survives_noise():
    rng = np.random.default_rng(9)
    base = np.abs(rng.normal(8, 4, 400))
    lag = 7
    checkout = np.roll(base, lag) * 0.6 + rng.normal(0, 0.8, 400)
    assert abs(best_lag(base, checkout, max_lag=30) - lag) <= 1


def test_lag_estimator_returns_none_on_flat_series():
    flat = np.zeros(60)
    assert best_lag(flat, flat) is None


def test_lag_estimator_returns_none_when_too_short():
    assert best_lag(np.array([1.0, 2.0]), np.array([1.0, 2.0])) is None


def test_forecaster_emits_and_uses_the_lag():
    """End-to-end: a burst at the door should raise lambda-hat and, with a slow
    counter, recommend opening more of them."""
    forecaster = Forecaster(period_s=60.0, conversion=0.6, default_service_s=90.0)
    clock = ManualClock()
    for minute in range(20):
        for _ in range(12):                       # 12 entries per minute
            forecaster.note_entry(minute * 60 + 1)
        for _ in range(7):                        # 7 of them reach a counter
            forecaster.note_checkout_arrival((minute + 5) * 60 + 1)

    clock.set(20 * 60)
    events = forecaster.step(20 * 60, clock, mu_per_min=1.0, open_counters=1)
    assert len(events) == 1
    data = events[0].data
    assert data["lambda_hat_per_min"] > 5.0
    assert data["recommended_counters"] > 1
    assert data["basis"].startswith("erlang_c")


def test_forecaster_only_fires_once_per_period():
    forecaster = Forecaster(period_s=60.0)
    clock = ManualClock()
    assert forecaster.step(0.0, clock, mu_per_min=1.0, open_counters=1)
    assert forecaster.step(30.0, clock, mu_per_min=1.0, open_counters=1) == []
    clock.set(60.0)
    assert forecaster.step(60.0, clock, mu_per_min=1.0, open_counters=1)


def test_lambda_hat_falls_back_to_the_door_when_no_counter_camera_exists():
    forecaster = Forecaster(conversion=0.5)
    for _ in range(10):
        forecaster.note_entry(30.0)
    assert forecaster.lambda_hat(30.0, None) == pytest.approx(5.0)
