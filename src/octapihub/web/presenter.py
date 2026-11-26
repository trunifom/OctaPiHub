"""Pure presentation logic: German labels, rounding for display, and status notices.

Nothing here changes stored values; rounding happens only when text is produced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo

from octapihub.application.ble_runtime import CubeConnectionState, CubeRuntimeStatus
from octapihub.application.hue_service import HueState, HueStatus
from octapihub.application.matrix.canvas import BLACK, Frame
from octapihub.application.music.service import MusicStatus
from octapihub.application.sense_hat import (
    ColorSensorReading,
    Orientation,
    SenseHatCapabilities,
    SenseHatVersionSource,
    Vector3,
)
from octapihub.application.sense_hat_worker import (
    SenseHatSample,
    SensorGroupState,
    SensorGroupStatus,
)
from octapihub.core.config import (
    JOYSTICK_FACE_LABELS,
    JOYSTICK_SOURCE,
    AppConfig,
    RuleActionConfig,
    RuleMappingConfig,
    SenseHatConfig,
    SenseHatVersion,
    SensorPresentation,
)
from octapihub.core.logging_support import LogLine
from octapihub.infrastructure.system_info import SystemSnapshot
from octapihub.web.face_detail import describe_face
from octapihub.web.forms import choice_label, mapping_sources

# Scalar groups that can be drawn as a time series.
CHARTABLE_GROUPS = ("temperature", "humidity", "pressure", "compass")


@dataclass(frozen=True, slots=True)
class GroupInfo:
    """Static German texts for one sensor group."""

    label: str
    description: str


GROUP_INFO: Mapping[str, GroupInfo] = {
    "temperature": GroupInfo(
        "Temperatur",
        "Lufttemperatur am Sense HAT. Die Wärme des Raspberry Pi verfälscht sie meist nach "
        "oben; unter Einstellungen lässt sie sich korrigieren.",
    ),
    "humidity": GroupInfo("Luftfeuchtigkeit", "Relative Luftfeuchtigkeit in Prozent."),
    "pressure": GroupInfo("Luftdruck", "Luftdruck in Hektopascal (hPa)."),
    "orientation": GroupInfo("Lage", "Neigung des Geräts: Nicken, Rollen und Gieren."),
    "compass": GroupInfo("Kompass", "Richtung relativ zum magnetischen Norden."),
    "gyroscope": GroupInfo("Drehrate", "Wie schnell sich das Gerät um seine Achsen dreht."),
    "accelerometer": GroupInfo("Beschleunigung", "Beschleunigung entlang der drei Achsen."),
    "color": GroupInfo("Farbe und Helligkeit", "Umgebungsfarbe und -licht (nur Sense HAT v2)."),
}

SECTIONS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("environment", "Umgebung", ("temperature", "humidity", "pressure")),
    ("motion", "Bewegung", ("orientation", "compass", "gyroscope", "accelerometer")),
    ("light", "Licht", ("color",)),
)

_UNITS = {"%RH": "%", "mbar": "hPa", "degrees": "°", "radians": "rad"}
_COMPONENT_LABELS: Mapping[str, str] = {
    "pitch": "Nicken",
    "roll": "Rollen",
    "yaw": "Gieren",
    "x": "X",
    "y": "Y",
    "z": "Z",
}
_STATE_TEXT = {
    "ok": "Aktuell",
    "stale": "Veraltet",
    "error": "Nicht verfügbar",
    "waiting": "Wird gemessen …",
    "unavailable": "Nicht auf diesem Sense HAT",
    "stopped": "Angehalten",
}


def format_number(value: float, precision: int) -> str:
    """Round only for display; negative zero is shown as zero."""
    text = f"{value:.{precision}f}"
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def display_unit(unit: str) -> str:
    """Map library unit names to the symbols shown in the interface."""
    return _UNITS.get(unit, unit)


def correction_active(config: SenseHatConfig) -> bool:
    """Whether a temperature correction differs from the identity."""
    return config.temperature_correction_factor != 1.0 or config.temperature_offset_c != 0.0


@dataclass(frozen=True, slots=True)
class GroupView:
    """Everything a template needs to draw one live card."""

    group: str
    label: str
    description: str
    state: str
    state_text: str
    primary: str
    unit: str
    secondary: str
    components: tuple[tuple[str, str], ...]
    swatch: str
    measured_text: str
    error_text: str
    error_code: str
    chartable: bool


def _swatch(reading: ColorSensorReading) -> str:
    red, green, blue, _clear = (max(0, min(255, int(c))) for c in reading.scaled_rgba)
    return f"#{red:02x}{green:02x}{blue:02x}"


def _view_state(status: SensorGroupStatus) -> str:
    match status.state:
        case SensorGroupState.OK:
            return "ok"
        case SensorGroupState.ERROR:
            return "stale" if status.last_sample is not None else "error"
        case SensorGroupState.WAITING:
            return "waiting"
        case SensorGroupState.UNAVAILABLE:
            return "unavailable"
        case _:
            return "stopped"


def build_group_view(
    group: str,
    status: SensorGroupStatus,
    config: SenseHatConfig,
    tz: tzinfo,
) -> GroupView:
    """Format one sensor group for display using its configured precision."""
    info = GROUP_INFO[group]
    precision = config.sensors[group].precision
    state = _view_state(status)
    primary = ""
    unit = ""
    secondary = ""
    components: tuple[tuple[str, str], ...] = ()
    swatch = ""
    measured = ""
    sample: SenseHatSample | None = status.last_sample
    if sample is not None:
        reading = sample.reading
        unit = display_unit(reading.unit)
        value = reading.value
        measured = f"Gemessen um {sample.measured_at.astimezone(tz).strftime('%H:%M:%S')}"
        if isinstance(value, float):
            shown = value
            if group == "temperature" and sample.corrected_temperature_c is not None:
                shown = sample.corrected_temperature_c
                if correction_active(config):
                    secondary = f"Rohwert {format_number(value, precision)} {unit}"
            primary = format_number(shown, precision)
        elif isinstance(value, Orientation | Vector3):
            values = (
                (("pitch", value.pitch), ("roll", value.roll), ("yaw", value.yaw))
                if isinstance(value, Orientation)
                else (("x", value.x), ("y", value.y), ("z", value.z))
            )
            components = tuple(
                (_COMPONENT_LABELS[name], f"{format_number(number, precision)} {unit}".strip())
                for name, number in values
            )
        else:
            swatch = _swatch(value)
            red, green, blue, clear = value.scaled_rgba
            components = (
                ("Rot", str(red)),
                ("Grün", str(green)),
                ("Blau", str(blue)),
                ("Hell", str(clear)),
            )
    if state in {"error", "unavailable", "waiting"} and not primary and not components:
        primary = "–"
    return GroupView(
        group=group,
        label=info.label,
        description=info.description,
        state=state,
        state_text=_STATE_TEXT[state],
        primary=primary,
        unit=unit if primary not in {"", "–"} else "",
        secondary=secondary,
        components=components,
        swatch=swatch,
        measured_text=measured,
        error_text=status.last_error or "",
        error_code=status.last_error_code or "",
        chartable=group in CHARTABLE_GROUPS,
    )


def build_group_views(
    config: SenseHatConfig,
    statuses: Sequence[SensorGroupStatus],
    tz: tzinfo,
) -> tuple[GroupView, ...]:
    """Live cards for groups that are enabled and set to show a value."""
    by_group = {status.group: status for status in statuses}
    views: list[GroupView] = []
    for _key, _title, groups in SECTIONS:
        for group in groups:
            sensor = config.sensors.get(group)
            status = by_group.get(group)
            if (
                sensor is None
                or status is None
                or sensor.presentation
                not in {SensorPresentation.VALUE, SensorPresentation.VALUE_AND_CHART}
            ):
                continue
            views.append(build_group_view(group, status, config, tz))
    return tuple(views)


def chart_groups(config: SenseHatConfig) -> tuple[str, ...]:
    """Groups the user asked to see as a chart and that can be charted."""
    return tuple(
        group
        for group in CHARTABLE_GROUPS
        if (sensor := config.sensors.get(group)) is not None
        and sensor.enabled
        and sensor.presentation in {SensorPresentation.CHART, SensorPresentation.VALUE_AND_CHART}
    )


@dataclass(frozen=True, slots=True)
class Notice:
    """A message for the user: what happened, how severe, and what to do."""

    level: str  # "error" | "warn" | "info" | "ok" | "off"
    title: str
    detail: str = ""
    code: str = ""
    hint: str = ""


_HARDWARE_HINT = (
    "Prüfen Sie, ob das Sense HAT fest aufgesteckt ist, I2C aktiviert ist "
    "(sudo raspi-config) und das Paket sense-hat installiert ist."
)


def build_notices(
    config: SenseHatConfig,
    statuses: Sequence[SensorGroupStatus],
    *,
    hardware_error: str | None,
    hardware_error_code: str | None,
    history_error: str | None,
    history_error_code: str | None,
    running: bool,
) -> tuple[Notice, ...]:
    """Summarize the Sense HAT state; the first notice is the most important."""
    if not config.enabled:
        return (
            Notice(
                "off",
                "Das Sense HAT ist ausgeschaltet",
                "Es werden keine Messwerte erfasst.",
                hint="Schalten Sie es unter Einstellungen ein.",
            ),
        )
    if hardware_error is not None:
        return (
            Notice(
                "error",
                "Das Sense HAT ist nicht erreichbar",
                hardware_error,
                hardware_error_code or "",
                _HARDWARE_HINT,
            ),
        )
    notices: list[Notice] = []
    if running and statuses:
        failing = [s for s in statuses if s.state is SensorGroupState.ERROR]
        unavailable = [s for s in statuses if s.state is SensorGroupState.UNAVAILABLE]
        working = [s for s in statuses if s.state is SensorGroupState.OK]
        if failing:
            first = failing[0]
            names = ", ".join(GROUP_INFO[s.group].label for s in failing)
            notices.append(
                Notice(
                    "warn" if working else "error",
                    "Einzelne Sensoren liefern keine Werte"
                    if working
                    else "Keine Sensoren liefern Werte",
                    f"Betroffen: {names}. {first.last_error or ''}".strip(),
                    first.last_error_code or "",
                    "Die Messung wird automatisch erneut versucht.",
                )
            )
        if unavailable:
            names = ", ".join(GROUP_INFO[s.group].label for s in unavailable)
            notices.append(
                Notice(
                    "info",
                    "Nicht jeder aktivierte Sensor ist vorhanden",
                    f"Auf diesem Sense HAT nicht verfügbar: {names}.",
                    hint="Deaktivieren Sie diese Sensoren unter Einstellungen.",
                )
            )
    if history_error is not None:
        notices.append(
            Notice(
                "warn",
                "Der Verlauf wird nicht gespeichert",
                history_error,
                history_error_code or "",
                "Die aktuellen Werte funktionieren weiter. Prüfen Sie Speicherplatz und "
                "Schreibrechte des Datenordners.",
            )
        )
    if not notices:
        count = sum(1 for s in statuses if s.state is SensorGroupState.OK)
        waiting = bool(running and statuses and count == 0)
        notices.append(
            Notice(
                "info" if waiting else "ok",
                "Die Messung startet …" if waiting else "Alles in Ordnung",
                "" if waiting else f"{count} Sensorgruppen liefern aktuelle Werte.",
            )
        )
    return tuple(notices)


def overall_level(notices: Sequence[Notice]) -> str:
    """Worst level among notices."""
    order = ("error", "warn", "info", "off", "ok")
    levels = {notice.level for notice in notices}
    return next((level for level in order if level in levels), "ok")


def describe_hardware(capabilities: SenseHatCapabilities | None, config: SenseHatConfig) -> str:
    """Plain-language hardware line for the settings page."""
    if capabilities is None:
        return "Noch nicht erkannt – das Sense HAT ist nicht erreichbar oder ausgeschaltet."
    if capabilities.version_source is SenseHatVersionSource.CONFIGURATION:
        version = capabilities.hardware_version or SenseHatVersion.AUTO
        return f"Manuell auf {version.value} eingestellt."
    if capabilities.version_source is SenseHatVersionSource.COLOR_SENSOR:
        return "Sense HAT v2 erkannt (Farbsensor vorhanden)."
    return (
        "Version nicht sicher erkannt. Es wurde kein Farbsensor gemeldet; das beweist "
        "nicht, dass es ein v1 ist. Bei Bedarf unten manuell festlegen."
    )


def local_time_text(moment: datetime, tz: tzinfo) -> str:
    """Wall-clock time in the display time zone."""
    return moment.astimezone(tz).strftime("%d.%m.%Y %H:%M:%S")


# ---------- cubes ----------

_ROLE_TEXT = {"light": "Licht", "music": "Musik", "general": "Universal"}
# state -> (level, headline, what the user can do)
_CUBE_STATE: Mapping[CubeConnectionState, tuple[str, str, str]] = {
    CubeConnectionState.WAITING_FOR_RADIO: (
        "info",
        "Wartet auf Bluetooth",
        "Der Würfel wird gesucht.",
    ),
    CubeConnectionState.CONNECTING: ("info", "Verbindet …", "Das kann einige Sekunden dauern."),
    CubeConnectionState.DISCOVERING: ("info", "Prüft das Gerät …", ""),
    CubeConnectionState.SUBSCRIBING: ("info", "Richtet die Verbindung ein …", ""),
    CubeConnectionState.WAITING_FOR_BASELINE: (
        "info",
        "Verbunden",
        "Legen Sie den Würfel einmal auf eine Seite, damit er sich meldet.",
    ),
    CubeConnectionState.READY: ("ok", "Bereit", ""),
    CubeConnectionState.RECONNECT_WAIT: (
        "warn",
        "Verbindung unterbrochen",
        "Es wird automatisch neu verbunden. Halten Sie den Würfel in Reichweite.",
    ),
    CubeConnectionState.UNSUPPORTED: (
        "error",
        "Gerät nicht unterstützt",
        "Dieses Bluetooth-Gerät sendet nicht die erwarteten Daten. "
        "Prüfen Sie die Adresse in der Konfiguration.",
    ),
    CubeConnectionState.FAILED: (
        "error",
        "Fehler",
        "Details stehen im Protokoll. Starten Sie den Dienst neu.",
    ),
    CubeConnectionState.STOPPED: ("off", "Angehalten", ""),
}
_CUBE_ERROR_CODES = {
    CubeConnectionState.RECONNECT_WAIT: "CH-BLE-002",
    CubeConnectionState.UNSUPPORTED: "CH-BLE-003",
    CubeConnectionState.FAILED: "CH-GENERIC-000",
}
_ACTION_TEXT = {
    "scene.activate": "Szene aktivieren",
    "light.brightness": "Helligkeit setzen",
    "play": "Abspielen",
    "pause": "Pause",
    "resume": "Weiter",
    "toggle": "Pause / Weiter",
    "stop": "Stopp",
    "stop.all": "Alles stoppen",
    "next": "Nächster Titel",
    "previous": "Vorheriger Titel",
    "volume.set": "Lautstärke setzen",
    "volume.up": "Lauter",
    "volume.down": "Leiser",
    "light.toggle": "Ein/Aus umschalten",
    "light.on": "Einschalten",
    "light.off": "Ausschalten",
    "light.brighter": "Heller",
    "light.darker": "Dunkler",
    "light.color": "Farbe setzen",
    "light.color.next": "Nächste Farbe",
    "scene.next": "Nächste Szene",
    "scene.previous": "Vorherige Szene",
    "trigger": "Auslösen",
}
_KIND_TEXT = {"hue": "Hue-Licht", "music": "Musik", "smarthome": "Smart Home"}


@dataclass(frozen=True, slots=True)
class CubeView:
    """One cube card; the Bluetooth address is deliberately not included."""

    name: str
    role: str
    level: str
    connected: bool
    state_text: str
    hint: str
    face_text: str
    face_number: str
    error_text: str
    error_code: str
    attempts: int
    battery_percent: int | None = None
    battery_level: str = "off"
    battery_text: str = ""
    battery_hint: str = ""
    battery_checked: str = ""


@dataclass(frozen=True, slots=True)
class MappingView:
    """One configured cube face and the actions it triggers."""

    cube: str
    face: int
    actions: tuple[str, ...]


def _face_text(value: int | None) -> tuple[str, str]:
    if value is None:
        return "Noch keine Seite erkannt", "–"
    if value == 0:
        return "Zuletzt: keine Seite (Würfel in Zwischenlage)", "0"
    return "Zuletzt erkannte Seite", str(value)


BATTERY_LOW_PERCENT = 20
BATTERY_CRITICAL_PERCENT = 10


def describe_battery(
    status: CubeRuntimeStatus | None, tz: tzinfo
) -> tuple[int | None, str, str, str, str]:
    """(percent, level, text, hint, checked) for the battery of one cube."""
    if status is None or not status.connected and status.battery_percent is None:
        return None, "off", "Unbekannt", "Der Akkustand wird nach dem Verbinden gelesen.", ""
    percent = status.battery_percent
    if percent is None:
        if status.battery_supported is False:
            return (
                None,
                "off",
                "Nicht abrufbar",
                "Dieser Würfel meldet keinen Akkustand.",
                "",
            )
        if status.battery_error:
            return None, "warn", "Nicht lesbar", status.battery_error, ""
        return None, "off", "Wird gelesen …", "", ""
    checked = (
        f"Geprüft um {datetime.fromtimestamp(status.battery_checked_at, tz).strftime('%H:%M')}"
        if status.battery_checked_at is not None
        else ""
    )
    if percent <= BATTERY_CRITICAL_PERCENT:
        return percent, "error", f"{percent} %", "Bitte jetzt laden.", checked
    if percent <= BATTERY_LOW_PERCENT:
        return percent, "warn", f"{percent} %", "Bald laden.", checked
    hint = status.battery_error or ""
    if hint:
        hint = "Letzter Wert; aktuell nicht lesbar."
    return percent, "ok", f"{percent} %", hint, checked


def build_cube_views(
    config: AppConfig,
    statuses: Sequence[CubeRuntimeStatus],
    tz: tzinfo = UTC,
) -> tuple[CubeView, ...]:
    """Cards for all configured cubes; cubes without a status show as stopped."""
    by_name = {status.name: status for status in statuses}
    views: list[CubeView] = []
    for cube in config.cubes:
        status = by_name.get(cube.name)
        state = status.state if status is not None else CubeConnectionState.STOPPED
        level, headline, hint = _CUBE_STATE[state]
        face_text, face_number = _face_text(status.last_value if status else None)
        b_percent, b_level, b_text, b_hint, b_checked = describe_battery(status, tz)
        views.append(
            CubeView(
                name=cube.name,
                role=_ROLE_TEXT.get(cube.role.value, cube.role.value),
                level=level,
                connected=status.connected if status else False,
                state_text=headline,
                hint=hint,
                face_text=face_text,
                face_number=face_number,
                error_text=(status.last_error or "") if status else "",
                error_code=_CUBE_ERROR_CODES.get(state, "") if status and status.last_error else "",
                attempts=status.reconnect_attempt if status else 0,
                battery_percent=b_percent,
                battery_level=b_level,
                battery_text=b_text,
                battery_hint=b_hint,
                battery_checked=b_checked,
            )
        )
    return tuple(views)


def describe_action(action: RuleActionConfig) -> str:
    """Readable one-line summary of a configured action."""
    kind = _KIND_TEXT.get(action.kind, action.kind)
    name = _ACTION_TEXT.get(action.action, action.action)
    parameters = ", ".join(f"{key}: {value}" for key, value in action.parameters.items())
    return f"{kind}: {name}" + (f" ({parameters})" if parameters else "")


def build_mapping_views(config: AppConfig) -> tuple[MappingView, ...]:
    """Configured mappings ordered by cube order, then face."""
    order = {cube.name.casefold(): index for index, cube in enumerate(config.cubes)}
    mappings = sorted(config.mappings, key=lambda m: (order.get(m.cube.casefold(), 99), m.face))
    return tuple(
        MappingView(m.cube, m.face, tuple(describe_action(a) for a in m.actions)) for m in mappings
    )


def battery_warnings(views: Sequence[CubeView]) -> tuple[tuple[str, str], ...]:
    """(level, text) per cube whose battery should be charged soon."""
    return tuple(
        (
            view.battery_level,
            f"Würfel „{view.name}“: Akku {view.battery_text}. {view.battery_hint}".strip(),
        )
        for view in views
        if view.battery_level in {"warn", "error"} and view.battery_percent is not None
    )


def cube_summary(views: Sequence[CubeView]) -> tuple[str, str]:
    """(level, text) for the overview card."""
    if not views:
        return "off", "Keine Würfel eingerichtet."
    ready = sum(1 for v in views if v.connected)
    worst = next(
        (
            lvl
            for lvl in ("error", "warn", "info", "off", "ok")
            if any(v.level == lvl for v in views)
        ),
        "ok",
    )
    return worst, f"{ready} von {len(views)} Würfeln verbunden."


# ---------- hue ----------


@dataclass(frozen=True, slots=True)
class HueView:
    """Status of the Hue connection in plain language."""

    level: str
    badge: str
    headline: str
    detail: str
    hint: str
    error_text: str
    error_code: str
    host: str
    checked_text: str
    state: str


def build_hue_view(status: HueStatus, tz: tzinfo) -> HueView:
    """Describe the Hue state; connection is only claimed after a successful check."""
    checked = (
        f"Zuletzt geprüft am {local_time_text(status.scenes_checked_at, tz)}"
        if status.scenes_checked_at is not None
        else "Noch nicht geprüft"
    )
    base = {
        "error_text": status.last_error or "",
        "error_code": status.last_error_code or "",
        "host": status.host,
        "checked_text": checked,
        "state": status.state.value,
    }
    if status.state is HueState.DISABLED:
        return HueView(
            "off",
            "Ausgeschaltet",
            "Hue ist ausgeschaltet",
            "Die Würfel können kein Licht steuern.",
            "Schalten Sie Hue unten ein und tragen Sie die Adresse Ihrer Bridge ein.",
            **base,
        )
    if status.state is HueState.NOT_PAIRED:
        return HueView(
            "warn",
            "Nicht verbunden",
            "Die Bridge ist noch nicht verbunden",
            "Der Würfel kann noch kein Licht schalten.",
            "Drücken Sie die runde Taste auf der Hue Bridge und klicken Sie danach auf "
            "„Mit Bridge verbinden“.",
            **base,
        )
    if status.last_error:
        return HueView(
            "error",
            "Störung",
            "Die Bridge antwortet nicht wie erwartet",
            status.last_error,
            "Prüfen Sie Netzwerk, Adresse und Zertifikat der Bridge. Fehlgeschlagene Aktionen "
            "werden automatisch wiederholt.",
            **base,
        )
    return HueView(
        "ok",
        "Gekoppelt",
        "Mit der Bridge gekoppelt",
        "Die Verbindung wurde bestätigt." if status.scenes_checked_at else "",
        "" if status.scenes_checked_at else "Laden Sie die Szenen, um die Verbindung zu testen.",
        **base,
    )


# ---------- mapping editor ----------


@dataclass(frozen=True, slots=True)
class FaceTile:
    """One cube face in the mapping editor."""

    field: str
    face: int
    scene_id: str
    scene_label: str
    locked: bool
    locked_actions: tuple[str, ...]
    label: str = ""
    chip: str = ""
    saved: bool = False
    target: str = ""
    shuffle: str = ""
    repeat: str = ""
    group: str = ""
    name: str = ""
    color: str = ""
    detail: tuple[str, ...] = ()
    summary: str = ""
    delay: str = ""
    when_start: str = ""
    when_end: str = ""
    days: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class CubeTiles:
    """All eight faces of one cube."""

    name: str
    role: str
    tiles: tuple[FaceTile, ...]
    index: int = 0
    music_target: str = ""


_JOYSTICK_CHIPS = ("▲", "▶", "▼", "◀", "●")


def _face_summary(mapping: RuleMappingConfig | None, choice_text: str) -> str:
    """One short line: what the face does."""
    if mapping is None:
        return "Keine Aktion"
    if len(mapping.actions) > 1:
        return f"{len(mapping.actions)} Schritte"
    return choice_text or describe_action(mapping.actions[0])


def build_cube_tiles(
    config: AppConfig,
    scenes: Mapping[str, str],
    form: Mapping[str, str],
    playlists: Mapping[str, str] | None = None,
    groups: Mapping[str, str] | None = None,
) -> tuple[CubeTiles, ...]:
    """Tiles for the editor; ``form`` holds the currently selected choices (scene or music)."""
    playlist_names = playlists or {}
    lookup = {(m.cube.casefold(), m.face): m for m in config.mappings}
    result: list[CubeTiles] = []
    for index, (name, faces) in enumerate(mapping_sources(config)):
        is_joystick = name == JOYSTICK_SOURCE
        tiles: list[FaceTile] = []
        for face in faces:
            field = f"map.{index}.{face}"
            existing = lookup.get((name.casefold(), face))
            locked = existing is not None and field not in form
            scene_id = form.get(field, "")
            label = choice_label(scene_id, scenes, playlist_names) if scene_id else ""
            tiles.append(
                FaceTile(
                    field=field,
                    face=face,
                    scene_id=scene_id,
                    scene_label=label,
                    locked=locked,
                    locked_actions=tuple(describe_action(a) for a in existing.actions)
                    if existing is not None and locked
                    else (),
                    label=(
                        JOYSTICK_FACE_LABELS[face - 1]
                        if is_joystick
                        else ("Halterung" if face == 0 else f"Seite {face}")
                    ),
                    chip=(
                        _JOYSTICK_CHIPS[face - 1]
                        if is_joystick
                        else ("⏏" if face == 0 else str(face))
                    ),
                    saved=existing is not None,
                    target=form.get(f"{field}.target", ""),
                    shuffle=form.get(f"{field}.shuffle", ""),
                    repeat=form.get(f"{field}.repeat", ""),
                    group=form.get(f"{field}.group", ""),
                    name=form.get(f"{field}.label", ""),
                    color=form.get(f"{field}.color", ""),
                    detail=tuple(describe_face(config, existing, scenes, groups or {})),
                    summary=_face_summary(existing, label),
                    delay=form.get(f"{field}.delay", ""),
                    when_start=form.get(f"{field}.when_start", ""),
                    when_end=form.get(f"{field}.when_end", ""),
                    days=tuple(d for d in range(7) if form.get(f"{field}.day.{d}")),
                )
            )
        if is_joystick:
            role = "Sense HAT"
        else:
            cube_role = config.cubes[index].role.value
            role = _ROLE_TEXT.get(cube_role, cube_role)
        result.append(
            CubeTiles(name, role, tuple(tiles), index, form.get(f"cubetarget.{index}", ""))
        )
    return tuple(result)


# ---------- system ----------


@dataclass(frozen=True, slots=True)
class SystemItem:
    """One health value with its severity."""

    key: str
    label: str
    value: str
    detail: str
    level: str
    percent: int | None = None


@dataclass(frozen=True, slots=True)
class LogRow:
    """One log line for display."""

    time: str
    level: str
    logger: str
    message: str


def _duration_text(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days} Tage, {hours} Std."
    if hours:
        return f"{hours} Std., {minutes} Min."
    return f"{minutes} Min."


def _size_text(kilobytes: float) -> str:
    megabytes = kilobytes / 1024
    return f"{megabytes / 1024:.1f} GB" if megabytes >= 1024 else f"{megabytes:.0f} MB"


UNAVAILABLE = "Nicht verfügbar"
_PI_ONLY = "Nur auf einem Raspberry Pi verfügbar."


def _time_item(snapshot: SystemSnapshot, tz: tzinfo, zone_name: str) -> SystemItem:
    zone = f"Zeitzone {zone_name}. " if zone_name else ""
    if snapshot.time_synced is True:
        detail, level = f"{zone}Die Uhr wird über das Internet (NTP) automatisch gestellt.", "ok"
    elif snapshot.time_synced is False:
        detail = (
            f"{zone}Die Uhr ist noch nicht mit dem Internet abgeglichen. Ohne Netz kann sie "
            "abweichen; mit `timedatectl` prüfen, ob NTP eingeschaltet ist."
        )
        level = "warn"
    else:
        detail, level = f"{zone}Falsche Zeit? Prüfen Sie die Netzwerkzeit (NTP) des Geräts.", "ok"
    return SystemItem("time", "Uhrzeit", local_time_text(snapshot.taken_at, tz), detail, level)


def build_system_items(
    snapshot: SystemSnapshot, tz: tzinfo, zone_name: str = ""
) -> tuple[SystemItem, ...]:
    """Turn a snapshot into labelled items; unavailable values are explained, not hidden."""
    items: list[SystemItem] = [_time_item(snapshot, tz, zone_name)]
    items.append(
        SystemItem(
            "uptime",
            "Laufzeit",
            _duration_text(snapshot.uptime_seconds)
            if snapshot.uptime_seconds is not None
            else UNAVAILABLE,
            "Seit dem letzten Start des Geräts."
            if snapshot.uptime_seconds is not None
            else _PI_ONLY,
            "ok" if snapshot.uptime_seconds is not None else "off",
        )
    )
    temperature = snapshot.cpu_temperature_c
    if temperature is None:
        items.append(SystemItem("temperature", "CPU-Temperatur", UNAVAILABLE, _PI_ONLY, "off"))
    else:
        level = "error" if temperature >= 80 else "warn" if temperature >= 70 else "ok"
        detail = {
            "error": "Zu heiß: Der Raspberry Pi drosselt sich. Sorgen Sie für Kühlung und Luftzug.",
            "warn": "Warm. Bei dauerhaft über 80 °C drosselt sich der Raspberry Pi.",
            "ok": "Normal.",
        }[level]
        items.append(
            SystemItem(
                "temperature",
                "CPU-Temperatur",
                f"{format_number(temperature, 1)} °C",
                detail,
                level,
            )
        )
    percent = snapshot.memory_used_percent
    if percent is None or snapshot.memory_total_kb is None or snapshot.memory_available_kb is None:
        items.append(SystemItem("memory", "Arbeitsspeicher", UNAVAILABLE, _PI_ONLY, "off"))
    else:
        used = snapshot.memory_total_kb - snapshot.memory_available_kb
        level = "error" if percent >= 90 else "warn" if percent >= 80 else "ok"
        items.append(
            SystemItem(
                "memory",
                "Arbeitsspeicher",
                f"{percent:.0f} % belegt",
                f"{_size_text(used)} von {_size_text(snapshot.memory_total_kb)}.",
                level,
                round(percent),
            )
        )
    throttle = snapshot.throttle
    if throttle is None:
        items.append(SystemItem("power", "Stromversorgung", UNAVAILABLE, _PI_ONLY, "off"))
    elif throttle.under_voltage_now:
        items.append(
            SystemItem(
                "power",
                "Stromversorgung",
                "Unterspannung",
                "Das Netzteil liefert zu wenig Spannung. Verwenden Sie ein passendes "
                "Netzteil und ein kurzes Kabel.",
                "error",
            )
        )
    elif throttle.any_now:
        items.append(
            SystemItem(
                "power",
                "Stromversorgung",
                "Gedrosselt",
                "Der Prozessor wird gerade gebremst (Hitze oder Strom).",
                "warn",
            )
        )
    elif throttle.any_occurred:
        items.append(
            SystemItem(
                "power",
                "Stromversorgung",
                "In Ordnung",
                "Seit dem Start gab es zeitweise Unterspannung oder Drosselung.",
                "warn",
            )
        )
    else:
        items.append(
            SystemItem(
                "power", "Stromversorgung", "In Ordnung", "Keine Probleme seit dem Start.", "ok"
            )
        )
    if snapshot.load_average is not None:
        items.append(
            SystemItem(
                "load",
                "Auslastung",
                " / ".join(format_number(v, 2) for v in snapshot.load_average),
                "Durchschnitt der letzten 1, 5 und 15 Minuten.",
                "ok",
            )
        )
    if snapshot.disk_total_bytes and snapshot.disk_free_bytes is not None:
        free_percent = snapshot.disk_free_bytes * 100 / snapshot.disk_total_bytes
        level = "error" if free_percent < 5 else "warn" if free_percent < 15 else "ok"
        items.append(
            SystemItem(
                "disk",
                "Speicherplatz",
                f"{_size_text(snapshot.disk_free_bytes / 1024)} frei",
                f"{free_percent:.0f} % von "
                f"{_size_text(snapshot.disk_total_bytes / 1024)} sind frei.",
                level,
                round(100 - free_percent),
            )
        )
    return tuple(items)


def build_log_rows(lines: Sequence[LogLine], tz: tzinfo) -> tuple[LogRow, ...]:
    """Format recent log lines (already redacted) for the table."""
    return tuple(
        LogRow(
            line.time.astimezone(tz).strftime("%H:%M:%S"),
            line.level,
            line.logger.removeprefix("octapihub."),
            line.message,
        )
        for line in lines
    )


# ---------- LED matrix ----------


def frame_to_hex(frame: Frame) -> str:
    """Compact form of a frame for the browser: 64 pixels as ``rrggbb`` (384 characters)."""
    return "".join(f"{r:02x}{g:02x}{b:02x}" for r, g, b in frame)


def frame_to_fills(frame: Frame) -> list[str]:
    """CSS colours per pixel for server-rendered LED grids; an empty string means 'off'."""
    return [
        "" if pixel == BLACK else f"#{pixel[0]:02x}{pixel[1]:02x}{pixel[2]:02x}" for pixel in frame
    ]


@dataclass(frozen=True, slots=True)
class ViewCard:
    """One view in the gallery."""

    id: str
    label: str
    description: str
    controls: str
    enabled: bool
    active: bool
    thumbnail: list[str]


_DISPLAY_STATE_TEXT = {
    "running": ("ok", "Läuft"),
    "stopped": ("off", "Angehalten"),
    "disabled": ("off", "Ausgeschaltet"),
    "unavailable": ("warn", "Sense HAT nicht erreichbar"),
    "error": ("error", "Störung"),
}


def describe_display_state(state: str) -> tuple[str, str]:
    """(level, text) for the matrix state."""
    return _DISPLAY_STATE_TEXT.get(state, ("off", state))


# ---------- music ----------


@dataclass(frozen=True, slots=True)
class MusicSummary:
    """The music card of the overview in plain language."""

    level: str
    badge: str
    headline: str
    detail: str = ""


def build_music_summary(status: MusicStatus | None, playlists: int = 0) -> MusicSummary:
    """Describe what plays; errors of one device never hide that another one plays."""
    if status is None:
        return MusicSummary("off", "Nicht aktiv", "Musik ist in diesem Modus nicht aktiv.")
    if not status.enabled:
        return MusicSummary(
            "off", "Aus", "Musik ist ausgeschaltet.", "Einschalten auf der Seite Musik."
        )
    if not status.targets:
        return MusicSummary(
            "info",
            "Kein Gerät",
            "Es ist noch kein Wiedergabegerät eingerichtet.",
            "Legen Sie auf der Seite Musik ein Gerät an.",
        )
    active = [t for t in status.targets if t.state.value in ("playing", "paused", "buffering")]
    if active:
        best = sorted(active, key=lambda t: (t.state.value != "playing", not t.current))[0]
        label = {"playing": "Spielt", "paused": "Pausiert", "buffering": "Lädt"}[best.state.value]
        what = " · ".join(x for x in (best.track_title, best.title) if x) or "Musik"
        volume = f" · Lautstärke {best.volume} %" if best.volume is not None else ""
        return MusicSummary(
            "ok" if best.state.value == "playing" else "info",
            label,
            f"{what} auf {best.name}{volume}",
        )
    failing = [t for t in status.targets if t.error]
    if failing:
        first = failing[0]
        return MusicSummary(
            "warn",
            "Fehler",
            f"{first.name}: {first.error}",
            f"Fehlercode {first.error_code}" if first.error_code else "",
        )
    count = len(status.targets)
    return MusicSummary(
        "ok",
        "Bereit",
        f"{count} Wiedergabegerät{'e' if count != 1 else ''}, "
        f"{playlists} Playlist{'s' if playlists != 1 else ''}.",
    )
