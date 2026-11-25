"""Serialized BLE connection runtime for the configured cubes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from random import SystemRandom
from typing import cast

from octapihub.application.ble import DeviceProfile, DeviceTransport, TransportFactory
from octapihub.core.config import AppConfig, CubeConfig
from octapihub.core.errors import BleConnectionError, ProtocolError, UnsupportedDeviceError
from octapihub.domain.cube import OrientationDebouncer, OrientationEvent, reconnect_delay_seconds

logger = logging.getLogger(__name__)

OrientationDecoder = Callable[[bytes], int]
CapabilityCheck = Callable[[DeviceProfile], bool]
MonotonicClock = Callable[[], int]
Jitter = Callable[[float], float]
_UNSET = object()
_RANDOM = SystemRandom()


class CubeConnectionState(StrEnum):
    """Observable state of one cube worker."""

    WAITING_FOR_RADIO = "waiting_for_radio"
    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    SUBSCRIBING = "subscribing"
    WAITING_FOR_BASELINE = "waiting_for_baseline"
    READY = "ready"
    RECONNECT_WAIT = "reconnect_wait"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class CubeEvent:
    """One accepted orientation event tagged with its configured cube."""

    cube_name: str
    role: str
    orientation: OrientationEvent


@dataclass(frozen=True, slots=True)
class CubeRuntimeStatus:
    """Safe status snapshot that intentionally omits the Bluetooth address."""

    name: str
    role: str
    state: CubeConnectionState
    connected: bool
    reconnect_attempt: int
    last_value: int | None
    last_error: str | None
    dropped_notifications: int


@dataclass(frozen=True, slots=True)
class BleRuntimeTuning:
    """Timeout, queue, debounce, and reconnect controls for the BLE runtime."""

    connect_timeout_seconds: float = 20.0
    operation_timeout_seconds: float = 10.0
    backoff_base_seconds: float = 1.0
    backoff_cap_seconds: float = 60.0
    health_poll_seconds: float = 0.5
    notification_queue_size: int = 100
    event_queue_size: int = 100
    debounce_ms: int = 300

    def __post_init__(self) -> None:
        if self.connect_timeout_seconds <= 0 or self.operation_timeout_seconds <= 0:
            raise ValueError("BLE operation timeouts must be positive")
        if self.backoff_base_seconds <= 0 or self.backoff_cap_seconds < self.backoff_base_seconds:
            raise ValueError("Invalid BLE reconnect backoff limits")
        if self.health_poll_seconds <= 0:
            raise ValueError("health_poll_seconds must be positive")
        if self.notification_queue_size < 1 or self.event_queue_size < 1:
            raise ValueError("BLE queue sizes must be positive")
        if not 0 <= self.debounce_ms <= 999:
            raise ValueError("debounce_ms must be between 0 and 999")


def _monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _random_jitter(delay_seconds: float) -> float:
    """Apply bounded negative-or-positive jitter without exceeding the configured delay."""
    return _RANDOM.uniform(delay_seconds * 0.8, delay_seconds * 1.2)


class BleRuntime:
    """Own and supervise all cube transports in one asyncio loop.

    Connection setup (connect, capability discovery, and notification subscription) is
    serialized across configured cubes. Established connections can then remain active
    together. Silence from a sleeping cube is not considered a connection failure;
    reconnects are driven by the transport's connection state or disconnect callback.
    """

    def __init__(
        self,
        config: AppConfig,
        transport_factory: TransportFactory,
        *,
        orientation_characteristic_uuid: str,
        decode_orientation: OrientationDecoder,
        supports_orientation: CapabilityCheck,
        tuning: BleRuntimeTuning | None = None,
        monotonic_ms: MonotonicClock = _monotonic_ms,
        jitter: Jitter = _random_jitter,
    ) -> None:
        self._config = config
        self._factory = transport_factory
        self._orientation_uuid = orientation_characteristic_uuid
        self._decode = decode_orientation
        self._supports_orientation = supports_orientation
        self._tuning = tuning or BleRuntimeTuning(debounce_ms=config.debounce_ms)
        self._monotonic_ms = monotonic_ms
        self._jitter = jitter
        self._connection_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._events: asyncio.Queue[CubeEvent] = asyncio.Queue(
            maxsize=self._tuning.event_queue_size
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._statuses = {
            cube.name: CubeRuntimeStatus(
                name=cube.name,
                role=cube.role.value,
                state=CubeConnectionState.STOPPED,
                connected=False,
                reconnect_attempt=0,
                last_value=None,
                last_error=None,
                dropped_notifications=0,
            )
            for cube in self._config.cubes
        }
        self._dropped_events = 0

    @property
    def statuses(self) -> tuple[CubeRuntimeStatus, ...]:
        """Return status snapshots ordered by configured cube order."""
        return tuple(self._statuses[cube.name] for cube in self._config.cubes)

    @property
    def dropped_events(self) -> int:
        """Return the number of accepted events dropped from the bounded output queue."""
        return self._dropped_events

    async def start(self) -> None:
        """Start one worker per cube; all workers share this runtime's connection lock."""
        if self._started:
            raise RuntimeError("BLE runtime has already been started")
        self._started = True
        self._stop = asyncio.Event()
        self._connection_lock = asyncio.Lock()
        self._tasks = [
            asyncio.create_task(self._run_cube(cube), name=f"octapihub-ble-{index}")
            for index, cube in enumerate(self._config.cubes)
        ]

    async def stop(self) -> None:
        """Cancel workers and allow each transport's finally block to release resources."""
        if not self._started:
            return
        self._stop.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*self._tasks, return_exceptions=True)
        self._started = False
        self._tasks.clear()
        failures = [
            result
            for result in results
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError)
        ]
        if failures:
            raise RuntimeError("A BLE worker failed during shutdown") from failures[0]

    async def next_event(self) -> CubeEvent:
        """Wait for the next accepted event from either cube."""
        return await self._events.get()

    async def _run_cube(self, cube: CubeConfig) -> None:
        attempt = 0
        status = self._statuses[cube.name]
        while not self._stop.is_set():
            transport: DeviceTransport | None = None
            reached_ready = False
            try:
                status = self._replace_status(
                    status,
                    state=CubeConnectionState.WAITING_FOR_RADIO,
                    connected=False,
                    reconnect_attempt=attempt,
                    last_error=None,
                )
                transport = self._factory.create(cube.address, self._config.bluetooth.adapter)
                loop = asyncio.get_running_loop()
                notifications: asyncio.Queue[bytes | None] = asyncio.Queue(
                    maxsize=self._tuning.notification_queue_size
                )
                disconnected = asyncio.Event()
                status_ref = [status]

                def queue_item(
                    payload: bytes | None,
                    *,
                    notification_queue: asyncio.Queue[bytes | None] = notifications,
                    status_holder: list[CubeRuntimeStatus] = status_ref,
                ) -> None:
                    if notification_queue.full():
                        status_holder[0] = self._replace_status(
                            status_holder[0],
                            dropped_notifications=status_holder[0].dropped_notifications + 1,
                        )
                        with contextlib.suppress(asyncio.QueueEmpty):
                            notification_queue.get_nowait()
                    try:
                        notification_queue.put_nowait(payload)
                    except asyncio.QueueFull:
                        status_holder[0] = self._replace_status(
                            status_holder[0],
                            dropped_notifications=status_holder[0].dropped_notifications + 1,
                        )

                def on_notification(
                    payload: bytes,
                    *,
                    event_loop: asyncio.AbstractEventLoop = loop,
                    enqueue: Callable[..., None] = queue_item,
                    role: str = cube.role.value,
                ) -> None:
                    try:
                        event_loop.call_soon_threadsafe(enqueue, bytes(payload))
                    except RuntimeError:
                        logger.warning("Late BLE notification ignored for cube role %s", role)

                def on_disconnected(
                    *,
                    event_loop: asyncio.AbstractEventLoop = loop,
                    enqueue: Callable[..., None] = queue_item,
                    disconnect_event: asyncio.Event = disconnected,
                    role: str = cube.role.value,
                ) -> None:
                    def mark_disconnected() -> None:
                        disconnect_event.set()
                        enqueue(None)

                    try:
                        event_loop.call_soon_threadsafe(mark_disconnected)
                    except RuntimeError:
                        logger.warning(
                            "Late BLE disconnect callback ignored for cube role %s", role
                        )

                transport.set_disconnected_callback(on_disconnected)
                async with self._connection_lock:
                    if self._stop.is_set():
                        break
                    status = self._replace_status(status, state=CubeConnectionState.CONNECTING)
                    status_ref[0] = status
                    await asyncio.wait_for(
                        transport.connect(),
                        timeout=self._tuning.connect_timeout_seconds,
                    )
                    status = self._replace_status(
                        status, connected=True, state=CubeConnectionState.DISCOVERING
                    )
                    status_ref[0] = status
                    profile = await asyncio.wait_for(
                        transport.discover(),
                        timeout=self._tuning.operation_timeout_seconds,
                    )
                    if not self._supports_orientation(profile):
                        raise UnsupportedDeviceError(
                            "Cube does not expose the expected orientation notification"
                        )
                    status = self._replace_status(status, state=CubeConnectionState.SUBSCRIBING)
                    status_ref[0] = status
                    await asyncio.wait_for(
                        transport.start_notify(self._orientation_uuid, on_notification),
                        timeout=self._tuning.operation_timeout_seconds,
                    )

                if self._stop.is_set():
                    break
                debouncer = OrientationDebouncer(self._tuning.debounce_ms)
                status = self._replace_status(
                    status,
                    state=CubeConnectionState.WAITING_FOR_BASELINE,
                    connected=transport.is_connected,
                )
                status_ref[0] = status
                reached_ready = await self._consume_notifications(
                    cube,
                    transport,
                    notifications,
                    disconnected,
                    debouncer,
                    status_ref,
                )
                status = status_ref[0]
                if not self._stop.is_set():
                    attempt = 1 if reached_ready else attempt + 1
                    status = self._replace_status(
                        status,
                        state=CubeConnectionState.RECONNECT_WAIT,
                        connected=False,
                        reconnect_attempt=attempt,
                        last_error=BleConnectionError("Bluetooth connection was lost").user_message,
                    )
            except asyncio.CancelledError:
                raise
            except UnsupportedDeviceError as exc:
                status = self._replace_status(
                    status,
                    state=CubeConnectionState.UNSUPPORTED,
                    connected=False,
                    last_error=exc.user_message,
                )
                logger.error(
                    "BLE capability check failed for cube role %s: %s", cube.role.value, exc
                )
                return
            except (BleConnectionError, TimeoutError, OSError) as exc:
                attempt += 1
                message = (
                    exc.user_message
                    if isinstance(exc, BleConnectionError)
                    else (
                        "Bluetooth operation timed out"
                        if isinstance(exc, TimeoutError)
                        else "Bluetooth adapter operation failed"
                    )
                )
                status = self._replace_status(
                    status,
                    state=CubeConnectionState.RECONNECT_WAIT,
                    connected=False,
                    reconnect_attempt=attempt,
                    last_error=message,
                )
                logger.warning(
                    "BLE connection cycle failed for cube role %s: %s",
                    cube.role.value,
                    message,
                )
            except Exception:
                status = self._replace_status(
                    status,
                    state=CubeConnectionState.FAILED,
                    connected=False,
                    last_error="Unerwarteter Fehler im Bluetooth-Worker. Details stehen im Log.",
                )
                logger.exception("Unexpected BLE worker failure for cube role %s", cube.role.value)
                return
            finally:
                if transport is not None:
                    try:
                        await transport.disconnect()
                    except BleConnectionError as exc:
                        status = self._replace_status(status, last_error=exc.user_message)
                        logger.error(
                            "BLE transport cleanup failed for cube role %s: %s",
                            cube.role.value,
                            exc,
                        )
                if self._stop.is_set():
                    status = self._replace_status(
                        status,
                        state=CubeConnectionState.STOPPED,
                        connected=False,
                    )
                self._statuses[cube.name] = status

            if self._stop.is_set():
                break
            if status.state is CubeConnectionState.UNSUPPORTED:
                return
            delay = reconnect_delay_seconds(
                attempt,
                base=self._tuning.backoff_base_seconds,
                cap=self._tuning.backoff_cap_seconds,
            )
            delay = min(self._tuning.backoff_cap_seconds, max(0.0, self._jitter(delay)))
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=delay)

        self._statuses[cube.name] = self._replace_status(
            status,
            state=CubeConnectionState.STOPPED,
            connected=False,
        )

    async def _consume_notifications(
        self,
        cube: CubeConfig,
        transport: DeviceTransport,
        notifications: asyncio.Queue[bytes | None],
        disconnected: asyncio.Event,
        debouncer: OrientationDebouncer,
        status_ref: list[CubeRuntimeStatus],
    ) -> bool:
        reached_ready = False
        invalid_notifications = 0
        while not self._stop.is_set() and not disconnected.is_set():
            if not transport.is_connected:
                return reached_ready
            timeout = self._tuning.health_poll_seconds
            deadline = debouncer.next_deadline_ms()
            if deadline is not None:
                remaining = max(0.0, (deadline - self._monotonic_ms()) / 1000)
                timeout = min(timeout, remaining)
            try:
                payload = await asyncio.wait_for(notifications.get(), timeout=timeout)
            except TimeoutError:
                events = debouncer.tick(self._monotonic_ms())
            else:
                if payload is None:
                    return reached_ready
                try:
                    value = self._decode(payload)
                except ProtocolError as exc:
                    invalid_notifications += 1
                    status_ref[0] = self._replace_status(
                        status_ref[0],
                        last_error=f"{exc.error_code}: {exc.message}",
                    )
                    if invalid_notifications == 1:
                        logger.warning(
                            "Invalid orientation notification for cube role %s: %s",
                            cube.role.value,
                            exc,
                        )
                    else:
                        logger.debug(
                            "Ignoring invalid orientation notification for cube role %s (%d total)",
                            cube.role.value,
                            invalid_notifications,
                        )
                    continue
                events = debouncer.feed(value, self._monotonic_ms())

            for event in events:
                status = status_ref[0]
                if event.kind.value == "baseline":
                    reached_ready = True
                    status = self._replace_status(
                        status,
                        state=CubeConnectionState.READY,
                        reconnect_attempt=0,
                        last_error=None,
                    )
                status_ref[0] = self._replace_status(status, last_value=event.value)
                self._publish_event(CubeEvent(cube.name, cube.role.value, event))
        return reached_ready

    def _publish_event(self, event: CubeEvent) -> None:
        if self._events.full():
            try:
                self._events.get_nowait()
                self._dropped_events += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self._events.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_events += 1

    def _replace_status(
        self,
        status: CubeRuntimeStatus,
        *,
        state: CubeConnectionState | None = None,
        connected: bool | None = None,
        reconnect_attempt: int | None = None,
        last_value: int | None | object = _UNSET,
        last_error: str | None | object = _UNSET,
        dropped_notifications: int | None = None,
    ) -> CubeRuntimeStatus:
        updated = CubeRuntimeStatus(
            name=status.name,
            role=status.role,
            state=state if state is not None else status.state,
            connected=connected if connected is not None else status.connected,
            reconnect_attempt=(
                reconnect_attempt if reconnect_attempt is not None else status.reconnect_attempt
            ),
            last_value=(
                status.last_value if last_value is _UNSET else cast(int | None, last_value)
            ),
            last_error=(
                status.last_error if last_error is _UNSET else cast(str | None, last_error)
            ),
            dropped_notifications=(
                dropped_notifications
                if dropped_notifications is not None
                else status.dropped_notifications
            ),
        )
        self._statuses[status.name] = updated
        return updated
