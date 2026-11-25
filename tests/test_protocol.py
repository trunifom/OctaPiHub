from octapihub.core.errors import ProtocolError
from octapihub.infrastructure.ble.protocol import decode_orientation


def test_decodes_faces() -> None:
    assert [decode_orientation(bytes([value])) for value in range(1, 9)] == list(range(1, 9))


def test_normalises_documented_neutral_values() -> None:
    assert decode_orientation(b"\x00") == 0
    assert decode_orientation(b"\x0a") == 0


def test_rejects_unknown_payloads_without_guessing() -> None:
    for payload in (b"", b"\x09", b"\xff", b"\x01\x02"):
        try:
            decode_orientation(payload)
        except ProtocolError:
            pass
        else:
            raise AssertionError(f"Expected ProtocolError for {payload.hex()}")
