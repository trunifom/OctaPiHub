from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime, BleRuntimeTuning, CubeConnectionState
from octapihub.application.cube_service import CubeService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import (
    MAX_MUSIC_TARGETS,
    MAX_PLAYLISTS,
    AppConfig,
    MusicConfig,
    MusicTargetKind,
    RepeatMode,
    load_config,
    music_table,
    parse_config,
    save_config,
    with_music_table,
)
from octapihub.core.errors import ValidationError
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox


def base(music: object = None, **root: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": 6,
        "bluetooth": {"adapter": "hci0"},
        "cubes": [
            {"name": "Musik", "address": "AA:BB:CC:DD:EE:01", "role": "music"},
            {"name": "Licht", "address": "AA:BB:CC:DD:EE:02", "role": "light"},
        ],
    }
    if music is not None:
        data["music"] = music
    data.update(root)
    return data


FULL: dict[str, object] = {
    "enabled": True,
    "library_dir": "/home/pi/Music",
    "media_host": "192.168.1.50",
    "targets": [
        {
            "id": "wohnzimmer",
            "name": "Wohnzimmer (Nest Mini)",
            "kind": "cast",
            "device": "Wohnzimmer",
            "volume": 35,
        },
        {"id": "pi", "name": "Raspberry Pi", "kind": "local", "device": "", "volume": 60},
        {
            "id": "box",
            "name": "Bluetooth-Box",
            "kind": "bluetooth",
            "device": "pulse/bluez_output.AA_BB_CC_DD_EE_FF.1",
            "bluetooth_address": "aa:bb:cc:dd:ee:ff",
        },
    ],
    "playlists": [
        {
            "id": "morgen",
            "name": "Morgenmusik",
            "source": "https://example.org/radio.mp3",
            "target": "wohnzimmer",
            "repeat": "all",
        },
        {
            "id": "ordner",
            "name": "Ordner",
            "source": "/home/pi/Music/Jazz",
            "shuffle": True,
            "volume": 25,
        },
    ],
}


def test_defaults_are_off_and_empty() -> None:
    config = parse_config(base())
    assert config.music == MusicConfig()
    assert not config.music.enabled
    assert config.schema_version == 6


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_older_schemas_migrate_with_default_music(version: int) -> None:
    data = base(schema_version=version)
    config = parse_config(data)
    assert config.music == MusicConfig()
    assert config.schema_version == 6


def test_music_section_is_not_allowed_before_schema_v6() -> None:
    with pytest.raises(ValidationError, match="unsupported fields"):
        parse_config(base(FULL, schema_version=5))


def test_full_music_configuration_round_trips_through_toml(tmp_path: Path) -> None:
    config = parse_config(base(FULL))
    music = config.music
    assert [t.kind for t in music.targets] == [
        MusicTargetKind.CAST,
        MusicTargetKind.LOCAL,
        MusicTargetKind.BLUETOOTH,
    ]
    assert music.targets[2].bluetooth_address == "AA:BB:CC:DD:EE:FF"  # normalized
    assert music.playlists[0].repeat is RepeatMode.ALL
    assert music.playlists[1].shuffle and music.playlists[1].volume == 25
    assert music.playlists[0].volume is None
    path = tmp_path / "c.toml"
    save_config(path, config)
    assert load_config(path) == config
    assert with_music_table(config, music_table(config.music)) == config


def test_special_characters_survive_the_round_trip(tmp_path: Path) -> None:
    music = {
        "enabled": True,
        "targets": [{"id": "k", "name": 'Küche "Hub" \\ test', "kind": "cast", "device": "Küche"}],
        "playlists": [{"id": "p", "name": "Ä&Ö", "source": "/home/pi/Music/Café Del Mar/Alben"}],
    }
    config = parse_config(base(music))
    path = tmp_path / "c.toml"
    save_config(path, config)
    assert load_config(path) == config


