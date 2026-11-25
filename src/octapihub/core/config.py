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

SUPPORTED_SCHEMA_VERSION = 6
MAX_CUBES = 3
# The Sense HAT joystick is a built-in action source next to the cubes. Its five inputs are
# the "faces" 1..5; mappings address it by this reserved name.
JOYSTICK_SOURCE = "Joystick"
JOYSTICK_FACE_LABELS: tuple[str, ...] = ("Hoch", "Rechts", "Runter", "Links", "Mitte")
JOYSTICK_FACES = len(JOYSTICK_FACE_LABELS)
CUBE_FACES = 8
HOLDER_FACE = 0  # "no face up": the cube rests in its holder (mappable, like the faces)
MAX_MUSIC_TARGETS = 8
MAX_PLAYLISTS = 64
MAX_SOURCE_LENGTH = 2000
_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_MAC_PATTERN = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
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
    GENERAL = "general"


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


# Every view the LED matrix can show; the order here is the default order.
MATRIX_VIEW_IDS: tuple[str, ...] = (
    "overview",
    "cubes",
    "control",
    "battery",
    "temperature",
    "humidity",
    "pressure",
    "graph",
    "clock",
    "clock_binary",
    "ticker",
    "compass",
    "level",
    "ambient",
    "lamp",
    "rainbow",
    "breathing",
    "palette",
    "brightness",
    "off",
)
DEFAULT_MATRIX_VIEWS: tuple[str, ...] = (
    "overview",
    "cubes",
    "battery",
    "temperature",
    "humidity",
    "pressure",
    "clock",
    "brightness",
)
MATRIX_NIGHT_MODES = ("off", "dim")
_TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


@dataclass(frozen=True, slots=True)
class SenseHatMatrixConfig:
    """LED matrix operating settings.

    ``views`` lists the enabled views in joystick order; ``brightness`` is a percentage
    applied in software on top of ``low_light``. During the optional night window the
    display is switched off or dimmed.
    """

    enabled: bool
    rotation: int
    low_light: bool
    ambient_brightness: bool
    brightness: int = 60
    default_view: str = "overview"
    views: tuple[str, ...] = DEFAULT_MATRIX_VIEWS
    auto_cycle_seconds: int = 0
    night_enabled: bool = False
    night_start: str = "22:00"
    night_end: str = "07:00"
    night_mode: str = "off"


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


class MusicTargetKind(StrEnum):
    """Where music can be played."""

    CAST = "cast"  # Google Nest speakers and Nest Hub displays (Google Cast)
    LOCAL = "local"  # the audio output of this Raspberry Pi
    BLUETOOTH = "bluetooth"  # a Bluetooth speaker connected to this Raspberry Pi


class RepeatMode(StrEnum):
    """What happens at the end of a track or playlist."""

    OFF = "off"
    ALL = "all"
    ONE = "one"


@dataclass(frozen=True, slots=True)
class MusicTargetConfig:
    """One place where music can play.

    ``device`` is the Cast device's friendly name (kind ``cast``) or the audio device name
    (kinds ``local`` and ``bluetooth``; empty means the system default). For Bluetooth,
    ``bluetooth_address`` is connected before playing.
    """

    id: str
    name: str
    kind: MusicTargetKind
    device: str = ""
    bluetooth_address: str = ""
    volume: int = 40


@dataclass(frozen=True, slots=True)
class PlaylistConfig:
    """A named music source (link, stream, or local path) with playback preferences."""

    id: str
    name: str
    source: str
    target: str = ""
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    volume: int | None = None


@dataclass(frozen=True, slots=True)
class MusicConfig:
    """Music playback settings (everything is off until enabled)."""

    enabled: bool = False
    library_dir: str = ""
    media_host: str = ""
    targets: tuple[MusicTargetConfig, ...] = ()
    playlists: tuple[PlaylistConfig, ...] = ()


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
    cubes: tuple[CubeConfig, ...]
    mappings: tuple[RuleMappingConfig, ...] = ()
    sense_hat: SenseHatConfig = field(default_factory=SenseHatConfig)
    hue: HueConfig = field(default_factory=HueConfig)
    music: MusicConfig = field(default_factory=MusicConfig)


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
            "brightness": sense_hat.matrix.brightness,
            "default_view": sense_hat.matrix.default_view,
            "views": list(sense_hat.matrix.views),
            "auto_cycle_seconds": sense_hat.matrix.auto_cycle_seconds,
            "night_enabled": sense_hat.matrix.night_enabled,
            "night_start": sense_hat.matrix.night_start,
            "night_end": sense_hat.matrix.night_end,
            "night_mode": sense_hat.matrix.night_mode,
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


