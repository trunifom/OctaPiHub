from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from octapihub.application.hue_service import (
    HUE_KEY_NAME,
    HueGateway,
    HueScene,
    HueService,
    HueState,
    SceneInfo,
)
from octapihub.application.plugins import PluginRegistry
from octapihub.core.config import HueConfig
from octapihub.core.errors import (
    HueBridgeError,
    HueCertificateError,
    HueLinkButtonError,
    HueNotConfiguredError,
    HueRateLimitError,
    SecretsError,
    ValidationError,
)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
ENABLED = HueConfig(enabled=True, host="192.168.1.20")
SECRET_KEY = "super-secret-app-key"


@dataclass(frozen=True)
class _Scene:
    id: str
    name: str


class _MemorySecrets:
    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self.values = dict(initial or {})
        self.fail_get: Exception | None = None
        self.fail_set: Exception | None = None
        self.fail_delete: Exception | None = None

    def get(self, key: str) -> str | None:
        if self.fail_get:
            raise self.fail_get
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        if self.fail_set:
            raise self.fail_set
        self.values[key] = value

    def delete(self, key: str) -> bool:
        if self.fail_delete:
            raise self.fail_delete
        return self.values.pop(key, None) is not None


class _Gateway:
    def __init__(self, config: HueConfig, key: str) -> None:
        self.config = config
        self.key = key
        self.recalled: list[str] = []
        self.scenes: list[SceneInfo] = [_Scene("s1", "Abend"), _Scene("s2", "Hell")]
        self.fail: Exception | None = None

    async def list_scenes(self) -> Sequence[SceneInfo]:
        if self.fail:
            raise self.fail
        return list(self.scenes)

    async def recall_scene(self, scene_id: str) -> None:
        if self.fail:
            raise self.fail
        self.recalled.append(scene_id)


class _Env:
    """Wires a HueService to in-memory fakes and records what was created."""

    def __init__(self, config: HueConfig = ENABLED, secrets: _MemorySecrets | None = None) -> None:
        self.secrets = secrets or _MemorySecrets()
        self.gateways: list[_Gateway] = []
        self.pair_calls = 0
        self.pair_result: str | Exception = SECRET_KEY
        self.gateway_error: Exception | None = None
        self.service = HueService(
            config, self.secrets, self._make_gateway, self._pair, clock=lambda: NOW
        )

    def _make_gateway(self, config: HueConfig, key: str) -> HueGateway:
        if self.gateway_error:
            raise self.gateway_error
        gateway = _Gateway(config, key)
        self.gateways.append(gateway)
        return gateway

    async def _pair(self, _config: HueConfig) -> str:
        self.pair_calls += 1
        await asyncio.sleep(0)
        if isinstance(self.pair_result, Exception):
            raise self.pair_result
        return self.pair_result


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ---------- states ----------


def test_disabled_hue_rejects_actions_and_pairing() -> None:
    env = _Env(HueConfig())

    async def scenario() -> None:
        await env.service.start()
        assert env.service.state is HueState.DISABLED
        with pytest.raises(HueNotConfiguredError):
            await env.service.execute("scene.activate", {"scene_id": "s1"})
        with pytest.raises(HueNotConfiguredError):
            await env.service.pair()
        with pytest.raises(HueNotConfiguredError):
            await env.service.refresh_scenes()

    run(scenario())
    assert env.pair_calls == 0


def test_enabled_without_key_is_not_paired_and_actions_fail_retryably() -> None:
    env = _Env()

    async def scenario() -> None:
        await env.service.start()
        assert env.service.state is HueState.NOT_PAIRED
        with pytest.raises(HueNotConfiguredError) as error:
            await env.service.execute("scene.activate", {"scene_id": "s1"})
        assert error.value.error_code == "CH-HUE-005"
        assert hasattr(error.value, "retry_after_seconds")  # retryable by the outbox

    run(scenario())


