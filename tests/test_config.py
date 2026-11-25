from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import octapihub.core.config as config_module
from octapihub.core.config import (
    DEFAULT_DEBOUNCE_MS,
    DEFAULT_HISTORY_RETENTION_DAYS,
    DEFAULT_SENSOR_INTERVAL_SECONDS,
    AppConfig,
    CubeRole,
    HueConfig,
    SenseHatVersion,
    SensorPresentation,
    hue_table,
    load_config,
    parse_config,
    save_config,
    with_hue_table,
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
    assert config.schema_version == 4
    assert config.bluetooth.adapter == "hci0"
    assert config.debounce_ms == DEFAULT_DEBOUNCE_MS
    assert config.cubes[0].address == "AA:BB:CC:DD:EE:01"
    assert config.cubes[0].role is CubeRole.LIGHT
    assert config.cubes[1].role is CubeRole.MUSIC
    assert config.sense_hat.hardware_version is SenseHatVersion.AUTO
    assert config.sense_hat.sensors["temperature"].enabled
    assert config.sense_hat.sensors["humidity"].enabled
    assert config.sense_hat.sensors["pressure"].enabled
    assert not config.sense_hat.sensors["orientation"].enabled
    assert not config.sense_hat.sensors["color"].enabled
    assert config.sense_hat.sensors["temperature"].interval_seconds == (
        DEFAULT_SENSOR_INTERVAL_SECONDS
    )
    assert config.sense_hat.history.retention_days == DEFAULT_HISTORY_RETENTION_DAYS
    assert (
        config.sense_hat.sensors["temperature"].presentation is SensorPresentation.VALUE_AND_CHART
    )


def test_schema_v1_is_loaded_as_current_config_version() -> None:
    config = parse_config(valid_config())

    assert config.schema_version == 4
    assert config.mappings == ()


def test_saving_schema_v1_upgrades_it_to_current_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_config(config_path, parse_config(valid_config()))

    saved = config_path.read_text(encoding="utf-8")
    assert load_config(config_path).schema_version == 4
    assert saved.startswith("schema_version = 4\n")
    assert "[sense_hat.sensors.temperature]" in saved


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

    assert config.schema_version == 4
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
        "schema_version = 4\ndebounce_ms = 300\n"
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
    data["schema_version"] = 5
    with pytest.raises(ConfigurationError, match="Unsupported configuration schema"):
        parse_config(data)

    data = valid_config()
    data["web"] = {"port": 8080}
    with pytest.raises(ValidationError, match="unsupported fields"):
        parse_config(data)


def test_schema_v3_parses_and_round_trips_sense_hat_settings(tmp_path: Path) -> None:
    data = valid_config()
    data["schema_version"] = 3
    data["sense_hat"] = {
        "hardware_version": "v2",
        "temperature_correction_factor": 0.95,
        "temperature_offset_c": -2.0,
        "color_gain": 16,
        "color_integration_cycles": 64,
        "sensors": {
            "temperature": {
                "enabled": True,
                "interval_seconds": 15,
                "history_enabled": False,
                "presentation": "value_and_chart",
                "precision": 2,
            },
            "orientation": {"enabled": True, "presentation": "chart"},
        },
        "history": {"retention_days": 14},
        "matrix": {"rotation": 90, "ambient_brightness": True},
        "joystick": {"enabled": False},
    }

    config = parse_config(data)
    sense_hat = config.sense_hat

    assert sense_hat.hardware_version is SenseHatVersion.V2
    assert sense_hat.temperature_correction_factor == 0.95
    assert sense_hat.temperature_offset_c == -2.0
    assert sense_hat.color_gain == 16
    assert sense_hat.color_integration_cycles == 64
    assert sense_hat.sensors["temperature"].interval_seconds == 15
    assert not sense_hat.sensors["temperature"].history_enabled
    assert sense_hat.sensors["temperature"].presentation is SensorPresentation.VALUE_AND_CHART
    assert sense_hat.sensors["orientation"].enabled
    assert sense_hat.sensors["orientation"].presentation is SensorPresentation.CHART
    assert sense_hat.history.retention_days == 14
    assert sense_hat.matrix.rotation == 90
    assert sense_hat.matrix.ambient_brightness
    assert not sense_hat.joystick_enabled

    path = tmp_path / "config.toml"
    save_config(path, config)
    assert load_config(path) == config


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("sensors", "interval_seconds", 0),
        ("sensors", "precision", 7),
        ("sensors", "presentation", "graph"),
        ("history", "retention_days", 0),
        ("matrix", "rotation", 45),
    ],
)
def test_schema_v3_rejects_invalid_sense_hat_settings(
    section: str,
    field: str,
    value: object,
) -> None:
    sense_hat: dict[str, object] = {}
    data = valid_config()
    data["schema_version"] = 3
    data["sense_hat"] = sense_hat
    if section == "sensors":
        sense_hat["sensors"] = {"temperature": {field: value}}
    else:
        sense_hat[section] = {field: value}

    with pytest.raises(ValidationError):
        parse_config(data)


