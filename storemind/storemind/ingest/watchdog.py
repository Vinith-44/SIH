"""Stream watchdog: detects dead/frozen streams and paces reconnects (M2).

Pure logic with an injectable clock, so it is fully unit-testable. The frame
reader calls on_frame() / on_open_failed() / on_reconnect_attempt(); the
watchdog decides the state and when to retry, and produces the CAMERA_HEALTH
payload via health().

States:  starting -> online <-> stale -> (reconnect) -> online
                   \\-> offline (could not open) -> (reconnect) -> online
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

STARTING, ONLINE, STALE, OFFLINE = "starting", "online", "stale", "offline"


class StreamWatchdog:
    def __init__(self, camera: str, stale_after_s: float = 5.0, backoff_initial_s: float = 1.0,
                 backoff_max_s: float = 30.0, fps_window_s: float = 5.0,
                 min_span_s: float = 0.5,
                 clock: Callable[[], float] = time.monotonic,
                 on_change: Callable[[dict], None] | None = None) -> None:
        self.camera = camera
        self.stale_after_s = stale_after_s
        self.backoff_initial_s = backoff_initial_s
        self.backoff_max_s = backoff_max_s
        self.fps_window_s = fps_window_s
        self.min_span_s = min_span_s
        self.clock = clock
        self.on_change = on_change
        self.state = STARTING
        self.reconnects = 0
        self.last_frame_at: float | None = None
        self.next_attempt_at = 0.0
        self._backoff = backoff_initial_s
        self._frames: deque[float] = deque()
        self._t0 = clock()

    # --- events from the reader -------------------------------------------------
    def on_frame(self) -> None:
        now = self.clock()
        recovering = self.state != ONLINE
        if recovering:
            # A reconnect delivers the frames buffered during the outage all at
            # once.  Mixing that burst with samples from before the outage makes
            # the rate meaningless, so recovery starts a fresh window.
            self._frames.clear()
        self.last_frame_at = now
        self._frames.append(now)
        self._trim(now)
        if recovering:
            self._backoff = self.backoff_initial_s
            self._set(ONLINE)

    def on_open_failed(self) -> None:
        self._set(OFFLINE)

    def on_reconnect_attempt(self) -> None:
        """Call just before reopening the stream; schedules the next allowed attempt."""
        self.reconnects += 1
        self.next_attempt_at = self.clock() + self._backoff
        self._backoff = min(self._backoff * 2, self.backoff_max_s)

    # --- decisions -----------------------------------------------------------------
    def check(self) -> None:
        """Call regularly: marks an online stream stale if frames stopped."""
        now = self.clock()
        self._trim(now)
        if self.state == ONLINE and self.last_frame_at is not None \
                and now - self.last_frame_at > self.stale_after_s:
            self._set(STALE)
        elif self.state == STARTING and now - self._t0 > self.stale_after_s * 2:
            self._set(OFFLINE)

    def should_reconnect(self) -> bool:
        return self.state in (STALE, OFFLINE) and self.clock() >= self.next_attempt_at

    def fps(self) -> float:
        """Frames per second over the rolling window, or 0.0 when unmeasured.

        A rate needs wall-clock time behind it.  The burst that arrives on
        reconnect can put a dozen frames inside a few milliseconds, and
        dividing by that span reported **1532 FPS on a 15 FPS stream** into
        CAMERA_HEALTH.  Below `min_span_s` of history we say 0.0 - "not
        measured yet" - rather than publishing a number no camera could produce.

        Half a second is the smallest window that still measures a slow camera
        honestly while excluding a millisecond burst.  It is a floor, not a
        cure: a burst long enough to span it would still read high for one
        window, which is why tools/e2e_smoke.py also asserts a ceiling on every
        rate it sees published.
        """
        now = self.clock()
        self._trim(now)
        if len(self._frames) < 2:
            return 0.0
        span = self._frames[-1] - self._frames[0]
        if span < self.min_span_s:
            return 0.0
        return round((len(self._frames) - 1) / span, 2)

    def health(self) -> dict:
        """CAMERA_HEALTH payload. Map these keys to the contract's fields."""
        self.check()
        now = self.clock()
        age = None if self.last_frame_at is None else round(now - self.last_frame_at, 2)
        return {
            "camera": self.camera,
            "state": self.state,
            "online": self.state == ONLINE,
            "fps": self.fps() if self.state == ONLINE else 0.0,
            "last_frame_age_s": age,
            "reconnects": self.reconnects,
        }

    # --- internals ----------------------------------------------------------------
    def _trim(self, now: float) -> None:
        while self._frames and now - self._frames[0] > self.fps_window_s:
            self._frames.popleft()

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state = state
            if self.on_change:
                self.on_change(self.health())
