from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
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
    HueScene,
    HueService,
    SceneInfo,
)
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import (
    AppConfig,
    HueConfig,
    load_config,
    mappings_table,
    parse_config,
    save_config,
    with_mappings_table,
)
from octapihub.core.errors import ConfigurationError, PluginError, ValidationError
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import forms
from octapihub.web.app import WebBackend, create_app

TOKEN = "s3cret-token"
LIGHT = "AA:BB:CC:DD:EE:01"


def _config(
    tmp_path: Path, mappings: list[dict[str, object]] | None = None, cube_count: int = 2
) -> AppConfig:
    cubes = [
        {"name": "Licht", "address": LIGHT, "role": "light"},
        {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
        {"name": "Extra", "address": "AA:BB:CC:DD:EE:03", "role": "general"},
    ][:cube_count]
    return parse_config(
        {
            "schema_version": 4,
            "bluetooth": {"adapter": "hci0"},
            "cubes": cubes,
            "mappings": mappings if mappings is not None else [],
            "hue": {"enabled": True, "host": "192.168.1.20"},
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
        }
    )


def _scene_action(scene_id: str) -> dict[str, object]:
    return {"kind": "hue", "action": "scene.activate", "parameters": {"scene_id": scene_id}}


def _mapping(cube: str, face: int, *actions: dict[str, object]) -> dict[str, object]:
    return {"cube": cube, "face": face, "actions": list(actions)}


# ---------- configuration helpers ----------


def test_mappings_table_round_trips_through_validation(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        [_mapping("Licht", 2, _scene_action("a")), _mapping("Musik", 7, _scene_action("b"))],
    )
    rebuilt = with_mappings_table(config, mappings_table(config))
    assert rebuilt == config


@pytest.mark.parametrize(
    "raw",
    [
        [_mapping("Unbekannt", 1, _scene_action("a"))],
        [_mapping("Licht", 9, _scene_action("a"))],
        [_mapping("Licht", -1, _scene_action("a"))],
        [_mapping("Licht", 1)],
        [_mapping("Licht", 1, _scene_action("a")), _mapping("licht", 1, _scene_action("b"))],
        [_mapping("Licht", 1, {"kind": "Bad Kind", "action": "x", "parameters": {}})],
        ["not a table"],
    ],
)
def test_invalid_mappings_are_rejected(tmp_path: Path, raw: list[object]) -> None:
    with pytest.raises(ValidationError):
        with_mappings_table(_config(tmp_path), raw)


# ---------- live rules ----------


class _Plugin:
    kind = "hue"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, action: str, parameters: object) -> None:
        assert isinstance(parameters, dict)
        self.calls.append(str(parameters.get("scene_id")))


def test_dispatcher_switches_rules_and_keeps_old_ones_when_plugin_is_missing(
    tmp_path: Path,
) -> None:
    old = _config(tmp_path, [_mapping("Licht", 1, _scene_action("old"))])
    new = _config(tmp_path, [_mapping("Licht", 1, _scene_action("new"))])
    other_kind = _config(
        tmp_path,
        [_mapping("Licht", 1, {"kind": "audio", "action": "play", "parameters": {}})],
    )
    registry = PluginRegistry()
    registry.register(_Plugin())
    dispatcher = OutboxDispatcher(RuleEngine(old), SQLiteOutbox(tmp_path / "o.db"), registry)

    dispatcher.update_rules(RuleEngine(new))
    batch = dispatcher.enqueue_face_change("Licht", 1)
    assert batch is not None
    with pytest.raises(PluginError):
        dispatcher.update_rules(RuleEngine(other_kind))
    # the rejected update left the previous (new) rules active
    assert dispatcher.enqueue_face_change("Licht", 1) is not None


# ---------- cube service: no reconnect on change ----------