@pytest.mark.parametrize(
    ("music", "field"),
    [
        ({"unknown": 1}, "music"),
        ({"enabled": "yes"}, "music.enabled"),
        ({"library_dir": "a\nb"}, "music.library_dir"),
        ({"library_dir": 5}, "music.library_dir"),
        ({"media_host": "bad host"}, "music.media_host"),
        ({"targets": "x"}, "music.targets"),
        ({"playlists": "x"}, "music.playlists"),
        ({"targets": [{"id": "A", "name": "x", "kind": "local"}]}, "music.targets[0].id"),
        ({"targets": [{"id": "1a", "name": "x", "kind": "local"}]}, "music.targets[0].id"),
        ({"targets": [{"id": "", "name": "x", "kind": "local"}]}, "music.targets[0].id"),
        ({"targets": [{"id": "ok", "name": "", "kind": "local"}]}, "music.targets[0].name"),
        ({"targets": [{"id": "ok", "name": "x" * 61, "kind": "local"}]}, "music.targets[0].name"),
        ({"targets": [{"id": "ok", "name": "x", "kind": "radio"}]}, "music.targets[0].kind"),
        ({"targets": [{"id": "ok", "name": "x", "kind": "cast"}]}, "music.targets[0].device"),
        (
            {
                "targets": [
                    {"id": "ok", "name": "x", "kind": "bluetooth", "bluetooth_address": "nope"}
                ]
            },
            "music.targets[0].bluetooth_address",
        ),
        (
            {"targets": [{"id": "ok", "name": "x", "kind": "local", "volume": 101}]},
            "music.targets[0].volume",
        ),
        (
            {"targets": [{"id": "ok", "name": "x", "kind": "local", "volume": -1}]},
            "music.targets[0].volume",
        ),
        (
            {"targets": [{"id": "ok", "name": "x", "kind": "local", "volume": True}]},
            "music.targets[0].volume",
        ),
        ({"targets": [{"id": "ok", "name": "x", "kind": "local", "extra": 1}]}, "music.targets[0]"),
        ({"playlists": [{"id": "p", "name": "x", "source": ""}]}, "music.playlists[0].source"),
        ({"playlists": [{"id": "p", "name": "x", "source": "a\nb"}]}, "music.playlists[0].source"),
        (
            {"playlists": [{"id": "p", "name": "x", "source": "s" * 2001}]},
            "music.playlists[0].source",
        ),
        (
            {"playlists": [{"id": "p", "name": "x", "source": "s", "target": "ghost"}]},
            "music.playlists[0].target",
        ),
        (
            {"playlists": [{"id": "p", "name": "x", "source": "s", "repeat": "forever"}]},
            "music.playlists[0].repeat",
        ),
        (
            {"playlists": [{"id": "p", "name": "x", "source": "s", "shuffle": 1}]},
            "music.playlists[0].shuffle",
        ),
        (
            {"playlists": [{"id": "p", "name": "x", "source": "s", "volume": 200}]},
            "music.playlists[0].volume",
        ),
        ({"playlists": [{"id": "BAD", "name": "x", "source": "s"}]}, "music.playlists[0].id"),
    ],
)
def test_invalid_music_settings_name_the_field(music: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError) as error:
        parse_config(base(music))
    assert error.value.details["field"] == field


def test_ids_must_be_unique_and_counts_are_limited() -> None:
    target = {"id": "same", "name": "x", "kind": "local"}
    with pytest.raises(ValidationError, match="target ids must be unique"):
        parse_config(base({"targets": [target, dict(target, name="y")]}))
    playlist = {"id": "same", "name": "x", "source": "s"}
    with pytest.raises(ValidationError, match="Playlist ids must be unique"):
        parse_config(base({"playlists": [playlist, dict(playlist, name="y")]}))
    many_targets = [
        {"id": f"t{i}", "name": "x", "kind": "local"} for i in range(MAX_MUSIC_TARGETS + 1)
    ]
    with pytest.raises(ValidationError):
        parse_config(base({"targets": many_targets}))
    many_lists = [{"id": f"p{i}", "name": "x", "source": "s"} for i in range(MAX_PLAYLISTS + 1)]
    with pytest.raises(ValidationError):
        parse_config(base({"playlists": many_lists}))
    at_limit = [{"id": f"t{i}", "name": "x", "kind": "local"} for i in range(MAX_MUSIC_TARGETS)]
    assert len(parse_config(base({"targets": at_limit})).music.targets) == MAX_MUSIC_TARGETS