def music_table(music: MusicConfig) -> dict[str, object]:
    """Return the music settings as a plain TOML-shaped table for editing."""
    return {
        "enabled": music.enabled,
        "library_dir": music.library_dir,
        "media_host": music.media_host,
        "targets": [
            {
                "id": t.id,
                "name": t.name,
                "kind": t.kind.value,
                "device": t.device,
                "bluetooth_address": t.bluetooth_address,
                "volume": t.volume,
            }
            for t in music.targets
        ],
        "playlists": [
            {
                "id": p.id,
                "name": p.name,
                "source": p.source,
                "target": p.target,
                "shuffle": p.shuffle,
                "repeat": p.repeat.value,
                **({"volume": p.volume} if p.volume is not None else {}),
            }
            for p in music.playlists
        ],
    }


def with_music_table(config: AppConfig, table: Mapping[str, object]) -> AppConfig:
    """Return ``config`` with replaced, fully validated music settings.

    Raises:
        ValidationError: If any music value violates the schema.
    """
    return replace(config, music=_parse_music(table))


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
        "music": music_table(config.music),
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
            f"brightness = {sense_hat.matrix.brightness}",
            f"default_view = {_toml_string(sense_hat.matrix.default_view)}",
            f"views = {_toml_value(list(sense_hat.matrix.views))}",
            f"auto_cycle_seconds = {sense_hat.matrix.auto_cycle_seconds}",
            f"night_enabled = {_toml_value(sense_hat.matrix.night_enabled)}",
            f"night_start = {_toml_string(sense_hat.matrix.night_start)}",
            f"night_end = {_toml_string(sense_hat.matrix.night_end)}",
            f"night_mode = {_toml_string(sense_hat.matrix.night_mode)}",
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
    music = validated.music
    lines.extend(
        [
            "",
            "[music]",
            f"enabled = {_toml_value(music.enabled)}",
            f"library_dir = {_toml_string(music.library_dir)}",
            f"media_host = {_toml_string(music.media_host)}",
        ]
    )
    for target in music.targets:
        lines.extend(
            [
                "",
                "[[music.targets]]",
                f"id = {_toml_string(target.id)}",
                f"name = {_toml_string(target.name)}",
                f"kind = {_toml_string(target.kind.value)}",
                f"device = {_toml_string(target.device)}",
                f"bluetooth_address = {_toml_string(target.bluetooth_address)}",
                f"volume = {target.volume}",
            ]
        )
    for playlist in music.playlists:
        lines.extend(
            [
                "",
                "[[music.playlists]]",
                f"id = {_toml_string(playlist.id)}",
                f"name = {_toml_string(playlist.name)}",
                f"source = {_toml_string(playlist.source)}",
                f"target = {_toml_string(playlist.target)}",
                f"shuffle = {_toml_value(playlist.shuffle)}",
                f"repeat = {_toml_string(playlist.repeat.value)}",
            ]
        )
        if playlist.volume is not None:
            lines.append(f"volume = {playlist.volume}")
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
    if schema_version not in {LEGACY_SCHEMA_VERSION, 2, 3, 4, 5, SUPPORTED_SCHEMA_VERSION}:
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
    if schema_version >= 6:
        allowed_root_keys.add("music")
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
    if not isinstance(raw_cubes, list) or not 1 <= len(raw_cubes) <= MAX_CUBES:
        raise ValidationError(
            f"cubes must contain from 1 to {MAX_CUBES} cubes",
            details={"field": "cubes"},
        )
    cubes = tuple(_parse_cube(item, index) for index, item in enumerate(raw_cubes))
    if len({cube.name.casefold() for cube in cubes}) != len(cubes):
        raise ValidationError("Cube names must be unique", details={"field": "cubes"})
    if any(cube.name.casefold() == JOYSTICK_SOURCE.casefold() for cube in cubes):
        raise ValidationError(
            f"The cube name {JOYSTICK_SOURCE!r} is reserved for the joystick",
            details={"field": "cubes"},
        )
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
    music = _parse_music(root.get("music", {})) if schema_version >= 6 else MusicConfig()

    return AppConfig(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        bluetooth=BluetoothConfig(adapter=adapter),
        debounce_ms=debounce_ms,
        cubes=cubes,
        mappings=mappings,
        sense_hat=sense_hat,
        hue=hue,
        music=music,
    )


