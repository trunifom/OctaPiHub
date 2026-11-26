"""Secure local Hue CLIP v2 client and action plugin."""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import ipaddress
import json
import math
import re
import ssl
import threading
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)

from octapihub.core.errors import (
    HueBridgeError,
    HueCertificateError,
    HueLinkButtonError,
    HueRateLimitError,
    ValidationError,
)

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_STREAM_LINE = 262_144
_OPEN = object()
_END = object()
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
    tls_server_name: str | None = None
    stream_timeout_seconds: float = 90.0  # the bridge sends a keep-alive far more often

    def __post_init__(self) -> None:
        _validate_host(self.host)
        if self.tls_server_name is not None:
            _validate_host(self.tls_server_name)
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
    group_id: str = ""  # the room or zone the scene belongs to


class _NamedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to one address but verify the certificate for another name.

    Hue bridges commonly present a certificate for their bridge ID, not their IP address.
    Verification stays fully enabled; only the expected name is chosen explicitly.
    """

    def __init__(
        self,
        *args: Any,
        server_name: str,
        tls_context: ssl.SSLContext,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, context=tls_context, **kwargs)
        self._server_name = server_name
        self._tls_context = tls_context

    def connect(self) -> None:
        http.client.HTTPConnection.connect(self)
        self.sock = self._tls_context.wrap_socket(self.sock, server_hostname=self._server_name)


class _NamedHTTPSHandler(HTTPSHandler):
    def __init__(self, context: ssl.SSLContext, server_name: str) -> None:
        super().__init__(context=context)
        self._named_context = context
        self._server_name = server_name

    def https_open(self, req: Request) -> http.client.HTTPResponse:
        def connection(host: str, **kwargs: Any) -> http.client.HTTPSConnection:
            kwargs.pop("context", None)
            return _NamedHTTPSConnection(
                host,
                server_name=self._server_name,
                tls_context=self._named_context,
                **kwargs,
            )

        return self.do_open(connection, req)


def _build_opener(
    ca_file: Path | None, server_name: str | None
) -> tuple[ssl.SSLContext, OpenerDirector]:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_file is not None:
        try:
            context.load_verify_locations(cafile=str(ca_file))
        except (OSError, ssl.SSLError) as exc:
            raise HueCertificateError(
                "Could not load the configured Hue CA certificate",
                details={"reason": type(exc).__name__},
            ) from exc
    https_handler: HTTPSHandler = (
        _NamedHTTPSHandler(context, server_name) if server_name else HTTPSHandler(context=context)
    )
    opener = build_opener(ProxyHandler({}), https_handler, _NoRedirectHandler())
    return context, opener


def _open_json(opener: OpenerDirector, request: Request, timeout_seconds: float) -> Any:
    """Perform one request and translate every transport failure to a Hue error."""
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
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
            "Die Hue Bridge hat den Zugriff verweigert. Bitte Kopplung und Application Key prüfen."
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
    except (OSError, http.client.HTTPException) as exc:
        raise HueBridgeError(
            "Could not connect to the Hue Bridge",
            details={"reason": type(exc).__name__},
        ) from exc
    if len(raw_response) > _MAX_RESPONSE_BYTES:
        raise HueBridgeError("Hue Bridge response exceeded the size limit")
    try:
        return json.loads(raw_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HueBridgeError("Hue Bridge returned invalid JSON") from exc


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


from octapihub.application.hue_lights import HueGroup  # noqa: E402


class HueBridgeClient:
    """Async CLIP v2 client with CA and hostname verification always enabled."""

    def __init__(self, settings: HueBridgeSettings) -> None:
        self._settings = settings
        self._ssl_context, self._opener = _build_opener(settings.ca_file, settings.tls_server_name)
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
            group = item.get("group")
            group_id = group.get("rid") if isinstance(group, dict) else None
            scenes.append(HueScene(scene_id, name, group_id if isinstance(group_id, str) else ""))
        return tuple(scenes)

    async def list_groups(self) -> tuple[HueGroup, ...]:
        """Rooms, zones and the whole home with the grouped-light resource that controls them."""
        found: list[HueGroup] = []
        for kind, path in (
            ("home", "/clip/v2/resource/bridge_home"),
            ("room", "/clip/v2/resource/room"),
            ("zone", "/clip/v2/resource/zone"),
        ):
            response = await self._request("GET", path)
            data = response.get("data")
            if not isinstance(data, list):
                raise HueBridgeError("Hue group response did not contain a data array")
            for item in data:
                group = _parse_group(kind, item)
                if group is not None:
                    found.append(group)
        return tuple(found)

    async def set_group(self, group_id: str, body: dict[str, object]) -> None:
        """Send a command to a grouped light (on/off, brightness, colour)."""
        if not isinstance(group_id, str) or not group_id.strip():
            raise ValidationError("Hue group ID must not be empty")
        await self._request(
            "PUT", f"/clip/v2/resource/grouped_light/{quote(group_id, safe='')}", body
        )

    async def group_is_on(self, group_id: str) -> bool | None:
        """Whether any light of the group is on (``None`` if the bridge does not say)."""
        response = await self._request(
            "GET", f"/clip/v2/resource/grouped_light/{quote(group_id, safe='')}"
        )
        data = response.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            return None
        state = data[0].get("on")
        value = state.get("on") if isinstance(state, dict) else None
        return value if isinstance(value, bool) else None

    async def light_events(self) -> AsyncIterator[tuple[str, bool] | None]:
        """Live on/off changes of grouped lights from the bridge's event stream.

        Yields ``None`` once when the stream is open (state before that is unknown) and then
        ``(grouped_light_id, is_on)`` for every change. Ends with ``HueBridgeError`` when the
        connection fails or goes silent; the caller reconnects.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue()
        stop = threading.Event()
        opened: list[http.client.HTTPResponse] = []
        host = _url_host(self._settings.host)
        request = Request(
            f"https://{host}:{self._settings.port}/eventstream/clip/v2",
            headers={
                "Accept": "text/event-stream",
                "hue-application-key": self._settings.application_key,
            },
            method="GET",
        )

        def put(item: object) -> None:
            with contextlib.suppress(RuntimeError):  # the loop is already closed
                loop.call_soon_threadsafe(queue.put_nowait, item)

        def read() -> None:
            try:
                with self._opener.open(
                    request, timeout=self._settings.stream_timeout_seconds
                ) as response:
                    opened.append(response)
                    put(_OPEN)
                    data: list[str] = []
                    size = 0
                    while not stop.is_set():
                        raw = response.readline(_MAX_STREAM_LINE + 1)
                        if not raw:
                            break  # the bridge closed the stream
                        if len(raw) > _MAX_STREAM_LINE:
                            raise HueBridgeError("Hue event stream line exceeded the size limit")
                        line = raw.decode("utf-8", "replace").rstrip(chr(13) + chr(10))
                        if line:
                            if line.startswith("data:"):
                                size += len(line)
                                if size > _MAX_RESPONSE_BYTES:
                                    raise HueBridgeError("Hue event exceeded the size limit")
                                data.append(line[5:].lstrip())
                            continue
                        if data:
                            for change in _parse_light_events(chr(10).join(data)):
                                put(change)
                        data, size = [], 0
                put(_END)
            except HueBridgeError as exc:
                put(exc)
            except HTTPError as exc:
                status = exc.code
                exc.close()
                put(HueBridgeError("Hue event stream was refused", details={"status": status}))
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                put(
                    HueBridgeError(
                        "Hue event stream failed", details={"reason": type(exc).__name__}
                    )
                )

        thread = threading.Thread(target=read, name="hue-events", daemon=True)
        thread.start()
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    raise HueBridgeError("Hue event stream was closed by the bridge")
                if isinstance(item, Exception):
                    raise item
                yield None if item is _OPEN else cast(tuple[str, bool], item)
        finally:
            stop.set()
            for response in opened:
                with contextlib.suppress(Exception):
                    response.close()

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
        payload = _open_json(self._opener, request, self._settings.timeout_seconds)
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


