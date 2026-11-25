"""Cube orientation parsing and debounce rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from octapihub.core.errors import ValidationError

MAX_FACE = 8
NEUTRAL_VALUES = frozenset({0, 10})


class OrientationKind(Enum):
    """Kind of a stable orientation event."""

    BASELINE = "baseline"
    FACE_CHANGE = "face_change"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class OrientationEvent:
    """A stable orientation event."""

    kind: OrientationKind
    value: int
    observed_at_ms: int


class OrientationDebouncer:
    """Baseline, duplicate suppression and stable-face debounce."""

    def __init__(self, debounce_ms: int = 300) -> None:
        if isinstance(debounce_ms, bool) or not isinstance(debounce_ms, int) or debounce_ms < 0:
            raise ValidationError("debounce_ms must be a non-negative integer")
        self.debounce_ms = debounce_ms
        self._committed: int | None = None
        self._candidate: tuple[int, int] | None = None
        self._last_now_ms: int | None = None

    @property
    def committed_value(self) -> int | None:
        """Return the last stable value."""
        return self._committed

    @property
    def pending_value(self) -> int | None:
        """Return the candidate waiting for stability."""
        return self._candidate[0] if self._candidate else None

    def reset(self) -> None:
        """Forget state after a new connection."""
        self._committed = None
        self._candidate = None
        self._last_now_ms = None

    def feed(self, value: int, now_ms: int) -> list[OrientationEvent]:
        """Process one decoded orientation value."""
        self._validate(value, now_ms)
        if self._committed is None:
            self._committed = value
            return [OrientationEvent(OrientationKind.BASELINE, value, now_ms)]

        events = self._commit_if_due(now_ms)
        if value == self._committed:
            self._candidate = None
            return events

        if self._candidate is None or self._candidate[0] != value:
            self._candidate = (value, now_ms)
        return events + self._commit_if_due(now_ms)

    def tick(self, now_ms: int) -> list[OrientationEvent]:
        """Commit a candidate whose stability period has elapsed."""
        self._validate_time(now_ms)
        return self._commit_if_due(now_ms)

    def _validate(self, value: int, now_ms: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_FACE:
            raise ValidationError(f"Orientation value must be 0..8, got {value!r}")
        self._validate_time(now_ms)

    def _validate_time(self, now_ms: int) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            raise ValidationError("now_ms must be an integer")
        if self._last_now_ms is not None and now_ms < self._last_now_ms:
            raise ValidationError("Monotonic time moved backwards")
        self._last_now_ms = now_ms

    def _commit_if_due(self, now_ms: int) -> list[OrientationEvent]:
        if self._candidate is None:
            return []
        value, since_ms = self._candidate
        if now_ms - since_ms < self.debounce_ms:
            return []
        self._candidate = None
        self._committed = value
        kind = OrientationKind.NEUTRAL if value == 0 else OrientationKind.FACE_CHANGE
        return [OrientationEvent(kind, value, since_ms)]


def reconnect_delay_seconds(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Return bounded exponential reconnect delay."""
    if attempt < 0:
        raise ValidationError("Reconnect attempt must not be negative")
    if base <= 0 or cap < base:
        raise ValidationError("Invalid reconnect backoff limits")
    if attempt <= 1:
        return base
    return min(cap, base * (2.0 ** (attempt - 1)))
