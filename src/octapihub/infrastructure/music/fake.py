"""In-memory music players and a resolver for tests and the demo (no audio, no network)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping

from octapihub.application.music.models import (
    PlayerState,
    PlayerStatus,
    PlayRequest,
    Track,
    TrackKind,
)
from octapihub.application.music.queue import following, play_order, preceding
from octapihub.application.music.sources import MusicSource, SourceKind
from octapihub.core.config import MusicTargetConfig, RepeatMode
from octapihub.core.errors import MusicSourceError

Failure = Callable[[], Exception]


class FakePlayer:
    """A player that only keeps state; operations can be made to fail or to take time."""

    def __init__(
        self,
        target: MusicTargetConfig,
        *,
        failures: Mapping[str, Failure] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.target = target
        self.failures = dict(failures or {})
        self.delay = delay
        self.calls: list[tuple[str, object]] = []
        self.closed = False
        self._tracks: tuple[Track, ...] = ()
        self._order: list[int] = []
        self._position = 0
        self._repeat = RepeatMode.OFF
        self._state = PlayerState.IDLE
        self._volume: int | None = None

    async def _enter(self, operation: str, detail: object = None) -> None:
        self.calls.append((operation, detail))
        if self.delay:
            await asyncio.sleep(self.delay)
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure()

    async def play(self, request: PlayRequest) -> None:
        await self._enter("play", request)
        self._tracks = request.tracks
        self._order = play_order(len(request.tracks), shuffle=request.shuffle, seed=7)
        self._position = 0
        self._repeat = request.repeat
        self._state = PlayerState.PLAYING
        if request.volume is not None:
            self._volume = request.volume

    async def pause(self) -> None:
        await self._enter("pause")
        if self._state is PlayerState.PLAYING:
            self._state = PlayerState.PAUSED

    async def resume(self) -> None:
        await self._enter("resume")
        if self._state is PlayerState.PAUSED:
            self._state = PlayerState.PLAYING

    async def stop(self) -> None:
        await self._enter("stop")
        self._state = PlayerState.IDLE
        self._tracks = ()
        self._order = []

    async def next(self) -> None:
        await self._enter("next")
        following_position = following(self._position, len(self._order), self._repeat, by_user=True)
        if following_position is None:
            self._state = PlayerState.IDLE
        else:
            self._position = following_position

    async def previous(self) -> None:
        await self._enter("previous")
        self._position = preceding(self._position, len(self._order), self._repeat)

    async def set_volume(self, percent: int) -> None:
        await self._enter("set_volume", percent)
        self._volume = percent

    async def status(self) -> PlayerStatus:
        await self._enter("status")
        title = ""
        if self._order and self._state is not PlayerState.IDLE:
            title = self._tracks[self._order[self._position]].title
        return PlayerStatus(self._state, title, self._volume)

    async def close(self) -> None:
        self.calls.append(("close", None))
        self.closed = True


class FakePlayerFactory:
    """Creates :class:`FakePlayer` objects and remembers them per target id."""

    def __init__(
        self,
        failures: Mapping[str, Mapping[str, Failure]] | None = None,
        *,
        delay: float = 0.0,
        create_failure: Failure | None = None,
    ) -> None:
        self._failures = failures or {}
        self._delay = delay
        self._create_failure = create_failure
        self.players: dict[str, FakePlayer] = {}
        self.created: list[str] = []

    def create(self, target: MusicTargetConfig) -> FakePlayer:
        self.created.append(target.id)
        if self._create_failure is not None:
            raise self._create_failure()
        player = FakePlayer(target, failures=self._failures.get(target.id), delay=self._delay)
        self.players[target.id] = player
        return player


class FakeResolver:
    """Resolves every accepted source to a few numbered tracks."""

    def __init__(self, *, count: int = 3, missing: set[str] | None = None) -> None:
        self.count = count
        self.missing = missing or set()
        self.requests: list[tuple[str, str]] = []

    def resolve(self, source: MusicSource, library_dir: str) -> tuple[Track, ...]:
        self.requests.append((source.uri, library_dir))
        if source.ref in self.missing or source.uri in self.missing:
            raise MusicSourceError(
                "Source not found", user_message="Die Quelle wurde nicht gefunden."
            )
        kind = {
            SourceKind.LOCAL: TrackKind.FILE,
            SourceKind.URL: TrackKind.URL,
            SourceKind.YOUTUBE: TrackKind.YOUTUBE,
        }.get(source.kind, TrackKind.URL)
        return tuple(
            Track(f"{source.ref or source.playlist}/{i}", f"Titel {i + 1}", kind)
            for i in range(self.count)
        )
