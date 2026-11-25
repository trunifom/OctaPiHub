"""Sense HAT settings form: flat string fields <-> validated configuration table."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from octapihub.core.config import (
    CUBE_FACES,
    JOYSTICK_FACES,
    JOYSTICK_SOURCE,
    MATRIX_VIEW_IDS,
    AppConfig,
    HueConfig,
    RuleMappingConfig,
    SenseHatConfig,
    SensorPresentation,
    sense_hat_table,
    with_hue_table,
)
from octapihub.core.errors import ValidationError

SENSOR_IDS = (
    "temperature",
    "humidity",
    "pressure",
    "orientation",
    "compass",
    "gyroscope",
    "accelerometer",
    "color",
)

INTERVAL_LABELS: Mapping[int, str] = {
    1: "jede Sekunde",
    2: "alle 2 Sekunden",
    5: "alle 5 Sekunden",
    10: "alle 10 Sekunden",
    30: "alle 30 Sekunden",
    60: "jede Minute",
    120: "alle 2 Minuten",
    300: "alle 5 Minuten",
    600: "alle 10 Minuten",
    900: "alle 15 Minuten",
    1800: "alle 30 Minuten",
    3600: "jede Stunde",
}
RETENTION_LABELS: Mapping[int, str] = {
    1: "1 Tag",
    7: "7 Tage",
    14: "14 Tage",
    30: "30 Tage",
    90: "90 Tage",
    180: "180 Tage",
    365: "1 Jahr",
}
PRECISION_LABELS: Mapping[int, str] = {
    0: "ganze Zahl (21)",
    1: "1 Stelle (21.3)",
    2: "2 Stellen (21.34)",
    3: "3 Stellen (21.347)",
}
PRESENTATION_LABELS: Mapping[str, str] = {
    SensorPresentation.HIDDEN.value: "Ausgeblendet",
    SensorPresentation.VALUE.value: "Nur aktueller Wert",
    SensorPresentation.CHART.value: "Nur Verlaufsdiagramm",
    SensorPresentation.VALUE_AND_CHART.value: "Wert und Diagramm",
}
VERSION_LABELS: Mapping[str, str] = {
    "auto": "Automatisch erkennen (empfohlen)",
    "v1": "Sense HAT v1 (ohne Farbsensor)",
    "v2": "Sense HAT v2 (mit Farbsensor)",
}
BRIGHTNESS_LABELS: Mapping[int, str] = {
    5: "5 % (sehr dunkel)",
    10: "10 %",
    20: "20 %",
    30: "30 %",
    40: "40 %",
    50: "50 %",
    60: "60 %",
    70: "70 %",
    80: "80 %",
    90: "90 %",
    100: "100 % (volle Helligkeit)",
}
CYCLE_LABELS: Mapping[int, str] = {
    0: "Aus (nur per Joystick wechseln)",
    10: "alle 10 Sekunden",
    20: "alle 20 Sekunden",
    30: "alle 30 Sekunden",
    60: "jede Minute",
    120: "alle 2 Minuten",
    300: "alle 5 Minuten",
}
NIGHT_MODE_LABELS: Mapping[str, str] = {
    "off": "Anzeige ausschalten",
    "dim": "Anzeige stark dimmen",
}
_TIME_FIELD = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
GAIN_LABELS: Mapping[int, str] = {1: "1× (hell)", 4: "4×", 16: "16×", 60: "60× (dunkel)"}
ROTATION_LABELS: Mapping[int, str] = {0: "0°", 90: "90°", 180: "180°", 270: "270°"}

FORM_ERROR_KEY = "_form"


@dataclass(frozen=True, slots=True)
class FormResult:
    """Parsed submission: either a validated table or per-field error messages."""

    table: dict[str, object] | None
    errors: dict[str, str]


def config_to_form(config: SenseHatConfig) -> dict[str, str]:
    """Flat string values for rendering the saved settings."""
    table = sense_hat_table(config)
    form: dict[str, str] = {}
    form["enabled"] = _flag(table["enabled"])
    form["hardware_version"] = str(table["hardware_version"])
    form["temperature_correction_factor"] = _number_text(table["temperature_correction_factor"])
    form["temperature_offset_c"] = _number_text(table["temperature_offset_c"])
    form["color_gain"] = str(table["color_gain"])
    form["color_integration_cycles"] = str(table["color_integration_cycles"])
    history = _table(table["history"])
    form["history.enabled"] = _flag(history["enabled"])
    form["history.retention_days"] = str(history["retention_days"])
    matrix = _table(table["matrix"])
    form["matrix.enabled"] = _flag(matrix["enabled"])
    form["matrix.rotation"] = str(matrix["rotation"])
    form["matrix.low_light"] = _flag(matrix["low_light"])
    form["matrix.ambient_brightness"] = _flag(matrix["ambient_brightness"])
    form["matrix.brightness"] = str(matrix["brightness"])
    form["matrix.default_view"] = str(matrix["default_view"])
    enabled_views = matrix["views"]
    assert isinstance(enabled_views, list)
    for view_id in MATRIX_VIEW_IDS:
        form[f"matrix.views.{view_id}"] = _flag(view_id in enabled_views)
    form["matrix.auto_cycle_seconds"] = str(matrix["auto_cycle_seconds"])
    form["matrix.night_enabled"] = _flag(matrix["night_enabled"])
    form["matrix.night_start"] = str(matrix["night_start"])
    form["matrix.night_end"] = str(matrix["night_end"])
    form["matrix.night_mode"] = str(matrix["night_mode"])
    form["joystick.enabled"] = _flag(_table(table["joystick"])["enabled"])
    for sensor_id, raw in _table(table["sensors"]).items():
        sensor = _table(raw)
        form[f"sensor.{sensor_id}.enabled"] = _flag(sensor["enabled"])
        form[f"sensor.{sensor_id}.interval_seconds"] = str(sensor["interval_seconds"])
        form[f"sensor.{sensor_id}.history_enabled"] = _flag(sensor["history_enabled"])
        form[f"sensor.{sensor_id}.presentation"] = str(sensor["presentation"])
        form[f"sensor.{sensor_id}.precision"] = str(sensor["precision"])
    return form


def parse_form(form: Mapping[str, str], current: SenseHatConfig) -> FormResult:
    """Turn submitted fields into a configuration table, or German field errors."""
    errors: dict[str, str] = {}
    table = copy.deepcopy(sense_hat_table(current))
    table["enabled"] = _checked(form, "enabled")
    table["hardware_version"] = _choice(form, "hardware_version", set(VERSION_LABELS), errors)
    table["temperature_correction_factor"] = _decimal(
        form, "temperature_correction_factor", errors, positive=True
    )
    table["temperature_offset_c"] = _decimal(form, "temperature_offset_c", errors)
    table["color_gain"] = _integer_choice(form, "color_gain", set(GAIN_LABELS), errors)
    table["color_integration_cycles"] = _integer(form, "color_integration_cycles", 1, 256, errors)
    history = _table(table["history"])
    history["enabled"] = _checked(form, "history.enabled")
    history["retention_days"] = _integer(form, "history.retention_days", 1, 3650, errors)
    matrix = _table(table["matrix"])
    matrix["enabled"] = _checked(form, "matrix.enabled")
    matrix["rotation"] = _integer_choice(form, "matrix.rotation", set(ROTATION_LABELS), errors)
    matrix["low_light"] = _checked(form, "matrix.low_light")
    matrix["ambient_brightness"] = _checked(form, "matrix.ambient_brightness")
    matrix["brightness"] = _integer_choice(
        form, "matrix.brightness", set(BRIGHTNESS_LABELS), errors
    )
    chosen_views = [v for v in MATRIX_VIEW_IDS if _checked(form, f"matrix.views.{v}")]
    if not chosen_views:
        errors["matrix.views"] = "Mindestens eine Ansicht muss aktiviert sein."
    matrix["views"] = chosen_views
    default_view = form.get("matrix.default_view", "")
    if chosen_views and default_view not in chosen_views:
        errors["matrix.default_view"] = "Die Startansicht muss eine aktivierte Ansicht sein."
    matrix["default_view"] = (
        default_view
        if default_view in chosen_views
        else (chosen_views[0] if chosen_views else "overview")
    )
    matrix["auto_cycle_seconds"] = _integer_choice(
        form, "matrix.auto_cycle_seconds", set(CYCLE_LABELS), errors
    )
    matrix["night_enabled"] = _checked(form, "matrix.night_enabled")
    for key in ("night_start", "night_end"):
        text = form.get(f"matrix.{key}", "").strip()
        if not _TIME_FIELD.fullmatch(text):
            errors[f"matrix.{key}"] = "Bitte eine Uhrzeit wie 22:00 eingeben."
        matrix[key] = text if _TIME_FIELD.fullmatch(text) else "22:00"
    matrix["night_mode"] = _choice(form, "matrix.night_mode", set(NIGHT_MODE_LABELS), errors)
    _table(table["joystick"])["enabled"] = _checked(form, "joystick.enabled")
    for sensor_id in SENSOR_IDS:
        sensor = _table(_table(table["sensors"])[sensor_id])
        prefix = f"sensor.{sensor_id}."
        sensor["enabled"] = _checked(form, prefix + "enabled")
        sensor["interval_seconds"] = _integer(form, prefix + "interval_seconds", 1, 3600, errors)
        sensor["history_enabled"] = _checked(form, prefix + "history_enabled")
        sensor["presentation"] = _choice(
            form, prefix + "presentation", set(PRESENTATION_LABELS), errors
        )
        sensor["precision"] = _integer(form, prefix + "precision", 0, 6, errors)
    if errors:
        return FormResult(None, errors)
    return FormResult(table, {})


def _table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Expected a table")
    return value


def _flag(value: object) -> str:
    return "on" if value else ""


def _number_text(value: object) -> str:
    return str(float(str(value)))


def _checked(form: Mapping[str, str], key: str) -> bool:
    return form.get(key, "") not in {"", "off", "0", "false"}


def _choice(form: Mapping[str, str], key: str, allowed: set[str], errors: dict[str, str]) -> str:
    value = form.get(key, "")
    if value not in allowed:
        errors[key] = "Bitte eine der angebotenen Möglichkeiten wählen."
    return value


def _integer(form: Mapping[str, str], key: str, low: int, high: int, errors: dict[str, str]) -> int:
    raw = form.get(key, "").strip()
    try:
        value = int(raw)
    except ValueError:
        errors[key] = f"Bitte eine ganze Zahl von {low} bis {high} eingeben."
        return low
    if not low <= value <= high:
        errors[key] = f"Der Wert muss zwischen {low} und {high} liegen."
    return value


def _integer_choice(
    form: Mapping[str, str], key: str, allowed: set[int], errors: dict[str, str]
) -> int:
    try:
        value = int(form.get(key, "").strip())
    except ValueError:
        value = -1
    if value not in allowed:
        errors[key] = "Bitte eine der angebotenen Möglichkeiten wählen."
        return min(allowed)
    return value


def _decimal(
    form: Mapping[str, str], key: str, errors: dict[str, str], *, positive: bool = False
) -> float:
    raw = form.get(key, "").strip().replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        errors[key] = "Bitte eine Zahl eingeben, zum Beispiel 0.9 oder -1.5."
        return 1.0 if positive else 0.0
    if not math.isfinite(value) or abs(value) > 1000:
        errors[key] = "Bitte eine Zahl zwischen -1000 und 1000 eingeben."
    elif positive and value <= 0:
        errors[key] = "Der Faktor muss größer als 0 sein."
    return value


# ---------- Hue connection form ----------

HUE_FIELD_MESSAGES: Mapping[str, str] = {
    "hue.enabled": "Bitte Ein oder Aus wählen.",
    "hue.host": "Bitte die Adresse der Bridge eintragen, zum Beispiel 192.168.1.20 (ohne „http://“).",
    "hue.port": "Der Port muss eine Zahl von 1 bis 65535 sein (üblich: 443).",
    "hue.tls_server_name": (
        "Bitte einen gültigen Namen eintragen (zum Beispiel die Bridge-ID) oder das Feld "
        "leer lassen."
    ),
    "hue.ca_file": "Der Pfad darf nur eine Zeile enthalten.",
}


def hue_config_to_form(hue: HueConfig) -> dict[str, str]:
    """Flat string values for rendering the saved Hue settings."""
    return {
        "hue.enabled": _flag(hue.enabled),
        "hue.host": hue.host,
        "hue.port": str(hue.port),
        "hue.ca_file": hue.ca_file,
        "hue.tls_server_name": hue.tls_server_name,
    }


def parse_hue_form(form: Mapping[str, str], current: AppConfig) -> FormResult:
    """Turn submitted Hue fields into a validated table, or German field errors.

    Final validation is delegated to the configuration schema so the form can never accept
    something the file would reject.
    """
    errors: dict[str, str] = {}
    port_text = form.get("hue.port", "").strip()
    try:
        port = int(port_text)
    except ValueError:
        errors["hue.port"] = HUE_FIELD_MESSAGES["hue.port"]
        port = 443
    table: dict[str, object] = {
        "enabled": _checked(form, "hue.enabled"),
        "host": form.get("hue.host", "").strip(),
        "port": port,
        "ca_file": form.get("hue.ca_file", "").strip(),
        "tls_server_name": form.get("hue.tls_server_name", "").strip(),
    }
    if not errors:
        try:
            with_hue_table(current, table)
        except ValidationError as exc:
            field = str(exc.details.get("field", ""))
            errors[field if field in HUE_FIELD_MESSAGES else FORM_ERROR_KEY] = (
                HUE_FIELD_MESSAGES.get(field, exc.user_message)
            )
    return FormResult(None if errors else table, errors)


# ---------- cube mapping form ----------


def mapping_sources(config: AppConfig) -> list[tuple[str, int]]:
    """(name, number of faces) of everything that can trigger actions: cubes, then joystick."""
    sources = [(cube.name, CUBE_FACES) for cube in config.cubes]
    sources.append((JOYSTICK_SOURCE, JOYSTICK_FACES))
    return sources


EDITABLE_KIND = "hue"
EDITABLE_ACTION = "scene.activate"
MAX_SCENE_ID_LENGTH = 200


def mapping_field(cube_index: int, face: int) -> str:
    """Form field name for one cube face."""
    return f"map.{cube_index}.{face}"


def scene_of(mapping: RuleMappingConfig | None) -> str | None:
    """Scene ID if the mapping is exactly one Hue scene activation, else ``None``."""
    if mapping is None or len(mapping.actions) != 1:
        return None
    action = mapping.actions[0]
    scene = action.parameters.get("scene_id")
    if (
        action.kind == EDITABLE_KIND
        and action.action == EDITABLE_ACTION
        and set(action.parameters) == {"scene_id"}
        and isinstance(scene, str)
    ):
        return scene
    return None


def _mapping_lookup(config: AppConfig) -> dict[tuple[str, int], RuleMappingConfig]:
    return {(m.cube.casefold(), m.face): m for m in config.mappings}


def mapping_config_to_form(config: AppConfig) -> dict[str, str]:
    """Selected scene ID per editable face; faces with other actions have no field."""
    lookup = _mapping_lookup(config)
    values: dict[str, str] = {}
    for index, (name, faces) in enumerate(mapping_sources(config)):
        for face in range(1, faces + 1):
            mapping = lookup.get((name.casefold(), face))
            if mapping is None:
                values[mapping_field(index, face)] = ""
            elif (scene := scene_of(mapping)) is not None:
                values[mapping_field(index, face)] = scene
    return values


@dataclass(frozen=True, slots=True)
class MappingResult:
    """Parsed mapping submission: raw mapping tables, or per-field errors."""

    mappings: list[dict[str, object]] | None
    errors: dict[str, str]


def parse_mapping_form(
    form: Mapping[str, str], config: AppConfig, known_scene_ids: Collection[str]
) -> MappingResult:
    """Build the new mapping list from the submitted scene choices.

    Faces whose actions the form cannot represent (several actions or other plugins) are
    kept exactly as they are. A scene may only be newly chosen from the loaded Hue scene
    list; an already configured scene stays valid even if the bridge is offline.
    """
    lookup = _mapping_lookup(config)
    known = set(known_scene_ids)
    errors: dict[str, str] = {}
    result: list[dict[str, object]] = []
    for index, (name, faces) in enumerate(mapping_sources(config)):
        for face in range(1, faces + 1):
            existing = lookup.get((name.casefold(), face))
            if existing is not None and scene_of(existing) is None:
                result.append(_raw_mapping(existing))
                continue
            field = mapping_field(index, face)
            chosen = form.get(field, "").strip()
            if not chosen:
                continue
            if len(chosen) > MAX_SCENE_ID_LENGTH or any(ord(c) < 32 for c in chosen):
                errors[field] = "Diese Auswahl ist ungültig."
                continue
            if chosen != scene_of(existing):
                if not known:
                    errors[field] = (
                        "Es sind noch keine Hue-Szenen geladen. Verbinden Sie zuerst Hue "
                        "auf der Seite Licht."
                    )
                    continue
                if chosen not in known:
                    errors[field] = "Diese Szene gibt es in der geladenen Liste nicht."
                    continue
            result.append(
                {
                    "cube": name,
                    "face": face,
                    "actions": [
                        {
                            "kind": EDITABLE_KIND,
                            "action": EDITABLE_ACTION,
                            "parameters": {"scene_id": chosen},
                        }
                    ],
                }
            )
    return MappingResult(None if errors else result, errors)


def _raw_mapping(mapping: RuleMappingConfig) -> dict[str, object]:
    return {
        "cube": mapping.cube,
        "face": mapping.face,
        "actions": [
            {
                "kind": action.kind,
                "action": action.action,
                "parameters": dict(action.parameters),
            }
            for action in mapping.actions
        ],
    }
