"""Plain data types shared by the music service and the players."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from octapihub.application.music.sources import MusicSource
from octapihub.core.config import MusicTargetConfig, RepeatMode


class TrackKind(StrEnum):
    """How a track is reached."""

    FILE = "file"  # a file on this device (absolute path)
    URL = "url"  # a stream or file address
    YOUTUBE = "youtube"  # a YouTube video or playlist address


@dataclass(frozen=True, slots=True)
class Track:
    """One playable item."""

    location: str
    title: str
    kind: TrackKind


@dataclass(frozen=True, slots=True)
class PlayRequest:
    """What a player should play."""

    tracks: tuple[Track, ...]
    source: MusicSource
    title: str
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    volume: int | None = None


class PlayerState(StrEnum):
    """What a player is doing."""

    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PlayerStatus:
    """What a player reports; every field except ``state`` may be unknown."""

    state: PlayerState = PlayerState.IDLE
    track_title: str = ""
    volume: int | None = None
    error: str | None = None
    error_code: str | None = None


class MusicPlayer(Protocol):
    """Controls playback on one target.

    All methods may block on the network; the service calls them with a timeout. Failures are
    reported as :class:`~octapihub.core.errors.MusicError` subclasses.
    """

    async def play(self, request: PlayRequest) -> None:
        """Start playing, replacing whatever plays."""

    async def pause(self) -> None:
        """Pause."""

    async def resume(self) -> None:
        """Continue after a pause."""

    async def stop(self) -> None:
        """Stop and forget the queue."""

    async def next(self) -> None:
        """Go to the next track."""

    async def previous(self) -> None:
        """Go to the previous track."""

    async def set_volume(self, percent: int) -> None:
        """Set the volume (0..100)."""

    async def status(self) -> PlayerStatus:
        """Report the current state."""

    async def close(self) -> None:
        """Release connections and processes."""


class PlayerFactory(Protocol):
    """Create the player for one configured target."""

    def create(self, target: MusicTargetConfig) -> MusicPlayer:
        """Return a player; connecting may be deferred until the first command."""


class TrackResolver(Protocol):
    """Turn a source into playable tracks (may read files; called in a worker thread)."""

    def resolve(self, source: MusicSource, library_dir: str) -> tuple[Track, ...]:
        """Expand a source into tracks.

        Raises:
            MusicSourceError: The source is invalid, missing, empty, or outside the library.
        """
