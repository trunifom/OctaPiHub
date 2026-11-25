"""Versioned TOML configuration loading, validation, and atomic persistence."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from octapihub.core.errors import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_DEBOUNCE_MS = 300
MAX_DEBOUNCE_MS = 999
_ADAPTER_PATTERN = re.compile(r"^hci[0-9]+$")
_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


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
class BluetoothConfig:
    """Bluetooth adapter selection."""

    adapter: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated configuration currently supported by OctaPiHub."""

    schema_version: int
    bluetooth: BluetoothConfig
    debounce_ms: int
    cubes: tuple[CubeConfig, CubeConfig]


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
    contents = "\n".join(lines) + "\n"
    try:
        tomllib.loads(contents)
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(
            "Configuration values cannot be represented as valid TOML",
            details={"reason": str(exc)},
        ) from exc
    return contents


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_config(data: object) -> AppConfig:
    """Validate a parsed TOML document and return immutable configuration values."""
    root = _require_table(data, "root")
    _check_keys(root, {"schema_version", "bluetooth", "debounce_ms", "cubes"}, "root")

    schema_version = root.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValidationError(
            "schema_version must be an integer", details={"field": "schema_version"}
        )
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationError(
            "Unsupported configuration schema version",
            details={
                "schema_version": schema_version,
                "supported_schema_version": SUPPORTED_SCHEMA_VERSION,
            },
        )

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

    return AppConfig(
        schema_version=schema_version,
        bluetooth=BluetoothConfig(adapter=adapter),
        debounce_ms=debounce_ms,
        cubes=(cubes[0], cubes[1]),
    )


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
