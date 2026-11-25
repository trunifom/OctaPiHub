"""Secure local Hue CLIP v2 client and action plugin."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from octapihub.core.errors import (
    HueBridgeError,
    HueCertificateError,
    HueRateLimitError,
    ValidationError,
)

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_MAX_RESPONSE_BYTES = 1_048_576
_DEFAULT_REQUEST_INTERVAL_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class HueBridgeSettings:
    """Connection settings injected by the future composition root.

    The application key is deliberately excluded from representations and must be
    supplied by a secrets provider rather than the checked-in TOML configuration.
    """

    host: str
    application_key: str = field(repr=False)
    ca_file: Path | None = None
    port: int = 443
    timeout_seconds: float = 5.0
    minimum_request_interval_seconds: float = _DEFAULT_REQUEST_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        _validate_host(self.host)
        if (
            not isinstance(self.application_key, str)
            or not self.application_key
            or any(
                ord(character) < 33 or ord(character) > 126 for character in self.application_key
            )
        ):
            raise ValidationError("Hue application key must be a non-empty printable ASCII value")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValidationError("Hue port must be an integer from 1 to 65535")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValidationError("Hue timeout must be positive and finite")
        if (
            isinstance(self.minimum_request_interval_seconds, bool)
            or not isinstance(self.minimum_request_interval_seconds, (int, float))
            or not math.isfinite(self.minimum_request_interval_seconds)
            or self.minimum_request_interval_seconds < 0
        ):
            raise ValidationError("Hue request interval must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class HueScene:
    """Minimal Hue scene resource returned by CLIP v2."""

    id: str
    name: str


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward the application-key header to a redirect target."""

    def redirect_request(
        self,
        request: Request,
        file_pointer: object,
        code: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


class HueBridgeClient:
    """Async CLIP v2 client with CA and hostname verification always enabled."""

    def __init__(self, settings: HueBridgeSettings) -> None:
        self._settings = settings
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        if settings.ca_file is not None:
            try:
                self._ssl_context.load_verify_locations(cafile=str(settings.ca_file))
            except (OSError, ssl.SSLError) as exc:
                raise HueCertificateError(
                    "Could not load the configured Hue CA certificate",
                    details={"reason": type(exc).__name__},
                ) from exc
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=self._ssl_context),
            _NoRedirectHandler(),
        )
        self._request_lock = asyncio.Lock()
        self._next_request_at = 0.0

    async def list_scenes(self) -> tuple[HueScene, ...]:
        """Read scene IDs and names from the local CLIP v2 API."""
        response = await self._request("GET", "/clip/v2/resource/scene")
        data = response.get("data")
        if not isinstance(data, list):
            raise HueBridgeError("Hue scene response did not contain a data array")
        scenes: list[HueScene] = []
        for item in data:
            if not isinstance(item, dict):
                raise HueBridgeError("Hue scene response contained an invalid resource")
            scene_id = item.get("id")
            metadata = item.get("metadata")
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if not isinstance(scene_id, str) or not scene_id:
                raise HueBridgeError("Hue scene response contained an invalid scene ID")
            if not isinstance(name, str) or not name:
                raise HueBridgeError("Hue scene response contained an invalid scene name")
            scenes.append(HueScene(scene_id, name))
        return tuple(scenes)

    async def recall_scene(self, scene_id: str) -> None:
        """Activate a scene by resource ID."""
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValidationError("Hue scene ID must not be empty")
        path = f"/clip/v2/resource/scene/{quote(scene_id, safe='')}"
        await self._request("PUT", path, {"recall": {"action": "active"}})

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        host = _url_host(self._settings.host)
        url = f"https://{host}:{self._settings.port}{path}"
        headers = {
            "Accept": "application/json",
            "hue-application-key": self._settings.application_key,
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)

        async with self._request_lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_request_at = time.monotonic() + float(
                self._settings.minimum_request_interval_seconds
            )
            return await asyncio.to_thread(self._perform_request, request)

    def _perform_request(self, request: Request) -> dict[str, object]:
        try:
            with self._opener.open(request, timeout=self._settings.timeout_seconds) as response:
                raw_response = response.read(_MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
            exc.close()
            if status == 429:
                details: dict[str, object] = {"status": status}
                if retry_after is not None:
                    details["retry_after_seconds"] = retry_after
                raise HueRateLimitError(
                    "Hue Bridge returned HTTP 429",
                    retry_after_seconds=retry_after,
                    details=details,
                ) from exc
            user_message = (
                "Die Hue Bridge hat den Zugriff verweigert. Bitte Kopplung und "
                "Application Key prüfen."
                if status in {401, 403}
                else None
            )
            raise HueBridgeError(
                "Hue Bridge returned an unsuccessful HTTP status",
                user_message=user_message,
                details={"status": status},
            ) from exc
        except (ssl.SSLError, URLError) as exc:
            reason = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(reason, ssl.SSLError):
                raise HueCertificateError(
                    "Hue Bridge TLS certificate verification failed",
                    details={"reason": type(reason).__name__},
                ) from exc
            raise HueBridgeError(
                "Could not connect to the Hue Bridge",
                details={"reason": type(reason).__name__},
            ) from exc
        except TimeoutError as exc:
            raise HueBridgeError("Hue Bridge request timed out") from exc
        except OSError as exc:
            raise HueBridgeError(
                "Could not connect to the Hue Bridge",
                details={"reason": type(exc).__name__},
            ) from exc

        if len(raw_response) > _MAX_RESPONSE_BYTES:
            raise HueBridgeError("Hue Bridge response exceeded the size limit")
        try:
            payload = json.loads(raw_response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HueBridgeError("Hue Bridge returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HueBridgeError("Hue Bridge response must be a JSON object")
        errors = payload.get("errors", [])
        if not isinstance(errors, list):
            raise HueBridgeError("Hue Bridge response contained an invalid errors field")
        if errors:
            raise HueBridgeError(
                "Hue Bridge reported an API error",
                details={"error_count": len(errors)},
            )
        return cast(dict[str, object], payload)


class HueActionPlugin:
    """Expose the supported Hue scene operation through the action-plugin contract."""

    kind = "hue"

    def __init__(self, client: HueBridgeClient) -> None:
        self._client = client

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        if action != "scene.activate":
            raise ValidationError("Unsupported Hue action")
        scene_id = parameters.get("scene_id")
        if not isinstance(scene_id, str):
            raise ValidationError("Hue scene action requires a string scene_id")
        await self._client.recall_scene(scene_id)


def _validate_host(host: str) -> None:
    if not isinstance(host, str) or not host or host != host.strip():
        raise ValidationError("Hue host must be a hostname or IP address")
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    if len(host) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in labels):
        raise ValidationError("Hue host must be a hostname or IP address")


def _url_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{host}]" if address.version == 6 else host


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            return None
        delay = (retry_at - datetime.now(UTC)).total_seconds()
    if not math.isfinite(delay) or delay < 0:
        return None
    return delay
