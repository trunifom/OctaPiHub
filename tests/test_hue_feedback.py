"""Live feedback from the Hue event stream (fakes and a local TLS server, no real bridge)."""

from __future__ import annotations

import asyncio
import json
import ssl
import threading
from collections.abc import AsyncIterator, Iterator
from datetime import UTC
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

import pytest

from octapihub.application.hue_lights import HueGroup
from octapihub.application.hue_service import HueService
from octapihub.core.config import HueConfig
from octapihub.core.errors import HueBridgeError
from octapihub.infrastructure.hue import HueBridgeClient, HueBridgeSettings, _parse_light_events
from octapihub.web.presenter import build_hue_view

_FIXTURES = Path(__file__).parent / "fixtures"
_CA_FILE = _FIXTURES / "hue-test-ca.pem"
_KEY_FILE = _FIXTURES / "hue-test-server.key"


def event(*resources: dict[str, object]) -> str:
    return json.dumps([{"type": "update", "data": list(resources)}])


def light(ident: str, on: bool) -> dict[str, object]:
    return {"id": ident, "type": "grouped_light", "on": {"on": on}}


# ---------- parsing ----------


def test_only_grouped_light_changes_are_taken_from_a_message() -> None:
    text = event(
        light("g1", True),
        {"id": "x", "type": "light", "on": {"on": False}},
        {"id": "g2", "type": "grouped_light", "dimming": {"brightness": 30}},
        light("g3", False),
        {"type": "grouped_light", "on": {"on": True}},
        {"id": "g4", "type": "grouped_light", "on": {"on": "yes"}},
    )
    assert _parse_light_events(text) == [("g1", True), ("g3", False)]


@pytest.mark.parametrize("text", ["", "nonsense", "{}", "[1, 2]", '[{"data": 5}]', "[null]"])
def test_garbage_messages_are_ignored(text: str) -> None:
    assert _parse_light_events(text) == []


# ---------- the client against a local TLS server ----------


class _Stream:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.path = ""
        self.key: str | None = None
        self.accept: str | None = None
        self.status = 200


