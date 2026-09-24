"""PIR wake-up: process cameras at full rate only when someone is there (M2).

Cameras idle at a low frame rate (default 1 fps) to save CPU on the Pi. A PIR
PRESENCE event for a zone wakes that zone's cameras to full rate for hold_s
seconds; every new PRESENCE extends the hold. Cameras with no PIR mapped are
always active, so a missing sensor never blinds a camera.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable


class PirWake:
    def __init__(self, zone_cameras: dict[str, Iterable[str]], idle_fps: float = 1.0,
                 hold_s: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        if idle_fps <= 0:
            raise ValueError("idle_fps must be > 0")
        self.zone_cameras = {z: set(c) for z, c in zone_cameras.items()}
        self.idle_interval = 1.0 / idle_fps
        self.hold_s = hold_s
        self.clock = clock
        self._awake_until: dict[str, float] = {}
        self._last_allowed: dict[str, float] = {}
        self._covered = set().union(*self.zone_cameras.values()) if self.zone_cameras else set()

    def on_presence(self, zone: str) -> list[str]:
        """Handle a PIR PRESENCE for a zone; returns the cameras it woke."""
        until = self.clock() + self.hold_s
        woken = sorted(self.zone_cameras.get(zone, ()))
        for cam in woken:
            self._awake_until[cam] = max(self._awake_until.get(cam, 0.0), until)
        return woken

    def is_active(self, camera: str) -> bool:
        if camera not in self._covered:
            return True
        return self.clock() < self._awake_until.get(camera, 0.0)

    def allow(self, camera: str) -> bool:
        """Should this frame go to the pipeline? Call once per decoded frame."""
        if self.is_active(camera):
            return True
        now = self.clock()
        if now - self._last_allowed.get(camera, float("-inf")) >= self.idle_interval:
            self._last_allowed[camera] = now
            return True
        return False