def _parse_light_events(text: str) -> list[tuple[str, bool]]:
    """On/off changes of grouped lights in one event-stream message (anything else is ignored)."""
    try:
        events = json.loads(text)
    except json.JSONDecodeError:
        return []
    changes: list[tuple[str, bool]] = []
    for event in events if isinstance(events, list) else []:
        data = event.get("data") if isinstance(event, dict) else None
        for resource in data if isinstance(data, list) else []:
            if not isinstance(resource, dict) or resource.get("type") != "grouped_light":
                continue
            state = resource.get("on")
            value = state.get("on") if isinstance(state, dict) else None
            ident = resource.get("id")
            if isinstance(ident, str) and ident and isinstance(value, bool):
                changes.append((ident, value))
    return changes


def _parse_group(kind: str, item: object) -> HueGroup | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    services = item.get("services")
    light = next(
        (
            s["rid"]
            for s in (services if isinstance(services, list) else [])
            if isinstance(s, dict)
            and s.get("rtype") == "grouped_light"
            and isinstance(s.get("rid"), str)
        ),
        None,
    )
    if light is None:
        return None
    metadata = item.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    if kind == "home":
        name = "Alle Lampen"
    if not isinstance(name, str) or not name:
        return None
    return HueGroup(light, name, kind, item["id"])


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


_PAIRING_DEVICE_TYPE = "octapihub#hub"
_LINK_BUTTON_NOT_PRESSED = 101


async def pair_bridge(
    host: str,
    *,
    port: int = 443,
    ca_file: Path | None = None,
    tls_server_name: str | None = None,
    timeout_seconds: float = 10.0,
) -> str:
    """Create an application key; the bridge's link button must have been pressed.

    Raises:
        HueLinkButtonError: The button has not been pressed (the caller may retry).
        HueCertificateError: The bridge certificate could not be verified.
        HueBridgeError: Any other connection or protocol failure.
    """
    _validate_host(host)
    if tls_server_name is not None:
        _validate_host(tls_server_name)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError("Hue port must be an integer from 1 to 65535")
    _context, opener = _build_opener(ca_file, tls_server_name)
    body = json.dumps({"devicetype": _PAIRING_DEVICE_TYPE, "generate_clientkey": False})
    request = Request(
        f"https://{_url_host(host)}:{port}/api",
        data=body.encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    payload = await asyncio.to_thread(_open_json, opener, request, timeout_seconds)
    return _extract_application_key(payload)


def _extract_application_key(payload: object) -> str:
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise HueBridgeError("Hue Bridge pairing response has an unexpected shape")
    first = cast(dict[str, object], payload[0])
    error = first.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if error_type == _LINK_BUTTON_NOT_PRESSED:
            raise HueLinkButtonError("Hue Bridge link button was not pressed")
        raise HueBridgeError(
            "Hue Bridge refused the pairing request",
            details={"error_type": error_type if isinstance(error_type, int) else None},
        )
    success = first.get("success")
    key = success.get("username") if isinstance(success, dict) else None
    if (
        not isinstance(key, str)
        or not key
        or len(key) > 512
        or any(ord(character) < 33 or ord(character) > 126 for character in key)
    ):
        raise HueBridgeError("Hue Bridge pairing response did not contain a usable key")
    return key