class _Server(ThreadingHTTPServer):
    def __init__(self, stream: _Stream) -> None:
        self.stream = stream
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        stream = cast(_Server, self.server).stream
        stream.path = self.path
        stream.key = self.headers.get("hue-application-key")
        stream.accept = self.headers.get("Accept")
        self.send_response(stream.status)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in stream.chunks:
            self.wfile.write(chunk)
            self.wfile.flush()

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def stream_server() -> Iterator[tuple[int, _Stream]]:
    stream = _Stream()
    server = _Server(stream)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(_CA_FILE, _KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield server.server_port, stream
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def client(port: int) -> HueBridgeClient:
    return HueBridgeClient(
        HueBridgeSettings(
            host="localhost",
            application_key="test-only-application-key",
            ca_file=_CA_FILE,
            port=port,
            stream_timeout_seconds=3,
        )
    )


def collect(c: HueBridgeClient) -> tuple[list[object], Exception | None]:
    async def run() -> tuple[list[object], Exception | None]:
        seen: list[object] = []
        try:
            async for item in c.light_events():
                seen.append(item)
        except HueBridgeError as exc:
            return seen, exc
        return seen, None

    return asyncio.run(run())


def test_the_client_reports_open_and_changes_then_the_end_of_the_stream(
    stream_server: tuple[int, _Stream],
) -> None:
    port, stream = stream_server
    first = event(light("g1", True)).encode()
    second = event(light("g1", False), light("g2", True)).encode()
    stream.chunks = [
        b": hi\n\n",
        b"id: 1:0\ndata: " + first + b"\n\n",
        b"id: 2:0\r\ndata: " + second + b"\r\n\r\n",
    ]
    seen, error = collect(client(port))
    assert seen == [None, ("g1", True), ("g1", False), ("g2", True)]
    assert error is not None and "closed" in str(error)
    assert stream.path == "/eventstream/clip/v2"
    assert stream.key == "test-only-application-key"
    assert stream.accept == "text/event-stream"


def test_a_refused_stream_is_a_bridge_error_without_the_key(
    stream_server: tuple[int, _Stream],
) -> None:
    port, stream = stream_server
    stream.status = 403
    seen, error = collect(client(port))
    assert seen == [] and isinstance(error, HueBridgeError)
    assert "test-only-application-key" not in str(error) + repr(getattr(error, "details", ""))


def test_a_bridge_that_is_not_there_is_a_bridge_error() -> None:
    seen, error = collect(client(1))
    assert seen == [] and isinstance(error, HueBridgeError)


# ---------- the service ----------

GROUPS = (HueGroup("g-wz", "Wohnzimmer", "room", "room-wz"),)


class Gateway:
    def __init__(self) -> None:
        self.on_state: bool | None = False
        self.asked = 0
        self.commands: list[dict[str, object]] = []
        self.events: asyncio.Queue[object] = asyncio.Queue()
        self.connections = 0

    async def list_scenes(self):  # type: ignore[no-untyped-def]
        return []

    async def recall_scene(self, scene_id: str) -> None: ...

    async def list_groups(self):  # type: ignore[no-untyped-def]
        return list(GROUPS)

    async def set_group(self, group_id: str, body: dict[str, object]) -> None:
        self.commands.append(body)

    async def group_is_on(self, group_id: str) -> bool | None:
        self.asked += 1
        return self.on_state

    async def light_events(self) -> AsyncIterator[tuple[str, bool] | None]:
        self.connections += 1
        yield None
        while True:
            item = await self.events.get()
            if isinstance(item, Exception):
                raise item
            yield cast(tuple[str, bool], item)


class Secrets:
    def get(self, key: str) -> str | None:
        return "key"

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> bool:
        return True


def make(gateway: Any) -> HueService:
    async def pair(_c: HueConfig) -> str:
        return "key"

    config = HueConfig(enabled=True, host="192.168.1.20")
    return HueService(config, Secrets(), lambda _c, _k: gateway, pair)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_toggle_uses_the_live_state_and_asks_only_once() -> None:
    async def scenario() -> None:
        gateway = Gateway()
        service = make(gateway)
        await service.start()
        await settle()
        assert service.status.live
        # unknown at first: asks the bridge once, then remembers it
        await service.execute("light.toggle", {"group": "g-wz"})
        assert gateway.asked == 1 and gateway.commands[-1] == {"on": {"on": True}}
        # our own command is taken over at once: the next toggle switches off without asking
        await service.execute("light.toggle", {"group": "g-wz"})
        assert gateway.asked == 1 and gateway.commands[-1] == {"on": {"on": False}}
        # somebody uses the Hue app: the event wins over our memory
        await gateway.events.put(("g-wz", False))
        await settle()
        await service.execute("light.toggle", {"group": "g-wz"})
        assert gateway.commands[-1] == {"on": {"on": True}} and gateway.asked == 1
        await service.stop()

    asyncio.run(scenario())


def test_events_update_the_status_and_the_view() -> None:
    async def scenario() -> None:
        gateway = Gateway()
        service = make(gateway)
        await service.start()
        await settle()
        await service.refresh_groups()
        await gateway.events.put(("g-wz", True))
        await settle()
        status = service.status
        assert status.lights == (("g-wz", True),)
        view = build_hue_view(status, UTC, {"g-wz": "Wohnzimmer"})
        assert view.lights == (("Wohnzimmer", True),) and "aktiv" in view.live_text
        await service.stop()
        assert not service.status.live and service.status.lights == ()

    asyncio.run(scenario())


def test_without_a_stream_every_toggle_asks_the_bridge() -> None:
    class Plain(Gateway):
        light_events = None  # type: ignore[assignment]

    async def scenario() -> None:
        gateway = Plain()
        service = make(gateway)
        await service.start()
        await service.execute("light.toggle", {"group": "g-wz"})
        await service.execute("light.toggle", {"group": "g-wz"})
        assert gateway.asked == 2 and not service.status.live
        view = build_hue_view(service.status, UTC)
        assert "Keine Live-Rückmeldung" in view.live_text
        await service.stop()

    asyncio.run(scenario())


def test_a_lost_stream_falls_back_to_asking_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octapihub.application.hue_service._FIRST_RETRY_SECONDS", 0.3)

    async def scenario() -> None:
        gateway = Gateway()
        service = make(gateway)
        await service.start()
        await settle()
        await gateway.events.put(("g-wz", True))
        await settle()
        await gateway.events.put(HueBridgeError("gone"))
        await settle()
        assert not service.status.live and service.status.lights == ()
        gateway.on_state = False
        await service.execute("light.toggle", {"group": "g-wz"})
        assert gateway.asked == 1  # no stale memory is trusted
        for _ in range(200):
            if service.status.live:
                break
            await asyncio.sleep(0.01)
        assert service.status.live and gateway.connections == 2
        await service.stop()

    asyncio.run(scenario())


def test_stopping_twice_and_without_start_is_harmless() -> None:
    async def scenario() -> None:
        service = make(Gateway())
        await service.stop()
        await service.start()
        await service.stop()
        await service.stop()

    asyncio.run(scenario())
