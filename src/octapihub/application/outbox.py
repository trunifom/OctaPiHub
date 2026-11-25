"""Application contracts and action dispatch for the durable outbox."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from octapihub.application.ble_runtime import CubeEvent
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import RuleActionConfig
from octapihub.core.errors import RetryableError, ValidationError
from octapihub.domain.cube import OrientationKind, reconnect_delay_seconds

logger = logging.getLogger(__name__)

Clock = Callable[[], float]
Jitter = Callable[[float], float]


class OutboxState(StrEnum):
    """Durable status of one action step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    """Immutable snapshot of one queued action."""

    id: int
    batch_id: str
    cube: str
    sequence: int
    action: RuleActionConfig
    attempts: int
    created_at: float
    available_at: float
    expires_at: float
    state: OutboxState
    last_error: str | None


class OutboxStore(Protocol):
    """Persistence boundary used by event routing and action dispatch."""

    def enqueue(
        self,
        cube: str,
        actions: Sequence[RuleActionConfig],
        *,
        created_at: float,
        expires_at: float,
    ) -> str:
        """Atomically append an ordered action batch and return its identifier."""

    def claim_next(self, cube: str, *, now: float) -> OutboxEntry | None:
        """Claim only the oldest eligible action for one cube."""

    def complete(self, entry_id: int, *, finished_at: float) -> None:
        """Mark an in-progress action as successful."""

    def retry(
        self,
        entry_id: int,
        *,
        now: float,
        retry_at: float,
        error_code: str,
    ) -> OutboxState:
        """Schedule a retry or mark an action expired."""

    def get_entry(self, entry_id: int) -> OutboxEntry:
        """Return the current persisted status of one action."""

    def get_batch(self, batch_id: str) -> tuple[OutboxEntry, ...]:
        """Return action statuses for one ordered event batch."""

    def prune_terminal(self, *, finished_before: float) -> int:
        """Delete only old terminal entries and return the number removed."""


class OutboxDispatcher:
    """Route cube events to the outbox and execute queued actions in isolation."""

    def __init__(
        self,
        rules: RuleEngine,
        outbox: OutboxStore,
        plugins: PluginRegistry,
        *,
        ttl_seconds: float = 900.0,
        ttl_by_kind: Mapping[str, float] | None = None,
        backoff_base_seconds: float = 1.0,
        backoff_cap_seconds: float = 60.0,
        clock: Clock = time.time,
        jitter: Jitter = lambda delay: delay,
        observer: Callable[[OutboxEntry, bool], None] | None = None,
    ) -> None:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValidationError("Outbox TTL must be positive and finite")
        if (
            isinstance(backoff_base_seconds, bool)
            or isinstance(backoff_cap_seconds, bool)
            or not isinstance(backoff_base_seconds, (int, float))
            or not isinstance(backoff_cap_seconds, (int, float))
            or not math.isfinite(backoff_base_seconds)
            or not math.isfinite(backoff_cap_seconds)
            or backoff_base_seconds <= 0
            or backoff_cap_seconds < backoff_base_seconds
        ):
            raise ValidationError("Invalid outbox retry backoff limits")
        self._rules = rules
        self._outbox = outbox
        self._plugins = plugins
        self._ttl_seconds = ttl_seconds
        self._ttl_by_kind = dict(ttl_by_kind or {})
        for kind, value in self._ttl_by_kind.items():
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValidationError(f"Outbox TTL for {kind!r} must be positive and finite")
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_cap_seconds = backoff_cap_seconds
        self._clock = clock
        self._jitter = jitter
        self._observer = observer
        for kind in rules.required_plugin_kinds:
            plugins.require(kind)

    def set_observer(self, observer: Callable[[OutboxEntry, bool], None] | None) -> None:
        """Be told after every action attempt whether it succeeded (for status displays)."""
        self._observer = observer

    def _notify(self, entry: OutboxEntry, succeeded: bool) -> None:
        observer = self._observer
        if observer is None:
            return
        try:
            observer(entry, succeeded)
        except Exception:
            logger.exception("Outbox observer failed")

    def update_rules(self, rules: RuleEngine) -> None:
        """Switch to new rules without a restart; every needed plugin must be registered.

        Raises:
            PluginError: A plugin kind used by the new rules is not registered; the
                current rules stay active.
        """
        for kind in rules.required_plugin_kinds:
            self._plugins.require(kind)
        self._rules = rules

    def enqueue_event(self, event: CubeEvent) -> str | None:
        """Persist actions for a configured face change or holder event; ignore the rest."""
        if event.orientation.kind not in (OrientationKind.FACE_CHANGE, OrientationKind.NEUTRAL):
            return None
        return self.enqueue_face_change(event.cube_name, event.orientation.value)

    def enqueue_face_change(self, cube_name: str, face: int) -> str | None:
        """Queue actions for a face from BLE, web, joystick, or another input source."""
        actions = self._rules.actions_for(cube_name, face)
        if not actions:
            return None
        created_at = self._clock()
        return self._outbox.enqueue(
            cube_name,
            actions,
            created_at=created_at,
            # an action that must not run late (music) gives the whole batch a short life
            expires_at=created_at
            + min(
                [self._ttl_seconds]
                + [self._ttl_by_kind[a.kind] for a in actions if a.kind in self._ttl_by_kind]
            ),
        )

    async def dispatch_next(self, cube: str) -> bool:
        """Execute at most one action for a cube, returning whether work was claimed."""
        entry = self._outbox.claim_next(cube, now=self._clock())
        if entry is None:
            return False
        try:
            plugin = self._plugins.require(entry.action.kind)
            await plugin.execute(entry.action.action, entry.action.parameters)
        except asyncio.CancelledError:
            now = self._clock()
            self._outbox.retry(
                entry.id,
                now=now,
                retry_at=now,
                error_code="CancelledError",
            )
            raise
        except Exception as exc:
            now = self._clock()
            delay = reconnect_delay_seconds(
                entry.attempts,
                base=self._backoff_base_seconds,
                cap=self._backoff_cap_seconds,
            )
            jittered = self._jitter(delay)
            if not math.isfinite(jittered):
                raise ValidationError("Outbox retry jitter must be finite") from exc
            delay = min(self._backoff_cap_seconds, max(0.0, jittered))
            if isinstance(exc, RetryableError) and exc.retry_after_seconds is not None:
                delay = max(delay, float(exc.retry_after_seconds))
            # errors the user must fix (invalid source, missing target) are never retried
            permanent = getattr(exc, "retryable", True) is False
            state = self._outbox.retry(
                entry.id,
                now=now,
                retry_at=entry.expires_at if permanent else now + delay,
                error_code=type(exc).__name__,
            )
            logger.warning(
                "Outbox action %d for cube %s failed (%s); state=%s",
                entry.id,
                entry.cube,
                type(exc).__name__,
                state.value,
            )
            self._notify(entry, False)
            return True
        self._outbox.complete(entry.id, finished_at=self._clock())
        self._notify(entry, True)
        return True

    async def run_cube(
        self,
        cube: str,
        stop: asyncio.Event,
        *,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        """Continuously dispatch one cube's FIFO queue until the owner requests stop."""
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(poll_interval_seconds)
            or poll_interval_seconds <= 0
        ):
            raise ValidationError("Outbox poll interval must be positive and finite")
        while not stop.is_set():
            if await self.dispatch_next(cube):
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval_seconds)
            except TimeoutError:
                continue
