from __future__ import annotations

from pathlib import Path

import pytest

from octapihub.core.config import (
    DEFAULT_MATRIX_VIEWS,
    MATRIX_VIEW_IDS,
    CubeRole,
    load_config,
    parse_config,
    save_config,
    sense_hat_table,
    with_sense_hat_table,
)
from octapihub.core.errors import ValidationError


def _config(extra_matrix: dict[str, object] | None = None, cubes: int = 2):  # type: ignore[no-untyped-def]
    roles = ["light", "music", "general"]
    return parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": f"Cube {i}", "address": f"AA:BB:CC:DD:EE:0{i}", "role": roles[i - 1]}
                for i in range(1, cubes + 1)
            ],
            "sense_hat": {"matrix": extra_matrix or {}},
        }
    )


def test_defaults_are_conservative_and_all_defaults_are_known_views() -> None:
    matrix = _config().sense_hat.matrix
    assert matrix.views == DEFAULT_MATRIX_VIEWS
    assert set(DEFAULT_MATRIX_VIEWS) <= set(MATRIX_VIEW_IDS)
    assert matrix.default_view == "overview"
    assert matrix.brightness == 60
    assert matrix.auto_cycle_seconds == 0
    assert not matrix.night_enabled
    assert len(set(MATRIX_VIEW_IDS)) == len(MATRIX_VIEW_IDS)


def test_matrix_settings_round_trip_through_toml(tmp_path: Path) -> None:
    config = _config(
        {
            "brightness": 35,
            "default_view": "clock",
            "views": ["clock", "battery", "lamp", "rainbow", "off"],
            "auto_cycle_seconds": 20,
            "night_enabled": True,
            "night_start": "21:30",
            "night_end": "06:15",
            "night_mode": "dim",
        }
    )
    path = tmp_path / "c.toml"
    save_config(path, config)
    reloaded = load_config(path)
    assert reloaded == config
    assert reloaded.sense_hat.matrix.views == ("clock", "battery", "lamp", "rainbow", "off")


def test_three_cubes_with_general_role_work() -> None:
    config = _config(cubes=3)
    assert [cube.role for cube in config.cubes] == [
        CubeRole.LIGHT,
        CubeRole.MUSIC,
        CubeRole.GENERAL,
    ]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"brightness": 4}, "brightness"),
        ({"brightness": 101}, "brightness"),
        ({"brightness": True}, "brightness"),
        ({"brightness": "60"}, "brightness"),
        ({"auto_cycle_seconds": -1}, "auto_cycle_seconds"),
        ({"auto_cycle_seconds": 601}, "auto_cycle_seconds"),
        ({"views": []}, "views"),
        ({"views": ["nonexistent"]}, "views"),
        ({"views": ["clock", "clock"]}, "views"),
        ({"views": "clock"}, "views"),
        ({"views": [1]}, "views"),
        ({"views": ["clock"], "default_view": "overview"}, "default_view"),
        ({"default_view": 5}, "default_view"),
        ({"night_start": "24:00"}, "night_start"),
        ({"night_start": "9:00"}, "night_start"),
        ({"night_end": "07:60"}, "night_end"),
        ({"night_end": 7}, "night_end"),
        ({"night_mode": "blink"}, "night_mode"),
        ({"night_enabled": "yes"}, "night_enabled"),
        ({"unknown_option": 1}, "matrix"),
    ],
)
def test_invalid_matrix_settings_name_the_field(change: dict[str, object], field: str) -> None:
    with pytest.raises(ValidationError) as error:
        _config(change)
    assert str(error.value.details["field"]).endswith(field)


def test_editing_through_the_table_helper_keeps_other_settings() -> None:
    config = _config()
    table = sense_hat_table(config.sense_hat)
    matrix = table["matrix"]
    assert isinstance(matrix, dict)
    matrix["brightness"] = 80
    updated = with_sense_hat_table(config, table)
    assert updated.sense_hat.matrix.brightness == 80
    assert updated.sense_hat.matrix.views == config.sense_hat.matrix.views
    assert updated.sense_hat.sensors == config.sense_hat.sensors
