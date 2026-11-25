"""Form handling for the Musik page: from the submitted fields to a validated music table.

Targets use fields ``t.<row>.<name>`` and playlists ``p.<row>.<name>``; ``<row>`` is the row
number of an existing entry or ``new`` for the empty row at the end. Every row is validated
on its own so one submission reports all mistakes at once.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from octapihub.application.music.sources import SourceKind, parse_source
from octapihub.core.config import (
    MAX_MUSIC_TARGETS,
    MAX_PLAYLISTS,
    AppConfig,
    MusicConfig,
    MusicTargetKind,
    RepeatMode,
    with_music_table,
)
from octapihub.core.errors import MusicSourceError, ValidationError

FORM_ERROR_KEY = "_form"
TARGET_KIND_LABELS = {
    MusicTargetKind.CAST.value: "Google Nest / Home / Nest Hub",
    MusicTargetKind.LOCAL.value: "Audioausgang dieses Raspberry Pi",
    MusicTargetKind.BLUETOOTH.value: "Bluetooth-Box",
}
REPEAT_LABELS = {
    RepeatMode.OFF.value: "Nicht wiederholen",
    RepeatMode.ALL.value: "Alles wiederholen",
    RepeatMode.ONE.value: "Titel wiederholen",
}
_UMLAUTS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)
_ROW_KEY = re.compile(r"^([tp])\.(\d+|new)\.(\w+)$")
_FIELD_INDEX = re.compile(r"music\.(targets|playlists)\[(\d+)\]\.(\w+)")
_TARGET_FIELDS = ("name", "kind", "device", "bluetooth_address", "volume")
_PLAYLIST_FIELDS = ("name", "source", "target", "repeat", "volume")


@dataclass(frozen=True, slots=True)
class MusicFormResult:
    """Parsed submission: the validated config, or per-field errors."""

    config: AppConfig | None
    errors: dict[str, str]


def slugify(name: str, taken: set[str], fallback: str) -> str:
    """A unique id (``[a-z][a-z0-9_-]{0,31}``) derived from a display name."""
    name = name.translate(_UMLAUTS)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:28] or fallback
    if not base[0].isalpha():
        base = f"{fallback}-{base}"[:28]
    candidate, counter = base, 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def music_to_form(music: MusicConfig) -> dict[str, str]:
    """Current music settings as form values."""
    values = {
        "music.enabled": "on" if music.enabled else "",
        "music.library_dir": music.library_dir,
        "music.media_host": music.media_host,
    }
    for index, target in enumerate(music.targets):
        prefix = f"t.{index}."
        values.update(
            {
                prefix + "id": target.id,
                prefix + "name": target.name,
                prefix + "kind": target.kind.value,
                prefix + "device": target.device,
                prefix + "bluetooth_address": target.bluetooth_address,
                prefix + "volume": str(target.volume),
            }
        )
    for index, playlist in enumerate(music.playlists):
        prefix = f"p.{index}."
        values.update(
            {
                prefix + "id": playlist.id,
                prefix + "name": playlist.name,
                prefix + "source": playlist.source,
                prefix + "target": playlist.target,
                prefix + "shuffle": "on" if playlist.shuffle else "",
                prefix + "repeat": playlist.repeat.value,
                prefix + "volume": "" if playlist.volume is None else str(playlist.volume),
            }
        )
    return values


def row_keys(form: Mapping[str, str], group: str) -> list[str]:
    """Row keys of ``group`` ("t" or "p") present in ``form``: numbers ascending, then ``new``."""
    keys: set[str] = set()
    for name in form:
        match = _ROW_KEY.match(name)
        if match and match.group(1) == group:
            keys.add(match.group(2))
    numbers = sorted((k for k in keys if k.isdigit()), key=int)
    return numbers + (["new"] if "new" in keys else [])


def with_blank_rows(values: dict[str, str]) -> dict[str, str]:
    """Add the empty "new" row for targets and playlists (what the page shows at the end)."""
    values.setdefault("t.new.kind", MusicTargetKind.CAST.value)
    values.setdefault("t.new.volume", "40")
    values.setdefault("p.new.repeat", RepeatMode.OFF.value)
    return values


def _number(text: str) -> int | str:
    stripped = text.strip()
    return int(stripped) if re.fullmatch(r"\d{1,4}", stripped) else stripped


def _is_blank(form: Mapping[str, str], group: str, key: str, fields: tuple[str, ...]) -> bool:
    keys = [f for f in fields if f not in ("kind", "volume", "repeat")]
    return not any(form.get(f"{group}.{key}.{f}", "").strip() for f in keys)


def _target_row(form: Mapping[str, str], key: str, taken: set[str]) -> dict[str, object]:
    def get(name: str) -> str:
        return form.get(f"t.{key}.{name}", "").strip()

    name = get("name")
    target_id = get("id") or slugify(name, taken, "geraet")
    return {
        "id": target_id,
        "name": name,
        "kind": get("kind"),
        "device": get("device"),
        "bluetooth_address": get("bluetooth_address"),
        "volume": _number(get("volume") or "40"),
    }


def _playlist_row(form: Mapping[str, str], key: str, taken: set[str]) -> dict[str, object]:
    def get(name: str) -> str:
        return form.get(f"p.{key}.{name}", "").strip()

    name = get("name")
    row: dict[str, object] = {
        "id": get("id") or slugify(name, taken, "playlist"),
        "name": name,
        "source": get("source"),
        "target": get("target"),
        "shuffle": bool(form.get(f"p.{key}.shuffle")),
        "repeat": get("repeat") or RepeatMode.OFF.value,
    }
    if get("volume"):
        row["volume"] = _number(get("volume"))
    return row


_FIELD_MESSAGES = {
    "name": "Bitte einen Namen eingeben (höchstens 60 Zeichen).",
    "kind": "Bitte eine Art von Wiedergabegerät wählen.",
    "device": "Für Google-Geräte den Gerätenamen eintragen, wie er in der Google-Home-App steht.",
    "bluetooth_address": "Die Adresse muss so aussehen: AA:BB:CC:DD:EE:FF.",
    "volume": "Die Lautstärke muss eine Zahl von 0 bis 100 sein.",
    "source": "Bitte eine Quelle eintragen (Ordner, Datei, Radio-Adresse oder YouTube-Link).",
    "target": "Dieses Wiedergabegerät gibt es nicht.",
    "repeat": "Bitte eine der Wiederholungs-Optionen wählen.",
    "id": "Die interne Kennung ist ungültig. Bitte den Namen ändern.",
}


def _map_error(exc: ValidationError, group: str, key: str) -> tuple[str, str]:
    field = str(exc.details.get("field", ""))
    match = _FIELD_INDEX.search(field)
    name = match.group(3) if match else "name"
    return f"{group}.{key}.{name}", _FIELD_MESSAGES.get(name, exc.user_message)


_NEUTRAL: dict[str, Any] = {"enabled": False, "targets": [], "playlists": []}
_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _volume_problem(value: object) -> bool:
    return not isinstance(value, int) or not 0 <= value <= 100


def _target_problems(row: dict[str, object]) -> dict[str, str]:
    """The mistakes in one target row, by field (all of them, not just the first)."""
    found: dict[str, str] = {}
    name = str(row["name"])
    if not name or len(name) > 60:
        found["name"] = _FIELD_MESSAGES["name"]
    if row["kind"] not in TARGET_KIND_LABELS:
        found["kind"] = _FIELD_MESSAGES["kind"]
    if row["kind"] == MusicTargetKind.CAST.value and not row["device"]:
        found["device"] = _FIELD_MESSAGES["device"]
    address = str(row["bluetooth_address"])
    if address and not _MAC.fullmatch(address):
        found["bluetooth_address"] = _FIELD_MESSAGES["bluetooth_address"]
    if _volume_problem(row["volume"]):
        found["volume"] = _FIELD_MESSAGES["volume"]
    return found


def _playlist_problems(row: dict[str, object]) -> dict[str, str]:
    found: dict[str, str] = {}
    name = str(row["name"])
    if not name or len(name) > 60:
        found["name"] = _FIELD_MESSAGES["name"]
    source = str(row["source"])
    if not source:
        found["source"] = _FIELD_MESSAGES["source"]
    elif (problem := _check_playlist_source(source)) is not None:
        found["source"] = problem
    if row["repeat"] not in REPEAT_LABELS:
        found["repeat"] = _FIELD_MESSAGES["repeat"]
    if "volume" in row and _volume_problem(row["volume"]):
        found["volume"] = _FIELD_MESSAGES["volume"]
    return found


def _check_playlist_source(source: str) -> str | None:
    try:
        parsed = parse_source(source)
    except MusicSourceError as exc:
        return exc.user_message
    if parsed.kind is SourceKind.SPOTIFY:
        return (
            "Spotify-Links können noch nicht abgespielt werden. Bitte einen YouTube-Link, "
            "eine Radio-Adresse oder Dateien im Musikordner verwenden."
        )
    return None


def parse_music_form(form: Mapping[str, str], current: AppConfig) -> MusicFormResult:
    """Validate the whole Musik form against ``current``."""
    errors: dict[str, str] = {}
    base: dict[str, Any] = {
        "enabled": bool(form.get("music.enabled")),
        "library_dir": form.get("music.library_dir", "").strip(),
        "media_host": form.get("music.media_host", "").strip(),
        "targets": [],
        "playlists": [],
    }
    for field in ("library_dir", "media_host"):
        try:
            with_music_table(current, {**base, field: base[field]})
        except ValidationError as exc:
            errors[f"music.{field}"] = exc.user_message

    kept_targets: list[tuple[str, dict[str, object]]] = []
    taken: set[str] = set()
    for key in row_keys(form, "t"):
        if form.get(f"t.{key}.remove") or (
            key == "new" and _is_blank(form, "t", key, _TARGET_FIELDS)
        ):
            continue
        row = _target_row(form, key, taken)
        taken.add(str(row["id"]))
        own = _target_problems(row)
        errors.update({f"t.{key}.{name}": text for name, text in own.items()})
        try:
            if not own:
                with_music_table(current, {**_NEUTRAL, "targets": [row]})
        except ValidationError as exc:
            field, message = _map_error(exc, "t", key)
            errors[field] = message
        kept_targets.append((key, row))
    target_ids = {str(row["id"]) for _, row in kept_targets}

    kept_playlists: list[tuple[str, dict[str, object]]] = []
    taken = set()
    for key in row_keys(form, "p"):
        if form.get(f"p.{key}.remove") or (
            key == "new" and _is_blank(form, "p", key, _PLAYLIST_FIELDS)
        ):
            continue
        row = _playlist_row(form, key, taken)
        taken.add(str(row["id"]))
        own = _playlist_problems(row)
        errors.update({f"p.{key}.{name}": text for name, text in own.items()})
        try:
            only_known = {**row, "target": row["target"] if row["target"] in target_ids else ""}
            if not own:
                with_music_table(
                    current,
                    {
                        **_NEUTRAL,
                        "targets": [dict(r) for _, r in kept_targets[:MAX_MUSIC_TARGETS]],
                        "playlists": [only_known],
                    },
                )
        except ValidationError as exc:
            field, message = _map_error(exc, "p", key)
            errors.setdefault(field, message)
        if row["target"] and row["target"] not in target_ids:
            errors[f"p.{key}.target"] = "Dieses Wiedergabegerät gibt es nicht (mehr)."
        kept_playlists.append((key, row))

    if len(kept_targets) > MAX_MUSIC_TARGETS:
        errors[FORM_ERROR_KEY] = f"Es sind höchstens {MAX_MUSIC_TARGETS} Wiedergabegeräte möglich."
    if len(kept_playlists) > MAX_PLAYLISTS:
        errors[FORM_ERROR_KEY] = f"Es sind höchstens {MAX_PLAYLISTS} Playlists möglich."
    if errors:
        return MusicFormResult(None, errors)

    table = {
        **base,
        "targets": [row for _, row in kept_targets],
        "playlists": [row for _, row in kept_playlists],
    }
    try:
        return MusicFormResult(with_music_table(current, table), {})
    except ValidationError as exc:
        return MusicFormResult(None, {FORM_ERROR_KEY: exc.user_message})
