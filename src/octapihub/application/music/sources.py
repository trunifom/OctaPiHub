"""Music sources: recognise and normalise what the user enters (pure, no network or disk).

=========================================================  ==========================
entered                                                    normalised
=========================================================  ==========================
``/home/pi/Music/Jazz`` / ``Jazz/Album`` / ``file:///...``   ``local:<path>``
``https://music.youtube.com/watch?v=ID`` / ``youtu.be/ID``   ``youtube:ID`` (+ ``?list=``)
``https://www.youtube.com/playlist?list=ID``                 ``youtube:?list=ID``
``https://open.spotify.com/track|album|playlist/ID``         ``spotify:<type>:<id>``
any other ``http(s)://`` address (a radio stream or file)    ``url:<address>``
=========================================================  ==========================

Spotify links are *recognised* but cannot be played yet; the service explains that. Nothing is
guessed: anything that is not a path or a safe link is rejected with an explanation. Opening
files and safety checks for stream addresses happen where they are used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, unquote, urlsplit

from octapihub.core.errors import MusicSourceError

SPOTIFY_TYPES = ("track", "album", "playlist", "artist", "episode", "show")
_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_LIST = re.compile(r"^[A-Za-z0-9_-]{10,60}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
MAX_LENGTH = 2000
_YOUTUBE_HOSTS = {"music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com"}


class SourceKind(StrEnum):
    """What kind of source an entry is."""

    LOCAL = "local"
    URL = "url"
    YOUTUBE = "youtube"
    SPOTIFY = "spotify"


@dataclass(frozen=True, slots=True)
class MusicSource:
    """A recognised source.

    ``ref`` is the path (local), the address (url), the video id (YouTube, empty for a whole
    playlist), or ``<type>:<id>`` (Spotify). ``playlist`` is a YouTube ``list=`` id.
    """

    kind: SourceKind
    ref: str
    playlist: str | None = None
    original: str = ""

    @property
    def uri(self) -> str:
        """The normalised text form."""
        if self.kind is SourceKind.YOUTUBE:
            return f"youtube:{self.ref}" + (f"?list={self.playlist}" if self.playlist else "")
        return f"{self.kind.value}:{self.ref}"

    @property
    def description(self) -> str:
        """A short German description for the interface."""
        if self.kind is SourceKind.LOCAL:
            return "Datei, Ordner oder Playlist-Datei auf dem Gerät"
        if self.kind is SourceKind.URL:
            return "Radio- oder Stream-Adresse"
        if self.kind is SourceKind.YOUTUBE:
            if not self.ref:
                return "YouTube-Playlist"
            return "YouTube-Video" + (" mit Playlist" if self.playlist else "")
        return "Spotify (noch nicht abspielbar)"

    @property
    def youtube_url(self) -> str:
        """The canonical YouTube address (only for ``kind == YOUTUBE``)."""
        if not self.ref:
            return f"https://www.youtube.com/playlist?list={self.playlist}"
        suffix = f"&list={self.playlist}" if self.playlist else ""
        return f"https://www.youtube.com/watch?v={self.ref}{suffix}"


def _reject(reason: str) -> MusicSourceError:
    return MusicSourceError(
        f"Unrecognised music source: {reason}",
        user_message=(
            f"{reason} Erkannt werden Pfade im Musikordner (Jazz/Album), Radio- und "
            "Stream-Adressen (https://…), YouTube- und YouTube-Music-Links sowie "
            "Playlist-Dateien (.m3u)."
        ),
    )


def parse_source(text: str) -> MusicSource:
    """Recognise an entered link or path.

    Raises:
        MusicSourceError: With a German explanation if nothing sensible can be made of it.
    """
    if not isinstance(text, str) or not text.strip():
        raise _reject("Bitte eine Quelle eingeben.")
    raw = text.strip().strip('"').strip("'").strip()
    if len(raw) > MAX_LENGTH or any(ord(c) < 32 for c in raw):
        raise _reject("Die Eingabe ist zu lang oder enthält Steuerzeichen.")
    if raw.lower().startswith("file:"):
        path = unquote(urlsplit(raw).path)
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
        return _local(path, raw)
    if raw.startswith("spotify:"):
        return _spotify(raw, raw)
    if "://" not in raw:
        if raw.startswith("//") or raw.replace("/", "\\").startswith("\\\\"):
            raise _reject("Netzwerkpfade sind nicht erlaubt.")
        return _local(raw, raw)
    if any(c.isspace() for c in raw):
        raise _reject("Der Link enthält Leerzeichen.")
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise _reject("Der Link ist ungültig.") from None
    if parts.scheme not in ("http", "https") or not host:
        raise _reject("Das ist kein Link (er muss mit https:// beginnen).")
    if parts.username is not None or parts.password is not None:
        raise _reject("Benutzername und Passwort gehören nicht in einen Link.")
    if host == "open.spotify.com":
        segments = [s for s in parts.path.split("/") if s]
        if segments and segments[0].startswith("intl-"):
            segments = segments[1:]
        if len(segments) >= 2 and segments[0] in SPOTIFY_TYPES:
            return _spotify(f"spotify:{segments[0]}:{segments[1]}", raw)
        raise _reject("Dieser Spotify-Link zeigt auf keinen Titel, kein Album und keine Playlist.")
    if host in _YOUTUBE_HOSTS:
        return _youtube(parse_qs(parts.query), raw)
    if host == "youtu.be":
        return _youtube({"v": [parts.path.strip("/")], **parse_qs(parts.query)}, raw)
    return MusicSource(SourceKind.URL, raw, original=raw)


def _local(path: str, original: str) -> MusicSource:
    cleaned = path.strip()
    if not cleaned or "\x00" in cleaned:
        raise _reject("Der Pfad ist leer oder ungültig.")
    if ".." in cleaned.replace("\\", "/").split("/"):
        raise _reject("Pfade mit „..“ sind nicht erlaubt.")
    return MusicSource(SourceKind.LOCAL, cleaned, original=original)


def _spotify(uri: str, original: str) -> MusicSource:
    pieces = uri.split(":")
    if len(pieces) != 3 or pieces[1] not in SPOTIFY_TYPES or not _SPOTIFY_ID.match(pieces[2]):
        raise _reject("Dieser Spotify-Verweis ist ungültig.")
    return MusicSource(SourceKind.SPOTIFY, f"{pieces[1]}:{pieces[2]}", original=original)


def _youtube(query: dict[str, list[str]], original: str) -> MusicSource:
    video = (query.get("v") or [""])[0]
    playlist = (query.get("list") or [""])[0] or None
    if playlist is not None and not _YOUTUBE_LIST.match(playlist):
        playlist = None
    if not video and playlist is not None:
        return MusicSource(SourceKind.YOUTUBE, "", playlist, original)
    if not _YOUTUBE_ID.match(video):
        raise _reject("In diesem YouTube-Link fehlt ein gültiges Video (v=…) oder eine Playlist.")
    return MusicSource(SourceKind.YOUTUBE, video, playlist, original)
