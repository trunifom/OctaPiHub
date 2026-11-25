from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import (
    BleRuntime,
    BleRuntimeTuning,
    CubeConnectionState,
    CubeRuntimeStatus,
)
from octapihub.core.config import parse_config
from octapihub.core.errors import BleConnectionError, ProtocolError, UnsupportedDeviceError
from octapihub.infrastructure.ble.fake import FakeDeviceTransport, FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_battery_level,
    decode_orientation,
    supports_battery_read,
    supports_orientation_notifications,
)

ADDRESSES = ("AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03")
ORIENTATION = CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"}))
BATTERY = CharacteristicInfo(BATTERY_LEVEL_CHARACTERISTIC_UUID, frozenset({"read"}))


def _config(count: int = 2):  # type: ignore[no-untyped-def]
    roles = ["light", "music", "general"]
    return parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": f"Cube{i + 1}", "address": ADDRESSES[i], "role": roles[i]}
                for i in range(count)
            ],
        }
    )


def _runtime(
    factory: FakeTransportFactory, count: int = 2, poll: float = 600.0, clock: float = 1000.0
) -> BleRuntime:
    return BleRuntime(
        _config(count),
        factory,
        orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
        decode_orientation=decode_orientation,
        supports_orientation=supports_orientation_notifications,
        tuning=BleRuntimeTuning(
            connect_timeout_seconds=0.5,
            operation_timeout_seconds=0.5,
            backoff_base_seconds=0.01,
            backoff_cap_seconds=0.02,
            health_poll_seconds=0.01,
            debounce_ms=0,
            battery_poll_seconds=poll,
        ),
        jitter=lambda delay: delay,
        battery_characteristic_uuid=BATTERY_LEVEL_CHARACTERISTIC_UUID,
        decode_battery=decode_battery_level,
        supports_battery=supports_battery_read,
        wall_clock=lambda: clock,
    )


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def _by_name(runtime: BleRuntime) -> dict[str, CubeRuntimeStatus]:
    return {status.name: status for status in runtime.statuses}


def _transport(factory: FakeTransportFactory, address: str) -> FakeDeviceTransport:
    return next(t for a, _, t in factory.created if a == address)


# ---------- protocol ----------


@pytest.mark.parametrize("value", [0, 1, 20, 99, 100])
def test_decode_accepts_the_valid_range(value: int) -> None:
    assert decode_battery_level(bytes([value])) == value


@pytest.mark.parametrize("payload", [b"", b"\x01\x02", b"\x65", b"\xff"])
def test_decode_rejects_wrong_length_and_out_of_range(payload: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_battery_level(payload)


def test_support_requires_the_standard_uuid_with_read_property() -> None:
    assert supports_battery_read(DeviceProfile((ORIENTATION, BATTERY)))
    assert not supports_battery_read(DeviceProfile((ORIENTATION,)))
    notify_only = CharacteristicInfo(BATTERY_LEVEL_CHARACTERISTIC_UUID, frozenset({"notify"}))
    assert not supports_battery_read(DeviceProfile((ORIENTATION, notify_only)))
    upper = CharacteristicInfo(BATTERY_LEVEL_CHARACTERISTIC_UUID.upper(), frozenset({"read"}))
    assert supports_battery_read(DeviceProfile((upper,)))


# ---------- fake transport ----------


def test_fake_transport_read_is_guarded() -> None:
    async def scenario() -> None:
        factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
        transport = factory.create(ADDRESSES[0], "hci0")
        with pytest.raises(BleConnectionError):
            await transport.read_characteristic(BATTERY_LEVEL_CHARACTERISTIC_UUID)
        await transport.connect()
        await transport.discover()
        with pytest.raises(UnsupportedDeviceError):
            await transport.read_characteristic(ORIENTATION_CHARACTERISTIC_UUID)
        assert await transport.read_characteristic(BATTERY_LEVEL_CHARACTERISTIC_UUID) == b""

    asyncio.run(scenario())


# ---------- runtime ----------


def test_battery_is_read_after_connecting_and_timestamped() -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    factory.values[ADDRESSES[0]] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([83])}
    runtime = _runtime(factory, count=1, clock=1234.5)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].battery_percent is not None)
        status = runtime.statuses[0]
        assert status.battery_percent == 83
        assert status.battery_checked_at == 1234.5
        assert status.battery_supported is True
        assert status.battery_error is None
        assert status.connected
        await runtime.stop()

    asyncio.run(scenario())