def test_schema_v2_migrates_mappings_and_defaults_sense_hat_settings() -> None:
    data = valid_config()
    data["schema_version"] = 2
    data["mappings"] = []

    config = parse_config(data)

    assert config.schema_version == 4
    assert config.mappings == ()
    assert config.sense_hat.sensors["temperature"].enabled
    assert not config.sense_hat.sensors["color"].enabled


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


def test_hue_defaults_to_disabled_and_older_schemas_migrate() -> None:
    for version in (1, 2, 3):
        data = valid_config()
        data["schema_version"] = version
        config = parse_config(data)
        assert config.hue == HueConfig()
        assert not config.hue.enabled


def test_schema_v4_hue_round_trips_through_toml(tmp_path: Path) -> None:
    data = valid_config()
    data["schema_version"] = 4
    data["hue"] = {
        "enabled": True,
        "host": "192.168.1.20",
        "port": 8443,
        "ca_file": "/etc/octapihub/hue-ca.pem",
        "tls_server_name": "ecb5fafffe123456",
    }
    config = parse_config(data)
    path = tmp_path / "config.toml"
    save_config(path, config)
    assert load_config(path) == config
    assert config.hue.host == "192.168.1.20"
    assert hue_table(config.hue)["tls_server_name"] == "ecb5fafffe123456"


def test_hue_table_replaces_only_hue_settings() -> None:
    config = parse_config(valid_config())
    updated = with_hue_table(
        config,
        {"enabled": True, "host": "hue.local", "port": 443, "ca_file": "", "tls_server_name": ""},
    )
    assert updated.hue.host == "hue.local"
    assert updated.cubes == config.cubes
    assert updated.sense_hat == config.sense_hat


@pytest.mark.parametrize(
    ("table", "field"),
    [
        ({"enabled": True}, "hue.host"),
        ({"enabled": True, "host": " "}, "hue.host"),
        ({"host": "bad host"}, "hue.host"),
        ({"host": "http://192.168.1.2"}, "hue.host"),
        ({"host": "192.168.1.2", "port": 0}, "hue.port"),
        ({"host": "192.168.1.2", "port": 70000}, "hue.port"),
        ({"host": "192.168.1.2", "port": True}, "hue.port"),
        ({"host": "192.168.1.2", "port": "443"}, "hue.port"),
        ({"enabled": "yes", "host": "192.168.1.2"}, "hue.enabled"),
        ({"host": "192.168.1.2", "tls_server_name": "no spaces"}, "hue.tls_server_name"),
        ({"host": "192.168.1.2", "ca_file": "a\nb"}, "hue.ca_file"),
        ({"host": "192.168.1.2", "ca_file": 5}, "hue.ca_file"),
        ({"host": 5}, "hue.host"),
        ({"host": "192.168.1.2", "application_key": "secret"}, "hue"),
    ],
)
def test_invalid_hue_settings_are_rejected_with_field(table: dict[str, object], field: str) -> None:
    data = valid_config()
    data["schema_version"] = 4
    data["hue"] = table
    with pytest.raises(ValidationError) as error:
        parse_config(data)
    assert error.value.details["field"] == field


def test_application_key_is_never_accepted_in_the_config_file() -> None:
    data = valid_config()
    data["schema_version"] = 4
    data["hue"] = {"host": "192.168.1.2", "application_key": "abc"}
    with pytest.raises(ValidationError, match="unsupported fields"):
        parse_config(data)
