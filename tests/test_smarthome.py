from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from octapihub.application.smarthome import SmartHomeService, secret_key
from octapihub.core.config import (
    AppConfig,
    SmartHomeConfig,
    load_config,
    parse_config,
    save_config,
    smarthome_table,
)
from octapihub.core.errors import (
    SmartHomeConnectionError,
    SmartHomeError,
    SmartHomeNotConfiguredError,
    SmartHomeRejectedError,
    ValidationError,
)
from octapihub.infrastructure.smarthome import http_client, mqtt_client
from octapihub.web import forms
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend
from octapihub.web.face_detail import describe_face


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ---------- web request client ----------


@dataclass
class Seen:
    requests: list[tuple[str, str, bytes, dict[str, str]]] = field(default_factory=list)
    status: int = 200
    delay: float = 0.0
    headers: dict[str, str] = field(default_factory=dict)


@pytest.fixture
def http_server() -> Iterator[tuple[int, Seen]]:
    seen = Seen()

    class Handler(BaseHTTPRequestHandler):
        def _go(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            seen.requests.append((self.command, self.path, body, dict(self.headers)))
            if seen.delay:
                threading.Event().wait(seen.delay)
            self.send_response(seen.status)
            for key, value in seen.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _go

        def log_message(self, *_a: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()


def test_a_request_is_sent_with_method_body_and_headers(http_server: tuple[int, Seen]) -> None:
    port, seen = http_server
    status = run(
        http_client.send_request(
            "POST",
            f"http://127.0.0.1:{port}/hook?x=1",
            '{"a":1}',
            "application/json",
            {"X-Test": "1"},
        )
    )
    assert status == 200
    method, path, body, headers = seen.requests[0]
    assert (method, path, body) == ("POST", "/hook?x=1", b'{"a":1}')
    assert headers["Content-Type"] == "application/json" and headers["X-Test"] == "1"
    assert headers["User-Agent"] == "OctaPiHub"
    run(http_client.send_request("GET", f"http://127.0.0.1:{port}/"))
    assert seen.requests[1][0] == "GET" and seen.requests[1][2] == b""


@pytest.mark.parametrize(
    ("status", "exception", "text"),
    [
        (401, SmartHomeRejectedError, "Zugang abgelehnt"),
        (403, SmartHomeRejectedError, "Zugang abgelehnt"),
        (404, SmartHomeRejectedError, "Fehler 404"),
        (500, SmartHomeRejectedError, "Fehler 500"),
        (302, SmartHomeRejectedError, "Fehler 302"),
    ],
)
def test_error_statuses_and_redirects_are_rejected(
    http_server: tuple[int, Seen], status: int, exception: type[Exception], text: str
) -> None:
    port, seen = http_server
    seen.status = status
    seen.headers = {"Location": "http://example.org/"} if status == 302 else {}
    with pytest.raises(exception) as error:
        run(http_client.send_request("GET", f"http://127.0.0.1:{port}/"))
    assert text in error.value.user_message  # type: ignore[attr-defined]
    assert len(seen.requests) == 1  # the redirect was not followed


def test_connection_failures_are_retryable_and_timeouts_after_sending_are_not(
    http_server: tuple[int, Seen],
) -> None:
    with pytest.raises(SmartHomeConnectionError) as refused:
        run(http_client.send_request("GET", "http://127.0.0.1:1/"))
    assert refused.value.retryable is True
    port, seen = http_server
    seen.delay = 1.0
    with pytest.raises(SmartHomeRejectedError) as silent:
        run(http_client.send_request("GET", f"http://127.0.0.1:{port}/", timeout=0.2))
    assert silent.value.retryable is False and "trotzdem" in silent.value.user_message


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.org/x",
        "http://",
        "http://user:pw@example.org/",
        "http://example.org:99999/",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/",
        "http://exa mple.org/",
        "javascript:alert(1)",
    ],
)
def test_unacceptable_addresses_are_refused_before_any_connection(url: str) -> None:
    with pytest.raises(SmartHomeNotConfiguredError):
        http_client.check_url(url)
    with pytest.raises(SmartHomeNotConfiguredError):
        run(http_client.send_request("GET", url))


def test_unknown_methods_are_refused() -> None:
    with pytest.raises(SmartHomeNotConfiguredError):
        run(http_client.send_request("TRACE", "http://127.0.0.1:1/"))
    assert http_client.check_url("http://192.168.1.5:8080/a?b=c") == "http://192.168.1.5:8080/a?b=c"


# ---------- MQTT client ----------


