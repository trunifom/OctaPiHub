"""What the LED matrix is told about the hub: a plain, immutable snapshot."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

LEVELS = ("ok", "info", "warn", "error", "off")
_SEVERITY = {"error": 4, "warn": 3, "info": 2, "ok": 1, "off": 0}
BATTERY_LOW_PERCENT = 20
BATTERY_CRITICAL_PERCENT = 10


@dataclass(frozen=True, slots=True)
class CubeDisplay:
    """One configured cube as the matrix shows it."""

    name: str
    role: str
    level: str
    connected: bool
    last_face: int | None = None
    battery_percent: int | None = None


@dataclass(frozen=True, slots=True)
class Reading:
    """The latest value of one Sense HAT sensor group (scalar, vector, or colour)."""

    value: float | None = None
    vector: tuple[float, float, float] | None = None
    rgba: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class DisplayState:
    """Snapshot rendered by the views.

    ``readings`` holds only groups that currently have a value; ``history`` holds the most
    recent values per group, oldest first, for the small graph view.
    """

    now: datetime
    cubes: tuple[CubeDisplay, ...] = ()
    hue_level: str = "off"
    sense_level: str = "off"
    host_level: str = "off"
    joystick_faces: tuple[int, ...] = ()
    readings: Mapping[str, Reading] = field(default_factory=lambda: MappingProxyType({}))
    history: Mapping[str, tuple[float, ...]] = field(default_factory=lambda: MappingProxyType({}))


def worst_level(levels: list[str] | tuple[str, ...]) -> str:
    """Most severe of the given levels; ``off`` entries never raise the result."""
    best = "off"
    for level in levels:
        if _SEVERITY.get(level, 0) > _SEVERITY[best]:
            best = level
    return best


def battery_level(percent: int | None) -> str:
    """Severity of one battery value; unknown is ``off``."""
    if percent is None:
        return "off"
    if percent <= BATTERY_CRITICAL_PERCENT:
        return "error"
    if percent <= BATTERY_LOW_PERCENT:
        return "warn"
    return "ok"


def overall_level(state: DisplayState) -> str:
    """Single severity for the whole hub (batteries count, unknown parts do not)."""
    levels = [cube.level for cube in state.cubes]
    levels.extend(battery_level(cube.battery_percent) for cube in state.cubes)
    levels.extend([state.hue_level, state.sense_level, state.host_level])
    return worst_level(levels) if any(level != "off" for level in levels) else "off"


_LEVEL_WORDS = {"ok": "OK", "info": "START", "warn": "HINWEIS", "error": "FEHLER", "off": "AUS"}


def level_word(level: str) -> str:
    """Short German word for a level, usable in the 3x5 font."""
    return _LEVEL_WORDS.get(level, "?")


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def status_messages(state: DisplayState) -> list[tuple[str, str]]:
    """Short (text, level) status lines for the ticker, most important first."""
    messages: list[tuple[str, str]] = []
    for cube in state.cubes:
        messages.append((f"{cube.name} {level_word(cube.level)}", cube.level))
        if cube.battery_percent is not None:
            messages.append(
                (f"{cube.name} AKKU {cube.battery_percent}%", battery_level(cube.battery_percent))
            )
    messages.append((f"HUE {level_word(state.hue_level)}", state.hue_level))
    messages.append((f"SENSOR {level_word(state.sense_level)}", state.sense_level))
    temperature = state.readings.get("temperature")
    if temperature is not None and _finite(temperature.value):
        messages.append((f"TEMP {float(temperature.value):.0f} GRAD", "ok"))  # type: ignore[arg-type]
    humidity = state.readings.get("humidity")
    if humidity is not None and _finite(humidity.value):
        messages.append((f"FEUCHTE {float(humidity.value):.0f}%", "ok"))  # type: ignore[arg-type]
    messages.sort(key=lambda item: -_SEVERITY.get(item[1], 0))
    return messages
