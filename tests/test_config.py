from __future__ import annotations

from pathlib import Path

import pytest

from octapihub.core.config import (
    DEFAULT_DEBOUNCE_MS,
    AppConfig,
    CubeRole,
    load_config,
    parse_config,
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
    assert config.schema_version == 1
    assert config.bluetooth.adapter == "hci0"
    assert config.debounce_ms == DEFAULT_DEBOUNCE_MS
    assert config.cubes[0].address == "AA:BB:CC:DD:EE:01"
    assert config.cubes[0].role is CubeRole.LIGHT
    assert config.cubes[1].role is CubeRole.MUSIC


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
    data["schema_version"] = 2
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