class Broker:
    def __init__(self, *, connack: int = 0, puback: bool = True, hang: bool = False) -> None:
        self.connack = connack
        self.puback = puback
        self.hang = hang
        self.packets: list[bytes] = []
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _read(self, reader: asyncio.StreamReader) -> bytes:
        first = await reader.readexactly(1)
        length, shift = 0, 0
        raw = b""
        while True:
            byte = (await reader.readexactly(1))[0]
            raw += bytes([byte])
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        return first + raw + (await reader.readexactly(length) if length else b"")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.packets.append(await self._read(reader))  # CONNECT
            if self.hang:
                await asyncio.sleep(5)
            writer.write(bytes([0x20, 0x02, 0x00, self.connack]))
            await writer.drain()
            if self.connack:
                return
            publish = await self._read(reader)
            self.packets.append(publish)
            if publish[0] & 0x06 and self.puback:
                writer.write(bytes([0x40, 0x02, 0x00, 0x01]))
                await writer.drain()
            try:
                self.packets.append(await asyncio.wait_for(self._read(reader), 1))  # DISCONNECT
            except (asyncio.IncompleteReadError, TimeoutError):
                pass
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


def broker_run(broker: Broker, coro_factory: Callable[[], Any]):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        await broker.start()
        try:
            return await coro_factory()
        finally:
            await broker.stop()

    return asyncio.run(go())


def test_packet_encoding_matches_the_specification() -> None:
    connect = mqtt_client.build_connect("id", "user", "pw")
    assert connect[0] == 0x10 and b"MQTT" in connect and connect[9] == 0xC2  # user + pw + clean
    anonymous = mqtt_client.build_connect("id", "", "")
    assert anonymous[9] == 0x02
    publish = mqtt_client.build_publish("a/b", b"hi", qos=1, retain=True)
    assert publish[0] == 0x33 and publish.endswith(b"hi")
    big = mqtt_client.build_publish("t", b"x" * 300, qos=0, retain=False)
    assert big[1] == 0xAF and big[2] == 0x02  # 303 as a variable-length integer


def test_a_message_is_published_with_credentials_and_options() -> None:
    broker = Broker()
    broker_run(
        broker,
        lambda: mqtt_client.publish(
            "127.0.0.1",
            broker.port,
            "z2m/lamp/set",
            '{"state":"ON"}',
            username="hub",
            password="pw",
            qos=1,
            retain=True,
        ),
    )
    connect, publish = broker.packets[0], broker.packets[1]
    assert b"hub" in connect and b"pw" in connect
    assert publish[0] == 0x33 and b"z2m/lamp/set" in publish and b'{"state":"ON"}' in publish
    assert broker.packets[-1] == b"\xe0\x00"


def test_qos0_needs_no_acknowledgement() -> None:
    broker = Broker(puback=False)
    broker_run(broker, lambda: mqtt_client.publish("127.0.0.1", broker.port, "t", "x"))
    assert broker.packets[1][0] == 0x30


def test_a_refused_login_is_permanent_with_a_clear_message() -> None:
    broker = Broker(connack=4)
    with pytest.raises(SmartHomeRejectedError) as error:
        broker_run(
            broker,
            lambda: mqtt_client.publish(
                "127.0.0.1", broker.port, "t", "x", username="a", password="b"
            ),
        )
    assert "Passwort" in error.value.user_message and error.value.retryable is False


def test_no_broker_is_a_retryable_connection_error() -> None:
    with pytest.raises(SmartHomeConnectionError) as error:
        run(mqtt_client.publish("127.0.0.1", 1, "t", "x", timeout=1))
    assert error.value.retryable is True


def test_a_broker_that_never_answers_the_handshake_is_a_connection_error() -> None:
    broker = Broker(hang=True)
    with pytest.raises(SmartHomeConnectionError):
        broker_run(
            broker, lambda: mqtt_client.publish("127.0.0.1", broker.port, "t", "x", timeout=0.3)
        )


def test_a_missing_acknowledgement_leaves_the_outcome_unknown_and_is_not_retried() -> None:
    broker = Broker(puback=False)
    with pytest.raises(SmartHomeRejectedError) as error:
        broker_run(
            broker,
            lambda: mqtt_client.publish("127.0.0.1", broker.port, "t", "x", qos=1, timeout=0.3),
        )
    assert error.value.retryable is False and "trotzdem" in error.value.user_message


# ---------- configuration ----------