def test_start_with_stored_key_is_paired_without_contacting_the_bridge() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        assert env.service.state is HueState.PAIRED
        await env.service.execute("scene.activate", {"scene_id": "s1"})

    run(scenario())
    assert env.pair_calls == 0
    assert env.gateways[0].key == SECRET_KEY
    assert env.gateways[0].recalled == ["s1"]


# ---------- pairing ----------


def test_pairing_stores_key_and_enables_actions_and_registers_as_plugin() -> None:
    env = _Env()
    registry = PluginRegistry()
    registry.register(env.service)

    async def scenario() -> None:
        await env.service.start()
        await env.service.pair()
        assert env.service.state is HueState.PAIRED
        await registry.require("hue").execute("scene.activate", {"scene_id": "s2"})

    run(scenario())
    assert env.secrets.values == {HUE_KEY_NAME: SECRET_KEY}
    assert env.gateways[0].recalled == ["s2"]
    assert env.service.status.last_error is None


def test_pairing_without_button_press_reports_and_leaves_state_unpaired() -> None:
    env = _Env()
    env.pair_result = HueLinkButtonError("button")

    async def scenario() -> None:
        await env.service.start()
        with pytest.raises(HueLinkButtonError):
            await env.service.pair()
        assert env.service.state is HueState.NOT_PAIRED
        assert env.service.status.last_error_code == "CH-HUE-004"
        # A second try after pressing the button works and clears the error.
        env.pair_result = SECRET_KEY
        await env.service.pair()
        assert env.service.state is HueState.PAIRED
        assert env.service.status.last_error is None

    run(scenario())


@pytest.mark.parametrize(
    "failure",
    [HueCertificateError("tls"), HueBridgeError("offline"), RuntimeError("bug")],
)
def test_pairing_failures_are_typed_and_never_store_a_key(failure: Exception) -> None:
    env = _Env()
    env.pair_result = failure

    async def scenario() -> None:
        await env.service.start()
        with pytest.raises(HueBridgeError):
            await env.service.pair()
        assert env.service.state is HueState.NOT_PAIRED
        assert env.service.status.last_error is not None

    run(scenario())
    assert env.secrets.values == {}


def test_failing_secret_storage_after_pairing_does_not_mark_paired() -> None:
    env = _Env()
    env.secrets.fail_set = SecretsError("read-only")

    async def scenario() -> None:
        await env.service.start()
        with pytest.raises(SecretsError):
            await env.service.pair()
        assert env.service.state is HueState.NOT_PAIRED
        assert env.service.status.last_error_code == "CH-SEC-001"

    run(scenario())
    assert env.gateways == []


def test_concurrent_pairing_requests_are_serialized() -> None:
    env = _Env()

    async def scenario() -> None:
        await env.service.start()
        results = await asyncio.gather(
            env.service.pair(), env.service.pair(), return_exceptions=True
        )
        assert results == [None, None]

    run(scenario())
    assert env.pair_calls == 2  # one after the other, never interleaved
    assert env.service.state is HueState.PAIRED


def test_status_never_contains_the_key() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()

    run(scenario())
    assert SECRET_KEY not in repr(env.service.status)
    assert SECRET_KEY not in repr(env.service.config)


# ---------- unpair / reconfigure ----------


def test_unpair_removes_key_and_scenes() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        await env.service.refresh_scenes()
        await env.service.unpair()
        assert env.service.state is HueState.NOT_PAIRED
        assert env.service.scenes == ()
        with pytest.raises(HueNotConfiguredError):
            await env.service.execute("scene.activate", {"scene_id": "s1"})

    run(scenario())
    assert env.secrets.values == {}


def test_failed_unpair_keeps_the_key_active_and_reports() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))
    env.secrets.fail_delete = SecretsError("locked")

    async def scenario() -> None:
        await env.service.start()
        with pytest.raises(SecretsError):
            await env.service.unpair()
        assert env.service.state is HueState.PAIRED
        await env.service.execute("scene.activate", {"scene_id": "s1"})

    run(scenario())


