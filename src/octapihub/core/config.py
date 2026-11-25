"""Versioned TOML configuration loading, validation, and atomic persistence."""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import re
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from octapihub.core.errors import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 4
LEGACY_SCHEMA_VERSION = 1
DEFAULT_DEBOUNCE_MS = 300
MAX_DEBOUNCE_MS = 999
DEFAULT_SENSOR_INTERVAL_SECONDS = 30
DEFAULT_HISTORY_RETENTION_DAYS = 30
MAX_SENSOR_INTERVAL_SECONDS = 3600
MAX_HISTORY_RETENTION_DAYS = 3650
_ADAPTER_PATTERN = re.compile(r"^hci[0-9]+$")
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
DEFAULT_HUE_PORT = 443
_ADDRESS_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ACTION_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SENSE_HAT_SENSOR_IDS = (
    "temperature",
    "humidity",
    "pressure",
    "orientation",
    "compass",
    "gyroscope",
    "accelerometer",
    "color",
)


class CubeRole(StrEnum):
    """Supported functional roles of the two configured cubes."""

    LIGHT = "light"
    MUSIC = "music"


class SenseHatVersion(StrEnum):
    """Hardware-version selection for automatic detection or a manual override."""

    AUTO = "auto"
    V1 = "v1"
    V2 = "v2"


class SensorPresentation(StrEnum):
    """How a sensor should be presented in a future dashboard."""

    HIDDEN = "hidden"
    VALUE = "value"
    CHART = "chart"
    VALUE_AND_CHART = "value_and_chart"


@dataclass(frozen=True, slots=True)
class SensorConfig:
    """Settings for one independently configurable Sense HAT sensor group."""

    enabled: bool
    interval_seconds: int
    history_enabled: bool
    presentation: SensorPresentation
    precision: int


@dataclass(frozen=True, slots=True)
class SenseHatHistoryConfig:
    """Persistent sensor-history settings."""

    enabled: bool
    retention_days: int
    database_path: str


@dataclass(frozen=True, slots=True)
class SenseHatMatrixConfig:
    """LED matrix operating settings."""

    enabled: bool
    rotation: int
    low_light: bool
    ambient_brightness: bool