CONNECTIONS = [
    {
        "id": "ha",
        "name": "Home Assistant",
        "kind": "homeassistant",
        "address": "http://192.168.1.20:8123/",
    },
    {
        "id": "mq",
        "name": "Broker",
        "kind": "mqtt",
        "address": "192.168.1.20:1883",
        "username": "hub",
    },
]
ACTIONS = [
    {
        "id": "tor",
        "name": "Tor",
        "kind": "http",
        "url": "http://192.168.1.31/relay/0?turn=on",
        "method": "GET",
    },
    {
        "id": "lamp",
        "name": "Lampe",
        "kind": "mqtt",
        "connection": "mq",
        "topic": "z2m/l/set",
        "payload": "ON",
        "qos": 1,
        "retain": True,
    },
    {
        "id": "kino",
        "name": "Kino",
        "kind": "homeassistant",
        "connection": "ha",
        "service": "scene.turn_on",
        "entity_id": "scene.kino",
        "data": '{"transition": 2}',
    },
]


def config_with(smarthome: dict[str, Any] | None = None) -> AppConfig:
    return parse_config(
        {
            "schema_version": 7,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
            "smarthome": smarthome
            if smarthome is not None
            else {"connections": CONNECTIONS, "actions": ACTIONS},
        }
    )


def test_smarthome_settings_round_trip_through_the_file(tmp_path: Path) -> None:
    config = config_with()
    assert config.smarthome.connections[0].address == "http://192.168.1.20:8123"  # slash trimmed
    path = tmp_path / "c.toml"
    save_config(path, config)
    text = path.read_text(encoding="utf-8")
    assert "[[smarthome.connections]]" in text and "[[smarthome.actions]]" in text
    again = load_config(path)
    assert again.smarthome == config.smarthome
    assert smarthome_table(again.smarthome)["actions"][1]["retain"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    "smarthome",
    [
        {"connections": [{**CONNECTIONS[1], "address": "kein-port"}]},
        {"connections": [{**CONNECTIONS[1], "address": "host:99999"}]},
        {"connections": [{**CONNECTIONS[0], "address": "ftp://x"}]},
        {"connections": [{**CONNECTIONS[0], "kind": "zigbee"}]},
        {"connections": [CONNECTIONS[0], CONNECTIONS[0]]},
        {"connections": [{**CONNECTIONS[0], "id": "Bad Id"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "url": "ftp://x"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "url": "http://u:p@h/"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "method": "TRACE"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "content_type": "x/y"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "connection": "ha"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "connection": "nope"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "topic": ""}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "topic": "a/#"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "topic": "a/+/b"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "topic": "$SYS/x"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[1], "qos": 2}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[2], "service": "turn_on"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[2], "entity_id": "../x"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[2], "data": "nicht json"}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[2], "data": "[1]"}]},
        {"connections": CONNECTIONS, "actions": [ACTIONS[0], ACTIONS[0]]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "body": "x" * 501}]},
        {"connections": CONNECTIONS, "actions": [{**ACTIONS[0], "extra": 1}]},
        {"actions": [{**ACTIONS[0], "kind": "carrier-pigeon"}]},
    ],
)
def test_invalid_smarthome_settings_are_rejected(smarthome: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        config_with(smarthome)


# ---------- service ----------


class Secrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class Recorder:
    def __init__(self) -> None:
        self.http_calls: list[tuple[Any, ...]] = []
        self.mqtt_calls: list[tuple[Any, dict[str, Any]]] = []
        self.fail: Exception | None = None

    async def http(self, *args: Any, **_kw: Any) -> int:
        self.http_calls.append(args)
        if self.fail:
            raise self.fail
        return 200

    async def mqtt(self, *args: Any, **kw: Any) -> None:
        self.mqtt_calls.append((args, kw))
        if self.fail:
            raise self.fail


def make_service(secrets: dict[str, str] | None = None) -> tuple[SmartHomeService, Recorder]:
    recorder = Recorder()
    service = SmartHomeService(
        config_with().smarthome,
        Secrets(
            {secret_key("ha"): "TOKEN", secret_key("mq"): "PW"} if secrets is None else secrets
        ),
        http=recorder.http,
        mqtt=recorder.mqtt,
    )
    return service, recorder


def test_each_kind_of_action_sends_the_right_thing() -> None:
    service, recorder = make_service()
    run(service.execute("trigger", {"id": "tor"}))
    assert recorder.http_calls[0][:4] == (
        "GET",
        "http://192.168.1.31/relay/0?turn=on",
        "",
        "application/json",
    )
    run(service.execute("trigger", {"id": "lamp"}))
    args, kw = recorder.mqtt_calls[0]
    assert args == ("192.168.1.20", 1883, "z2m/l/set", "ON")
    assert kw == {"tls": False, "username": "hub", "password": "PW", "qos": 1, "retain": True}
    run(service.execute("trigger", {"id": "kino"}))
    method, url, body, content_type, headers = recorder.http_calls[1]
    assert (method, url) == ("POST", "http://192.168.1.20:8123/api/services/scene/turn_on")
    assert json.loads(body) == {"transition": 2, "entity_id": "scene.kino"}
    assert headers == {"Authorization": "Bearer TOKEN"} and content_type == "application/json"
    assert all(r.ok for r in service.results.values()) and len(service.results) == 3


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("explode", {"id": "tor"}),
        ("trigger", {}),
        ("trigger", {"id": 5}),
        ("trigger", {"id": "gibtsnicht"}),
    ],
)
def test_invalid_triggers_are_refused_without_sending(
    action: str, parameters: dict[str, object]
) -> None:
    service, recorder = make_service()
    with pytest.raises(SmartHomeNotConfiguredError) as error:
        run(service.execute(action, parameters))
    assert error.value.retryable is False
    assert recorder.http_calls == [] and recorder.mqtt_calls == []


