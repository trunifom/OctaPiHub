"""Periodic Sense HAT measurement worker with per-group scheduling and fault isolation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from octapihub.application.sense_hat import (
    SenseHatCapabilities,
    SenseHatMetric,
    SenseHatPort,
    SenseHatReading,
)
from octapihub.core.config import SenseHatConfig, SensorConfig
from octapihub.core.errors import OctaPiHubError

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]
type SampleSink = Callable[[SenseHatSample], None]

# Public sensor-group name -> metric read from the port. Raw (unscaled) variants are not
# used for the orientation-style groups so that values stay in documented units.
GROUP_METRICS: dict[str, SenseHatMetric] = {
    "temperature": SenseHatMetric.TEMPERATURE,
    "humidity": SenseHatMetric.HUMIDITY,
    "pressure": SenseHatMetric.PRESSURE,
    "orientation": SenseHatMetric.ORIENTATION_DEGREES,
    "compass": SenseHatMetric.COMPASS,
    "gyroscope": SenseHatMetric.GYROSCOPE,
    "accelerometer": SenseHatMetric.ACCELEROMETER,
    "color": SenseHatMetric.COLOR,
}
_IMU_GROUPS = frozenset({"orientation", "compass", "gyroscope", "accelerometer"})


class SensorGroupState(StrEnum):
    """Observable state of one sensor group."""

    WAITING = "waiting"
    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class SenseHatSample:
    """One measurement with its unmodified reading.

    ``corrected_temperature_c`` is only set for the temperature group; the raw value stays
    in ``reading`` so that history and display can choose independently.
    """

    group: str
    reading: SenseHatReading
    measured_at: datetime
    corrected_temperature_c: float | None = None


@dataclass(frozen=True, slots=True)
class SensorGroupStatus:
    """Status snapshot of one sensor group; ``last_error`` is a safe user message."""

    group: str
    state: SensorGroupState
    last_sample: SenseHatSample | None
    last_error: str | None
    consecutive_failures: int
    last_error_code: str | None = None


def correct_temperature(raw_c: float, factor: float, offset_c: float) -> float:
    """Apply the configured linear temperature correction ``raw * factor + offset``."""
    return raw_c * factor + offset_c


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SenseHatMeasurementWorker:
    """Poll each enabled sensor group at its own interval inside one asyncio loop.

    The blocking port is called in a worker thread. A failing group is recorded and retried
    at its next interval; it never affects other groups, the sink, or the caller. Groups
    whose hardware is not present (for example colour on a board without the sensor) are
    marked unavailable and not polled.
    """

    def __init__(
        self,
        config: SenseHatConfig,
        port: SenseHatPort,
        sink: SampleSink | None = None,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._config = config
        self._port = port
        self._sink = sink
        self._clock = clock
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._statuses: dict[str, SensorGroupStatus] = {}

    @property
    def statuses(self) -> tuple[SensorGroupStatus, ...]:
        """Return status snapshots for all enabled groups in configuration order."""
        return tuple(self._statuses.values())

    async def start(self) -> None:
        """Start one polling task per enabled and supported sensor group."""
        if self._started:
            raise RuntimeError("Sense HAT worker has already been started")
        self._started = True
        self._stop = asyncio.Event()
        self._statuses = {}
        if not self._config.enabled:
            return
        try:
            capabilities = self._port.capabilities
        except Exception as exc:
            logger.exception("Sense HAT capability detection failed")
            message = (
                exc.user_message
                if isinstance(exc, OctaPiHubError)
                else OctaPiHubError("").user_message
            )
            for group, sensor in self._config.sensors.items():
                if sensor.enabled and group in GROUP_METRICS:
                    self._statuses[group] = self._status(group, SensorGroupState.ERROR, message)
            return
        for group, sensor in self._config.sensors.items():
            if not sensor.enabled or group not in GROUP_METRICS:
                continue
            if not self._supported(group, capabilities):
                self._statuses[group] = self._status(group, SensorGroupState.UNAVAILABLE, None)
                continue
            self._statuses[group] = self._status(group, SensorGroupState.WAITING, None)
            self._tasks.append(
                asyncio.create_task(
                    self._run_group(group, sensor), name=f"octapihub-sense-hat-{group}"
                )
            )

    async def stop(self) -> None:
        """Cancel all group tasks and mark the groups stopped."""
        if not self._started:
            return
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False
        for group, status in list(self._statuses.items()):
            if status.state is not SensorGroupState.UNAVAILABLE:
                self._statuses[group] = self._replace(status, state=SensorGroupState.STOPPED)

    @staticmethod
    def _supported(group: str, capabilities: SenseHatCapabilities) -> bool:
        if group in _IMU_GROUPS:
            return capabilities.imu
        if group == "color":
            return capabilities.color_sensor
        return capabilities.environmental_sensors

    async def _run_group(self, group: str, sensor: SensorConfig) -> None:
        metric = GROUP_METRICS[group]
        while not self._stop.is_set():
            await self._poll(group, metric)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=sensor.interval_seconds)

    async def _poll(self, group: str, metric: SenseHatMetric) -> None:
        previous = self._statuses[group]
        try:
            reading = await asyncio.to_thread(self._port.read_metric, metric)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate any sensor failure per group
            failures = previous.consecutive_failures + 1
            message = (
                exc.user_message
                if isinstance(exc, OctaPiHubError)
                else "Unerwarteter Fehler beim Lesen des Sensors. Details stehen im Log."
            )
            code = exc.error_code if isinstance(exc, OctaPiHubError) else OctaPiHubError.code
            self._statuses[group] = self._replace(
                previous,
                state=SensorGroupState.ERROR,
                last_error=message,
                last_error_code=code,
                consecutive_failures=failures,
            )
            if failures == 1:
                logger.warning("Sense HAT group %s read failed: %s", group, exc)
            else:
                logger.debug("Sense HAT group %s still failing (%d): %s", group, failures, exc)
            return

        corrected: float | None = None
        if group == "temperature" and isinstance(reading.value, float):
            corrected = correct_temperature(
                reading.value,
                self._config.temperature_correction_factor,
                self._config.temperature_offset_c,
            )
        sample = SenseHatSample(group, reading, self._clock(), corrected)
        self._statuses[group] = self._replace(
            previous,
            state=SensorGroupState.OK,
            last_sample=sample,
            last_error=None,
            consecutive_failures=0,
        )
        if self._sink is not None:
            try:
                self._sink(sample)
            except Exception:
                logger.exception("Sense HAT sample sink failed for group %s", group)

    @staticmethod
    def _status(group: str, state: SensorGroupState, error: str | None) -> SensorGroupStatus:
        return SensorGroupStatus(group, state, None, error, 0)

    @staticmethod
    def _replace(
        status: SensorGroupStatus,
        *,
        state: SensorGroupState | None = None,
        last_sample: SenseHatSample | None = None,
        last_error: str | None = None,
        last_error_code: str | None = None,
        consecutive_failures: int | None = None,
    ) -> SensorGroupStatus:
        """Copy a status; ``last_sample`` is kept on failure so stale data stays visible."""
        return SensorGroupStatus(
            status.group,
            state if state is not None else status.state,
            last_sample if last_sample is not None else status.last_sample,
            last_error if state in (SensorGroupState.ERROR,) else None,
            consecutive_failures
            if consecutive_failures is not None
            else status.consecutive_failures,
            last_error_code if state is SensorGroupState.ERROR else None,
        )