def test_three_cubes_report_their_own_levels() -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    levels = {ADDRESSES[0]: 95, ADDRESSES[1]: 18, ADDRESSES[2]: 4}
    for address, level in levels.items():
        factory.values[address] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([level])}
    runtime = _runtime(factory, count=3)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: all(s.battery_percent is not None for s in runtime.statuses))
        assert [s.battery_percent for s in runtime.statuses] == [95, 18, 4]
        assert all(s.connected for s in runtime.statuses)
        await runtime.stop()

    asyncio.run(scenario())


def test_cube_without_battery_service_is_marked_unsupported_and_never_read() -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION,)))
    runtime = _runtime(factory, count=1)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].state is CubeConnectionState.WAITING_FOR_BASELINE)
        await asyncio.sleep(0.05)
        status = runtime.statuses[0]
        assert status.battery_supported is False
        assert status.battery_percent is None
        assert status.battery_error is None
        await runtime.stop()

    asyncio.run(scenario())
    assert "read" not in _transport(factory, ADDRESSES[0]).operations


def test_runtime_without_battery_configuration_stays_silent() -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    runtime = BleRuntime(
        _config(1),
        factory,
        orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
        decode_orientation=decode_orientation,
        supports_orientation=supports_orientation_notifications,
        tuning=BleRuntimeTuning(health_poll_seconds=0.01, debounce_ms=0),
    )

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].connected)
        await asyncio.sleep(0.05)
        assert runtime.statuses[0].battery_supported is False
        await runtime.stop()

    asyncio.run(scenario())
    assert "read" not in _transport(factory, ADDRESSES[0]).operations


@pytest.mark.parametrize("payload", [b"", b"\x01\x02", b"\x65"])
def test_invalid_battery_data_is_reported_without_losing_the_connection(payload: bytes) -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    factory.values[ADDRESSES[0]] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: payload}
    runtime = _runtime(factory, count=1)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].battery_error is not None)
        status = runtime.statuses[0]
        assert status.battery_percent is None
        assert status.connected
        assert status.state is CubeConnectionState.WAITING_FOR_BASELINE
        await runtime.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [BleConnectionError("gatt"), UnsupportedDeviceError("no"), TimeoutError()],
)
def test_failing_read_keeps_the_connection_and_recovers_on_the_next_poll(
    failure: Exception,
) -> None:
    failures: dict[str, Callable[[], Exception]] = {"read": lambda: failure}
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)), failures=failures)
    factory.values[ADDRESSES[0]] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([70])}
    runtime = _runtime(factory, count=1, poll=0.03)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].battery_error is not None)
        assert runtime.statuses[0].connected
        failures.clear()  # the problem goes away
        await _until(lambda: runtime.statuses[0].battery_percent == 70)
        assert runtime.statuses[0].battery_error is None
        await runtime.stop()

    asyncio.run(scenario())


def test_battery_is_polled_periodically_and_keeps_the_last_value_on_failure() -> None:
    failures: dict[str, Callable[[], Exception]] = {}
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)), failures=failures)
    factory.values[ADDRESSES[0]] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([80])}
    runtime = _runtime(factory, count=1, poll=0.03)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].battery_percent == 80)
        _transport(factory, ADDRESSES[0]).set_value(BATTERY_LEVEL_CHARACTERISTIC_UUID, bytes([60]))
        await _until(lambda: runtime.statuses[0].battery_percent == 60)
        failures["read"] = lambda: BleConnectionError("flaky")
        await _until(lambda: runtime.statuses[0].battery_error is not None)
        assert runtime.statuses[0].battery_percent == 60  # last known value stays visible
        await runtime.stop()

    asyncio.run(scenario())


def test_battery_is_read_again_after_a_reconnect() -> None:
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    factory.values[ADDRESSES[0]] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([50])}
    runtime = _runtime(factory, count=1)

    async def scenario() -> None:
        await runtime.start()
        await _until(lambda: runtime.statuses[0].battery_percent == 50)
        _transport(factory, ADDRESSES[0]).lose_connection()
        await _until(lambda: len(factory.created) == 2)
        await _until(lambda: "read" in factory.created[1][2].operations)
        await runtime.stop()

    asyncio.run(scenario())


def test_tuning_rejects_non_positive_battery_interval() -> None:
    with pytest.raises(ValueError):
        BleRuntimeTuning(battery_poll_seconds=0)
