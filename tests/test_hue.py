from __future__ import annotations

import asyncio
import json
import ssl
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

from octapihub.core.errors import (
    HueBridgeError,
    HueCertificateError,
    HueRateLimitError,
    ValidationError,
)
from octapihub.infrastructure.hue import (
    HueActionPlugin,
    HueBridgeClient,
    HueBridgeSettings,
    HueScene,
)

_FIXTURES = Path(__file__).parent / "fixtures"
_CA_FILE = _FIXTURES / "hue-test-ca.pem"
_SERVER_KEY_FILE = _FIXTURES / "hue-test-server.key"


@dataclass(frozen=True, slots=True)
class _RecordedRequest:
    method: str
    path: str
    application_key: str | None
    body: bytes
    received_at: float


@dataclass(slots=True)
class _ServerState:
    status: int = 200
    response_body: bytes = field(
        default_factory=lambda: json.dumps(
            {
                "data": [
                    {
                        "id": "scene-one",
                        "metadata": {"name": "Evening"},
                    }
                ],
                "errors": [],
            }
        ).encode()
    )
    headers: dict[str, str] = field(default_factory=dict)
    requests: list[_RecordedRequest] = field(default_factory=list)


class _TestHueServer(ThreadingHTTPServer):
    def __init__(self, state: _ServerState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), _TestHueRequestHandler)


class _TestHueRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._respond()

    def do_PUT(self) -> None:
        self._respond()

    def _respond(self) -> None:
        server = cast(_TestHueServer, self.server)
        body_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(body_length)
        server.state.requests.append(
            _RecordedRequest(
                self.command,
                self.path,
                self.headers.get("hue-application-key"),
                body,
                time.monotonic(),
            )
        )
        self.send_response(server.state.status)
        for name, value in server.state.headers.items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(server.state.response_body)))
        self.end_headers()
        self.wfile.write(server.state.response_body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def local_hue_server() -> Iterator[tuple[int, _ServerState]]:
    state = _ServerState()
    server = _TestHueServer(state)
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.load_cert_chain(_CA_FILE, _SERVER_KEY_FILE)
    server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        daemon=True,
    )
    thread.start()
    try:
        yield server.server_port, state
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _client(
    port: int,
    *,
    host: str = "localhost",
    ca_file: Path | None = _CA_FILE,
    request_interval: float = 0.05,
) -> HueBridgeClient:
    return HueBridgeClient(
        HueBridgeSettings(
            host=host,
            application_key="test-only-application-key",
            ca_file=ca_file,
            port=port,
            minimum_request_interval_seconds=request_interval,
        )
    )


def test_hue_client_lists_scenes_and_plugin_recalls_scene_over_verified_tls(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, state = local_hue_server
    client = _client(port)
    plugin = HueActionPlugin(client)

    async def exercise() -> tuple[HueScene, ...]:
        scenes = await client.list_scenes()
        await plugin.execute("scene.activate", {"scene_id": "scene/one"})
        return scenes

    scenes = asyncio.run(exercise())

    assert scenes == (HueScene("scene-one", "Evening"),)
    assert [request.method for request in state.requests] == ["GET", "PUT"]
    assert state.requests[0].path == "/clip/v2/resource/scene"
    assert state.requests[1].path == "/clip/v2/resource/scene/scene%2Fone"
    assert all(request.application_key == "test-only-application-key" for request in state.requests)
    assert json.loads(state.requests[1].body) == {"recall": {"action": "active"}}
    assert state.requests[1].received_at - state.requests[0].received_at >= 0.04


def test_hue_client_rejects_untrusted_bridge_certificate(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, _ = local_hue_server

    with pytest.raises(HueCertificateError) as error:
        asyncio.run(_client(port, ca_file=None).list_scenes())

    assert error.value.error_code == "CH-HUE-003"


def test_hue_client_checks_certificate_hostname(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, _ = local_hue_server

    with pytest.raises(HueCertificateError) as error:
        asyncio.run(_client(port, host="127.0.0.1").list_scenes())

    assert error.value.error_code == "CH-HUE-003"


def test_hue_client_translates_rate_limit_and_retry_after_without_leaking_key(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, state = local_hue_server
    state.status = 429
    state.headers["Retry-After"] = "3"

    with pytest.raises(HueRateLimitError) as error:
        asyncio.run(_client(port).list_scenes())

    assert error.value.error_code == "CH-HUE-002"
    assert error.value.retry_after_seconds == 3.0
    assert error.value.details["status"] == 429
    assert "test-only-application-key" not in str(error.value)


def test_hue_client_accepts_http_date_retry_after(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, state = local_hue_server
    state.status = 429
    state.headers["Retry-After"] = format_datetime(
        datetime.now(UTC) + timedelta(seconds=5),
        usegmt=True,
    )

    with pytest.raises(HueRateLimitError) as error:
        asyncio.run(_client(port).list_scenes())

    assert error.value.retry_after_seconds is not None
    assert 0 <= error.value.retry_after_seconds <= 5


def test_hue_client_does_not_follow_redirects(
    local_hue_server: tuple[int, _ServerState],
) -> None:
    port, state = local_hue_server
    state.status = 302
    state.headers["Location"] = "https://example.invalid/collect"

    with pytest.raises(HueBridgeError) as error:
        asyncio.run(_client(port).list_scenes())

    assert error.value.details["status"] == 302
    assert len(state.requests) == 1


def test_hue_settings_reject_invalid_hosts_and_header_values() -> None:
    with pytest.raises(ValidationError, match="hostname"):
        HueBridgeSettings("https://localhost", "test-key")
    with pytest.raises(ValidationError, match="printable ASCII"):
        HueBridgeSettings("localhost", "key\r\ninjected: value")
