"""Export and import of settings as a portable JSON bundle.

A bundle holds whole sections of the configuration (never secrets, never the cube list or
Bluetooth adapter, which belong to one installation). Importing validates every section
against the current configuration first; nothing is changed unless all selected sections
are valid.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from octapihub.core.config import (
    JOYSTICK_SOURCE,
    AppConfig,
    face_labels_table,
    hue_table,
    mappings_table,
    music_table,
    sense_hat_table,
    with_face_labels_table,
    with_hue_table,
    with_mappings_table,
    with_music_table,
    with_sense_hat_table,
)
from octapihub.core.errors import ValidationError

FORMAT = "octapihub-export"
VERSION = 1
MAX_BUNDLE_BYTES = 512 * 1024

SECTION_LABELS = {
    "music": "Musik: Wiedergabegeräte und Playlists",
    "playlists": "Nur Playlists (ergänzt vorhandene)",
    "mappings": "Zuordnung der Würfelseiten und des Joysticks",
    "labels": "Namen und Farben der Würfelseiten",
    "cube": "Ein einzelner Würfel (Zuordnung, Namen, Farben), z. B. für einen weiteren Würfel",
    "sense_hat": "Sense HAT: Sensoren, Verlauf und LED-Anzeige",
    "hue": "Hue: Verbindungseinstellungen (ohne Schlüssel)",
}
SECTION_ORDER = tuple(SECTION_LABELS)


def _bad(message: str, user_message: str) -> ValidationError:
    return ValidationError(message, user_message=user_message)


@dataclass(frozen=True, slots=True)
class SectionPreview:
    """What importing one section would do."""

    key: str
    label: str
    valid: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Bundle:
    """A parsed export file."""

    created: str
    sections: Mapping[str, Any]
    ignored: tuple[str, ...] = ()


def build_bundle(config: AppConfig, sections: list[str], created: datetime, cube: str = "") -> str:
    """The JSON text exporting ``sections`` of ``config``.

    Raises:
        ValidationError: If a section name is unknown or none is selected.
    """
    chosen = [s for s in SECTION_ORDER if s in sections]
    unknown = [s for s in sections if s not in SECTION_LABELS]
    if unknown or not chosen:
        raise _bad(
            "Unknown or empty export selection",
            "Bitte mindestens einen gültigen Bereich zum Exportieren wählen.",
        )
    data: dict[str, Any] = {}
    for key in chosen:
        if key == "music":
            data[key] = music_table(config.music)
        elif key == "playlists":
            data[key] = music_table(config.music)["playlists"]
        elif key == "mappings":
            data[key] = mappings_table(config)
        elif key == "labels":
            data[key] = face_labels_table(config)
        elif key == "cube":
            data[key] = cube_profile(config, cube)
        elif key == "sense_hat":
            table = sense_hat_table(config.sense_hat)
            history = dict(table["history"])  # type: ignore[call-overload]
            history.pop("database_path", None)  # a path belongs to one installation
            table["history"] = history
            data[key] = table
        else:
            data[key] = hue_table(config.hue)
    bundle = {
        "format": FORMAT,
        "version": VERSION,
        "created": created.isoformat(timespec="seconds"),
        "sections": data,
    }
    return json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"


def parse_bundle(text: str) -> Bundle:
    """Read an export file.

    Raises:
        ValidationError: If it is too large, not JSON, not an OctaPiHub export, or newer
            than this program understands.
    """
    if len(text.encode("utf-8", errors="replace")) > MAX_BUNDLE_BYTES:
        raise _bad("Bundle too large", "Die Datei ist zu groß für einen Export (höchstens 512 KB).")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _bad("Bundle is not JSON", "Der Inhalt ist keine gültige Export-Datei.") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise _bad("Not an OctaPiHub export", "Das ist keine Export-Datei von OctaPiHub.")
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise _bad("Bad bundle version", "Die Version der Export-Datei ist ungültig.")
    if version > VERSION:
        raise _bad(
            "Bundle is newer",
            "Diese Export-Datei stammt aus einer neueren Version von OctaPiHub.",
        )
    raw = data.get("sections")
    if not isinstance(raw, dict) or not raw:
        raise _bad("Bundle has no sections", "Die Export-Datei enthält keine Einstellungen.")
    known = {key: value for key, value in raw.items() if key in SECTION_LABELS}
    ignored = tuple(sorted(str(k) for k in raw if k not in SECTION_LABELS))
    if not known:
        raise _bad("No known sections", "Die Export-Datei enthält keine bekannten Bereiche.")
    created = data.get("created")
    return Bundle(created if isinstance(created, str) else "", known, ignored)


def merge_playlists(config: AppConfig, imported: object) -> tuple[dict[str, object], list[str]]:
    """The music table with ``imported`` playlists added or replaced by id.

    A playlist that refers to a device this installation does not have loses that device
    (it then plays on the last used one); the notes say so.
    """
    if not isinstance(imported, list):
        raise _bad("Playlists must be a list", "Die Playlists in der Datei sind ungültig.")
    table = music_table(config.music)
    known_targets = {t.id for t in config.music.targets}
    playlists = {p["id"]: p for p in table["playlists"]}  # type: ignore[attr-defined]
    notes: list[str] = []
    for item in imported:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _bad("Bad playlist entry", "Eine Playlist in der Datei ist ungültig.")
        entry = dict(item)
        if entry.get("target") and entry["target"] not in known_targets:
            notes.append(
                f"„{entry.get('name', entry['id'])}“: das Wiedergabegerät gibt es hier nicht "
                "und wurde entfernt."
            )
            entry["target"] = ""
        playlists[entry["id"]] = entry
    table["playlists"] = list(playlists.values())
    return table, notes


def cube_profile(config: AppConfig, name: str) -> dict[str, Any]:
    """The mappings, names and colours of one cube (or the joystick) without its identity."""
    source = next(
        (
            n
            for n in [c.name for c in config.cubes] + [JOYSTICK_SOURCE]
            if n.casefold() == name.casefold()
        ),
        None,
    )
    if source is None:
        raise _bad("Unknown cube", "Bitte einen vorhandenen Würfel wählen.")
    return {
        "name": source,
        "mappings": [
            {k: v for k, v in m.items() if k != "cube"}
            for m in mappings_table(config)
            if str(m["cube"]).casefold() == source.casefold()
        ],
        "labels": [
            {k: v for k, v in label.items() if k != "cube"}
            for label in face_labels_table(config)
            if str(label["cube"]).casefold() == source.casefold()
        ],
    }


def default_target(config: AppConfig, profile_name: object) -> str:
    """The cube a profile is applied to unless the user chooses another."""
    names = [c.name for c in config.cubes] + [JOYSTICK_SOURCE]
    if isinstance(profile_name, str):
        for name in names:
            if name.casefold() == profile_name.casefold():
                return name
    return config.cubes[0].name


def _apply_profile(config: AppConfig, value: object, target: str) -> AppConfig:
    if not isinstance(value, dict):
        raise _bad("cube must be a table", "Die Würfel-Daten in der Datei sind ungültig.")
    names = [c.name for c in config.cubes] + [JOYSTICK_SOURCE]
    chosen = next((n for n in names if n.casefold() == target.casefold()), None)
    if chosen is None:
        raise _bad("Unknown target cube", "Bitte einen vorhandenen Würfel als Ziel wählen.")
    mappings = value.get("mappings", [])
    labels = value.get("labels", [])
    if not isinstance(mappings, list) or not isinstance(labels, list):
        raise _bad("Bad profile", "Die Würfel-Daten in der Datei sind ungültig.")
    keep_mappings = [
        m for m in mappings_table(config) if str(m["cube"]).casefold() != chosen.casefold()
    ]
    keep_labels = [
        la for la in face_labels_table(config) if str(la["cube"]).casefold() != chosen.casefold()
    ]
    try:
        new_mappings = [{**m, "cube": chosen} for m in mappings if isinstance(m, dict)]
        new_labels = [{**la, "cube": chosen} for la in labels if isinstance(la, dict)]
        updated = with_mappings_table(config, keep_mappings + new_mappings)
        return with_face_labels_table(updated, keep_labels + new_labels)
    except ValidationError as exc:
        raise _bad(
            str(exc),
            "Die Seiten passen nicht zum gewählten Ziel (zum Beispiel Würfel statt Joystick).",
        ) from exc


def prepare_section(
    config: AppConfig, key: str, value: object, target: str = ""
) -> tuple[AppConfig, list[str]]:
    """``config`` with section ``key`` replaced by ``value`` (validated), plus notes.

    Raises:
        ValidationError: If the section does not fit this installation.
    """
    notes: list[str] = []
    if key == "labels":
        if not isinstance(value, list):
            raise _bad("labels must be a list", "Die Namen in der Datei sind ungültig.")
        try:
            return with_face_labels_table(config, value), notes
        except ValidationError as exc:
            raise _bad(
                str(exc), "Die Namen passen nicht zu den Würfeln dieser Installation."
            ) from exc
    if key == "cube":
        chosen = target or default_target(
            config, value.get("name") if isinstance(value, dict) else None
        )
        notes.append(f"Wird auf „{chosen}“ angewendet und ersetzt dessen Zuordnung.")
        return _apply_profile(config, value, chosen), notes
    if key == "music":
        if not isinstance(value, dict):
            raise _bad("music must be a table", "Die Musik-Einstellungen sind ungültig.")
        return with_music_table(config, value), notes
    if key == "playlists":
        table, notes = merge_playlists(config, value)
        return with_music_table(config, table), notes
    if key == "mappings":
        if not isinstance(value, list):
            raise _bad("mappings must be a list", "Die Zuordnung in der Datei ist ungültig.")
        try:
            return with_mappings_table(config, value), notes
        except ValidationError as exc:
            raise _bad(
                str(exc),
                "Die Zuordnung passt nicht zu den Würfeln dieser Installation "
                "(Namen der Würfel oder Seiten).",
            ) from exc
    if key == "sense_hat":
        if not isinstance(value, dict):
            raise _bad("sense_hat must be a table", "Die Sense-HAT-Einstellungen sind ungültig.")
        table = json.loads(json.dumps(value))
        history = table.get("history")
        if isinstance(history, dict):
            history["database_path"] = config.sense_hat.history.database_path
        notes.append("Der Speicherort der Verlaufsdatenbank bleibt unverändert.")
        return with_sense_hat_table(config, table), notes
    if key == "hue":
        if not isinstance(value, dict):
            raise _bad("hue must be a table", "Die Hue-Einstellungen sind ungültig.")
        notes.append("Der Hue-Schlüssel wird nicht übertragen; ggf. neu verbinden.")
        return with_hue_table(config, value), notes
    raise _bad("Unknown section", "Dieser Bereich ist unbekannt.")


def describe_section(config: AppConfig, key: str) -> str:
    """A short German summary of what ``config`` holds for ``key``."""
    if key in ("music", "playlists"):
        return (
            f"{len(config.music.targets)} Wiedergabegeräte, {len(config.music.playlists)} Playlists"
        )
    if key == "mappings":
        return f"{len(config.mappings)} Zuordnungen"
    if key == "labels":
        return f"{len(config.face_labels)} benannte oder gefärbte Seiten"
    if key == "cube":
        return f"{len(config.mappings)} Zuordnungen insgesamt"
    if key == "sense_hat":
        enabled = sum(1 for s in config.sense_hat.sensors.values() if s.enabled)
        display = "an" if config.sense_hat.matrix.enabled else "aus"
        return f"{enabled} Sensoren aktiv, Anzeige {display}"
    return "Hue " + ("an" if config.hue.enabled else "aus")


def preview_bundle(config: AppConfig, bundle: Bundle, target: str = "") -> list[SectionPreview]:
    """Check every section of ``bundle`` against ``config`` without changing anything."""
    previews: list[SectionPreview] = []
    for key in SECTION_ORDER:
        if key not in bundle.sections:
            continue
        try:
            candidate, notes = prepare_section(config, key, bundle.sections[key], target)
        except ValidationError as exc:
            previews.append(SectionPreview(key, SECTION_LABELS[key], False, exc.user_message))
            continue
        detail = " ".join([describe_section(candidate, key), *notes])
        previews.append(SectionPreview(key, SECTION_LABELS[key], True, detail))
    return previews
