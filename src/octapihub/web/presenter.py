"""Pure presentation logic: German labels, rounding for display, and status notices.

Nothing here changes stored values; rounding happens only when text is produced.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo

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
from octapihub.core.config import SenseHatConfig, SenseHatVersion, SensorPresentation

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
