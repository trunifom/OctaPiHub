"""Light control for Hue groups: pure rules that turn an action into a bridge command.

The bridge talks about *grouped lights* (one per room, zone, and the whole home). This module
knows the actions (on, off, toggle, brighter, darker, set brightness, colour, next colour,
next/previous scene) and the colour palette; it has no network access.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from octapihub.core.errors import ValidationError

DEFAULT_STEP_PERCENT = 15
# name -> (German label, ("ct", mirek) for white tones or ("xy", (x, y)) for colours)
COLORS: dict[str, tuple[str, tuple[str, object]]] = {
    "warm": ("Warmweiß", ("ct", 454)),
    "neutral": ("Neutralweiß", ("ct", 300)),
    "cool": ("Kaltweiß", ("ct", 200)),
    "red": ("Rot", ("xy", (0.675, 0.322))),
    "orange": ("Orange", ("xy", (0.600, 0.375))),
    "yellow": ("Gelb", ("xy", (0.444, 0.516))),
    "green": ("Grün", ("xy", (0.214, 0.709))),
    "cyan": ("Türkis", ("xy", (0.170, 0.340))),
    "blue": ("Blau", ("xy", (0.153, 0.048))),
    "purple": ("Violett", ("xy", (0.272, 0.109))),
    "pink": ("Pink", ("xy", (0.382, 0.160))),
}
COLOR_CYCLE = tuple(COLORS)

# action -> German label (for the interface)
LIGHT_ACTIONS = {
    "light.toggle": "Ein/Aus umschalten",
    "light.on": "Einschalten",
    "light.off": "Ausschalten",
    "light.brighter": "Heller",
    "light.darker": "Dunkler",
    "light.color.next": "Nächste Farbe",
    "scene.next": "Nächste Szene",
    "scene.previous": "Vorherige Szene",
}
LIGHT_ACTION_NAMES = frozenset(
    {*LIGHT_ACTIONS, "light.color", "light.brightness", "scene.activate"}
)


@dataclass(frozen=True, slots=True)
class HueGroup:
    """A group of lights as the bridge offers it: ``id`` is the grouped-light resource."""

    id: str
    name: str
    kind: str  # "room", "zone" or "home"
    room_id: str = ""  # the room/zone/home resource that scenes refer to


def _bad(message: str, user_message: str) -> ValidationError:
    return ValidationError(message, user_message=user_message)


def step_percent(parameters: Mapping[str, object]) -> int:
    """The brightness step (1 to 100 percent) of a ``brighter``/``darker`` action."""
    raw = parameters.get("step", DEFAULT_STEP_PERCENT)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 100:
        raise _bad(
            "step must be an integer from 1 to 100",
            "Die Schrittweite muss eine Zahl von 1 bis 100 sein.",
        )
    return raw


def brightness_percent(parameters: Mapping[str, object]) -> float:
    """The target brightness of ``light.brightness`` as percent (``level`` is 0.0 to 1.0)."""
    level = parameters.get("level")
    if (
        isinstance(level, bool)
        or not isinstance(level, (int, float))
        or not math.isfinite(level)
        or not 0 <= level <= 1
    ):
        raise _bad(
            "level must be a number from 0 to 1",
            "Die Helligkeit muss eine Zahl von 0 bis 1 sein.",
        )
    return round(max(1.0, float(level) * 100), 1)  # 0 % would only be "off"


def color_body(name: object) -> dict[str, object]:
    """The bridge command that sets a palette colour."""
    if not isinstance(name, str) or name not in COLORS:
        raise _bad(
            "Unknown colour", "Diese Farbe gibt es nicht. Erlaubt sind: " + ", ".join(COLORS)
        )
    kind, value = COLORS[name][1]
    if kind == "ct":
        return {"on": {"on": True}, "color_temperature": {"mirek": value}}
    x, y = cast(tuple[float, float], value)
    return {"on": {"on": True}, "color": {"xy": {"x": x, "y": y}}}


def cycle_index(current: int, count: int, step: int) -> int:
    """Next position when stepping through ``count`` items from ``current`` (-1 = not started)."""
    if count <= 0:
        raise _bad(
            "Nothing to cycle", "Es gibt nichts zum Durchschalten (keine Szenen für diese Gruppe)."
        )
    if current < 0:
        return 0 if step > 0 else count - 1
    return (current + step) % count


def simple_body(
    action: str, parameters: Mapping[str, object], currently_on: bool | None
) -> dict[str, object]:
    """The bridge command for the actions that need no cycling state.

    ``currently_on`` is only needed for ``light.toggle``.
    """
    if action == "light.on":
        return {"on": {"on": True}}
    if action == "light.off":
        return {"on": {"on": False}}
    if action == "light.toggle":
        return {"on": {"on": not bool(currently_on)}}
    if action == "light.brighter":
        return {
            "on": {"on": True},
            "dimming_delta": {"action": "up", "brightness_delta": step_percent(parameters)},
        }
    if action == "light.darker":
        return {"dimming_delta": {"action": "down", "brightness_delta": step_percent(parameters)}}
    if action == "light.brightness":
        return {"on": {"on": True}, "dimming": {"brightness": brightness_percent(parameters)}}
    if action == "light.color":
        return color_body(parameters.get("color"))
    raise _bad("Unsupported Hue action", "Diese Hue-Aktion wird nicht unterstützt.")


def default_group(groups: Sequence[HueGroup]) -> HueGroup | None:
    """The whole-home group, else the only group."""
    for group in groups:
        if group.kind == "home":
            return group
    return groups[0] if len(groups) == 1 else None
