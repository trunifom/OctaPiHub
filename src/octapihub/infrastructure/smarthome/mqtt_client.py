"""Publish one MQTT 3.1.1 message (publish only; the protocol is simple enough to speak directly).

Why not a client library: a cube action only needs "connect, send one PUBLISH, disconnect". Doing
it here keeps the project free of another dependency and gives exactly what the action queue needs:
a hard time limit on every step and an exact answer to "did the message certainly *not* go out?".

Retry rule: failing to connect (refused, unreachable, TLS handshake) sent nothing and raises
:class:`SmartHomeConnectionError` (retried). A broker that refuses the credentials is a permanent
:class:`SmartHomeRejectedError`. If anything goes wrong *after* the PUBLISH was written, the outcome
is unknown and the error is permanent (repeating "TOGGLE" could switch twice).

Limits: MQTT 3.1.1, no subscribing, QoS 0 and 1, no websockets, no client certificates.
Verified against a protocol-level fake broker, not against Mosquitto.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import ssl
import struct
from typing import Final

from octapihub.core.errors import SmartHomeConnectionError, SmartHomeRejectedError

TIMEOUT_SECONDS: Final = 8.0
KEEP_ALIVE: Final = 15
MAX_REPLY_BYTES: Final = 1024
_CONNACK_PROBLEMS: Final = {
    1: "Der Broker unterstützt MQTT 3.1.1 nicht.",
    2: "Der Broker hat die Client-Kennung abgelehnt.",
    3: "Der Broker ist gerade nicht verfügbar.",
    4: "Benutzername oder Passwort sind falsch.",
    5: "Der Broker erlaubt diesem Benutzer den Zugriff nicht.",
}


def _utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 0xFFFF:
        raise SmartHomeRejectedError("MQTT string too long")
    return struct.pack("!H", len(raw)) + raw


def _remaining_length(length: int) -> bytes:
    encoded = bytearray()
    while True:
        byte, length = length % 128, length // 128
        encoded.append(byte | (0x80 if length else 0))
        if not length:
            return bytes(encoded)


def build_connect(client_id: str, username: str, password: str) -> bytes:
    """CONNECT packet: clean session, keep-alive 15 s, optional user name and password."""
    flags = 0x02
    payload = _utf8(client_id)
    if username:
        flags |= 0x80
        payload += _utf8(username)
        if password:
            flags |= 0x40
            payload += _utf8(password)
    body = _utf8("MQTT") + bytes([4, flags]) + struct.pack("!H", KEEP_ALIVE) + payload
    return b"\x10" + _remaining_length(len(body)) + body


def build_publish(topic: str, payload: bytes, *, qos: int, retain: bool) -> bytes:
    """PUBLISH packet (a packet identifier only for QoS 1)."""
    header = 0x30 | (qos << 1) | int(retain)
    body = _utf8(topic)
    if qos:
        body += struct.pack("!H", 1)
    body += payload
    return bytes([header]) + _remaining_length(len(body)) + body


DISCONNECT: Final = b"\xe0\x00"


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    first = (await reader.readexactly(1))[0]
    length, shift = 0, 0
    for _ in range(4):
        byte = (await reader.readexactly(1))[0]
        length |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    else:
        raise ValueError("malformed MQTT length")
    if length > MAX_REPLY_BYTES:
        raise ValueError("MQTT reply too large")
    return first, (await reader.readexactly(length) if length else b"")


async def publish(
    host: str,
    port: int,
    topic: str,
    payload: str,
    *,
    tls: bool = False,
    username: str = "",
    password: str = "",
    qos: int = 0,
    retain: bool = False,
    timeout: float = TIMEOUT_SECONDS,
) -> None:
    """Connect, publish one message, disconnect.

    Raises:
        SmartHomeConnectionError: Nothing was sent (safe to retry).
        SmartHomeRejectedError: The broker refused, or the outcome after sending is unknown.
    """
    context = ssl.create_default_context() if tls else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host if tls else None),
            timeout,
        )
    except (OSError, ssl.SSLError, TimeoutError) as exc:
        raise SmartHomeConnectionError("Could not connect to the broker") from exc
    sent = False
    try:
        try:
            client_id = "octapihub-" + secrets.token_hex(4)
            writer.write(build_connect(client_id, username, password))
            await asyncio.wait_for(writer.drain(), timeout)
            first, body = await asyncio.wait_for(_read_packet(reader), timeout)
            if first != 0x20 or len(body) != 2:
                raise SmartHomeRejectedError(
                    "Unexpected broker reply",
                    user_message="Der Broker hat unerwartet geantwortet. Ist das ein MQTT-Broker?",
                )
            if body[1] != 0:
                raise SmartHomeRejectedError(
                    f"Broker refused: {body[1]}",
                    user_message=_CONNACK_PROBLEMS.get(
                        body[1], "Der Broker hat die Verbindung abgelehnt."
                    ),
                )
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
            raise SmartHomeConnectionError("Broker did not complete the handshake") from exc
        try:
            writer.write(build_publish(topic, payload.encode("utf-8"), qos=qos, retain=retain))
            sent = True
            await asyncio.wait_for(writer.drain(), timeout)
            if qos:
                first, body = await asyncio.wait_for(_read_packet(reader), timeout)
                if first != 0x40 or len(body) != 2:
                    raise SmartHomeRejectedError(
                        "No acknowledgement",
                        user_message="Der Broker hat die Nachricht nicht bestätigt.",
                    )
            writer.write(DISCONNECT)
        except SmartHomeRejectedError:
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
            raise SmartHomeRejectedError(
                "Outcome unknown after sending" if sent else "Send failed",
                user_message=(
                    "Der Broker hat die Nachricht nicht bestätigt. Sie könnte trotzdem "
                    "angekommen sein."
                ),
            ) from exc
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError, ssl.SSLError):
            await asyncio.wait_for(writer.wait_closed(), 1.0)
