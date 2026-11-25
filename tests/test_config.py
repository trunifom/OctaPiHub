from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import octapihub.core.config as config_module
from octapihub.core.config import (
    DEFAULT_DEBOUNCE_MS,
    AppConfig,
    CubeRole,
    load_config,
    parse_config,
    save_config,
)
from octapihub.core.errors import ConfigurationError, ValidationError


def valid_config() -> dict[str, object]:
    return {
        "schema_version": 1,
        "bluetooth": {"adapter": "hci0"},
        "cubes": [
            {"name": "Licht", "address": "aa:bb:cc:dd:ee:01", "role": "light"},
            {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
        ],
    }


def test_parse_config_applies_defaults_and_normalizes_addresses() -> None:
    config = parse_config(valid_config())

    assert isinstance(config, AppConfig)
    assert config.schema_version == 2
    assert config.bluetooth.adapter == "hci0"
    assert config.debounce_ms == DEFAULT_DEBOUNCE_MS
    assert config.cubes[0].address == "AA:BB:CC:DD:EE:01"
    assert config.cubes[0].role is CubeRole.LIGHT
    assert config.cubes[1].role is CubeRole.MUSIC


def test_schema_v1_is_loaded_as_current_config_version() -> None:
    config = parse_config(valid_config())

    assert config.schema_version == 2
    assert config.mappings == ()


def test_saving_schema_v1_upgrades_it_to_schema_v2(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(config_path, parse_config(valid_config()))

    assert load_config(config_path).schema_version == 2
    assert config_path.read_text(encoding="utf-8").startswith("schema_version = 2\n")


def test_schema_v1_rejects_v2_mapping_fields() -> None:
    data = valid_config()
    data["mappings"] = []

    with pytest.raises(ValidationError, match="unsupported fields"):
        parse_config(data)


def test_schema_v2_parses_and_freezes_action_mapping_parameters() -> None:
    data = valid_config()
    data["schema_version"] = 2
    data["mappings"] = [
        {
            "cube": "licht",
            "face": 3,
            "actions": [
                {
                    "kind": "hue",
                    "action": "scene.set",
                    "parameters": {
                        "scene_id": "id-with-\\-slash",
                        "transition": {"seconds": 0.5},
                        "labels": ["warm", "evening"],
                    },
                }
            ],
        }
    ]

    config = parse_config(data)

    assert config.schema_version == 2
    assert config.mappings[0].cube == "Licht"
    assert config.mappings[0].face == 3
    assert config.mappings[0].actions[0].parameters["scene_id"] == "id-with-\\-slash"
    with pytest.raises(TypeError):
        config.mappings[0].actions[0].parameters["scene_id"] = "mutated"  # type: ignore[index]


@pytest.mark.parametrize(
    ("change", "expected_message"),
    [
        ("unknown_cube", "unknown cube"),
        ("invalid_face", "face must be"),
        ("empty_actions", "at least one action"),
        ("invalid_kind", "plugin identifier"),
        ("duplicate_face", "mappings must be unique"),
        ("non_finite_parameter", "TOML-compatible"),
    ],
)
def test_rejects_invalid_rule_mappings(change: str, expected_message: str) -> None:
    data = valid_config()
    data["schema_version"] = 2
    data["mappings"] = [
        {
            "cube": "Licht",
            "face": 2,
            "actions": [{"kind": "hue", "action": "scene.set"}],
        }
    ]
    mappings = data["mappings"]
    assert isinstance(mappings, list)
    if change == "unknown_cube":
        mappings[0]["cube"] = "Unknown"
    elif change == "invalid_face":
        mappings[0]["face"] = True
    elif change == "empty_actions":
        mappings[0]["actions"] = []
    elif change == "invalid_kind":
        mappings[0]["actions"] = [{"kind": "Hue", "action": "scene.set"}]
    elif change == "duplicate_face":
        mappings.append(
            {
                "cube": "licht",
                "face": 2,
                "actions": [{"kind": "hue", "action": "scene.set"}],
            }
        )
    elif change == "non_finite_parameter":
        mappings[0]["actions"] = [
            {
                "kind": "hue",
                "action": "scene.set",
                "parameters": {"transition": float("nan")},
            }
        ]

    with pytest.raises(ValidationError, match=expected_message):
        parse_config(data)


def test_load_config_reads_supported_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
schema_version = 1
debounce_ms = 999

[bluetooth]
adapter = "hci1"

[[cubes]]
name = "Licht"
address = "AA:BB:CC:DD:EE:01"
role = "light"

[[cubes]]
name = "Musik"
address = "AA:BB:CC:DD:EE:02"
role = "music"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.debounce_ms == 999
    assert config.bluetooth.adapter == "hci1"


def test_save_config_atomically_round_trips_validated_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    data = valid_config()
    data["schema_version"] = 2
    data["mappings"] = [
        {
            "cube": "Licht",
            "face": 2,
            "actions": [
                {
                    "kind": "hue",
                    "action": "scene.set",
                    "parameters": {"scene_id": 'id "West"', "brightness": 0.75},
                },
                {"kind": "hue", "action": "light.toggle"},
            ],
        },
        {
            "cube": "Musik",
            "face": 3,
            "actions": [{"kind": "audio", "action": "play.start"}],
        },
    ]
    parsed = parse_config(data)
    cube_name = 'Licht "Küche" \\ Ost'
    config = replace(
        parsed,
        cubes=(replace(parsed.cubes[0], name=cube_name), parsed.cubes[1]),
        mappings=(replace(parsed.mappings[0], cube=cube_name), parsed.mappings[1]),
    )
    config_path.write_text("outdated configuration", encoding="utf-8")

    save_config(config_path, config)

    assert load_config(config_path) == config
    assert config_path.read_text(encoding="utf-8").startswith(
        "schema_version = 2\ndebounce_ms = 300\n"
    )
    assert list(tmp_path.glob(".config.toml.*.tmp")) == []


def test_save_config_rejects_invalid_config_without_changing_existing_file(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing configuration", encoding="utf-8")
    config = parse_config(valid_config())
    invalid_config = replace(
        config,
        cubes=(replace(config.cubes[0], role=CubeRole.MUSIC), config.cubes[1]),
    )

    with pytest.raises(ValidationError, match="exactly one light cube"):
        save_config(config_path, invalid_config)

    assert config_path.read_text(encoding="utf-8") == "existing configuration"


def test_save_config_replace_failure_preserves_existing_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("existing configuration", encoding="utf-8")

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        assert Path(source).parent == Path(destination).parent
        raise OSError("replacement denied")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)

    with pytest.raises(ConfigurationError, match="atomically write") as error:
        save_config(config_path, parse_config(valid_config()))

    assert isinstance(error.value.__cause__, OSError)
    assert config_path.read_text(encoding="utf-8") == "existing configuration"
    assert list(tmp_path.glob(".config.toml.*.tmp")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("debounce_ms", -1),
        ("debounce_ms", 1000),
        ("debounce_ms", True),
    ],
)
def test_rejects_invalid_debounce(field: str, value: object) -> None:
    data = valid_config()
    data[field] = value

    with pytest.raises(ValidationError):
        parse_config(data)


def test_rejects_missing_cube_or_duplicate_roles() -> None:
    data = valid_config()
    data["cubes"] = [{"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"}]

    with pytest.raises(ValidationError, match="exactly one light cube"):
        parse_config(data)

    data = valid_config()
    cubes = data["cubes"]
    assert isinstance(cubes, list)
    cubes[1] = {"name": "Zweitlicht", "address": "AA:BB:CC:DD:EE:02", "role": "light"}
    with pytest.raises(ValidationError, match="exactly one light cube"):
        parse_config(data)


@pytest.mark.parametrize(
    ("change", "expected_message"),
    [
        ("duplicate_address", "addresses must be unique"),
        ("duplicate_name", "names must be unique"),
        ("invalid_role", "role must be"),
        ("invalid_address", "Bluetooth MAC address"),
        ("unknown_field", "unsupported fields"),
        ("invalid_adapter", "BlueZ adapter name"),
    ],
)
def test_rejects_invalid_cube_and_adapter_settings(change: str, expected_message: str) -> None:
    data = valid_config()
    cubes = data["cubes"]
    assert isinstance(cubes, list)

    if change == "duplicate_address":
        cubes[1]["address"] = "aa:bb:cc:dd:ee:01"  # type: ignore[index]
    elif change == "duplicate_name":
        cubes[1]["name"] = "licht"  # type: ignore[index]
    elif change == "invalid_role":
        cubes[0]["role"] = "audio"  # type: ignore[index]
    elif change == "invalid_address":
        cubes[0]["address"] = "not-a-mac"  # type: ignore[index]
    elif change == "unknown_field":
        cubes[0]["extra"] = "ignored?"  # type: ignore[index]
    elif change == "invalid_adapter":
        bluetooth = data["bluetooth"]
        assert isinstance(bluetooth, dict)
        bluetooth["adapter"] = "wlan0"

    with pytest.raises(ValidationError, match=expected_message):
        parse_config(data)


def test_rejects_unsupported_schema_and_unknown_root_keys() -> None:
    data = valid_config()
    data["schema_version"] = 3
    with pytest.raises(ConfigurationError, match="Unsupported configuration schema"):
        parse_config(data)

    data = valid_config()
    data["web"] = {"port": 8080}
    with pytest.raises(ValidationError, match="unsupported fields"):
        parse_config(data)


def test_load_config_translates_file_and_toml_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as missing:
        load_config(tmp_path / "missing.toml")
    assert missing.value.__cause__ is not None
    assert missing.value.details["reason"]

    invalid_toml = tmp_path / "invalid.toml"
    invalid_toml.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not valid TOML") as invalid:
        load_config(invalid_toml)
    assert invalid.value.__cause__ is not None
