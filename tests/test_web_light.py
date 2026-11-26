from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime, BleRuntimeTuning, CubeConnectionState
from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import (
    HUE_KEY_NAME,
    HueGateway,
    HueService,
    HueState,
    SceneInfo,
)
from octapihub.application.outbox import OutboxDispatcher, OutboxState
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.composition import build_hue_service
from octapihub.core.config import (
    AppConfig,
    HueConfig,
    load_config,
    parse_config,
    save_config,
)
from octapihub.core.errors import (
    ConfigurationError,
    HueBridgeError,
    HueCertificateError,
    HueLinkButtonError,
    ValidationError,
)
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import forms
from octapihub.web.app import WebBackend, create_app

TOKEN = "s3cret-token"
APP_KEY = "very-secret-hue-application-key"
HOST = "192.168.1.20"


@dataclass(frozen=True)
class _Scene:
    id: str
    name: str


class _Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class _Gateway:
    def __init__(self) -> None:
        self.scenes: list[SceneInfo] = [_Scene("s-1", "Abend"), _Scene("s-2", "<b>Hell</b>")]
        self.recalled: list[str] = []
        self.fail: Exception | None = None

    async def list_scenes(self) -> Sequence[SceneInfo]:
        if self.fail:
            raise self.fail
        return list(self.scenes)

    async def recall_scene(self, scene_id: str) -> None:
        if self.fail:
            raise self.fail
        self.recalled.append(scene_id)