def _default_sensors() -> Mapping[str, SensorConfig]:
    return MappingProxyType(
        {
            "temperature": SensorConfig(
                True,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.VALUE_AND_CHART,
                1,
            ),
            "humidity": SensorConfig(
                True,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.VALUE_AND_CHART,
                1,
            ),
            "pressure": SensorConfig(
                True,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.VALUE_AND_CHART,
                1,
            ),
            "orientation": SensorConfig(
                False,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.HIDDEN,
                2,
            ),
            "compass": SensorConfig(
                False,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.HIDDEN,
                2,
            ),
            "gyroscope": SensorConfig(
                False,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.HIDDEN,
                2,
            ),
            "accelerometer": SensorConfig(
                False,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.HIDDEN,
                2,
            ),
            "color": SensorConfig(
                False,
                DEFAULT_SENSOR_INTERVAL_SECONDS,
                True,
                SensorPresentation.HIDDEN,
                0,
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class SenseHatConfig:
    """Validated configuration for Sense HAT sensors and interfaces."""

    enabled: bool = True
    hardware_version: SenseHatVersion = SenseHatVersion.AUTO
    sensors: Mapping[str, SensorConfig] = field(default_factory=_default_sensors)
    history: SenseHatHistoryConfig = SenseHatHistoryConfig(
        True,
        DEFAULT_HISTORY_RETENTION_DAYS,
        "data/sense-hat-history.sqlite3",
    )
    matrix: SenseHatMatrixConfig = SenseHatMatrixConfig(True, 0, False, False)
    joystick_enabled: bool = True
    temperature_correction_factor: float = 1.0
    temperature_offset_c: float = 0.0
    color_gain: int = 1
    color_integration_cycles: int = 1


@dataclass(frozen=True, slots=True)
class HueConfig:
    """Philips Hue bridge connection settings (the application key lives in secrets.env).

    ``ca_file`` and ``tls_server_name`` are optional: the bridge certificate is always
    verified, and bridges often present a certificate for their bridge ID rather than
    their IP address, which ``tls_server_name`` accounts for.
    """

    enabled: bool = False
    host: str = ""
    port: int = DEFAULT_HUE_PORT
    ca_file: str = ""
    tls_server_name: str = ""


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
    sense_hat: SenseHatConfig = field(default_factory=SenseHatConfig)
    hue: HueConfig = field(default_factory=HueConfig)


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


def sense_hat_table(sense_hat: SenseHatConfig) -> dict[str, object]:
    """Return the Sense HAT settings as a plain TOML-shaped table for editing."""
    data: dict[str, object] = {
        "enabled": sense_hat.enabled,
        "hardware_version": sense_hat.hardware_version.value,
        "temperature_correction_factor": sense_hat.temperature_correction_factor,
        "temperature_offset_c": sense_hat.temperature_offset_c,
        "color_gain": sense_hat.color_gain,
        "color_integration_cycles": sense_hat.color_integration_cycles,
        "sensors": {
            sensor_id: {
                "enabled": sensor.enabled,
                "interval_seconds": sensor.interval_seconds,
                "history_enabled": sensor.history_enabled,
                "presentation": sensor.presentation.value,
                "precision": sensor.precision,
            }
            for sensor_id, sensor in sense_hat.sensors.items()
        },
        "history": {
            "enabled": sense_hat.history.enabled,
            "retention_days": sense_hat.history.retention_days,
            "database_path": sense_hat.history.database_path,
        },
        "matrix": {
            "enabled": sense_hat.matrix.enabled,
            "rotation": sense_hat.matrix.rotation,
            "low_light": sense_hat.matrix.low_light,
            "ambient_brightness": sense_hat.matrix.ambient_brightness,
        },
        "joystick": {"enabled": sense_hat.joystick_enabled},
    }
    return data


def hue_table(hue: HueConfig) -> dict[str, object]:
    """Return the Hue settings as a plain TOML-shaped table for editing."""
    return {
        "enabled": hue.enabled,
        "host": hue.host,
        "port": hue.port,
        "ca_file": hue.ca_file,
        "tls_server_name": hue.tls_server_name,
    }


def mappings_table(config: AppConfig) -> list[dict[str, object]]:
    """Return the cube mappings as plain TOML-shaped tables for editing."""
    return [
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
    ]


def with_mappings_table(config: AppConfig, raw_mappings: Sequence[object]) -> AppConfig:
    """Return ``config`` with replaced, fully validated cube mappings.

    Raises:
        ValidationError: If any mapping violates the schema.
    """
    return replace(config, mappings=_parse_mappings(raw_mappings, config.cubes))


def with_hue_table(config: AppConfig, table: Mapping[str, object]) -> AppConfig:
    """Return ``config`` with a replaced, fully validated Hue table.

    Raises:
        ValidationError: If any Hue value violates the schema.
    """
    return replace(config, hue=_parse_hue(table))


def with_sense_hat_table(config: AppConfig, table: Mapping[str, object]) -> AppConfig:
    """Return ``config`` with a replaced, fully validated Sense HAT table.

    Raises:
        ValidationError: If any Sense HAT value violates the schema.
    """
    return replace(config, sense_hat=_parse_sense_hat(table))


def config_to_toml(config: AppConfig) -> str:
    """Return the validated configuration as TOML text (no file is written)."""
    return _serialize_config(config)


def _serialize_config(config: AppConfig) -> str:
    sense_hat_data = sense_hat_table(config.sense_hat)
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
        "sense_hat": sense_hat_data,
        "hue": hue_table(config.hue),
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
    sense_hat = validated.sense_hat
    lines.extend(
        [
            "",
            "[sense_hat]",
            f"enabled = {_toml_value(sense_hat.enabled)}",
            f"hardware_version = {_toml_string(sense_hat.hardware_version.value)}",
            "temperature_correction_factor = "
            f"{_toml_value(sense_hat.temperature_correction_factor)}",
            f"temperature_offset_c = {_toml_value(sense_hat.temperature_offset_c)}",
            f"color_gain = {sense_hat.color_gain}",
            f"color_integration_cycles = {sense_hat.color_integration_cycles}",
            "",
            "[sense_hat.history]",
            f"enabled = {_toml_value(sense_hat.history.enabled)}",
            f"retention_days = {sense_hat.history.retention_days}",
            f"database_path = {_toml_string(sense_hat.history.database_path)}",
            "",
            "[sense_hat.matrix]",
            f"enabled = {_toml_value(sense_hat.matrix.enabled)}",
            f"rotation = {sense_hat.matrix.rotation}",
            f"low_light = {_toml_value(sense_hat.matrix.low_light)}",
            f"ambient_brightness = {_toml_value(sense_hat.matrix.ambient_brightness)}",
            "",
            "[sense_hat.joystick]",
            f"enabled = {_toml_value(sense_hat.joystick_enabled)}",
        ]
    )
    hue = validated.hue
    lines.extend(
        [
            "",
            "[hue]",
            f"enabled = {_toml_value(hue.enabled)}",
            f"host = {_toml_string(hue.host)}",
            f"port = {hue.port}",
            f"ca_file = {_toml_string(hue.ca_file)}",
            f"tls_server_name = {_toml_string(hue.tls_server_name)}",
        ]
    )
    for sensor_id in _SENSE_HAT_SENSOR_IDS:
        sensor = sense_hat.sensors[sensor_id]
        lines.extend(
            [
                "",
                f"[sense_hat.sensors.{sensor_id}]",
                f"enabled = {_toml_value(sensor.enabled)}",
                f"interval_seconds = {sensor.interval_seconds}",
                f"history_enabled = {_toml_value(sensor.history_enabled)}",
                f"presentation = {_toml_string(sensor.presentation.value)}",
                f"precision = {sensor.precision}",
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
    if schema_version not in {LEGACY_SCHEMA_VERSION, 2, 3, SUPPORTED_SCHEMA_VERSION}:
        raise ConfigurationError(
            "Unsupported configuration schema version",
            details={
                "schema_version": schema_version,
                "supported_schema_version": SUPPORTED_SCHEMA_VERSION,
            },
        )
    allowed_root_keys = {"schema_version", "bluetooth", "debounce_ms", "cubes"}
    if schema_version >= 2:
        allowed_root_keys.add("mappings")
    if schema_version >= 3:
        allowed_root_keys.add("sense_hat")
    if schema_version >= 4:
        allowed_root_keys.add("hue")
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
    if schema_version >= 2:
        raw_mappings = root.get("mappings", [])
        if not isinstance(raw_mappings, list):
            raise ValidationError("mappings must be an array", details={"field": "mappings"})
        mappings = _parse_mappings(raw_mappings, cubes)

    sense_hat = (
        _parse_sense_hat(root.get("sense_hat", {})) if schema_version >= 3 else SenseHatConfig()
    )

    hue = _parse_hue(root.get("hue", {})) if schema_version >= 4 else HueConfig()

    return AppConfig(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        bluetooth=BluetoothConfig(adapter=adapter),
        debounce_ms=debounce_ms,
        cubes=(cubes[0], cubes[1]),
        mappings=mappings,
        sense_hat=sense_hat,
        hue=hue,
    )


def _valid_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    labels = host.rstrip(".").split(".")
    return len(host) <= 253 and all(_HOST_LABEL_PATTERN.fullmatch(label) for label in labels)


def _optional_string(value: object, field: str) -> str:
    if not isinstance(value, str) or "\x00" in value or "\n" in value or "\r" in value:
        raise ValidationError("Expected a single-line string", details={"field": field})
    return value.strip()


def _parse_hue(value: object) -> HueConfig:
    table = _require_table(value, "hue")
    _check_keys(table, {"enabled", "host", "port", "ca_file", "tls_server_name"}, "hue")
    defaults = HueConfig()
    enabled = _require_bool(table.get("enabled", defaults.enabled), "hue.enabled")
    host = _optional_string(table.get("host", defaults.host), "hue.host")
    if host and not _valid_host(host):
        raise ValidationError(
            "hue.host must be a hostname or IP address", details={"field": "hue.host"}
        )
    if enabled and not host:
        raise ValidationError(
            "hue.host is required when Hue is enabled", details={"field": "hue.host"}
        )
    port = table.get("port", defaults.port)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError(
            "hue.port must be an integer from 1 to 65535", details={"field": "hue.port"}
        )
    server_name = _optional_string(
        table.get("tls_server_name", defaults.tls_server_name), "hue.tls_server_name"
    )
    if server_name and not _valid_host(server_name):
        raise ValidationError(
            "hue.tls_server_name must be a hostname", details={"field": "hue.tls_server_name"}
        )
    return HueConfig(
        enabled=enabled,
        host=host,
        port=port,
        ca_file=_optional_string(table.get("ca_file", defaults.ca_file), "hue.ca_file"),
        tls_server_name=server_name,
    )


def _parse_sense_hat(value: object) -> SenseHatConfig:
    table = _require_table(value, "sense_hat")
    _check_keys(
        table,
        {
            "enabled",
            "hardware_version",
            "sensors",
            "history",
            "matrix",
            "joystick",
            "temperature_correction_factor",
            "temperature_offset_c",
            "color_gain",
            "color_integration_cycles",
        },
        "sense_hat",
    )
    defaults = SenseHatConfig()
    hardware_version_value = _require_string(
        table.get("hardware_version", defaults.hardware_version.value),
        "sense_hat.hardware_version",
    )
    try:
        hardware_version = SenseHatVersion(hardware_version_value)
    except ValueError as exc:
        raise ValidationError(
            "sense_hat.hardware_version must be 'auto', 'v1', or 'v2'",
            details={"field": "sense_hat.hardware_version"},
        ) from exc

    raw_sensors = _require_table(table.get("sensors", {}), "sense_hat.sensors")
    _check_keys(raw_sensors, set(_SENSE_HAT_SENSOR_IDS), "sense_hat.sensors")
    sensors = {
        sensor_id: (
            _parse_sensor(
                raw_sensors[sensor_id],
                f"sense_hat.sensors.{sensor_id}",
                defaults.sensors[sensor_id],
            )
            if sensor_id in raw_sensors
            else defaults.sensors[sensor_id]
        )
        for sensor_id in _SENSE_HAT_SENSOR_IDS
    }

    raw_history = _require_table(table.get("history", {}), "sense_hat.history")
    _check_keys(raw_history, {"enabled", "retention_days", "database_path"}, "sense_hat.history")
    history = defaults.history
    retention_days = raw_history.get("retention_days", history.retention_days)
    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or not 1 <= retention_days <= MAX_HISTORY_RETENTION_DAYS
    ):
        raise ValidationError(
            f"sense_hat.history.retention_days must be from 1 to {MAX_HISTORY_RETENTION_DAYS}",
            details={"field": "sense_hat.history.retention_days"},
        )
    history = SenseHatHistoryConfig(
        enabled=_require_bool(
            raw_history.get("enabled", history.enabled),
            "sense_hat.history.enabled",
        ),
        retention_days=retention_days,
        database_path=_require_string(
            raw_history.get("database_path", history.database_path),
            "sense_hat.history.database_path",
        ),
    )

    raw_matrix = _require_table(table.get("matrix", {}), "sense_hat.matrix")
    _check_keys(
        raw_matrix,
        {"enabled", "rotation", "low_light", "ambient_brightness"},
        "sense_hat.matrix",
    )
    matrix = defaults.matrix
    rotation = raw_matrix.get("rotation", matrix.rotation)
    if (
        isinstance(rotation, bool)
        or not isinstance(rotation, int)
        or rotation not in {0, 90, 180, 270}
    ):
        raise ValidationError(
            "sense_hat.matrix.rotation must be 0, 90, 180, or 270",
            details={"field": "sense_hat.matrix.rotation"},
        )
    matrix = SenseHatMatrixConfig(
        enabled=_require_bool(
            raw_matrix.get("enabled", matrix.enabled), "sense_hat.matrix.enabled"
        ),
        rotation=rotation,
        low_light=_require_bool(
            raw_matrix.get("low_light", matrix.low_light), "sense_hat.matrix.low_light"
        ),
        ambient_brightness=_require_bool(
            raw_matrix.get("ambient_brightness", matrix.ambient_brightness),
            "sense_hat.matrix.ambient_brightness",
        ),
    )

    raw_joystick = _require_table(table.get("joystick", {}), "sense_hat.joystick")
    _check_keys(raw_joystick, {"enabled"}, "sense_hat.joystick")
    correction_factor = _require_finite_number(
        table.get("temperature_correction_factor", defaults.temperature_correction_factor),
        "sense_hat.temperature_correction_factor",
    )
    if correction_factor <= 0:
        raise ValidationError(
            "sense_hat.temperature_correction_factor must be greater than zero",
            details={"field": "sense_hat.temperature_correction_factor"},
        )
    color_gain = table.get("color_gain", defaults.color_gain)
    if (
        isinstance(color_gain, bool)
        or not isinstance(color_gain, int)
        or color_gain not in {1, 4, 16, 60}
    ):
        raise ValidationError(
            "sense_hat.color_gain must be 1, 4, 16, or 60",
            details={"field": "sense_hat.color_gain"},
        )
    integration_cycles = table.get("color_integration_cycles", defaults.color_integration_cycles)
    if (
        isinstance(integration_cycles, bool)
        or not isinstance(integration_cycles, int)
        or not 1 <= integration_cycles <= 256
    ):
        raise ValidationError(
            "sense_hat.color_integration_cycles must be from 1 to 256",
            details={"field": "sense_hat.color_integration_cycles"},
        )
    return SenseHatConfig(
        enabled=_require_bool(table.get("enabled", defaults.enabled), "sense_hat.enabled"),
        hardware_version=hardware_version,
        sensors=MappingProxyType(sensors),
        history=history,
        matrix=matrix,
        joystick_enabled=_require_bool(
            raw_joystick.get("enabled", defaults.joystick_enabled),
            "sense_hat.joystick.enabled",
        ),
        temperature_correction_factor=correction_factor,
        temperature_offset_c=_require_finite_number(
            table.get("temperature_offset_c", defaults.temperature_offset_c),
            "sense_hat.temperature_offset_c",
        ),
        color_gain=color_gain,
        color_integration_cycles=integration_cycles,
    )


def _parse_sensor(value: object, field: str, defaults: SensorConfig) -> SensorConfig:
    table = _require_table(value, field)
    _check_keys(
        table,
        {"enabled", "interval_seconds", "history_enabled", "presentation", "precision"},
        field,
    )
    interval_seconds = table.get("interval_seconds", defaults.interval_seconds)
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, int)
        or not 1 <= interval_seconds <= MAX_SENSOR_INTERVAL_SECONDS
    ):
        raise ValidationError(
            f"{field}.interval_seconds must be from 1 to {MAX_SENSOR_INTERVAL_SECONDS}",
            details={"field": f"{field}.interval_seconds"},
        )
    precision = table.get("precision", defaults.precision)
    if isinstance(precision, bool) or not isinstance(precision, int) or not 0 <= precision <= 6:
        raise ValidationError(
            f"{field}.precision must be an integer from 0 to 6",
            details={"field": f"{field}.precision"},
        )
    presentation_value = _require_string(
        table.get("presentation", defaults.presentation.value),
        f"{field}.presentation",
    )
    try:
        presentation = SensorPresentation(presentation_value)
    except ValueError as exc:
        raise ValidationError(
            f"{field}.presentation must be hidden, value, chart, or value_and_chart",
            details={"field": f"{field}.presentation"},
        ) from exc
    return SensorConfig(
        enabled=_require_bool(table.get("enabled", defaults.enabled), f"{field}.enabled"),
        interval_seconds=interval_seconds,
        history_enabled=_require_bool(
            table.get("history_enabled", defaults.history_enabled),
            f"{field}.history_enabled",
        ),
        presentation=presentation,
        precision=precision,
    )


def _parse_mappings(
    raw_mappings: Sequence[object], cubes: Sequence[CubeConfig]
) -> tuple[RuleMappingConfig, ...]:
    cube_names = {cube.name.casefold(): cube.name for cube in cubes}
    parsed = tuple(
        _parse_mapping(item, index, cube_names) for index, item in enumerate(raw_mappings)
    )
    if len({(item.cube.casefold(), item.face) for item in parsed}) != len(parsed):
        raise ValidationError(
            "Cube and face mappings must be unique",
            details={"field": "mappings"},
        )
    return parsed


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


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("Expected a boolean", details={"field": field})
    return value


def _require_finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Expected a finite number", details={"field": field})
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError("Expected a finite number", details={"field": field})
    return number


def _check_keys(table: Mapping[str, object], allowed: set[str], field: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ValidationError(
            "Configuration contains unsupported fields",
            details={"field": field, "unknown_fields": sorted(unknown)},
        )
