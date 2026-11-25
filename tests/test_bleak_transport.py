from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable

import pytest
from bleak.exc import BleakError

import octapihub.infrastructure.ble.bleak_transport as bleak_transport
from octapihub.core.errors import BleConnectionError, UnsupportedDeviceError
from octapihub.infrastructure.ble.bleak_transport import (
    BleakClientLike,
    BleakDeviceTransport,
    GattCharacteristic,
    GattService,
)
from octapihub.infrastructure.ble.protocol import ORIENTATION_CHARACTERISTIC_UUID


class FakeCharacteristic:
    def __init__(self, uuid: str, properties: Iterable[str]) -> None:
        self.uuid = uuid
        self.properties = list(properties)


class FakeService:
    def __init__(self, characteristics: list[FakeCharacteristic]) -> None:
        self.characteristics = characteristics


class FakeBleakClient:
    def __init__(
        self,
        disconnected_callback: Callable[[object], None],
        *,
        characteristics: list[FakeCharacteristic] | None = None,
        connect_error: Exception | None = None,
        connect_before_error: bool = False,
    ) -> None:
        self._disconnected_callback = disconnected_callback
        self._characteristics = characteristics or [
            FakeCharacteristic(ORIENTATION_CHARACTERISTIC_UUID, ("notify",))
        ]
        self._connect_error = connect_error
        self._connect_before_error = connect_before_error
        self._connected = False
        self._notification_callback: Callable[[GattCharacteristic, bytearray], None] | None = None
        self.start_notify_calls: list[str] = []
        self.disconnect_calls = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def services(self) -> Iterable[GattService]:
        return [FakeService(self._characteristics)]

    async def connect(self) -> None:
        if self._connect_error is not None:
            self._connected = self._connect_before_error
            raise self._connect_error
        self._connected = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False
        self._disconnected_callback(self)

    async def start_notify(
        self,
        characteristic: str,
        callback: Callable[[GattCharacteristic, bytearray], None],
    ) -> None:
        self.start_notify_calls.append(characteristic)
        self._notification_callback = callback

    def emit_notification(self, payload: bytes) -> None:
        callback = self._notification_callback
        if callback is None:
            raise AssertionError("Notification is not subscribed")
        characteristic = self._characteristics[0]
        callback(characteristic, bytearray(payload))

    def lose_connection(self) -> None:
        self._connected = False
        self._disconnected_callback(self)


def make_transport(
    *,
    characteristics: list[FakeCharacteristic] | None = None,
    connect_error: Exception | None = None,
    connect_before_error: bool = False,
) -> tuple[BleakDeviceTransport, list[tuple[str, str, FakeBleakClient]]]:
    constructed: list[tuple[str, str, FakeBleakClient]] = []

    def client_factory(
        address: str, disconnected_callback: Callable[[object], None], adapter: str
    ) -> BleakClientLike:
        client = FakeBleakClient(
            disconnected_callback,
            characteristics=characteristics,
            connect_error=connect_error,
            connect_before_error=connect_before_error,
        )
        constructed.append((address, adapter, client))
        return client

    return (
        BleakDeviceTransport(
            "AA:BB:CC:DD:EE:01",
            "hci1",
            client_factory=client_factory,
        ),
        constructed,
    )


def test_bleak_transport_selects_adapter_and_snapshots_capabilities() -> None:
    transport, constructed = make_transport()

    asyncio.run(transport.connect())
    profile = asyncio.run(transport.discover())

    assert constructed[0][:2] == ("AA:BB:CC:DD:EE:01", "hci1")
    assert transport.is_connected
    assert profile.characteristics[0].uuid == ORIENTATION_CHARACTERISTIC_UUID
    assert profile.characteristics[0].properties == frozenset({"notify"})


def test_default_bleak_client_factory_passes_adapter_to_bluez(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def create_client(
        address: str, *, disconnected_callback: Callable[[object], None], bluez: dict[str, str]
    ) -> object:
        captured.update(
            address=address,
            disconnected_callback=disconnected_callback,
            bluez=bluez,
        )
        return object()

    monkeypatch.setattr(bleak_transport, "BleakClient", create_client)
    bleak_transport._create_client("AA:BB:CC:DD:EE:01", lambda _: None, "hci1")

    assert captured["address"] == "AA:BB:CC:DD:EE:01"
    assert captured["bluez"] == {"adapter": "hci1"}


def test_bleak_transport_subscribes_and_delivers_bytes_only() -> None:
    transport, constructed = make_transport()
    received: list[bytes] = []

    asyncio.run(transport.connect())
    asyncio.run(transport.discover())
    asyncio.run(transport.start_notify(ORIENTATION_CHARACTERISTIC_UUID, received.append))
    constructed[0][2].emit_notification(b"\x07")

    assert received == [b"\x07"]
    assert constructed[0][2].start_notify_calls == [ORIENTATION_CHARACTERISTIC_UUID]


def test_bleak_transport_refuses_unsupported_characteristics() -> None:
    transport, _ = make_transport(
        characteristics=[FakeCharacteristic(ORIENTATION_CHARACTERISTIC_UUID, ("read",))]
    )
    asyncio.run(transport.connect())
    asyncio.run(transport.discover())

    with pytest.raises(UnsupportedDeviceError):
        asyncio.run(transport.start_notify(ORIENTATION_CHARACTERISTIC_UUID, lambda _: None))


def test_bleak_transport_requires_discovery_before_subscribing() -> None:
    transport, _ = make_transport()
    asyncio.run(transport.connect())

    with pytest.raises(UnsupportedDeviceError):
        asyncio.run(transport.start_notify(ORIENTATION_CHARACTERISTIC_UUID, lambda _: None))


def test_bleak_connection_errors_are_translated_and_retain_cause() -> None:
    cause = BleakError("connection refused")
    transport, _ = make_transport(connect_error=cause)

    with pytest.raises(BleConnectionError) as error:
        asyncio.run(transport.connect())

    assert error.value.__cause__ is cause
    assert not transport.is_connected


def test_failed_connection_cleans_up_a_partially_connected_client() -> None:
    cause = BleakError("connection failed after link establishment")
    transport, constructed = make_transport(connect_error=cause, connect_before_error=True)

    with pytest.raises(BleConnectionError) as error:
        asyncio.run(transport.connect())

    assert error.value.__cause__ is cause
    assert constructed[0][2].disconnect_calls == 1
    assert not constructed[0][2].is_connected
    assert not transport.is_connected


def test_unexpected_disconnect_notifies_owner_but_close_does_not() -> None:
    transport, constructed = make_transport()
    losses: list[bool] = []
    transport.set_disconnected_callback(lambda: losses.append(True))
    asyncio.run(transport.connect())
    constructed[0][2].lose_connection()

    assert losses == [True]

    asyncio.run(transport.disconnect())
    asyncio.run(transport.connect())
    asyncio.run(transport.disconnect())

    assert losses == [True]
    assert not transport.is_connected