class _Rig:
    """A web backend whose Hue side is fully fake."""

    def __init__(self, tmp_path: Path, *, hue_enabled: bool = True, with_hue: bool = True) -> None:
        self.tmp_path = tmp_path
        self.config_path = tmp_path / "octapihub.toml"
        self.secrets = _Secrets()
        self.gateway = _Gateway()
        self.pair_results: list[str | Exception] = []
        self.pair_hosts: list[str] = []
        config = parse_config(
            {
                "schema_version": 4,
                "bluetooth": {"adapter": "hci0"},
                "cubes": [
                    {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                    {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
                ],
                "hue": {"enabled": hue_enabled, "host": HOST if hue_enabled else ""},
                "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
            }
        )
        save_config(self.config_path, config)
        sense = SenseHatService(
            config.sense_hat,
            lambda _c: SenseHatAdapter(FakeSenseHat()),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.hue: HueService | None = (
            HueService(config.hue, self.secrets, self._gateway, self._pair) if with_hue else None
        )
        self.settings = SettingsManager(self.config_path, config, sense, self.hue)
        self.backend = WebBackend(self.settings, sense, TOKEN, hue=self.hue)

    def _gateway(self, _config: HueConfig, _key: str) -> HueGateway:
        return self.gateway

    async def _pair(self, config: HueConfig) -> str:
        self.pair_hosts.append(config.host)
        result = self.pair_results.pop(0) if self.pair_results else APP_KEY
        if isinstance(result, Exception):
            raise result
        return result

    def client(self) -> TestClient:
        return TestClient(create_app(self.backend, manage_service=True))


def _post(client: TestClient, fields: Mapping[str, str], token: str | None = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token is not None:
        body["token"] = token
    return client.post(
        "/light",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def _form(rig: _Rig, **changes: str) -> dict[str, str]:
    values = forms.hue_config_to_form(rig.settings.config.hue)
    values.update(changes)
    return values


@pytest.fixture
def rig(tmp_path: Path) -> _Rig:
    return _Rig(tmp_path)


# ---------- page states ----------


def test_page_shows_each_state_with_guidance(tmp_path: Path) -> None:
    disabled = _Rig(tmp_path / "a", hue_enabled=False) if (tmp_path / "a").mkdir() is None else None
    assert disabled is not None
    with disabled.client() as client:
        text = client.get("/light").text
        assert "Hue ist ausgeschaltet" in text
        assert 'name="hue.host"' in text
        assert "Nicht verbunden" not in text
    rig = _Rig(tmp_path / "b") if (tmp_path / "b").mkdir() is None else None
    assert rig is not None
    with rig.client() as client:
        text = client.get("/light").text
        assert "noch nicht verbunden" in text
        assert "runde Taste" in text
        assert "Szenen laden" not in text  # only after pairing


def test_without_hue_service_page_explains_and_writes_are_refused(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, with_hue=False)
    with rig.client() as client:
        page = client.get("/light")
        assert page.status_code == 200
        assert "nicht aktiv" in page.text
        response = _post(client, {"action": "refresh"})
        assert response.status_code == 503
        assert "CH-WEB-005" in response.text
        assert "nicht aktiv" in client.get("/").text


def test_read_only_without_token_disables_buttons(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    rig.backend.token = None
    with rig.client() as client:
        text = client.get("/light").text
        assert "Nur ansehen" in text
        assert "disabled" in text
        response = _post(client, {"action": "pair"}, token="x")
        assert response.status_code == 403
        assert "CH-WEB-003" in response.text
    assert rig.pair_hosts == []


# ---------- authorization ----------


@pytest.mark.parametrize("token", [None, "", "wrong-token-zzz"])
def test_bad_token_blocks_every_action_without_side_effects(rig: _Rig, token: str | None) -> None:
    from octapihub.core.auth_guard import FailureLimiter

    rig.backend.guard = FailureLimiter(max_failures=1000)  # the lock-out is tested elsewhere
    with rig.client() as client:
        for action in ("save", "pair", "refresh", "unpair", "test:s-1", "nonsense"):
            response = _post(client, _form(rig, action=action), token=token)
            assert response.status_code == 403, action
            assert "CH-WEB-004" in response.text
            assert "wrong-token-zzz" not in response.text
    assert rig.pair_hosts == []
    assert rig.secrets.values == {}
    assert rig.gateway.recalled == []


# ---------- saving ----------


def test_save_persists_and_reconfigures_hue(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, hue_enabled=False)
    with rig.client() as client:
        values = _form(
            rig, action="save", **{"hue.enabled": "on", "hue.host": "hue.local", "hue.port": "8443"}
        )
        response = _post(client, values)
        assert response.status_code == 200
        assert "Gespeichert und angewendet" in response.text
    saved = load_config(rig.config_path).hue
    assert (saved.enabled, saved.host, saved.port) == (True, "hue.local", 8443)
    assert rig.hue is not None
    assert rig.hue.state is HueState.NOT_PAIRED


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"hue.enabled": "on", "hue.host": ""}, "hue.host"),
        ({"hue.host": "http://192.168.1.2"}, "hue.host"),
        ({"hue.host": "bad host"}, "hue.host"),
        ({"hue.port": "abc"}, "hue.port"),
        ({"hue.port": "0"}, "hue.port"),
        ({"hue.port": "99999"}, "hue.port"),
        ({"hue.tls_server_name": "no spaces"}, "hue.tls_server_name"),
        ({"hue.ca_file": "a\nb"}, "hue.ca_file"),
    ],
)
def test_invalid_settings_show_field_error_and_change_nothing(
    rig: _Rig, changes: dict[str, str], field: str
) -> None:
    before = rig.config_path.read_text(encoding="utf-8")
    with rig.client() as client:
        response = _post(client, _form(rig, action="save", **changes))
    assert response.status_code == 422
    assert f'id="err-{field}"' in response.text
    assert "Es wurde nichts gespeichert" in response.text
    assert rig.config_path.read_text(encoding="utf-8") == before


def test_failed_file_write_keeps_old_settings_and_reports(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octapihub.application.settings as settings_module

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConfigurationError("disk full", user_message="Nicht gespeichert.")

    monkeypatch.setattr(settings_module, "save_config", broken)
    with rig.client() as client:
        response = _post(client, _form(rig, action="save", **{"hue.host": "10.0.0.5"}))
    assert response.status_code == 500
    assert "Nicht gespeichert." in response.text
    assert rig.settings.config.hue.host == HOST
    assert rig.hue is not None
    assert rig.hue.config.host == HOST


# ---------- pairing ----------


def test_pairing_flow_button_missing_then_success(rig: _Rig) -> None:
    rig.pair_results = [HueLinkButtonError("not pressed"), APP_KEY]
    with rig.client() as client:
        first = _post(client, _form(rig, action="pair"))
        assert first.status_code == 409
        assert "Taste an der Bridge" in first.text
        assert "CH-HUE-004" in first.text
        assert rig.secrets.values == {}

        second = _post(client, _form(rig, action="pair"))
        assert second.status_code == 200
        assert "Mit der Bridge verbunden" in second.text
        assert "Abend" in second.text  # scenes were loaded right after pairing
        assert 'value="test:s-1"' in second.text
    assert rig.secrets.values == {HUE_KEY_NAME: APP_KEY}


def test_pairing_uses_the_host_typed_in_the_form(rig: _Rig) -> None:
    with rig.client() as client:
        response = _post(client, _form(rig, action="pair", **{"hue.host": "10.9.8.7"}))
    assert response.status_code == 200
    assert rig.pair_hosts == ["10.9.8.7"]
    assert load_config(rig.config_path).hue.host == "10.9.8.7"


@pytest.mark.parametrize(
    ("failure", "code"),
    [(HueBridgeError("offline"), "CH-HUE-001"), (HueCertificateError("tls"), "CH-HUE-003")],
)
def test_pairing_failures_are_explained_with_code(rig: _Rig, failure: Exception, code: str) -> None:
    rig.pair_results = [failure]
    with rig.client() as client:
        response = _post(client, _form(rig, action="pair"))
    assert response.status_code == 502
    assert code in response.text
    assert "Traceback" not in response.text
    assert rig.secrets.values == {}


def test_pairing_with_invalid_form_does_not_contact_the_bridge(rig: _Rig) -> None:
    with rig.client() as client:
        response = _post(client, _form(rig, action="pair", **{"hue.host": ""}))
    assert response.status_code == 422
    assert rig.pair_hosts == []


# ---------- scenes ----------


def _paired(rig: _Rig) -> None:
    rig.secrets.values[HUE_KEY_NAME] = APP_KEY


def test_refresh_lists_scenes_escaped_and_keeps_list_on_failure(rig: _Rig) -> None:
    _paired(rig)
    with rig.client() as client:
        ok = _post(client, _form(rig, action="refresh"))
        assert ok.status_code == 200
        assert "2 Szenen geladen" in ok.text
        assert "&lt;b&gt;Hell&lt;/b&gt;" in ok.text
        assert "<b>Hell</b>" not in ok.text  # scene names are never trusted as HTML

        rig.gateway.fail = HueBridgeError("offline")
        failed = _post(client, _form(rig, action="refresh"))
        assert failed.status_code == 502
        assert "CH-HUE-001" in failed.text
        assert "Abend" in failed.text  # stale list stays visible


def test_scene_test_button_activates_only_known_scenes(rig: _Rig) -> None:
    _paired(rig)
    with rig.client() as client:
        _post(client, _form(rig, action="refresh"))
        ok = _post(client, _form(rig, action="test:s-1"))
        assert ok.status_code == 200
        assert "Szene wurde aktiviert" in ok.text
        unknown = _post(client, _form(rig, action="test:../../etc"))
        assert unknown.status_code == 422
        rig.gateway.fail = HueBridgeError("offline")
        failing = _post(client, _form(rig, action="test:s-2"))
        assert failing.status_code == 502
    assert rig.gateway.recalled == ["s-1"]


def test_actions_before_pairing_say_what_to_do(rig: _Rig) -> None:
    with rig.client() as client:
        response = _post(client, _form(rig, action="refresh"))
    assert response.status_code == 409
    assert "CH-HUE-005" in response.text


# ---------- unpair / misc ----------


def test_unpair_requires_confirmation_then_removes_key(rig: _Rig) -> None:
    _paired(rig)
    with rig.client() as client:
        unconfirmed = _post(client, _form(rig, action="unpair"))
        assert unconfirmed.status_code == 422
        assert rig.secrets.values == {HUE_KEY_NAME: APP_KEY}
        confirmed = _post(client, _form(rig, action="unpair", confirm_unpair="on"))
        assert confirmed.status_code == 200
        assert "Verbindung getrennt" in confirmed.text
    assert rig.secrets.values == {}


def test_unknown_action_and_oversized_body_are_rejected(rig: _Rig) -> None:
    with rig.client() as client:
        unknown = _post(client, {"action": "format-disk"})
        assert unknown.status_code == 422
        assert "CH-WEB-006" in unknown.text
        huge = client.post(
            "/light",
            content="a=" + "x" * 70_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert huge.status_code == 413
        assert "Traceback" not in huge.text


def test_application_key_never_appears_in_any_page_or_status(rig: _Rig) -> None:
    rig.pair_results = [APP_KEY]
    with rig.client() as client:
        _post(client, _form(rig, action="pair"))
        for path in ("/", "/light", "/settings", "/cubes", "/healthz"):
            assert APP_KEY not in client.get(path).text
        assert HUE_KEY_NAME not in client.get("/light").text


def test_overview_card_reflects_hue_state(rig: _Rig) -> None:
    with rig.client() as client:
        assert "Nicht verbunden" in client.get("/").text
        _post(client, _form(rig, action="pair"))
        assert "Gekoppelt" in client.get("/").text


# ---------- composition ----------


def test_build_hue_service_reports_bad_ca_file_instead_of_failing(tmp_path: Path) -> None:
    config = parse_config(
        {
            "schema_version": 4,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "hue": {"enabled": True, "host": HOST, "ca_file": str(tmp_path / "missing.pem")},
        }
    )
    secrets = tmp_path / "secrets.env"
    SecretsFile(secrets).set(HUE_KEY_NAME, APP_KEY)
    service = build_hue_service(config, secrets)

    async def scenario() -> None:
        await service.start()
        assert service.state is HueState.PAIRED
        assert service.status.last_error_code == "CH-HUE-003"
        with pytest.raises(HueCertificateError):
            await service.refresh_scenes()

    asyncio.run(scenario())


def test_build_hue_service_without_secrets_file_is_not_paired(tmp_path: Path) -> None:
    config = parse_config(
        {
            "schema_version": 4,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "hue": {"enabled": True, "host": HOST},
        }
    )
    service = build_hue_service(config, tmp_path / "secrets.env")
    asyncio.run(service.start())
    assert service.state is HueState.NOT_PAIRED


def test_settings_manager_rejects_invalid_hue_table_without_side_effects(rig: _Rig) -> None:
    before = rig.config_path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        asyncio.run(rig.settings.apply_hue({"enabled": True, "host": ""}))
    assert rig.config_path.read_text(encoding="utf-8") == before


# ---------- the whole chain: cube -> rules -> outbox -> Hue ----------

_LIGHT = "AA:BB:CC:DD:EE:01"


def _chain_config(tmp_path: Path) -> AppConfig:
    return parse_config(
        {
            "schema_version": 4,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": _LIGHT, "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 4,
                    "actions": [
                        {
                            "kind": "hue",
                            "action": "scene.activate",
                            "parameters": {"scene_id": "s-1"},
                        }
                    ],
                }
            ],
            "hue": {"enabled": True, "host": HOST},
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
        }
    )


def _attempts(outbox: SQLiteOutbox) -> int:
    try:
        return outbox.get_entry(1).attempts
    except Exception:
        return 0


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_gesture_before_pairing_is_retried_and_succeeds_after_pairing(tmp_path: Path) -> None:
    config = _chain_config(tmp_path)
    secrets = _Secrets()
    gateway = _Gateway()

    async def pair(_config: HueConfig) -> str:
        return APP_KEY

    hue = HueService(config.hue, secrets, lambda _c, _k: gateway, pair)
    outbox = SQLiteOutbox(tmp_path / "state.sqlite3")
    factory = FakeTransportFactory(
        DeviceProfile((CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),))
    )

    def make_runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            tuning=BleRuntimeTuning(
                connect_timeout_seconds=0.5,
                operation_timeout_seconds=0.5,
                backoff_base_seconds=0.01,
                backoff_cap_seconds=0.02,
                health_poll_seconds=0.01,
                debounce_ms=0,
            ),
            jitter=lambda delay: delay,
        )

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        registry = PluginRegistry()
        registry.register(hue)
        return OutboxDispatcher(
            RuleEngine(settings),
            outbox,
            registry,
            backoff_base_seconds=0.01,
            backoff_cap_seconds=0.02,
        )

    service = CubeService(config, make_runtime, make_dispatcher, poll_interval_seconds=0.005)

    async def scenario() -> None:
        await hue.start()
        assert hue.state is HueState.NOT_PAIRED
        await service.start()
        assert service.error is None
        await _until(
            lambda: (
                len(factory.created) == 2
                and all(
                    s.state is CubeConnectionState.WAITING_FOR_BASELINE for s in service.statuses
                )
            )
        )
        transport = next(t for address, _, t in factory.created if address == _LIGHT)
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")

        # The action fails (not paired) but stays queued for retry.
        await _until(lambda: _attempts(outbox) >= 2)
        assert outbox.get_entry(1).state is OutboxState.PENDING
        assert gateway.recalled == []

        await hue.pair()
        await _until(lambda: gateway.recalled == ["s-1"])
        await service.stop()

    asyncio.run(scenario())
    entries = [outbox.get_entry(1)]
    assert entries[0].state is OutboxState.SUCCEEDED
    assert entries[0].attempts >= 2
