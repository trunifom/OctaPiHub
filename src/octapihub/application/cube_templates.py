"""Ready-made cube profiles ("Licht-Würfel", "Musik-Würfel") that need no personal data.

A template is an ordinary cube profile (see ``backup.cube_profile``) and is applied through the
same import path, so the old file is backed up and everything is validated first. Templates use
only generic controls (no scene, playlist or device names) and therefore work on every setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Colours of the palette in web/forms.py (stored as hex in the file).
_GREEN, _BLUE, _PURPLE, _ORANGE, _RED, _GRAY, _TEAL, _PINK = (
    "#2fbf71",
    "#3b82f6",
    "#8b5cf6",
    "#f59e0b",
    "#ef4444",
    "#94a3b8",
    "#14b8a6",
    "#ec4899",
)


@dataclass(frozen=True, slots=True)
class CubeTemplate:
    """One template: what it is for and the faces it sets."""

    id: str
    name: str
    description: str
    kind: str  # the plugin it controls: "hue" or "music"
    faces: tuple[tuple[int, str, str, str, str], ...]  # face, kind, action, name, colour


TEMPLATES: tuple[CubeTemplate, ...] = (
    CubeTemplate(
        "licht",
        "Licht-Würfel",
        "Ein/Aus, heller, dunkler, Farbe wechseln, Warmweiß und Aus. Braucht Hue.",
        "hue",
        (
            (1, "hue", "light.toggle", "Licht Ein/Aus", _GREEN),
            (2, "hue", "light.brighter", "Heller", _ORANGE),
            (3, "hue", "light.darker", "Dunkler", _GRAY),
            (4, "hue", "light.color.next", "Farbe wechseln", _PINK),
            (5, "hue", "light.color", "Warmweiß", _ORANGE),
            (6, "hue", "light.off", "Alles aus", _RED),
        ),
    ),
    CubeTemplate(
        "musik",
        "Musik-Würfel",
        "Pause/Weiter, nächster und vorheriger Titel, lauter, leiser und Stopp. Braucht Musik.",
        "music",
        (
            (1, "music", "toggle", "Pause / Weiter", _GREEN),
            (2, "music", "next", "Nächster Titel", _BLUE),
            (3, "music", "previous", "Vorheriger Titel", _PURPLE),
            (4, "music", "volume.up", "Lauter", _ORANGE),
            (5, "music", "volume.down", "Leiser", _TEAL),
            (6, "music", "stop", "Stopp", _RED),
        ),
    ),
)


def template_by_id(template_id: str) -> CubeTemplate | None:
    """The template with this id, or ``None``."""
    return next((t for t in TEMPLATES if t.id == template_id), None)


def template_profile(template: CubeTemplate) -> dict[str, Any]:
    """The template as a cube profile (mappings and labels, no cube identity)."""
    mappings = []
    for face, kind, action, _name, _colour in template.faces:
        parameters: dict[str, object] = {"color": "warm"} if action == "light.color" else {}
        mappings.append(
            {
                "face": face,
                "actions": [{"kind": kind, "action": action, "parameters": parameters}],
            }
        )
    labels = [
        {"face": face, "name": name, "color": colour}
        for face, _kind, _action, name, colour in template.faces
    ]
    return {"name": template.name, "mappings": mappings, "labels": labels}
