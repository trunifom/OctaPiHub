"""Lifecycle owner that wires the Sense HAT port, worker, recorder, and history store."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from octapihub.application.sense_hat import SenseHatCapabilities, SenseHatPort
from octapihub.application.sense_hat_history import (
    SenseHatHistoryRecorder,
    SenseHatHistoryStore,
    SeriesPoint,
)
from octapihub.application.sense_hat_worker import (
    SenseHatMeasurementWorker,
    SensorGroupStatus,
)
from octapihub.core.config import SenseHatConfig
from octapihub.core.errors import OctaPiHubError

logger = logging.getLogger(__name__)

type PortFactory = Callable[[SenseHatConfig], SenseHatPort]
type StoreFactory = Callable[[SenseHatConfig], SenseHatHistoryStore]


class SenseHatService:
    """Start and stop Sense HAT measurement with degraded-mode fault isolation.

    A missing board or library leaves the service running without sensors, and an
    unusable history database leaves measurement running without history. Neither
    raises into the hub; both are reported through ``hardware_error`` and
    ``history_error`` (safe user messages).
    """

    def __init__(
        self,
        config: SenseHatConfig,
        port_factory: PortFactory,
        store_factory: StoreFactory,
    ) -> None:
        self._config = config
        self._port_factory = port_factory
        self._store_factory = store_factory
        self._worker: SenseHatMeasurementWorker | None = None
        self._recorder: SenseHatHistoryRecorder | None = None
        self._store: SenseHatHistoryStore | None = None
        self.hardware_error: str | None = None
        self.hardware_error_code: str | None = None
        self.history_error: str | None = None
        self.history_error_code: str | None = None
        self.capabilities: SenseHatCapabilities | None = None

    @property
    def statuses(self) -> tuple[SensorGroupStatus, ...]:
        """Per-group status; empty when disabled or the hardware is unavailable."""
        return self._worker.statuses if self._worker is not None else ()

    @property
    def config(self) -> SenseHatConfig:
        """The configuration the service is (or will be) running with."""
        return self._config

    @property
    def running(self) -> bool:
        """Whether a measurement worker is active."""
        return self._worker is not None

    async def start(self) -> None:
        """Create the port and store, apply colour settings, and start the worker."""
        if self._worker is not None:
            raise RuntimeError("Sense HAT service has already been started")
        self.hardware_error = None
        self.hardware_error_code = None
        self.history_error = None
        self.history_error_code = None
        self.capabilities = None
        if not self._config.enabled:
            return
        try:
            port = await asyncio.to_thread(self._port_factory, self._config)
        except OctaPiHubError as exc:
            self.hardware_error = exc.user_message
            self.hardware_error_code = exc.error_code
            logger.error("Sense HAT unavailable: %s", exc)
            return
        self.capabilities = port.capabilities
        await asyncio.to_thread(self._apply_color_settings, port)
        sink = None
        if self._config.history.enabled:
            try:
                self._store = await asyncio.to_thread(self._store_factory, self._config)
            except OctaPiHubError as exc:
                self.history_error = exc.user_message
                self.history_error_code = exc.error_code
                logger.error("Sense HAT history unavailable: %s", exc)
            else:
                self._recorder = SenseHatHistoryRecorder(self._config, self._store)
                sink = self._recorder.record
        worker = SenseHatMeasurementWorker(self._config, port, sink)
        await worker.start()
        self._worker = worker

    async def stop(self) -> None:
        """Stop polling, then persist buffered samples."""
        worker, recorder = self._worker, self._recorder
        self._worker = None
        self._recorder = None
        if worker is not None:
            await worker.stop()
        if recorder is not None:
            await asyncio.to_thread(recorder.flush)
            if recorder.pending:
                logger.error("Dropping %d unsaved Sense HAT samples", recorder.pending)

    async def restart(self, config: SenseHatConfig) -> None:
        """Stop, switch to ``config``, and start again; never raises for hardware faults."""
        await self.stop()
        self._config = config
        self._store = None
        await self.start()

    def query_series(
        self,
        group: str,
        since: datetime,
        until: datetime,
        *,
        bucket_seconds: int | None = None,
        corrected: bool = False,
    ) -> tuple[SeriesPoint, ...]:
        """Read history; returns no points when history is unavailable."""
        if self._store is None:
            return ()
        return self._store.query_series(
            group, since, until, bucket_seconds=bucket_seconds, corrected=corrected
        )

    def _apply_color_settings(self, port: SenseHatPort) -> None:
        sensor = self._config.sensors.get("color")
        if sensor is None or not sensor.enabled or not port.capabilities.color_sensor:
            return
        try:
            port.set_color_gain(self._config.color_gain)
            port.set_color_integration_cycles(self._config.color_integration_cycles)
        except OctaPiHubError as exc:
            logger.warning("Could not apply Sense HAT colour settings: %s", exc)
