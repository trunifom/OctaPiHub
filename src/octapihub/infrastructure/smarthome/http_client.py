"""Send one web request: for webhooks and for the Home Assistant service interface.

Rules (the same ones OctaLedger's webhook adapter uses):

* only ``http`` and ``https``; no redirects are followed; environment proxies are ignored;
* only the status line is evaluated, the answer body is read at most up to a small limit;
* the request is repeated by the caller only when :class:`SmartHomeConnectionError` was raised,
  which means that the request certainly did not reach the target.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import ssl
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit

from octapihub.core.errors import (
    SmartHomeConnectionError,
    SmartHomeNotConfiguredError,
    SmartHomeRejectedError,
)

TIMEOUT_SECONDS: Final = 8.0
MAX_ANSWER_BYTES: Final = 4096
METHODS: Final = ("GET", "POST", "PUT", "PATCH", "DELETE")
_BLOCKED_NETWORKS: Final = (
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, includes cloud metadata services
    ipaddress.ip_network("fe80::/10"),
)


def check_url(url: str) -> str:
    """Return ``url`` if it is an acceptable web address.

    Raises:
        SmartHomeNotConfiguredError: Not http(s), has credentials, or points to a link-local
            or metadata address.
    """
    bad = SmartHomeNotConfiguredError(
        "Unacceptable address",
        user_message="Die Adresse muss mit http:// oder https:// beginnen und ein Gerät nennen.",
    )
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError as exc:
        raise bad from exc
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise bad
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise bad
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if any(address in network for network in _BLOCKED_NETWORKS):
        raise SmartHomeNotConfiguredError(
            "Link-local address",
            user_message="Adressen wie 169.254.x.x sind aus Sicherheitsgründen nicht erlaubt.",
        )
    return url


def _perform(
    method: str, url: str, body: bytes | None, headers: Mapping[str, str], timeout: float
) -> int:
    """Connect first (a failure there sent nothing), then send and read the status line."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    secure = parts.scheme == "https"
    port = parts.port or (443 if secure else 80)
    connection: http.client.HTTPConnection
    if secure:
        connection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        try:
            connection.connect()
        except (OSError, ssl.SSLError) as exc:
            raise SmartHomeConnectionError("Could not connect") from exc
        try:
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            response.read(MAX_ANSWER_BYTES)
            return int(response.status)  # redirects (3xx) are reported, never followed
        except (OSError, http.client.HTTPException) as exc:
            raise SmartHomeRejectedError(
                "No usable answer after sending",
                user_message=(
                    "Das Gerät hat nicht (richtig) geantwortet. Die Aktion könnte trotzdem "
                    "ausgeführt worden sein."
                ),
            ) from exc
    finally:
        connection.close()


async def send_request(
    method: str,
    url: str,
    body: str = "",
    content_type: str = "application/json",
    headers: Mapping[str, str] | None = None,
    *,
    timeout: float = TIMEOUT_SECONDS,
) -> int:
    """Send the request and return the HTTP status.

    Raises:
        SmartHomeNotConfiguredError: The address or method is not acceptable.
        SmartHomeConnectionError: Nothing was sent (safe to retry).
        SmartHomeRejectedError: The target answered with an error status (4xx/5xx) or the
            outcome after sending is unknown.
    """
    if method not in METHODS:
        raise SmartHomeNotConfiguredError(
            "Unsupported method", user_message="Diese Anfrage-Art wird nicht unterstützt."
        )
    check_url(url)
    all_headers = {"User-Agent": "OctaPiHub", **(headers or {})}
    payload = body.encode("utf-8") if body else None
    if payload is not None:
        all_headers.setdefault("Content-Type", content_type)
    status = await asyncio.to_thread(_perform, method, url, payload, all_headers, timeout)
    if status in (401, 403):
        raise SmartHomeRejectedError(
            f"HTTP {status}",
            user_message="Das Gerät hat den Zugang abgelehnt (Token oder Passwort prüfen).",
            details={"status": status},
        )
    if status >= 300:
        raise SmartHomeRejectedError(
            f"HTTP {status}",
            user_message=f"Das Gerät antwortete mit Fehler {status}.",
            details={"status": status},
        )
    return status
