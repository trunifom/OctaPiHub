from __future__ import annotations

import asyncio

from octapihub.application.ble import CharacteristicInfo, DeviceProfile, DeviceTransport
from octapihub.application.ble_runtime import (
    BleRuntime,
    BleRuntimeTuning,
    CubeConnectionState,
)
from octapihub.core.config import parse_config
from octapihub.domain.cube import OrientationKind
from octapihub.infrastructure.ble.fake import FakeDeviceTransport, FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)


def valid_config():
    return parse_config(
        {
            "schema_version": 1,
            "bluetooth": {"adapter": "hci0"},
            "debounce_ms": 10,
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
        }
    )


def valid_profile() -> DeviceProfile:
    return DeviceProfile(
        (
            CharacteristicInfo(
                ORIENTATION_CHARACTERISTIC_UUID,
                frozenset({"notify"}),
            ),
        )
    )


def tuning(**overrides: object) -> BleRuntimeTuning:
    values: dict[str, object] = {
        "connect_timeout_seconds": 0.5,
        "operation_timeout_seconds": 0.5,
        "backoff_base_seconds": 0.01,
        "backoff_cap_seconds": 0.02,
        "health_poll_seconds": 0.01,
        "debounce_ms": 10,
    }
    values.update(overrides)
    return BleRuntimeTuning(**values)  # type: ignore[arg-type]