def test_reconfigure_rebuilds_gateway_for_new_host_and_keeps_key() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        await env.service.reconfigure(replace(ENABLED, host="192.168.1.99"))
        await env.service.execute("scene.activate", {"scene_id": "s1"})
        await env.service.reconfigure(HueConfig())
        assert env.service.state is HueState.DISABLED

    run(scenario())
    assert [g.config.host for g in env.gateways] == ["192.168.1.20", "192.168.1.99"]
    assert env.secrets.values == {HUE_KEY_NAME: SECRET_KEY}  # disabling never deletes the key


# ---------- faults ----------


def test_unreadable_secrets_are_reported_not_raised_on_start() -> None:
    env = _Env()
    env.secrets.fail_get = SecretsError("readable by others")

    async def scenario() -> None:
        await env.service.start()
        assert env.service.state is HueState.NOT_PAIRED
        assert env.service.status.last_error_code == "CH-SEC-001"

    run(scenario())


def test_bad_gateway_configuration_is_reported_and_each_use_fails_clearly() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))
    env.gateway_error = HueCertificateError("ca file missing")

    async def scenario() -> None:
        await env.service.start()
        assert env.service.state is HueState.PAIRED
        assert env.service.status.last_error_code == "CH-HUE-003"
        with pytest.raises(HueCertificateError):
            await env.service.execute("scene.activate", {"scene_id": "s1"})
        with pytest.raises(HueCertificateError):
            await env.service.refresh_scenes()
        # Fixing the problem and reconfiguring recovers without a restart.
        env.gateway_error = None
        await env.service.reconfigure(ENABLED)
        await env.service.execute("scene.activate", {"scene_id": "s1"})
        assert env.service.status.last_error is None

    run(scenario())


# ---------- scenes and actions ----------


def test_scene_refresh_caches_and_keeps_stale_list_on_failure() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        scenes = await env.service.refresh_scenes()
        assert scenes == (HueScene("s1", "Abend"), HueScene("s2", "Hell"))
        assert env.service.status.scenes_checked_at == NOW

        env.gateways[0].fail = HueRateLimitError("slow down", retry_after_seconds=3)
        with pytest.raises(HueRateLimitError):
            await env.service.refresh_scenes()
        assert env.service.scenes == scenes  # stale but still shown
        assert env.service.status.last_error_code == "CH-HUE-002"

        env.gateways[0].fail = RuntimeError("bug in client")
        with pytest.raises(HueBridgeError):
            await env.service.refresh_scenes()
        assert env.service.status.last_error is not None
        assert "bug in client" not in env.service.status.last_error

        env.gateways[0].fail = None
        await env.service.refresh_scenes()
        assert env.service.status.last_error is None

    run(scenario())


@pytest.mark.parametrize(
    ("action", "parameters", "message"),
    [
        ("light.explode", {}, "nicht unterstützt"),
        ("scene.activate", {}, "keine Hue-Szene"),
        ("scene.activate", {"scene_id": 5}, "keine Hue-Szene"),
        ("scene.activate", {"scene_id": "  "}, "keine Hue-Szene"),
    ],
)
def test_invalid_actions_are_rejected_before_any_bridge_call(
    action: str, parameters: Mapping[str, object], message: str
) -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        with pytest.raises(ValidationError) as error:
            await env.service.execute(action, parameters)
        assert message in error.value.user_message

    run(scenario())
    assert env.gateways[0].recalled == []


def test_action_failures_are_recorded_and_raised_for_outbox_retry() -> None:
    env = _Env(secrets=_MemorySecrets({HUE_KEY_NAME: SECRET_KEY}))

    async def scenario() -> None:
        await env.service.start()
        env.gateways[0].fail = HueBridgeError("offline")
        with pytest.raises(HueBridgeError):
            await env.service.execute("scene.activate", {"scene_id": "s1"})
        assert env.service.status.last_error_code == "CH-HUE-001"
        env.gateways[0].fail = None
        await env.service.execute("scene.activate", {"scene_id": "s1"})
        assert env.service.status.last_error is None

    run(scenario())
