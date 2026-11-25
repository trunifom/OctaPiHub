"""Serve local music files to Cast devices: tokens instead of paths, only inside the library."""

from __future__ import annotations

import ipaddress
import mimetypes
import secrets
import socket
import time
from collections.abc import Callable
from pathlib import Path

from octapihub.core.errors import MusicNotConfiguredError, MusicSourceError
from octapihub.infrastructure.music.library import AUDIO_EXTENSIONS

TOKEN_TTL_SECONDS = 6 * 3600
MAX_TOKENS = 5000
_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".wma": "audio/x-ms-wma",
    ".aiff": "audio/aiff",
}


def content_type_for(path: str) -> str:
    """MIME type of an audio file (falls back to ``audio/mpeg``)."""
    suffix = Path(path).suffix.lower()
    return _CONTENT_TYPES.get(suffix) or mimetypes.guess_type(path)[0] or "audio/mpeg"


def detect_lan_address() -> str | None:
    """The address of this device in the local network, or ``None`` if it cannot be found.

    Uses a UDP "connection" that never sends a packet, so it works without internet access.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            address = str(probe.getsockname()[0])
    except OSError:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    return None if parsed.is_loopback or parsed.is_unspecified else address


class MediaRegistry:
    """Maps unguessable tokens to music files so Cast devices can fetch them over HTTP.

    A token is only issued for an audio file that really lies inside the music folder, and the
    folder check is repeated on every request (a file replaced by a link to elsewhere is not
    served). Tokens expire.
    """

    def __init__(
        self,
        library_dir: Callable[[], str],
        *,
        ttl_seconds: float = TOKEN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(18),
    ) -> None:
        self._library_dir = library_dir
        self._ttl = ttl_seconds
        self._clock = clock
        self._token_factory = token_factory
        self._items: dict[str, tuple[Path, float]] = {}

    def _root(self) -> Path:
        library = self._library_dir().strip()
        if not library:
            raise MusicNotConfiguredError(
                "No music folder configured",
                user_message="Der Musikordner ist nicht eingerichtet.",
            )
        try:
            return Path(library).expanduser().resolve(strict=True)
        except OSError as exc:
            raise MusicSourceError(
                "Music folder not found", user_message="Der Musikordner wurde nicht gefunden."
            ) from exc

    def register(self, path: str) -> str:
        """Return a token for ``path``.

        Raises:
            MusicSourceError: The file is missing, not audio, or outside the music folder.
        """
        root = self._root()
        try:
            resolved = Path(path).resolve(strict=True)
        except OSError as exc:
            raise MusicSourceError(
                "File not found", user_message="Die Datei wurde nicht gefunden."
            ) from exc
        if (
            not resolved.is_relative_to(root)
            or not resolved.is_file()
            or resolved.suffix.lower() not in AUDIO_EXTENSIONS
        ):
            raise MusicSourceError(
                "File is not a music file inside the library",
                user_message="Diese Datei liegt nicht im Musikordner oder ist keine Musikdatei.",
            )
        self._expire()
        for token, (known, _expires) in self._items.items():
            if known == resolved:
                self._items[token] = (resolved, self._clock() + self._ttl)
                return token
        if len(self._items) >= MAX_TOKENS:
            oldest = min(self._items, key=lambda t: self._items[t][1])
            del self._items[oldest]
        token = self._token_factory()
        self._items[token] = (resolved, self._clock() + self._ttl)
        return token

    def resolve(self, token: str) -> Path | None:
        """The file behind ``token`` if it is valid, unexpired, and still inside the folder."""
        entry = self._items.get(token)
        if entry is None:
            return None
        path, expires = entry
        if self._clock() >= expires:
            del self._items[token]
            return None
        try:
            root = self._root()
            resolved = path.resolve(strict=True)
        except (OSError, MusicNotConfiguredError, MusicSourceError):
            return None
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return None
        return resolved

    def _expire(self) -> None:
        now = self._clock()
        for token in [t for t, (_p, expires) in self._items.items() if now >= expires]:
            del self._items[token]


class MediaUrls:
    """Builds the addresses Cast devices use to reach files served by this program."""

    def __init__(
        self,
        registry: MediaRegistry,
        *,
        port: int,
        configured_host: Callable[[], str] = lambda: "",
        detect: Callable[[], str | None] = detect_lan_address,
    ) -> None:
        self._registry = registry
        self._port = port
        self._configured_host = configured_host
        self._detect = detect

    def url_for(self, path: str) -> str:
        """HTTP address for a local file.

        Raises:
            MusicNotConfiguredError: This device's network address is unknown.
            MusicSourceError: The file may not be served.
        """
        host = self._configured_host().strip() or self._detect()
        if not host:
            raise MusicNotConfiguredError(
                "LAN address unknown",
                user_message=(
                    "Die Netzwerkadresse dieses Geräts ist unbekannt. Tragen Sie sie unter "
                    "Musik als „Adresse für Wiedergabegeräte“ ein."
                ),
            )
        token = self._registry.register(path)
        shown = f"[{host}]" if ":" in host else host
        return f"http://{shown}:{self._port}/media/{token}"
