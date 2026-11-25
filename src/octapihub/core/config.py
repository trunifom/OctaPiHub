"""Versioned TOML configuration loading, validation, and atomic persistence."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from octapihub.core.errors import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DEFAULT_DEBOUNCE_MS = 300
MAX_DEBOUNCE_MS = 999
_ADAPTER_PATTERN = re.compile(r"^hci[0-9]+$")
_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ACTION_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class CubeRole(StrEnum):
    """Supported functional roles of the two configured cubes."""

    LIGHT = "light"
    MUSIC = "music"


@dataclass(frozen=True, slots=True)
class CubeConfig:
    """Validated configuration for one cube."""

    name: str
    address: str
    role: CubeRole


@dataclass(frozen=True, slots=True)
class RuleActionConfig:
    """One named plugin action with immutable TOML-compatible parameters."""

    kind: str
    action: str
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RuleMappingConfig:
    """Actions associated with one configured cube and stable face."""

    cube: str
    face: int
    actions: tuple[RuleActionConfig, ...]


@dataclass(frozen=True, slots=True)
class BluetoothConfig:
    """Bluetooth adapter selection."""

    adapter: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated, current-version configuration supported by OctaPiHub."""

    schema_version: int
    bluetooth: BluetoothConfig
    debounce_ms: int
    cubes: tuple[CubeConfig, CubeConfig]
    mappings: tuple[RuleMappingConfig, ...] = ()


def load_config(path: str | Path) -> AppConfig:
    """Read and validate a TOML configuration file.

    Raises:
        ConfigurationError: If the file cannot be read, is invalid TOML, or uses an
            unsupported schema version.
        ValidationError: If a value violates the supported configuration schema.
    """
    try:
        with Path(path).open("rb") as config_file:
            data = tomllib.load(config_file)
    except OSError as exc:
        raise ConfigurationError(
            "Could not read the configuration file",
            details={"reason": exc.strerror or type(exc).__name__},
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            "Configuration file is not valid TOML",
            details={"reason": str(exc)},
        ) from exc
    return parse_config(data)


def save_config(path: str | Path, config: AppConfig) -> None:
    """Validate and atomically persist configuration as UTF-8 TOML.

    The temporary file is created beside the destination, flushed to disk, and then
    atomically replaces it. A failure before replacement leaves the current file intact.
    """
    contents = _serialize_config(config)
    target = Path(path)
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as config_file:
            config_file.write(contents)
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.error(
                    "Could not remove temporary configuration file (%s)",
                    type(cleanup_error).__name__,
                )
        raise ConfigurationError(
            "Could not atomically write the configuration file",
            user_message="Die Einstellungen konnten nicht sicher gespeichert werden.",
            details={"reason": exc.strerror or type(exc).__name__},
        ) from exc


def _serialize_config(config: AppConfig) -> str:
    data: dict[str, object] = {
        "schema_version": config.schema_version,
        "bluetooth": {"adapter": config.bluetooth.adapter},
        "debounce_ms": config.debounce_ms,
        "cubes": [
            {"name": cube.name, "address": cube.address, "role": cube.role.value}
            for cube in config.cubes
        ],
        "mappings": [
            {
                "cube": mapping.cube,
                "face": mapping.face,
                "actions": [
                    {
                        "kind": action.kind,
                        "action": action.action,
                        "parameters": _thaw_parameter(action.parameters),
                    }
                    for action in mapping.actions
                ],
            }
            for mapping in config.mappings
        ],
    }
    validated = parse_config(data)
    lines = [
        f"schema_version = {validated.schema_version}",
        f"debounce_ms = {validated.debounce_ms}",
        "",
        "[bluetooth]",
        f"adapter = {_toml_string(validated.bluetooth.adapter)}",
    ]
    for cube in validated.cubes:
        lines.extend(
            [
                "",
                "[[cubes]]",
                f"name = {_toml_string(cube.name)}",
                f"address = {_toml_string(cube.address)}",
                f"role = {_toml_string(cube.role.value)}",
            ]
        )
    for mapping in validated.mappings:
        lines.extend(
            [
                "",
                "[[mappings]]",
                f"cube = {_toml_string(mapping.cube)}",
                f"face = {mapping.face}",
            ]
        )
        for action in mapping.actions:
            lines.extend(
                [
                    "",
                    "[[mappings.actions]]",
                    f"kind = {_toml_string(action.kind)}",
                    f"action = {_toml_string(action.action)}",
                    f"parameters = {_toml_value(action.parameters)}",
                ]
            )
    contents = "\n".join(lines) + "\n"
    try:
        round_tripped = parse_config(tomllib.loads(contents))
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(
            "Configuration values cannot be represented as valid TOML",
            details={"reason": str(exc)},
        ) from exc
    if round_tripped != validated:
        raise ValidationError("Serialized configuration does not round-trip exactly")
    return contents


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (tuple, list)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        entries = (f"{_toml_string(key)} = {_toml_value(value[key])}" for key in sorted(value))
        return "{ " + ", ".join(entries) + " }"
    raise ValidationError(
        "Configuration contains a value that cannot be represented in TOML",
        details={"value_type": type(value).__name__},
    )


