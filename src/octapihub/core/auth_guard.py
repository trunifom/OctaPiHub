"""Protection against guessing the access token: a pause after repeated wrong attempts.

Wrong attempts are counted per client address. After ``max_failures`` mistakes within
``window_seconds`` the client is locked out; every further lock-out lasts twice as long (up to
``max_lockout_seconds``) until a correct token resets the count. While locked out even the correct
token is refused, otherwise guessing would simply continue.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Entry:
    failures: list[float] = field(default_factory=list)
    locked_until: float = 0.0
    strikes: int = 0
    last_seen: float = 0.0


class FailureLimiter:
    """Counts failed attempts per key (a client address) and locks out guessers."""

    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: float = 300.0,
        lockout_seconds: float = 60.0,
        max_lockout_seconds: float = 900.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 1024,
    ) -> None:
        if max_failures < 1 or window_seconds <= 0 or lockout_seconds <= 0:
            raise ValueError("limits must be positive")
        self._max_failures = max_failures
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._max_lockout = max_lockout_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._entries: dict[str, _Entry] = {}
        self.lockouts = 0  # how often someone was locked out (for the metrics)
        self.failures = 0  # all wrong attempts since start

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` may try again; 0 if it may try now."""
        entry = self._entries.get(key)
        if entry is None:
            return 0
        remaining = entry.locked_until - self._clock()
        return math.ceil(remaining) if remaining > 0 else 0

    def record_failure(self, key: str) -> None:
        """Count a wrong attempt; lock the key out when there are too many."""
        now = self._clock()
        self.failures += 1
        entry = self._entries.get(key)
        if entry is None:
            self._make_room()
            entry = self._entries[key] = _Entry()
        entry.last_seen = now
        entry.failures = [t for t in entry.failures if now - t < self._window]
        entry.failures.append(now)
        if len(entry.failures) >= self._max_failures:
            seconds = min(self._lockout * (2**entry.strikes), self._max_lockout)
            entry.locked_until = now + seconds
            entry.strikes += 1
            entry.failures.clear()
            self.lockouts += 1

    def record_success(self, key: str) -> None:
        """A correct token forgets all earlier mistakes of this key."""
        self._entries.pop(key, None)

    def _make_room(self) -> None:
        if len(self._entries) < self._max_keys:
            return
        now = self._clock()
        for key in [k for k, e in self._entries.items() if e.locked_until <= now]:
            del self._entries[key]
            if len(self._entries) < self._max_keys:
                return
        oldest = min(self._entries, key=lambda k: self._entries[k].last_seen)
        del self._entries[oldest]