def test_a_missing_home_assistant_token_is_explained_and_nothing_is_sent() -> None:
    service, recorder = make_service(secrets={})
    with pytest.raises(SmartHomeNotConfiguredError) as error:
        run(service.run("kino"))
    assert "Zugriffstoken" in error.value.user_message and recorder.http_calls == []
    assert service.results["kino"].ok is False and service.results["kino"].code == "CH-HOME-004"
    assert service.has_secret("ha") is False


def test_failures_are_typed_recorded_and_unexpected_ones_wrapped() -> None:
    service, recorder = make_service()
    recorder.fail = SmartHomeConnectionError("down")
    with pytest.raises(SmartHomeConnectionError):
        run(service.run("tor"))
    assert service.results["tor"].code == "CH-HOME-002"
    recorder.fail = RuntimeError("boom")
    with pytest.raises(SmartHomeError) as wrapped:
        run(service.run("tor"))
    assert wrapped.value.retryable is False and service.results["tor"].ok is False
    recorder.fail = None
    run(service.run("tor"))
    assert service.results["tor"].ok is True


def test_reconfigure_forgets_removed_actions_and_secret_values_never_appear() -> None:
    service, _ = make_service()
    run(service.run("tor"))
    service.reconfigure(SmartHomeConfig())
    assert service.results == {}
    assert service.has_secret("ha") is True
    assert "TOKEN" not in repr(service.results)


# ---------- web ----------


def client_and_backend() -> tuple[TestClient, Any]:
    backend = build_demo_backend()
    client = TestClient(create_app(backend, manage_service=True))
    client.__enter__()
    return client, backend


def post(client: TestClient, fields: dict[str, str], backend: Any, token: str = DEMO_TOKEN):  # type: ignore[no-untyped-def]
    body = {**forms_state(backend), **fields, "action": fields.get("action", "save")}
    if token:
        body["token"] = token
    return client.post("/smarthome", data=body)


def forms_state(backend: Any) -> dict[str, str]:
    from octapihub.web.smarthome_forms import smarthome_to_form

    return smarthome_to_form(backend.settings.config.smarthome)


def test_the_page_lists_connections_and_actions_without_secrets() -> None:
    client, backend = client_and_backend()
    try:
        page = client.get("/smarthome")
        assert page.status_code == 200
        for wanted in (
            "Home Assistant",
            "Mosquitto",
            "Schreibtischlampe an",
            "Garagentor",
            "Jetzt senden",
        ):
            assert wanted in page.text
        assert "demo-token" not in page.text and "gespeichert – leer lassen" in page.text
        assert 'href="/smarthome"' in page.text
    finally:
        client.__exit__(None, None, None)


def test_adding_removing_and_secrets() -> None:
    client, backend = client_and_backend()
    try:
        fields = {
            "c.new.name": "Zweiter Broker",
            "c.new.kind": "mqtt",
            "c.new.address": "10.0.0.5:8883",
            "c.new.username": "u",
            "c.new.secret": "geheimespasswort",
            "c.new.tls": "on",
            "a.new.name": "Ventilator",
            "a.new.kind": "mqtt",
            "a.new.connection": "zweiter-broker",
            "a.new.topic": "fan/set",
            "a.new.payload": "ON",
        }
        # the action refers to a connection that is created in the same submission
        response = post(client, fields, backend)
        assert response.status_code == 200, response.text[:400]
        config = backend.settings.config.smarthome
        assert [c.id for c in config.connections] == ["ha", "broker", "zweiter-broker"]
        assert config.connections[2].tls is True
        assert backend.smarthome.has_secret("zweiter-broker")
        saved = Path(backend.settings._path).read_text(encoding="utf-8")
        assert "geheimespasswort" not in saved and "geheimespasswort" not in response.text
        removed = post(client, {"c.2.remove": "on", "a.3.remove": "on"}, backend)
        assert removed.status_code == 200
        assert [c.id for c in backend.settings.config.smarthome.connections] == ["ha", "broker"]
        assert not backend.smarthome.has_secret("zweiter-broker")  # its secret was deleted
    finally:
        client.__exit__(None, None, None)


