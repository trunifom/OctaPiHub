"""Sense HAT history contract and a batching recorder that protects the SD card."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from octapihub.application.sense_hat_worker import SenseHatSample
from octapihub.core.config import SenseHatConfig
from octapihub.core.errors import OctaPiHubError

logger = logging.getLogger(__name__)

type MonotonicSeconds = Callable[[], float]
type WallClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SeriesPoint:
    """One scalar point; averaged over a bucket when a bucket size was requested."""

    timestamp: datetime
    value: float
    count: int


class SenseHatHistoryStore(Protocol):
    """Persistence boundary for sensor samples."""

    def append_many(self, samples: Sequence[SenseHatSample]) -> None:
        """Persist samples atomically."""

    def prune(self, older_than: datetime) -> int:
        """Delete samples measured before ``older_than``; return the number removed."""

    def query_series(
        self,
        group: str,
        since: datetime,
        until: datetime,
        *,
        bucket_seconds: int | None = None,
        corrected: bool = False,
    ) -> tuple[SeriesPoint, ...]:
        """Return scalar raw (or corrected temperature) values in time order."""


class SenseHatHistoryRecorder:
    """Sample sink that buffers samples and writes them in batches.

    Batching limits SQLite commits (and so SD-card writes). Only groups with history
    enabled are stored. Store failures keep the bounded buffer for the next attempt and
    never propagate to the measurement worker.
    """

    def __init__(
        self,
        config: SenseHatConfig,
        store: SenseHatHistoryStore,
        *,
        batch_size: int = 20,
        flush_interval_seconds: float = 300.0,
        max_buffer: int = 2000,
        prune_interval_seconds: float = 3600.0,
        monotonic: MonotonicSeconds = time.monotonic,
        wall_clock: WallClock = lambda: datetime.now(UTC),
    ) -> None:
        if batch_size < 1 or max_buffer < batch_size:
            raise ValueError("Invalid history buffer limits")
        if flush_interval_seconds <= 0 or prune_interval_seconds <= 0:
            raise ValueError("History intervals must be positive")
        self._config = config
        self._store = store
        self._batch_size = batch_size
        self._flush_interval = flush_interval_seconds
        self._max_buffer = max_buffer
        self._prune_interval = prune_interval_seconds
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._buffer: list[SenseHatSample] = []
        self._last_flush = monotonic()
        self._last_prune: float | None = None
        self.dropped_samples = 0

    @property
    def pending(self) -> int:
        """Number of buffered, not yet persisted samples."""
        return len(self._buffer)

    def record(self, sample: SenseHatSample) -> None:
        """Buffer one sample and flush when the batch is full or old enough."""
        history = self._config.history
        sensor = self._config.sensors.get(sample.group)
        if not history.enabled or sensor is None or not sensor.history_enabled:
            return
        self._buffer.append(sample)
        if len(self._buffer) > self._max_buffer:
            del self._buffer[0]
            self.dropped_samples += 1
        now = self._monotonic()
        if len(self._buffer) >= self._batch_size or now - self._last_flush >= self._flush_interval:
            self.flush()

    def flush(self) -> bool:
        """Write buffered samples and apply retention; return whether the buffer is empty."""
        now = self._monotonic()
        try:
            if self._buffer:
                self._store.append_many(tuple(self._buffer))
                self._buffer.clear()
            self._last_flush = now
            self._prune_if_due(now)
        except OctaPiHubError:
            self._last_flush = now
            logger.exception("Could not persist Sense HAT history; will retry")
        return not self._buffer

    def _prune_if_due(self, now: float) -> None:
        if self._last_prune is not None and now - self._last_prune < self._prune_interval:
            return
        self._last_prune = now
        retention = timedelta(days=self._config.history.retention_days)
        self._store.prune(self._wall_clock() - retention)
