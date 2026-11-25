"""Read-only Timeular / ZEI orientation protocol decoder."""

from octapihub.core.errors import ProtocolError
from octapihub.domain.cube import MAX_FACE, NEUTRAL_VALUES

ORIENTATION_SERVICE_UUID = "c7e70010-c847-11e6-8175-8c89a55d403c"
ORIENTATION_CHARACTERISTIC_UUID = "c7e70012-c847-11e6-8175-8c89a55d403c"


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