class _Rig:
    """Cube service with fake Bluetooth, a paired fake Hue, and a web backend."""

    def __init__(
        self,
        tmp_path: Path,
        mappings: list[dict[str, object]] | None = None,
        cube_count: int = 2,
    ) -> None:
        self.tmp_path = tmp_path
        self.config = _config(tmp_path, mappings, cube_count)
        self.path = tmp_path / "octapihub.toml"
        save_config(self.path, self.config)
        self.recalled: list[str] = []
        self.scenes: list[SceneInfo] = [
            _Scene("scene-abend", "Abend"),
            _Scene("scene-hell", "<i>Hell</i>"),
        ]
        self.save_should_fail = False

        class Secrets:
            values = {HUE_KEY_NAME: "k"}

            def get(self, key: str) -> str | None:
                return self.values.get(key)

            def set(self, key: str, value: str) -> None:
                self.values[key] = value

            def delete(self, key: str) -> bool:
                return self.values.pop(key, None) is not None

        rig = self

        class Gateway:
            async def list_scenes(self) -> Sequence[SceneInfo]:
                return list(rig.scenes)

            async def recall_scene(self, scene_id: str) -> None:
                rig.recalled.append(scene_id)

        async def pair(_c: HueConfig) -> str:
            return "k"

        def make_gateway(_c: HueConfig, _k: str) -> HueGateway:
            return Gateway()

        self.hue = HueService(self.config.hue, Secrets(), make_gateway, pair)
        self.factory = FakeTransportFactory(
            DeviceProfile(
                (CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),)
            )
        )
        self.registry_kinds: list[str] = []

        def make_runtime(settings: AppConfig) -> BleRuntime:
            return BleRuntime(
                settings,
                self.factory,
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
            registry.register(self.hue)
            return OutboxDispatcher(
                RuleEngine(settings),
                SQLiteOutbox(tmp_path / "state.sqlite3"),
                registry,
                backoff_base_seconds=0.01,
                backoff_cap_seconds=0.02,
            )

        self.cubes = CubeService(
            self.config, make_runtime, make_dispatcher, poll_interval_seconds=0.005
        )
        sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(FakeSenseHat()),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.settings = SettingsManager(self.path, self.config, sense, self.hue, self.cubes)
        self.backend = WebBackend(self.settings, sense, TOKEN, self.cubes, self.hue)

    def client(self) -> TestClient:
        return TestClient(create_app(self.backend, manage_service=True))

    def light_transport(self):  # type: ignore[no-untyped-def]
        return next(t for address, _, t in self.factory.created if address == LIGHT)


@dataclass(frozen=True)
class _Scene:
    id: str
    name: str


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _wait_sync(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    asyncio.run(_until(predicate, timeout))


def _ready(rig: _Rig) -> None:
    _wait_sync(
        lambda: (
            len(rig.factory.created) == 2
            and all(s.state is CubeConnectionState.WAITING_FOR_BASELINE for s in rig.cubes.statuses)
        )
    )


def test_new_mapping_applies_to_running_cubes_without_reconnecting(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, [_mapping("Licht", 4, _scene_action("scene-abend"))])

    async def scenario() -> None:
        await rig.hue.start()
        await rig.cubes.start()
        await _until(
            lambda: (
                len(rig.factory.created) == 2
                and all(
                    s.state is CubeConnectionState.WAITING_FOR_BASELINE for s in rig.cubes.statuses
                )
            )
        )
        transport = rig.light_transport()
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await _until(lambda: rig.recalled == ["scene-abend"])

        await rig.settings.apply_mappings([_mapping("Licht", 5, _scene_action("scene-hell"))])
        assert len(rig.factory.created) == 2  # nobody reconnected
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x05")
        await _until(lambda: rig.recalled == ["scene-abend", "scene-hell"])
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")  # removed face
        await asyncio.sleep(0.1)
        assert rig.recalled == ["scene-abend", "scene-hell"]
        await rig.cubes.stop()

    asyncio.run(scenario())
    assert [m.face for m in load_config(rig.path).mappings] == [5]


def test_unbuilt_pipeline_is_started_again_when_mappings_change(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    calls = {"n": 0}
    original = rig.cubes._dispatcher_factory

    def flaky(settings: AppConfig) -> OutboxDispatcher:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PluginError("missing plugin")
        return original(settings)

    rig.cubes._dispatcher_factory = flaky

    async def scenario() -> None:
        await rig.cubes.start()
        assert not rig.cubes.running
        assert rig.cubes.error_code == "CH-PLUG-001"
        await rig.settings.apply_mappings([_mapping("Licht", 1, _scene_action("scene-abend"))])
        assert rig.cubes.running
        assert rig.cubes.error is None
        await rig.cubes.stop()

    asyncio.run(scenario())


def test_save_failure_restores_the_running_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octapihub.application.settings as settings_module

    rig = _Rig(tmp_path, [_mapping("Licht", 4, _scene_action("scene-abend"))])

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ConfigurationError("disk full")

    async def scenario() -> None:
        await rig.hue.start()
        await rig.cubes.start()
        await _until(
            lambda: len(rig.factory.created) == 2 and all(s.connected for s in rig.cubes.statuses)
        )
        monkeypatch.setattr(settings_module, "save_config", broken)
        with pytest.raises(ConfigurationError):
            await rig.settings.apply_mappings([_mapping("Licht", 5, _scene_action("scene-hell"))])
        monkeypatch.undo()
        assert rig.settings.config.mappings == rig.config.mappings
        transport = rig.light_transport()
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await _until(lambda: rig.recalled == ["scene-abend"])  # old rule still active
        await rig.cubes.stop()

    asyncio.run(scenario())
    assert [m.face for m in load_config(rig.path).mappings] == [4]


def test_mapping_with_missing_plugin_is_rejected_and_file_unchanged(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")

    async def scenario() -> None:
        await rig.cubes.start()
        with pytest.raises(PluginError):
            await rig.settings.apply_mappings(
                [_mapping("Licht", 1, {"kind": "audio", "action": "play", "parameters": {}})]
            )
        await rig.cubes.stop()

    asyncio.run(scenario())
    assert rig.path.read_text(encoding="utf-8") == before
    assert rig.settings.config.mappings == ()


def test_invalid_mapping_changes_nothing(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with pytest.raises(ValidationError):
        asyncio.run(rig.settings.apply_mappings([_mapping("Licht", 12, _scene_action("a"))]))
    assert rig.path.read_text(encoding="utf-8") == before


# ---------- form logic ----------


SCENES = {"scene-abend", "scene-hell"}


def test_form_round_trip_keeps_mappings_identical(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        [
            _mapping("Licht", 3, _scene_action("scene-abend")),
            _mapping("Musik", 8, _scene_action("scene-hell")),
        ],
    )
    result = forms.parse_mapping_form(forms.mapping_config_to_form(config), config, SCENES)
    assert result.errors == {}
    assert result.mappings is not None
    assert with_mappings_table(config, result.mappings) == config


def test_form_adds_changes_and_removes_faces(tmp_path: Path) -> None:
    config = _config(tmp_path, [_mapping("Licht", 3, _scene_action("scene-abend"))])
    values = forms.mapping_config_to_form(config)
    values["map.0.3"] = ""  # remove
    values["map.0.6"] = "scene-hell"  # add
    values["map.1.1"] = "scene-abend"  # other cube
    result = forms.parse_mapping_form(values, config, SCENES)
    assert result.mappings is not None
    rebuilt = with_mappings_table(config, result.mappings)
    assert [(m.cube, m.face) for m in rebuilt.mappings] == [("Licht", 6), ("Musik", 1)]


def test_faces_with_other_actions_are_locked_and_preserved(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        [
            _mapping("Licht", 2, _scene_action("a"), _scene_action("b")),
            _mapping("Licht", 3, {"kind": "audio", "action": "play", "parameters": {"x": 1}}),
        ],
    )
    values = forms.mapping_config_to_form(config)
    assert "map.0.2" not in values
    assert "map.0.3" not in values
    # even a forged value for a locked face cannot overwrite it
    values["map.0.2"] = "scene-hell"
    result = forms.parse_mapping_form(values, config, SCENES)
    assert result.mappings is not None
    rebuilt = with_mappings_table(config, result.mappings)
    assert rebuilt.mappings == config.mappings


def test_new_scene_must_come_from_the_loaded_list(tmp_path: Path) -> None:
    config = _config(tmp_path, [_mapping("Licht", 3, _scene_action("legacy-id"))])
    values = forms.mapping_config_to_form(config)
    # an existing scene stays valid although it is not in the loaded list
    assert forms.parse_mapping_form(values, config, SCENES).errors == {}
    values["map.0.5"] = "made-up"
    errors = forms.parse_mapping_form(values, config, SCENES).errors
    assert errors == {"map.0.5": "Diese Szene gibt es in der geladenen Liste nicht."}


def test_without_loaded_scenes_only_existing_choices_are_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path, [_mapping("Licht", 3, _scene_action("legacy-id"))])
    values = forms.mapping_config_to_form(config)
    assert forms.parse_mapping_form(values, config, set()).errors == {}
    values["map.1.2"] = "scene-abend"
    result = forms.parse_mapping_form(values, config, set())
    assert "Licht" in result.errors["map.1.2"]
    assert result.mappings is None


@pytest.mark.parametrize("value", ["x" * 201, "bad\x00id", "new\nline"])
def test_malformed_scene_values_are_rejected(tmp_path: Path, value: str) -> None:
    config = _config(tmp_path)
    values = forms.mapping_config_to_form(config)
    values["map.0.1"] = value
    assert forms.parse_mapping_form(values, config, {value}).errors == {
        "map.0.1": "Diese Auswahl ist ungültig."
    }


def test_unknown_and_out_of_range_fields_are_ignored(tmp_path: Path) -> None:
    config = _config(tmp_path)
    values = forms.mapping_config_to_form(config)
    values.update({"map.0.9": "scene-abend", "map.7.1": "scene-abend", "evil": "x"})
    result = forms.parse_mapping_form(values, config, SCENES)
    assert result.mappings == []


# ---------- web page ----------


def _post(client: TestClient, fields: dict[str, str], token: str | None = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token is not None:
        body["token"] = token
    return client.post(
        "/cubes/mapping",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def _loaded_client(rig: _Rig) -> TestClient:
    client = rig.client()
    client.__enter__()
    _wait_sync(lambda: rig.cubes.running)
    asyncio.run(_load_scenes(rig))
    return client


async def _load_scenes(rig: _Rig) -> None:
    """Fill the scene cache directly (the Licht page does this through the bridge)."""
    rig.hue._scenes = tuple(HueScene(s.id, s.name) for s in rig.scenes)


def test_page_shows_a_tile_per_face_with_scene_choices_escaped(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, [_mapping("Licht", 3, _scene_action("scene-abend"))])
    client = _loaded_client(rig)
    try:
        text = client.get("/cubes").text
        assert text.count('class="face-tile') >= 16  # 8 faces x 2 cubes
        assert 'name="map.0.3"' in text
        assert "Seite 8" in text
        assert '<option value="scene-abend" selected>Abend</option>' in text
        assert "&lt;i&gt;Hell&lt;/i&gt;" in text
        assert "<i>Hell</i>" not in text
        assert "Es sind noch keine Hue-Szenen geladen" not in text
    finally:
        client.__exit__(None, None, None)


def test_page_without_scenes_explains_what_to_do(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, [_mapping("Licht", 3, _scene_action("old-id"))])
    with rig.client() as client:
        text = client.get("/cubes").text
        assert "noch keine Hue-Szenen geladen" in text
        assert 'href="/light"' in text
        assert "old-id (nicht in der Liste)" in text  # existing choice is not lost


def test_saving_through_the_page_persists_and_applies(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    client = _loaded_client(rig)
    try:
        values = forms.mapping_config_to_form(rig.settings.config)
        values["map.0.2"] = "scene-hell"
        response = _post(client, values)
        assert response.status_code == 200
        assert "Zuordnung gespeichert" in response.text
        assert '<option value="scene-hell" selected>' in response.text
    finally:
        client.__exit__(None, None, None)
    saved = load_config(rig.path).mappings
    assert [(m.cube, m.face) for m in saved] == [("Licht", 2)]
    assert saved[0].actions[0].parameters == {"scene_id": "scene-hell"}


def test_page_save_errors_are_shown_per_face_and_change_nothing(tmp_path: Path) -> None:
    rig = _Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    client = _loaded_client(rig)
    try:
        values = forms.mapping_config_to_form(rig.settings.config)
        values["map.1.4"] = "not-a-scene"
        response = _post(client, values)
        assert response.status_code == 422
        assert 'id="err-map.1.4"' in response.text
        assert "Es wurde nichts gespeichert" in response.text
        assert rig.path.read_text(encoding="utf-8") == before
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("token", [None, "", "wrong-zzz"])
def test_wrong_token_blocks_mapping_changes(tmp_path: Path, token: str | None) -> None:
    rig = _Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    client = _loaded_client(rig)
    try:
        values = forms.mapping_config_to_form(rig.settings.config)
        values["map.0.1"] = "scene-abend"
        response = _post(client, values, token=token)
        assert response.status_code == 403
        assert "wrong-zzz" not in response.text
        assert rig.path.read_text(encoding="utf-8") == before
    finally:
        client.__exit__(None, None, None)


def test_locked_face_is_shown_read_only(tmp_path: Path) -> None:
    rig = _Rig(
        tmp_path,
        [_mapping("Licht", 2, _scene_action("a"), _scene_action("b"))],
    )
    with rig.client() as client:
        text = client.get("/cubes").text
        assert "nur in der Konfigurationsdatei änderbar" in text
        assert 'name="map.0.2"' not in text


def test_three_cubes_get_three_editors_and_independent_mappings(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, [_mapping("Extra", 6, _scene_action("scene-abend"))], cube_count=3)
    client = _loaded_client(rig)
    try:
        text = client.get("/cubes").text
        assert (
            text.count("face-tile-head") == 32
        )  # 3 cubes x (8 faces + holder) + 5 joystick inputs
        assert "Universal" in text
        assert 'name="map.2.6"' in text
        values = forms.mapping_config_to_form(rig.settings.config)
        assert values["map.2.6"] == "scene-abend"
        values["map.0.1"] = "scene-hell"
        values["map.2.6"] = ""
        response = _post(client, values)
        assert response.status_code == 200
    finally:
        client.__exit__(None, None, None)
    saved = load_config(rig.path).mappings
    assert [(m.cube, m.face) for m in saved] == [("Licht", 1)]
    assert len(rig.cubes.statuses) in {0, 3}
