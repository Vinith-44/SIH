"""Stream watchdog and PIR wake-up: state machine, backoff, and idle pacing.

The clock is injected, so an outage that takes 30 s in the real world is tested
in microseconds and without a sleep.
"""

from __future__ import annotations

import pytest

from storemind.ingest.pir_wake import PirWake
from storemind.ingest.watchdog import OFFLINE, ONLINE, STALE, STARTING, StreamWatchdog


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _wd(clock, changes=None):
    return StreamWatchdog("cam1", stale_after_s=5, backoff_initial_s=1, backoff_max_s=8,
                          clock=clock, on_change=(changes.append if changes is not None else None))


def test_goes_online_then_stale():
    c, changes = Clock(), []
    wd = _wd(c, changes)
    assert wd.state == STARTING
    for _ in range(10):
        wd.on_frame()
        c.t += 0.1
    assert wd.state == ONLINE and wd.health()["online"]
    assert 9.0 <= wd.fps() <= 11.0
    c.t += 6
    wd.check()
    assert wd.state == STALE
    assert [h["state"] for h in changes] == [ONLINE, STALE]
    assert wd.health()["fps"] == 0.0 and wd.health()["last_frame_age_s"] > 5


def test_backoff_doubles_and_caps_then_resets():
    c = Clock()
    wd = _wd(c)
    wd.on_open_failed()
    waits = []
    for _ in range(5):
        assert wd.should_reconnect()
        wd.on_reconnect_attempt()
        waits.append(wd.next_attempt_at - c.t)
        assert not wd.should_reconnect()
        c.t = wd.next_attempt_at
    assert waits == [1, 2, 4, 8, 8]
    assert wd.reconnects == 5
    wd.on_frame()
    assert wd.state == ONLINE
    wd.on_open_failed()
    wd.on_reconnect_attempt()
    assert wd.next_attempt_at - c.t == 1  # backoff reset after recovery


def test_never_opened_becomes_offline():
    c = Clock()
    wd = _wd(c)
    c.t += 11
    wd.check()
    assert wd.state == OFFLINE and wd.health()["last_frame_age_s"] is None


def test_pir_idle_rate_and_wake():
    c = Clock()
    pir = PirWake({"aisle1": ["cam1"]}, idle_fps=1.0, hold_s=30, clock=c)
    allowed = 0
    for _ in range(20):  # 2 s of 10 fps frames while idle -> ~1 per second
        allowed += pir.allow("cam1")
        c.t += 0.1
    assert allowed == 2
    assert pir.on_presence("aisle1") == ["cam1"]
    assert all(pir.allow("cam1") for _ in range(5))
    c.t += 31
    assert not pir.is_active("cam1")


def test_presence_extends_hold_and_unmapped_always_active():
    c = Clock()
    pir = PirWake({"aisle1": ["cam1"]}, hold_s=30, clock=c)
    pir.on_presence("aisle1")
    c.t += 20
    pir.on_presence("aisle1")
    c.t += 20
    assert pir.is_active("cam1")
    assert pir.is_active("cam9") and pir.allow("cam9")
    assert pir.on_presence("unknown-zone") == []
    with pytest.raises(ValueError):
        PirWake({}, idle_fps=0)


# --------------------------------------------------------------------------- #
# The reconnect burst (found by tools/e2e_smoke.py, not by these tests)
# --------------------------------------------------------------------------- #


def test_a_reconnect_burst_is_not_reported_as_the_frame_rate():
    """A live run published 1532 FPS for a 15 FPS camera onto the health panel.

    On reconnect, go2rtc hands over every frame buffered during the outage at
    once. Those arrivals are real, but dividing them by a few milliseconds is
    not a frame rate, and CAMERA_HEALTH carried the result to the dashboard.
    """
    c = Clock()
    wd = _wd(c)
    for _ in range(15):                 # a healthy second at 15 FPS
        wd.on_frame()
        c.t += 1 / 15
    assert 14.0 <= wd.fps() <= 16.0

    wd.on_open_failed()                 # outage
    c.t += 10
    for _ in range(20):                 # reconnect: 20 buffered frames in 4 ms
        wd.on_frame()
        c.t += 0.0002

    assert wd.state == ONLINE
    assert wd.fps() == 0.0, "a 4 ms burst is not a measurable rate"


def test_the_rate_returns_once_there_is_real_time_behind_it():
    c = Clock()
    wd = _wd(c)
    wd.on_open_failed()
    for _ in range(30):                 # two seconds at 15 FPS after recovery
        wd.on_frame()
        c.t += 1 / 15
    assert 14.0 <= wd.fps() <= 16.0


def test_recovery_does_not_mix_in_samples_from_before_the_outage():
    c = Clock()
    wd = _wd(c)
    wd.on_frame()
    c.t += 0.1
    wd.on_frame()
    before = c.t
    wd.on_open_failed()
    c.t += 30                           # a long outage
    wd.on_frame()
    # The window restarts at recovery; a 30 s gap must not become the span.
    assert all(stamp >= before for stamp in wd._frames)


def test_health_reports_zero_rather_than_a_burst_rate():
    c = Clock()
    wd = _wd(c)
    wd.on_open_failed()
    for _ in range(10):
        wd.on_frame()
        c.t += 0.001
    assert wd.health()["fps"] == 0.0
