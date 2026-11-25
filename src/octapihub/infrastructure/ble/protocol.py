"""Read-only Timeular / ZEI orientation protocol decoder."""

from uuid import UUID

from octapihub.application.ble import DeviceProfile
from octapihub.core.errors import ProtocolError
from octapihub.domain.cube import MAX_FACE, NEUTRAL_VALUES

ORIENTATION_SERVICE_UUID = "c7e70010-c847-11e6-8175-8c89a55d403c"
ORIENTATION_CHARACTERISTIC_UUID = "c7e70012-c847-11e6-8175-8c89a55d403c"
# Standard Bluetooth SIG Battery Service / Battery Level (one byte, 0-100 percent).
BATTERY_LEVEL_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"


def normalize_uuid(value: str) -> str:
    """Normalize a full BLE UUID to its canonical lowercase representation."""
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ProtocolError(
            "Invalid BLE characteristic UUID",
            details={"uuid": value},
        ) from exc


def supports_orientation_notifications(profile: DeviceProfile) -> bool:
    """Return whether the device exposes the expected readable event channel."""
    expected_uuid = normalize_uuid(ORIENTATION_CHARACTERISTIC_UUID)
    return any(
        normalize_uuid(characteristic.uuid) == expected_uuid
        and bool({"notify", "indicate"} & characteristic.properties)
        for characteristic in profile.characteristics
    )


def decode_orientation(payload: bytes) -> int:
    """Decode one orientation notification, normalising only known neutral values."""
    if len(payload) != 1:
        raise ProtocolError(
            "Orientation packet must contain exactly one byte "
            f"(len={len(payload)} raw=0x{payload.hex() or '-'})",
            details={"length": len(payload), "raw": payload.hex()},
        )
    value = payload[0]
    if value in NEUTRAL_VALUES:
        return 0
    if value > MAX_FACE:
        raise ProtocolError(
            f"Unsupported orientation value (raw=0x{value:02X})",
            details={"value": value},
        )
    return value


def supports_battery_read(profile: DeviceProfile) -> bool:
    """Return whether the device exposes a readable standard battery level."""
    expected_uuid = normalize_uuid(BATTERY_LEVEL_CHARACTERISTIC_UUID)
    return any(
        normalize_uuid(characteristic.uuid) == expected_uuid and "read" in characteristic.properties
        for characteristic in profile.characteristics
    )


def decode_battery_level(payload: bytes) -> int:
    """Decode the standard Battery Level characteristic (one byte, 0-100)."""
    if len(payload) != 1:
        raise ProtocolError(
            f"Battery level packet must contain exactly one byte (len={len(payload)})",
            details={"length": len(payload)},
        )
    value = payload[0]
    if value > 100:
        raise ProtocolError(
            f"Battery level out of range (raw=0x{value:02X})", details={"value": value}
        )
    return value
