from __future__ import annotations

from octapihub.core.errors import BleConnectionError, ProtocolError, UnsupportedDeviceError
from octapihub.infrastructure.ble.fake import FakeDeviceTransport, FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    normalize_uuid,
    supports_orientation_notifications,
)
from octapihub.infrastructure.ble.transport import (
    CharacteristicInfo,
    DeviceProfile,
)


def profile_with_orientation(properties: frozenset[str] = frozenset({"notify"})) -> DeviceProfile:
    return DeviceProfile(
        characteristics=(CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, properties),)
    )


def test_fake_factory_creates_fresh_transports_with_adapter_selection() -> None:
    factory = FakeTransportFactory(profile_with_orientation())

    first = factory.create("AA:BB:CC:DD:EE:01", "hci0")
    second = factory.create("AA:BB:CC:DD:EE:02", "hci1")

    assert first is not second
    assert factory.created[0][:2] == ("AA:BB:CC:DD:EE:01", "hci0")
    assert factory.created[1][:2] == ("AA:BB:CC:DD:EE:02", "hci1")
    assert not hasattr(first, "write_gatt_char")


def test_fake_transport_requires_connection_before_discovery_or_subscription() -> None:
    transport = FakeDeviceTransport(profile_with_orientation())

    try:
        import asyncio

        asyncio.run(transport.discover())
    except BleConnectionError as exc:
        assert "not connected" in exc.message
    else:
        raise AssertionError("Discovery unexpectedly succeeded before connecting")


def test_fake_transport_delivers_only_active_subscribed_notifications() -> None:
    import asyncio

    transport = FakeDeviceTransport(profile_with_orientation())
    notifications: list[bytes] = []
    asyncio.run(transport.connect())
    profile = asyncio.run(transport.discover())
    assert supports_orientation_notifications(profile)
    asyncio.run(transport.start_notify(ORIENTATION_CHARACTERISTIC_UUID, notifications.append))

    transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID.upper(), bytearray(b"\x03"))

    assert notifications == [b"\x03"]
    asyncio.run(transport.disconnect())
    assert not transport.is_connected
    try:
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
    except BleConnectionError as exc:
        assert "No fake subscription" in exc.message
    else:
        raise AssertionError("Notification unexpectedly delivered after disconnect")


def test_fake_transport_rejects_missing_or_non_notifiable_characteristics() -> None:
    import asyncio

    for properties in (frozenset({"read"}), frozenset({"write"})):
        transport = FakeDeviceTransport(profile_with_orientation(properties))
        asyncio.run(transport.connect())
        try:
            asyncio.run(transport.start_notify(ORIENTATION_CHARACTERISTIC_UUID, lambda _: None))
        except UnsupportedDeviceError:
            pass
        else:
            raise AssertionError(f"Unexpected notification support for {properties}")


def test_fake_transport_reports_remote_disconnect_once() -> None:
    import asyncio

    transport = FakeDeviceTransport(profile_with_orientation())
    disconnects: list[bool] = []
    transport.set_disconnected_callback(lambda: disconnects.append(True))
    asyncio.run(transport.connect())

    transport.lose_connection()
    transport.lose_connection()

    assert not transport.is_connected
    assert disconnects == [True]


def test_fake_transport_preserves_injected_adapter_failure() -> None:
    import asyncio

    failure = OSError("radio unavailable")
    transport = FakeDeviceTransport(
        profile_with_orientation(), failures={"connect": lambda: failure}
    )

    try:
        asyncio.run(transport.connect())
    except OSError as exc:
        assert exc is failure
    else:
        raise AssertionError("Injected connect failure was not raised")
    assert not transport.is_connected


def test_orientation_capability_requires_expected_uuid_and_notify_or_indicate() -> None:
    wrong_uuid = DeviceProfile(
        characteristics=(
            CharacteristicInfo("00002a19-0000-1000-8000-00805f9b34fb", frozenset({"notify"})),
        )
    )
    read_only = profile_with_orientation(frozenset({"read"}))
    indication = profile_with_orientation(frozenset({"indicate"}))

    assert not supports_orientation_notifications(wrong_uuid)
    assert not supports_orientation_notifications(read_only)
    assert supports_orientation_notifications(indication)


def test_normalize_uuid_rejects_malformed_input_and_canonicalizes() -> None:
    assert (
        normalize_uuid(ORIENTATION_CHARACTERISTIC_UUID.upper()) == ORIENTATION_CHARACTERISTIC_UUID
    )

    try:
        normalize_uuid("not-a-uuid")
    except ProtocolError as exc:
        assert exc.details["uuid"] == "not-a-uuid"
    else:
        raise AssertionError("Malformed UUID was accepted")