async def wait_until(predicate, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError("Condition was not reached in time")
        await asyncio.sleep(0.001)


def make_runtime(
    factory: FakeTransportFactory,
    *,
    runtime_tuning: BleRuntimeTuning | None = None,
) -> BleRuntime:
    return BleRuntime(
        valid_config(),
        factory,
        orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
        decode_orientation=decode_orientation,
        supports_orientation=supports_orientation_notifications,
        tuning=runtime_tuning or tuning(),
        jitter=lambda delay: delay,
    )


def test_silent_connected_cube_does_not_trigger_reconnect() -> None:
    async def scenario() -> None:
        factory = FakeTransportFactory(valid_profile())
        runtime = make_runtime(factory)
        await runtime.start()
        try:
            await wait_until(
                lambda: all(
                    status.state is CubeConnectionState.WAITING_FOR_BASELINE
                    for status in runtime.statuses
                )
            )
            await asyncio.sleep(0.05)

            assert len(factory.created) == 2
            assert all(status.connected for status in runtime.statuses)
            assert all(
                status.state is CubeConnectionState.WAITING_FOR_BASELINE
                for status in runtime.statuses
            )
        finally:
            await runtime.stop()

        assert all(status.state is CubeConnectionState.STOPPED for status in runtime.statuses)

    asyncio.run(scenario())


def test_runtime_publishes_baseline_then_debounced_face_change() -> None:
    async def scenario() -> None:
        factory = FakeTransportFactory(valid_profile())
        runtime = make_runtime(factory)
        await runtime.start()
        try:
            await wait_until(
                lambda: (
                    len(factory.created) == 2
                    and all(
                        status.state is CubeConnectionState.WAITING_FOR_BASELINE
                        for status in runtime.statuses
                    )
                )
            )
            transports = [entry[2] for entry in factory.created]
            for transport in transports:
                transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")

            initial_events = [await asyncio.wait_for(runtime.next_event(), 1) for _ in range(2)]
            assert all(
                event.orientation.kind is OrientationKind.BASELINE for event in initial_events
            )
            assert all(status.state is CubeConnectionState.READY for status in runtime.statuses)

            light_transport = next(
                entry[2] for entry in factory.created if entry[0] == "AA:BB:CC:DD:EE:01"
            )
            light_transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
            event = await asyncio.wait_for(runtime.next_event(), 1)

            assert event.cube_name == "Licht"
            assert event.role == "light"
            assert event.orientation.kind is OrientationKind.FACE_CHANGE
            assert event.orientation.value == 4
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_runtime_serializes_connection_setup_for_both_cubes() -> None:
    class SerialFactory(FakeTransportFactory):
        def __init__(self) -> None:
            super().__init__(valid_profile())
            self.active_connections = 0
            self.maximum_active_connections = 0

        def create(self, address: str, adapter: str) -> DeviceTransport:
            factory = self

            class SlowTransport(FakeDeviceTransport):
                async def connect(self) -> None:
                    factory.active_connections += 1
                    factory.maximum_active_connections = max(
                        factory.maximum_active_connections,
                        factory.active_connections,
                    )
                    try:
                        await asyncio.sleep(0.01)
                        await super().connect()
                    finally:
                        factory.active_connections -= 1

            transport = SlowTransport(valid_profile())
            self.created.append((address, adapter, transport))
            return transport

    async def scenario() -> None:
        factory = SerialFactory()
        runtime = make_runtime(factory)
        await runtime.start()
        try:
            await wait_until(
                lambda: all(
                    status.state is CubeConnectionState.WAITING_FOR_BASELINE
                    for status in runtime.statuses
                )
            )
            assert factory.maximum_active_connections == 1
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_disconnect_reconnects_only_affected_cube_and_resubscribes() -> None:
    async def scenario() -> None:
        factory = FakeTransportFactory(valid_profile())
        runtime = make_runtime(factory)
        await runtime.start()
        try:
            await wait_until(
                lambda: (
                    len(factory.created) == 2
                    and all(
                        status.state is CubeConnectionState.WAITING_FOR_BASELINE
                        for status in runtime.statuses
                    )
                )
            )
            first_by_address = {address: transport for address, _, transport in factory.created}
            for transport in first_by_address.values():
                transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x02")
            for _ in range(2):
                await asyncio.wait_for(runtime.next_event(), 1)
            await wait_until(
                lambda: all(
                    status.state is CubeConnectionState.READY for status in runtime.statuses
                )
            )

            first_by_address["AA:BB:CC:DD:EE:01"].lose_connection()
            await wait_until(lambda: len(factory.created) >= 3)
            await wait_until(
                lambda: (
                    next(status for status in runtime.statuses if status.role == "light").state
                    is CubeConnectionState.WAITING_FOR_BASELINE
                )
            )
            retry = next(
                transport
                for address, _, transport in factory.created[2:]
                if address == "AA:BB:CC:DD:EE:01"
            )
            retry.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x03")
            baseline = await asyncio.wait_for(runtime.next_event(), 1)

            assert baseline.cube_name == "Licht"
            assert baseline.orientation.kind is OrientationKind.BASELINE
            music = next(status for status in runtime.statuses if status.role == "music")
            assert music.state is CubeConnectionState.READY
            assert music.connected
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_transient_connect_failure_is_retried() -> None:
    class TransientFactory:
        def __init__(self) -> None:
            self.created: list[tuple[str, FakeDeviceTransport]] = []
            self.light_attempts = 0

        def create(self, address: str, adapter: str) -> DeviceTransport:
            failures = None
            if address.endswith("01"):
                self.light_attempts += 1
                if self.light_attempts == 1:
                    failures = {"connect": lambda: OSError("temporary radio failure")}
            transport = FakeDeviceTransport(valid_profile(), failures=failures)
            self.created.append((address, transport))
            return transport

    async def scenario() -> None:
        factory = TransientFactory()
        runtime = make_runtime(factory)  # type: ignore[arg-type]
        await runtime.start()
        try:
            await wait_until(
                lambda: (
                    factory.light_attempts >= 2
                    and all(
                        status.state is CubeConnectionState.WAITING_FOR_BASELINE
                        for status in runtime.statuses
                    )
                )
            )
            assert factory.light_attempts == 2
            assert (
                next(
                    status for status in runtime.statuses if status.role == "light"
                ).reconnect_attempt
                == 1
            )
        finally:
            await runtime.stop()

    asyncio.run(scenario())


def test_unsupported_cube_is_not_retried_and_other_cube_remains_active() -> None:
    unsupported_profile = DeviceProfile(
        (
            CharacteristicInfo(
                ORIENTATION_CHARACTERISTIC_UUID,
                frozenset({"read"}),
            ),
        )
    )

    class PerCubeFactory:
        def __init__(self) -> None:
            self.created: list[tuple[str, FakeDeviceTransport]] = []

        def create(self, address: str, adapter: str) -> DeviceTransport:
            profile = unsupported_profile if address.endswith("01") else valid_profile()
            transport = FakeDeviceTransport(profile)
            self.created.append((address, transport))
            return transport

    async def scenario() -> None:
        factory = PerCubeFactory()
        runtime = make_runtime(factory)  # type: ignore[arg-type]
        await runtime.start()
        try:
            await wait_until(
                lambda: (
                    any(
                        status.state is CubeConnectionState.UNSUPPORTED
                        for status in runtime.statuses
                    )
                    and any(
                        status.state is CubeConnectionState.WAITING_FOR_BASELINE
                        for status in runtime.statuses
                    )
                )
            )
            await asyncio.sleep(0.05)

            light = next(status for status in runtime.statuses if status.role == "light")
            music = next(status for status in runtime.statuses if status.role == "music")
            assert light.state is CubeConnectionState.UNSUPPORTED
            assert music.connected
            assert len(factory.created) == 2
        finally:
            await runtime.stop()

    asyncio.run(scenario())
