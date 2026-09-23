"""Time base.

The single most important rule for honest evaluation: **replay must produce the
same timeline as live**.  If a wait timer used `time.time()` while replaying a
video at 8x speed, every wait/service/dwell number would be wrong by 8x and every
"measured" figure on our slides would be fiction.

So nothing in `storemind` calls `time.time()` for analytics.  Everything takes a
`Clock`:

*   `WallClock`   — live cameras; `now()` is real time.
*   `VideoClock`  — replay; `now()` is the video's own presentation timestamp,
    anchored to a start datetime so events still carry realistic ISO timestamps.

`monotonic_s()` is the float seconds used by all timers; `now()` is the datetime
used for event `ts`.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# India Standard Time: store analytics are read by shopkeepers in local time and
# an offline Pi has no tzdata guarantee, so the offset is explicit.
IST = timezone(timedelta(hours=5, minutes=30))


class Clock:
    def monotonic_s(self) -> float:
        raise NotImplementedError

    def now(self) -> datetime:
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class WallClock(Clock):
    def __init__(self, tz: timezone = IST) -> None:
        self.tz = tz

    def monotonic_s(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VideoClock(Clock):
    """Driven by the frame timestamps of a replayed file.

    `advance_to(video_s)` is called by the runner once per decoded frame with the
    video's presentation time in seconds.  Timers then measure video time.
    """

    def __init__(self, start: datetime | None = None, tz: timezone = IST) -> None:
        self.tz = tz
        self.start = start or datetime.now(tz).replace(microsecond=0)
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=tz)
        self._video_s = 0.0

    def advance_to(self, video_s: float) -> None:
        # Never let the timeline go backwards (some containers report jittery PTS).
        if video_s > self._video_s:
            self._video_s = video_s

    def monotonic_s(self) -> float:
        return self._video_s

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self._video_s)

    def sleep(self, seconds: float) -> None:  # replay is as fast as the CPU allows
        return None


class ManualClock(Clock):
    """For tests: time only moves when you move it."""

    def __init__(self, start: datetime | None = None, tz: timezone = IST) -> None:
        self.tz = tz
        self.start = start or datetime(2026, 1, 1, 9, 0, 0, tzinfo=tz)
        self._t = 0.0

    def advance(self, seconds: float) -> None:
        self._t += seconds

    def set(self, seconds: float) -> None:
        self._t = seconds

    def monotonic_s(self) -> float:
        return self._t

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self._t)

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)