def test_invalid_table_changes_nothing_when_editing() -> None:
    config = parse_config(base(FULL))
    table = music_table(config.music)
    table["targets"] = [{"id": "x", "name": "x", "kind": "weird"}]
    with pytest.raises(ValidationError):
        with_music_table(config, table)
    assert config.music.targets[0].id == "wohnzimmer"


# ---------- holder as face 0 ----------


def mappings(face: int) -> list[dict[str, object]]:
    return [
        {
            "cube": "Musik",
            "face": face,
            "actions": [{"kind": "fake", "action": "stop", "parameters": {}}],
        }
    ]


def test_holder_is_face_zero_for_cubes_but_not_for_the_joystick() -> None:
    config = parse_config(base(mappings=mappings(0)))
    assert config.mappings[0].face == 0
    assert parse_config(base(mappings=mappings(8))).mappings[0].face == 8
    for bad in (-1, 9):
        with pytest.raises(ValidationError):
            parse_config(base(mappings=mappings(bad)))
    joystick = [
        {
            "cube": "Joystick",
            "face": 0,
            "actions": [{"kind": "fake", "action": "x", "parameters": {}}],
        }
    ]
    with pytest.raises(ValidationError, match="from 1 to 5"):
        parse_config(base(mappings=joystick))


class Recorder:
    kind = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.calls.append(action)


def _pipeline(
    tmp_path: Path, config: AppConfig, plugin: Recorder
) -> tuple[CubeService, FakeTransportFactory]:
    factory = FakeTransportFactory(
        DeviceProfile((CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),))
    )

    def runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            tuning=BleRuntimeTuning(health_poll_seconds=0.01, debounce_ms=0),
        )

    def dispatcher(settings: AppConfig) -> OutboxDispatcher:
        registry = PluginRegistry()
        registry.register(plugin)
        return OutboxDispatcher(RuleEngine(settings), SQLiteOutbox(tmp_path / "o.db"), registry)

    return CubeService(config, runtime, dispatcher, poll_interval_seconds=0.005), factory


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_putting_the_cube_in_its_holder_runs_the_face_zero_actions(tmp_path: Path) -> None:
    config = parse_config(base(mappings=mappings(0)))
    plugin = Recorder()
    service, factory = _pipeline(tmp_path, config, plugin)

    async def scenario() -> None:
        await service.start()
        await until(
            lambda: (
                service.statuses
                and service.statuses[0].state is CubeConnectionState.WAITING_FOR_BASELINE
            )
        )
        transport = next(t for a, _, t in factory.created if a == "AA:BB:CC:DD:EE:01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x03")  # baseline: face 3
        transport.emit_notification(
            ORIENTATION_CHARACTERISTIC_UUID, b"\x00"
        )  # lifted into the holder
        await until(lambda: plugin.calls == ["stop"])
        transport.emit_notification(
            ORIENTATION_CHARACTERISTIC_UUID, b"\x0a"
        )  # holder, other raw value
        await asyncio.sleep(0.1)
        assert plugin.calls == ["stop"]  # still neutral: no duplicate
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x02")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x00")
        await until(lambda: plugin.calls == ["stop", "stop"])
        await service.stop()

    asyncio.run(scenario())


def test_a_cube_that_connects_while_resting_in_the_holder_does_nothing(tmp_path: Path) -> None:
    config = parse_config(base(mappings=mappings(0)))
    plugin = Recorder()
    service, factory = _pipeline(tmp_path, config, plugin)

    async def scenario() -> None:
        await service.start()
        await until(
            lambda: (
                service.statuses
                and service.statuses[0].state is CubeConnectionState.WAITING_FOR_BASELINE
            )
        )
        transport = next(t for a, _, t in factory.created if a == "AA:BB:CC:DD:EE:01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x00")  # baseline in holder
        await asyncio.sleep(0.15)
        assert plugin.calls == []
        await service.stop()

    asyncio.run(scenario())


def test_rule_engine_holder_lookup() -> None:
    engine = RuleEngine(parse_config(base(mappings=mappings(0))))
    assert engine.actions_for("musik", 0)[0].action == "stop"
    assert engine.actions_for("Musik", 1) == ()