def test_all_mistakes_are_reported_and_nothing_is_saved() -> None:
    client, backend = client_and_backend()
    try:
        before = backend.settings.config
        response = post(
            client,
            {
                "c.new.name": "X",
                "c.new.kind": "mqtt",
                "c.new.address": "kaputt",
                "a.new.name": "Y",
                "a.new.kind": "mqtt",
                "a.new.connection": "ha",
                "a.new.topic": "a/#",
                "a.0.topic": "",
                "a.2.url": "ftp://x",
            },
            backend,
        )
        assert response.status_code == 422
        for message in (
            "Format prüfen",
            "Das Thema darf nicht leer sein",
            "http:// oder https://",
            "passende Verbindung",
        ):
            assert message in response.text
        assert backend.settings.config == before
        assert post(client, {}, backend, token="").status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_the_test_button_reports_success_and_each_kind_of_failure() -> None:
    client, backend = client_and_backend()
    try:
        ok = post(client, {"action": "test:lampe-an"}, backend)
        assert ok.status_code == 200 and "Gesendet" in ok.text

        async def down(*_a: object, **_k: object) -> int:
            raise SmartHomeConnectionError("x")

        async def no(*_a: object, **_k: object) -> int:
            raise SmartHomeRejectedError("x")

        backend.smarthome._http = down
        assert post(client, {"action": "test:tor"}, backend).status_code == 502
        backend.smarthome._http = no
        refused = post(client, {"action": "test:tor"}, backend)
        assert refused.status_code == 502 and "CH-HOME-003" in refused.text
        assert post(client, {"action": "test:nope"}, backend).status_code == 422
        assert post(client, {"action": "explode"}, backend).status_code == 422
        page = client.get("/smarthome").text
        assert "Zuletzt fehlgeschlagen" in page
    finally:
        client.__exit__(None, None, None)


def test_without_the_service_the_page_explains_itself() -> None:
    client, backend = client_and_backend()
    try:
        backend.smarthome = None
        assert client.get("/smarthome").status_code == 200
        assert post(client, {}, backend).status_code == 503
    finally:
        client.__exit__(None, None, None)


def test_smart_home_actions_can_be_assigned_to_faces() -> None:
    client, backend = client_and_backend()
    try:
        page = client.get("/cubes").text
        assert 'value="home:lampe-an" selected' in page and "Smart Home" in page
        body = {
            **forms.mapping_config_to_form(backend.settings.config),
            "map.0.4": "home:tor",
            "token": DEMO_TOKEN,
        }
        assert client.post("/cubes/mapping", data=body).status_code == 200
        saved = {
            (m.cube, m.face): (m.actions[0].kind, dict(m.actions[0].parameters))
            for m in backend.settings.config.mappings
        }
        assert saved[("Licht", 4)] == ("smarthome", {"id": "tor"})
        bad = {**body, "map.0.5": "home:gibtsnicht"}
        assert client.post("/cubes/mapping", data=bad).status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_face_details_name_the_target_but_hide_web_address_secrets() -> None:
    client, backend = client_and_backend()
    try:
        config = backend.settings.config
        by_face = {(m.cube, m.face): m for m in config.mappings}
        lines = describe_face(config, by_face[("Extra", 1)], {}, {})
        assert any("Schreibtischlampe an" in line for line in lines)
        assert any("zigbee2mqtt/schreibtisch/set" in line and "Mosquitto" in line for line in lines)
        hook = forms.home_choice_options({"tor": "Tor"})
        assert hook == [("Smart Home", [("home:tor", "Tor")])]
        assert forms.home_choice_options({}) == []
    finally:
        client.__exit__(None, None, None)


def test_error_classes_have_the_retry_semantics_the_outbox_relies_on() -> None:
    assert SmartHomeConnectionError.retryable is True
    for error in (SmartHomeError, SmartHomeRejectedError, SmartHomeNotConfiguredError):
        assert error.retryable is False
    codes = {
        e.code
        for e in (
            SmartHomeError,
            SmartHomeConnectionError,
            SmartHomeRejectedError,
            SmartHomeNotConfiguredError,
        )
    }
    assert codes == {"CH-HOME-001", "CH-HOME-002", "CH-HOME-003", "CH-HOME-004"}
