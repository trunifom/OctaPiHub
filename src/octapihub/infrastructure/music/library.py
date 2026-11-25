"""Turn local paths and playlist files into tracks - only ever inside the music folder."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from octapihub.application.music.models import Track, TrackKind
from octapihub.application.music.sources import MusicSource, SourceKind
from octapihub.core.errors import MusicSourceError

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff"}
)
PLAYLIST_EXTENSIONS = frozenset({".m3u", ".m3u8", ".pls"})
MAX_TRACKS = 1000
MAX_PLAYLIST_BYTES = 1_000_000
_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> list[object]:
    """Sort key that orders ``2.mp3`` before ``10.mp3`` and ignores case."""
    return [int(part) if part.isdigit() else part.casefold() for part in _DIGITS.split(name)]


def _title(path: Path) -> str:
    return path.stem.replace("_", " ").strip() or path.name


def _fail(message: str, user_message: str) -> MusicSourceError:
    return MusicSourceError(message, user_message=user_message)


class LibraryResolver:
    """Resolves sources to tracks.

    Local files are only accepted below the configured music folder (symbolic links that lead
    out of it are refused), so a playlist or a typo can never expose other files.
    """

    def resolve(self, source: MusicSource, library_dir: str) -> tuple[Track, ...]:
        if source.kind is SourceKind.URL:
            host = urlsplit(source.ref).hostname or "Stream"
            return (Track(source.ref, host, TrackKind.URL),)
        if source.kind is SourceKind.YOUTUBE:
            return (Track(source.youtube_url, "YouTube", TrackKind.YOUTUBE),)
        if source.kind is not SourceKind.LOCAL:
            raise _fail("Unsupported source kind", "Diese Quelle kann nicht abgespielt werden.")
        root = self._root(library_dir)
        target = self._inside(root, source.ref)
        return self._tracks_for(root, target)

    # ----- helpers -----

    def _root(self, library_dir: str) -> Path:
        if not library_dir.strip():
            raise _fail(
                "No music folder configured",
                "Der Musikordner ist nicht eingerichtet. Tragen Sie ihn unter Musik ein.",
            )
        try:
            root = Path(library_dir).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise _fail("Music folder not found", "Der Musikordner wurde nicht gefunden.") from exc
        if not root.is_dir():
            raise _fail("Music folder is not a directory", "Der Musikordner ist kein Ordner.")
        return root

    def _inside(self, root: Path, reference: str, base: Path | None = None) -> Path:
        candidate = Path(reference)
        joined = candidate if candidate.is_absolute() else (base or root) / candidate
        try:
            resolved = joined.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise _fail(
                "Path not found", "Die Datei oder der Ordner wurde nicht gefunden."
            ) from exc
        if not resolved.is_relative_to(root):
            raise _fail(
                "Path outside the music folder",
                "Dieser Pfad liegt außerhalb des Musikordners und wird nicht abgespielt.",
            )
        return resolved

    def _tracks_for(self, root: Path, target: Path) -> tuple[Track, ...]:
        if target.is_dir():
            tracks = self._folder(target)
        elif target.suffix.lower() in PLAYLIST_EXTENSIONS:
            tracks = self._playlist_file(root, target)
        elif target.suffix.lower() in AUDIO_EXTENSIONS:
            tracks = (Track(str(target), _title(target), TrackKind.FILE),)
        else:
            raise _fail(
                "Unsupported file type",
                "Dieser Dateityp wird nicht unterstützt "
                + "(erlaubt sind z. B. mp3, m4a, flac, ogg, wav, m3u).",
            )
        if not tracks:
            raise _fail("No playable tracks", "Es wurden keine abspielbaren Musikdateien gefunden.")
        return tracks

    def _folder(self, folder: Path) -> tuple[Track, ...]:
        found: list[Path] = []
        for current, directories, files in os.walk(folder, followlinks=False):
            directories[:] = sorted(
                (d for d in directories if not d.startswith(".")), key=natural_key
            )
            for name in sorted(files, key=natural_key):
                path = Path(current, name)
                if name.startswith(".") or path.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                if path.is_symlink():
                    continue
                found.append(path)
                if len(found) > MAX_TRACKS:
                    raise _fail(
                        "Too many files in the folder",
                        f"Der Ordner enthält mehr als {MAX_TRACKS} Titel. "
                        + "Bitte einen kleineren Ordner wählen.",
                    )
        return tuple(Track(str(path), _title(path), TrackKind.FILE) for path in found)

    def _playlist_file(self, root: Path, playlist: Path) -> tuple[Track, ...]:
        try:
            if playlist.stat().st_size > MAX_PLAYLIST_BYTES:
                raise _fail("Playlist file too large", "Die Playlist-Datei ist zu groß.")
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise _fail(
                "Could not read the playlist file", "Die Playlist-Datei ist nicht lesbar."
            ) from exc
        entries = _parse_pls(text) if playlist.suffix.lower() == ".pls" else _parse_m3u(text)
        tracks: list[Track] = []
        for location, title in entries:
            if len(tracks) >= MAX_TRACKS:
                break
            track = self._entry(root, playlist.parent, location, title)
            if track is not None:
                tracks.append(track)
        return tuple(tracks)

    def _entry(self, root: Path, base: Path, location: str, title: str) -> Track | None:
        if location.lower().startswith(("http://", "https://")):
            if any(c.isspace() for c in location) or urlsplit(location).username:
                return None
            return Track(location, title or urlsplit(location).hostname or "Stream", TrackKind.URL)
        if "://" in location or location.lower().startswith("file:"):
            logger.warning("Skipping unsupported playlist entry")
            return None
        try:
            path = self._inside(root, location, base)
        except MusicSourceError:
            logger.warning("Skipping a playlist entry that is missing or outside the folder")
            return None
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            return None
        return Track(str(path), title or _title(path), TrackKind.FILE)


def _parse_m3u(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    pending_title = ""
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            _, _, title = line.partition(",")
            pending_title = title.strip()
            continue
        if line.startswith("#"):
            continue
        entries.append((line, pending_title))
        pending_title = ""
    return entries


def _parse_pls(text: str) -> list[tuple[str, str]]:
    files: dict[int, str] = {}
    titles: dict[int, str] = {}
    for raw in text.splitlines():
        key, _, value = raw.strip().partition("=")
        match = re.fullmatch(r"(File|Title)(\d+)", key.strip(), flags=re.IGNORECASE)
        if not match or not value.strip():
            continue
        index = int(match.group(2))
        (files if match.group(1).lower() == "file" else titles)[index] = value.strip()
    return [(files[i], titles.get(i, "")) for i in sorted(files)]