def _freeze_parameter(value: object, field: str) -> object:
    if isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return tuple(_freeze_parameter(item, field) for item in value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValidationError(
                "Action parameter keys must be strings",
                details={"field": field},
            )
        return MappingProxyType(
            {key: _freeze_parameter(item, field) for key, item in value.items()}
        )
    raise ValidationError(
        "Action parameters must contain TOML-compatible values",
        details={"field": field, "value_type": type(value).__name__},
    )


def _thaw_parameter(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_parameter(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_parameter(item) for item in value]
    return value


def parse_config(data: object) -> AppConfig:
    """Validate a parsed TOML document and return immutable configuration values."""
    root = _require_table(data, "root")

    schema_version = root.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValidationError(
            "schema_version must be an integer", details={"field": "schema_version"}
        )
    if schema_version not in {LEGACY_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSION}:
        raise ConfigurationError(
            "Unsupported configuration schema version",
            details={
                "schema_version": schema_version,
                "supported_schema_version": SUPPORTED_SCHEMA_VERSION,
            },
        )
    allowed_root_keys = {"schema_version", "bluetooth", "debounce_ms", "cubes"}
    if schema_version == SUPPORTED_SCHEMA_VERSION:
        allowed_root_keys.add("mappings")
    _check_keys(root, allowed_root_keys, "root")

    bluetooth_table = _require_table(root.get("bluetooth"), "bluetooth")
    _check_keys(bluetooth_table, {"adapter"}, "bluetooth")
    adapter = _require_string(bluetooth_table.get("adapter"), "bluetooth.adapter")
    if not _ADAPTER_PATTERN.fullmatch(adapter):
        raise ValidationError(
            "bluetooth.adapter must be a BlueZ adapter name such as hci0",
            details={"field": "bluetooth.adapter"},
        )

    debounce_ms = root.get("debounce_ms", DEFAULT_DEBOUNCE_MS)
    if (
        isinstance(debounce_ms, bool)
        or not isinstance(debounce_ms, int)
        or not 0 <= debounce_ms <= MAX_DEBOUNCE_MS
    ):
        raise ValidationError(
            f"debounce_ms must be an integer from 0 to {MAX_DEBOUNCE_MS}",
            details={"field": "debounce_ms"},
        )

    raw_cubes = root.get("cubes")
    if not isinstance(raw_cubes, list) or len(raw_cubes) != 2:
        raise ValidationError(
            "cubes must contain exactly one light cube and one music cube",
            details={"field": "cubes"},
        )
    cubes = tuple(_parse_cube(item, index) for index, item in enumerate(raw_cubes))
    if {cube.role for cube in cubes} != {CubeRole.LIGHT, CubeRole.MUSIC}:
        raise ValidationError(
            "cubes must contain exactly one light cube and one music cube",
            details={"field": "cubes"},
        )
    if len({cube.name.casefold() for cube in cubes}) != len(cubes):
        raise ValidationError("Cube names must be unique", details={"field": "cubes"})
    if len({cube.address for cube in cubes}) != len(cubes):
        raise ValidationError("Cube addresses must be unique", details={"field": "cubes"})

    mappings: tuple[RuleMappingConfig, ...] = ()
    if schema_version == SUPPORTED_SCHEMA_VERSION:
        raw_mappings = root.get("mappings", [])
        if not isinstance(raw_mappings, list):
            raise ValidationError("mappings must be an array", details={"field": "mappings"})
        cube_names = {cube.name.casefold(): cube.name for cube in cubes}
        parsed_mappings = tuple(
            _parse_mapping(item, index, cube_names) for index, item in enumerate(raw_mappings)
        )
        if len({(item.cube.casefold(), item.face) for item in parsed_mappings}) != len(
            parsed_mappings
        ):
            raise ValidationError(
                "Cube and face mappings must be unique",
                details={"field": "mappings"},
            )
        mappings = parsed_mappings

    return AppConfig(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        bluetooth=BluetoothConfig(adapter=adapter),
        debounce_ms=debounce_ms,
        cubes=(cubes[0], cubes[1]),
        mappings=mappings,
    )


def _parse_mapping(
    value: object,
    index: int,
    cube_names: Mapping[str, str],
) -> RuleMappingConfig:
    field = f"mappings[{index}]"
    mapping = _require_table(value, field)
    _check_keys(mapping, {"cube", "face", "actions"}, field)
    cube_name = _require_string(mapping.get("cube"), f"{field}.cube")
    canonical_cube_name = cube_names.get(cube_name.casefold())
    if canonical_cube_name is None:
        raise ValidationError(
            "Mapping references an unknown cube",
            details={"field": f"{field}.cube"},
        )
    face = mapping.get("face")
    if isinstance(face, bool) or not isinstance(face, int) or not 1 <= face <= 8:
        raise ValidationError(
            "Mapping face must be an integer from 1 to 8",
            details={"field": f"{field}.face"},
        )
    raw_actions = mapping.get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValidationError(
            "Mapping must contain at least one action",
            details={"field": f"{field}.actions"},
        )
    actions = tuple(
        _parse_rule_action(item, f"{field}.actions[{action_index}]")
        for action_index, item in enumerate(raw_actions)
    )
    return RuleMappingConfig(canonical_cube_name, face, actions)


def _parse_rule_action(value: object, field: str) -> RuleActionConfig:
    action = _require_table(value, field)
    _check_keys(action, {"kind", "action", "parameters"}, field)
    kind = _require_string(action.get("kind"), f"{field}.kind")
    name = _require_string(action.get("action"), f"{field}.action")
    if not _ACTION_IDENTIFIER_PATTERN.fullmatch(kind):
        raise ValidationError(
            "Action kind must be a lowercase plugin identifier",
            details={"field": f"{field}.kind"},
        )
    if not _ACTION_IDENTIFIER_PATTERN.fullmatch(name):
        raise ValidationError(
            "Action name must be a lowercase identifier",
            details={"field": f"{field}.action"},
        )
    raw_parameters = _require_table(action.get("parameters", {}), f"{field}.parameters")
    parameters = MappingProxyType(
        {
            key: _freeze_parameter(item, f"{field}.parameters.{key}")
            for key, item in raw_parameters.items()
        }
    )
    return RuleActionConfig(kind, name, parameters)


def _parse_cube(value: object, index: int) -> CubeConfig:
    field = f"cubes[{index}]"
    cube = _require_table(value, field)
    _check_keys(cube, {"name", "address", "role"}, field)

    name = _require_string(cube.get("name"), f"{field}.name")
    address = _require_string(cube.get("address"), f"{field}.address")
    if not _ADDRESS_PATTERN.fullmatch(address):
        raise ValidationError(
            "Cube address must be a Bluetooth MAC address with six hexadecimal octets",
            details={"field": f"{field}.address"},
        )
    address = address.upper()

    role_value = _require_string(cube.get("role"), f"{field}.role")
    try:
        role = CubeRole(role_value)
    except ValueError as exc:
        raise ValidationError(
            "Cube role must be 'light' or 'music'",
            details={"field": f"{field}.role"},
        ) from exc
    return CubeConfig(name=name, address=address, role=role)


def _require_table(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValidationError("Expected a TOML table", details={"field": field})
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("Expected a non-empty string", details={"field": field})
    return value.strip()


def _check_keys(table: Mapping[str, object], allowed: set[str], field: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValidationError(
            "Configuration contains unsupported fields",
            details={"field": field, "unknown_fields": sorted(unknown)},
        )
