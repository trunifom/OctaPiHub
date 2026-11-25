"""Plain-language details of what one cube face does (for the detail view in the editor)."""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

from octapihub.application.hue_lights import COLORS
from octapihub.application.music.sources import SourceKind, parse_source
from octapihub.core.config import (
    AppConfig,
    MusicTargetKind,
    PlaylistConfig,
    RepeatMode,
    RuleActionConfig,
    RuleMappingConfig,
)
from octapihub.core.errors import MusicSourceError

_REPEAT = {
    RepeatMode.OFF: "nicht wiederholen",
    RepeatMode.ALL: "ganze Playlist wiederholen",
    RepeatMode.ONE: "einen Titel endlos wiederholen",
}
_TARGET_KIND = {
    MusicTargetKind.CAST: "Google-Gerät im WLAN",
    MusicTargetKind.LOCAL: "Audioausgang des Raspberry Pi",
    MusicTargetKind.BLUETOOTH: "Bluetooth-Box am Raspberry Pi",
}
_LIGHT = {
    "light.toggle": "Schaltet das Licht ein, wenn es aus ist, sonst aus",
    "light.on": "Schaltet das Licht ein",
    "light.off": "Schaltet das Licht aus",
    "light.brighter": "Macht das Licht heller",
    "light.darker": "Macht das Licht dunkler",
    "light.color.next": "Wechselt zur nächsten Farbe",
    "scene.next": "Wählt die nächste Szene des Raumes (alphabetisch, am Ende von vorn)",
    "scene.previous": "Wählt die vorherige Szene des Raumes",
}
_MUSIC = {
    "pause": "Pausiert die Wiedergabe",
    "resume": "Setzt die Wiedergabe fort",
    "toggle": "Pausiert oder setzt fort",
    "stop": "Stoppt die Wiedergabe",
    "stop.all": "Stoppt die Wiedergabe auf allen Geräten",
    "next": "Spielt den nächsten Titel",
    "previous": "Spielt den vorherigen Titel",
    "volume.up": "Macht die Musik lauter (10 Prozentpunkte)",
    "volume.down": "Macht die Musik leiser (10 Prozentpunkte)",
}


def _target_line(config: AppConfig, target_id: str, fallback: str) -> str:
    for target in config.music.targets:
        if target.id == target_id:
            return f"Auf {target.name} ({_TARGET_KIND[target.kind]})"
    return fallback if not target_id else f"Auf einem Gerät „{target_id}“, das es nicht mehr gibt"


def _source_lines(config: AppConfig, playlist: PlaylistConfig) -> list[str]:
    try:
        source = parse_source(playlist.source)
    except MusicSourceError:
        return [f"Quelle „{playlist.source}“ ist ungültig"]
    if source.kind is SourceKind.LOCAL:
        folder = config.music.library_dir or "(kein Musikordner eingestellt)"
        return [
            f"Quelle: {source.ref} im Musikordner {folder}",
            "Die Dateien werden beim Auslösen gelesen (Ordner samt Unterordnern, "
            "in natürlicher Reihenfolge)",
        ]
    if source.kind is SourceKind.URL:
        return [f"Quelle: Radio-/Stream-Adresse {source.ref}"]
    if source.kind is SourceKind.YOUTUBE:
        return [f"Quelle: YouTube {source.youtube_url}"]
    return [f"Quelle: {source.description}"]


def _music_lines(
    config: AppConfig, action: RuleActionConfig, playlist_names: Mapping[str, PlaylistConfig]
) -> list[str]:
    params = action.parameters
    playlist = playlist_names.get(str(params.get("playlist", "")))
    target_id = str(params.get("target", ""))
    lines: list[str] = []
    if action.action in ("play", "toggle"):
        if playlist is None:
            return ["Die gewählte Playlist gibt es nicht mehr"]
        verb = "Spielt" if action.action == "play" else "Startet oder pausiert"
        lines.append(f"{verb} die Playlist „{playlist.name}“")
        lines.extend(_source_lines(config, playlist))
        lines.append(
            _target_line(config, target_id or playlist.target, "Auf dem zuletzt verwendeten Gerät")
        )
        shuffle = params.get("shuffle")
        shuffle = playlist.shuffle if not isinstance(shuffle, bool) else shuffle
        repeat_value = params.get("repeat")
        repeat = (
            RepeatMode(repeat_value) if repeat_value in ("off", "all", "one") else playlist.repeat
        )
        lines.append(
            f"Reihenfolge: {'zufällig' if shuffle else 'wie in der Quelle'}, {_REPEAT[repeat]}"
        )
        if playlist.volume is not None:
            lines.append(f"Lautstärke: {playlist.volume} %")
        return lines
    lines.append(_MUSIC.get(action.action, action.action))
    lines.append(_target_line(config, target_id, "Auf dem Gerät, das gerade spielt"))
    return lines


def _hue_lines(
    action: RuleActionConfig, scenes: Mapping[str, str], groups: Mapping[str, str]
) -> list[str]:
    params = action.parameters
    group = str(params.get("group", ""))
    room = f"Raum: {groups.get(group, group)}" if group else "Alle Lampen im Haus"
    if action.action == "scene.activate":
        scene_id = str(params.get("scene_id", ""))
        return [f"Schaltet die Hue-Szene „{scenes.get(scene_id, scene_id)}“ ein"]
    if action.action == "light.color":
        color = str(params.get("color", ""))
        return [f"Setzt die Farbe {COLORS.get(color, (color,))[0]}", room]
    if action.action == "light.brightness":
        level = params.get("level", 0)
        percent = round(float(level) * 100) if isinstance(level, (int, float)) else 0
        return [f"Setzt die Helligkeit auf {percent} %", room]
    text = _LIGHT.get(action.action, action.action)
    if action.action.startswith("scene."):
        return [text, room]
    return [text, room]


def _home_lines(config: AppConfig, action: RuleActionConfig) -> list[str]:
    found = next((a for a in config.smarthome.actions if a.id == action.parameters.get("id")), None)
    if found is None:
        return ["Die gewählte Smart-Home-Aktion gibt es nicht mehr"]
    if found.kind.value == "http":
        return [f"Smart Home: „{found.name}“", f"Ruft {found.method} {_hide_query(found.url)} auf"]
    connection = next((c for c in config.smarthome.connections if c.id == found.connection), None)
    where = f" über {connection.name}" if connection else ""
    if found.kind.value == "mqtt":
        return [f"Smart Home: „{found.name}“", f"Sendet an das Thema {found.topic}{where}"]
    return [f"Smart Home: „{found.name}“", f"Ruft den Dienst {found.service} auf{where}"]


def _hide_query(url: str) -> str:
    """Web addresses can carry secrets (webhook ids); show only scheme, host and port."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/…" if parts.path.strip("/") or parts.query else url


def describe_face(
    config: AppConfig,
    mapping: RuleMappingConfig | None,
    scenes: Mapping[str, str],
    groups: Mapping[str, str],
) -> list[str]:
    """Readable lines about what a face does; empty if nothing is assigned."""
    if mapping is None:
        return []
    playlists = {p.id: p for p in config.music.playlists}
    lines: list[str] = []
    for index, action in enumerate(mapping.actions):
        if len(mapping.actions) > 1:
            lines.append(f"Schritt {index + 1}:")
        if action.kind == "music":
            lines.extend(_music_lines(config, action, playlists))
        elif action.kind == "hue":
            lines.extend(_hue_lines(action, scenes, groups))
        elif action.kind == "smarthome":
            lines.extend(_home_lines(config, action))
        else:
            lines.append(f"{action.kind}: {action.action}")
    return lines
