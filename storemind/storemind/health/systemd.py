"""systemd readiness + watchdog pings (sd_notify), with no dependency.

Each StoreMind process runs as its own systemd unit with `Type=notify` and
`WatchdogSec=` (research/26 section 4.7, deploy/pi5/).  systemd kills and
restarts a process that stops pinging, so a hung loop recovers on its own.

On Windows, or when not started by systemd (`NOTIFY_SOCKET` unset), every call
is a no-op, so the same code runs on the laptop.
"""

from __future__ import annotations

import os
import socket
import time


class Notifier:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        env = os.environ if env is None else env
        self.address = env.get("NOTIFY_SOCKET") or None
        usec = env.get("WATCHDOG_USEC")
        # Ping at half the watchdog period, as sd_watchdog_enabled(3) recommends.
        self.interval_s = int(usec) / 2e6 if usec and usec.isdigit() else None
        self._last = 0.0
        self.sent: list[str] = []          # for tests

    @property
    def enabled(self) -> bool:
        return self.address is not None and hasattr(socket, "AF_UNIX")

    def notify(self, state: str) -> bool:
        if not self.enabled:
            return False
        address = self.address
        if address.startswith("@"):         # abstract namespace socket
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(address)
                sock.sendall(state.encode())
        except OSError:
            return False
        self.sent.append(state)
        return True

    def ready(self, status: str = "") -> bool:
        return self.notify("READY=1" + (f"\nSTATUS={status}" if status else ""))

    def watchdog(self, now: float | None = None) -> bool:
        """Call often; it only pings once per half watchdog period."""
        if self.interval_s is None:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last < self.interval_s:
            return False
        self._last = now
        return self.notify("WATCHDOG=1")

    def status(self, text: str) -> bool:
        return self.notify(f"STATUS={text}")

    def stopping(self) -> bool:
        return self.notify("STOPPING=1")