def _single_line(value: object, field: str, *, maximum: int, required: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ValidationError(
            f"{field} must be a single line of at most {maximum} characters",
            details={"field": field},
        )
    text = value.strip()
    if required and not text:
        raise ValidationError(f"{field} must not be empty", details={"field": field})
    return text


def _volume(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValidationError(f"{field} must be an integer from 0 to 100", details={"field": field})
    return value


def _parse_music(value: object) -> MusicConfig:
    table = _require_table(value, "music")
    _check_keys(table, {"enabled", "library_dir", "media_host", "targets", "playlists"}, "music")
    media_host = _single_line(table.get("media_host", ""), "music.media_host", maximum=253)
    if media_host and not _valid_host(media_host):
        raise ValidationError(
            "music.media_host must be a hostname or IP address",
            details={"field": "music.media_host"},
        )
    raw_targets = table.get("targets", [])
    if not isinstance(raw_targets, (list, tuple)) or len(raw_targets) > MAX_MUSIC_TARGETS:
        raise ValidationError(
            f"music.targets must be a list of at most {MAX_MUSIC_TARGETS} entries",
            details={"field": "music.targets"},
        )
    targets = tuple(_parse_target(item, index) for index, item in enumerate(raw_targets))
    if len({t.id for t in targets}) != len(targets):
        raise ValidationError("Music target ids must be unique", details={"field": "music.targets"})
    raw_playlists = table.get("playlists", [])
    if not isinstance(raw_playlists, (list, tuple)) or len(raw_playlists) > MAX_PLAYLISTS:
        raise ValidationError(
            f"music.playlists must be a list of at most {MAX_PLAYLISTS} entries",
            details={"field": "music.playlists"},
        )
    playlists = tuple(
        _parse_playlist(item, index, {t.id for t in targets})
        for index, item in enumerate(raw_playlists)
    )
    if len({p.id for p in playlists}) != len(playlists):
        raise ValidationError("Playlist ids must be unique", details={"field": "music.playlists"})
    return MusicConfig(
        enabled=_require_bool(table.get("enabled", False), "music.enabled"),
        library_dir=_single_line(table.get("library_dir", ""), "music.library_dir", maximum=500),
        media_host=media_host,
        targets=targets,
        playlists=playlists,
    )


def _parse_target(value: object, index: int) -> MusicTargetConfig:
    field = f"music.targets[{index}]"
    table = _require_table(value, field)
    _check_keys(table, {"id", "name", "kind", "device", "bluetooth_address", "volume"}, field)
    target_id = _single_line(table.get("id"), f"{field}.id", maximum=32, required=True)
    if not _SLUG_PATTERN.fullmatch(target_id):
        raise ValidationError(
            f"{field}.id must be lower-case letters, digits, '-' or '_' (start with a letter)",
            details={"field": f"{field}.id"},
        )
    try:
        kind = MusicTargetKind(_require_string(table.get("kind"), f"{field}.kind"))
    except ValueError as exc:
        raise ValidationError(
            "music target kind must be 'cast', 'local' or 'bluetooth'",
            details={"field": f"{field}.kind"},
        ) from exc
    device = _single_line(table.get("device", ""), f"{field}.device", maximum=200)
    if kind is MusicTargetKind.CAST and not device:
        raise ValidationError(
            "A Cast target needs the device's name", details={"field": f"{field}.device"}
        )
    address = _single_line(
        table.get("bluetooth_address", ""), f"{field}.bluetooth_address", maximum=17
    )
    if address and not _MAC_PATTERN.fullmatch(address):
        raise ValidationError(
            "bluetooth_address must look like AA:BB:CC:DD:EE:FF",
            details={"field": f"{field}.bluetooth_address"},
        )
    return MusicTargetConfig(
        id=target_id,
        name=_single_line(table.get("name"), f"{field}.name", maximum=60, required=True),
        kind=kind,
        device=device,
        bluetooth_address=address.upper(),
        volume=_volume(table.get("volume", 40), f"{field}.volume"),
    )


def _parse_playlist(value: object, index: int, target_ids: set[str]) -> PlaylistConfig:
    field = f"music.playlists[{index}]"
    table = _require_table(value, field)
    _check_keys(table, {"id", "name", "source", "target", "shuffle", "repeat", "volume"}, field)
    playlist_id = _single_line(table.get("id"), f"{field}.id", maximum=32, required=True)
    if not _SLUG_PATTERN.fullmatch(playlist_id):
        raise ValidationError(
            f"{field}.id must be lower-case letters, digits, '-' or '_' (start with a letter)",
            details={"field": f"{field}.id"},
        )
    target = _single_line(table.get("target", ""), f"{field}.target", maximum=32)
    if target and target not in target_ids:
        raise ValidationError(
            "A playlist refers to a music target that does not exist",
            details={"field": f"{field}.target"},
        )
    try:
        repeat = RepeatMode(_require_string(table.get("repeat", "off"), f"{field}.repeat"))
    except ValueError as exc:
        raise ValidationError(
            "repeat must be 'off', 'all' or 'one'", details={"field": f"{field}.repeat"}
        ) from exc
    raw_volume = table.get("volume")
    return PlaylistConfig(
        id=playlist_id,
        name=_single_line(table.get("name"), f"{field}.name", maximum=60, required=True),
        source=_single_line(
            table.get("source"), f"{field}.source", maximum=MAX_SOURCE_LENGTH, required=True
        ),
        target=target,
        shuffle=_require_bool(table.get("shuffle", False), f"{field}.shuffle"),
        repeat=repeat,
        volume=None if raw_volume is None else _volume(raw_volume, f"{field}.volume"),
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
        {
            "enabled",
            "rotation",
            "low_light",
            "ambient_brightness",
            "brightness",
            "default_view",
            "views",
            "auto_cycle_seconds",
            "night_enabled",
            "night_start",
            "night_end",
            "night_mode",
        },
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
    matrix = _parse_matrix_extras(raw_matrix, matrix, rotation)

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


def _parse_matrix_extras(
    raw: Mapping[str, object], defaults: SenseHatMatrixConfig, rotation: int
) -> SenseHatMatrixConfig:
    brightness = raw.get("brightness", defaults.brightness)
    if (
        isinstance(brightness, bool)
        or not isinstance(brightness, int)
        or not 5 <= brightness <= 100
    ):
        raise ValidationError(
            "sense_hat.matrix.brightness must be from 5 to 100",
            details={"field": "sense_hat.matrix.brightness"},
        )
    cycle = raw.get("auto_cycle_seconds", defaults.auto_cycle_seconds)
    if isinstance(cycle, bool) or not isinstance(cycle, int) or not 0 <= cycle <= 600:
        raise ValidationError(
            "sense_hat.matrix.auto_cycle_seconds must be from 0 to 600",
            details={"field": "sense_hat.matrix.auto_cycle_seconds"},
        )
    raw_views = raw.get("views", list(defaults.views))
    if (
        not isinstance(raw_views, (list, tuple))
        or not raw_views
        or any(not isinstance(view, str) or view not in MATRIX_VIEW_IDS for view in raw_views)
        or len(set(raw_views)) != len(raw_views)
    ):
        raise ValidationError(
            "sense_hat.matrix.views must be a non-empty list of unique, known view names",
            details={"field": "sense_hat.matrix.views"},
        )
    views = tuple(str(view) for view in raw_views)
    default_view = raw.get("default_view", defaults.default_view)
    if not isinstance(default_view, str) or default_view not in views:
        raise ValidationError(
            "sense_hat.matrix.default_view must be one of the enabled views",
            details={"field": "sense_hat.matrix.default_view"},
        )
    times: list[str] = []
    for key, default in (("night_start", defaults.night_start), ("night_end", defaults.night_end)):
        value = raw.get(key, default)
        if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
            raise ValidationError(
                f"sense_hat.matrix.{key} must be a time such as 22:00",
                details={"field": f"sense_hat.matrix.{key}"},
            )
        times.append(value)
    night_mode = raw.get("night_mode", defaults.night_mode)
    if not isinstance(night_mode, str) or night_mode not in MATRIX_NIGHT_MODES:
        raise ValidationError(
            "sense_hat.matrix.night_mode must be 'off' or 'dim'",
            details={"field": "sense_hat.matrix.night_mode"},
        )
    return SenseHatMatrixConfig(
        enabled=_require_bool(raw.get("enabled", defaults.enabled), "sense_hat.matrix.enabled"),
        rotation=rotation,
        low_light=_require_bool(
            raw.get("low_light", defaults.low_light), "sense_hat.matrix.low_light"
        ),
        ambient_brightness=_require_bool(
            raw.get("ambient_brightness", defaults.ambient_brightness),
            "sense_hat.matrix.ambient_brightness",
        ),
        brightness=brightness,
        default_view=default_view,
        views=views,
        auto_cycle_seconds=cycle,
        night_enabled=_require_bool(
            raw.get("night_enabled", defaults.night_enabled), "sense_hat.matrix.night_enabled"
        ),
        night_start=times[0],
        night_end=times[1],
        night_mode=night_mode,
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
    cube_names[JOYSTICK_SOURCE.casefold()] = JOYSTICK_SOURCE
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
    is_joystick = canonical_cube_name == JOYSTICK_SOURCE
    lowest = 1 if is_joystick else HOLDER_FACE
    highest = JOYSTICK_FACES if is_joystick else CUBE_FACES
    if isinstance(face, bool) or not isinstance(face, int) or not lowest <= face <= highest:
        raise ValidationError(
            f"Mapping face must be an integer from {lowest} to {highest}",
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
