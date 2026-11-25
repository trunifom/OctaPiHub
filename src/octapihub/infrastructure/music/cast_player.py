"""Music on Google Nest / Google Home speakers and Nest Hub displays (Google Cast).

``pychromecast`` is blocking, so every call to the backend runs in a worker thread. The
speaker plays one address at a time; the queue (next/previous, shuffle, repeat) is kept here
and a monitor task moves on when a track has finished.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from octapihub.application.music.models import (
    PlayerState,
    PlayerStatus,
    PlayRequest,
    Track,
    TrackKind,
)
from octapihub.application.music.queue import following, play_order, preceding
from octapihub.application.music.sources import SourceKind, parse_source
from octapihub.core.config import MusicTargetConfig, RepeatMode
from octapihub.core.errors import MusicError, MusicTargetError, MusicUnsupportedError
from octapihub.infrastructure.music.media import content_type_for

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10.0
MONITOR_INTERVAL_SECONDS = 2.0
_CAST_ERROR = "Das Gerät lässt sich nicht erreichen. Ist es eingeschaltet und im selben Netzwerk?"


@dataclass(frozen=True, slots=True)
class CastStatus:
    """What the device reports."""

    state: PlayerState
    title: str = ""
    volume: int | None = None
    finished: bool = False  # the last item played to its end


class CastSession(Protocol):
    """A blocking connection to one Cast device (called from worker threads)."""

    @property
    def alive(self) -> bool:
        """Whether the connection is still usable."""

    def play_url(self, url: str, content_type: str, title: str, live: bool) -> None:
        """Play an HTTP address (``live`` for radio streams, otherwise a finite file)."""

    def play_youtube(self, video_id: str, playlist_id: str | None) -> None:
        """Start a YouTube video or playlist."""

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def stop(self) -> None: ...

    def set_volume(self, percent: int) -> None: ...

    def status(self) -> CastStatus: ...

    def close(self) -> None: ...


class CastBackend(Protocol):
    """Finds and connects Cast devices (blocking)."""

    def connect(self, device: str) -> CastSession:
        """Connect to the device with this friendly name.

        Raises:
            MusicTargetError: The device was not found or refused the connection.
        """

    def discover(self, timeout: float) -> list[str]:
        """Friendly names of the Cast devices visible in the network."""


class PyChromecastBackend:
    """The real backend based on ``pychromecast`` (optional dependency ``music``)."""

    def connect(self, device: str) -> CastSession:
        try:
            import pychromecast
        except ImportError as exc:
            raise MusicUnsupportedError(
                "pychromecast is not installed",
                user_message=(
                    "Für Google-Lautsprecher fehlt das Paket „pychromecast“ "
                    "(pip install 'octapihub[music]')."
                ),
            ) from exc
        browser = None
        try:
            casts, browser = pychromecast.get_listed_chromecasts(
                friendly_names=[device], timeout=CONNECT_TIMEOUT_SECONDS
            )
            if not casts:
                raise MusicTargetError(
                    "Cast device not found",
                    user_message=f"„{device}“ wurde nicht gefunden. {_CAST_ERROR}",
                )
            cast = casts[0]
            cast.wait(timeout=CONNECT_TIMEOUT_SECONDS)
            return _PyChromecastSession(cast)
        except MusicError:
            raise
        except Exception as exc:
            raise MusicTargetError(
                "Could not connect the Cast device", user_message=_CAST_ERROR
            ) from exc
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.stop_discovery()

    def discover(self, timeout: float) -> list[str]:
        try:
            import pychromecast
        except ImportError:
            return []
        browser = None
        try:
            casts, browser = pychromecast.get_chromecasts(timeout=timeout)
            return sorted({str(cast.cast_info.friendly_name) for cast in casts})
        except Exception:
            logger.warning("Cast discovery failed", exc_info=True)
            return []
        finally:
            if browser is not None:
                with contextlib.suppress(Exception):
                    browser.stop_discovery()


class _PyChromecastSession:
    def __init__(self, cast: Any) -> None:
        self._cast = cast
        self._youtube: Any = None
        self._played = False

    @property
    def alive(self) -> bool:
        socket_client = getattr(self._cast, "socket_client", None)
        return bool(socket_client is not None and not getattr(socket_client, "is_stopped", False))

    def play_url(self, url: str, content_type: str, title: str, live: bool) -> None:
        controller = self._cast.media_controller
        controller.play_media(
            url,
            content_type,
            title=title,
            autoplay=True,
            stream_type="LIVE" if live else "BUFFERED",  # files can end, seek and report FINISHED
        )
        controller.block_until_active(timeout=CONNECT_TIMEOUT_SECONDS)
        self._played = True

    def play_youtube(self, video_id: str, playlist_id: str | None) -> None:
        if self._youtube is None:
            from pychromecast.controllers.youtube import YouTubeController

            self._youtube = YouTubeController()
            self._cast.register_handler(self._youtube)
        self._youtube.play_video(video_id, playlist_id=playlist_id)
        self._played = True

    def pause(self) -> None:
        self._cast.media_controller.pause()

    def resume(self) -> None:
        self._cast.media_controller.play()

    def stop(self) -> None:
        self._cast.media_controller.stop()
        self._played = False

    def set_volume(self, percent: int) -> None:
        self._cast.set_volume(max(0, min(100, percent)) / 100)

    def status(self) -> CastStatus:
        media = self._cast.media_controller.status
        level = getattr(self._cast.status, "volume_level", None)
        volume = round(level * 100) if isinstance(level, (int, float)) else None
        raw = str(getattr(media, "player_state", "IDLE") or "IDLE").upper()
        state = {
            "PLAYING": PlayerState.PLAYING,
            "PAUSED": PlayerState.PAUSED,
            "BUFFERING": PlayerState.BUFFERING,
        }.get(raw, PlayerState.IDLE)
        finished = (
            state is PlayerState.IDLE
            and self._played
            and str(getattr(media, "idle_reason", "") or "").upper() == "FINISHED"
        )
        return CastStatus(state, str(getattr(media, "title", "") or ""), volume, finished)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._cast.disconnect(timeout=2)


class CastPlayer:
    """The player for a Cast target; keeps the queue and advances it."""

    def __init__(
        self,
        target: MusicTargetConfig,
        backend: CastBackend,
        url_for: Callable[[str], str],
        *,
        interval: float = MONITOR_INTERVAL_SECONDS,
        seed: Callable[[], int] = lambda: random.SystemRandom().randrange(1 << 30),
    ) -> None:
        self._target = target
        self._backend = backend
        self._url_for = url_for
        self._interval = interval
        self._seed = seed
        self._session: CastSession | None = None
        self._tracks: tuple[Track, ...] = ()
        self._order: list[int] = []
        self._position = 0
        self._repeat = RepeatMode.OFF
        self._volume = target.volume
        self._monitor: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def _ready(self) -> CastSession:
        session = self._session
        if session is not None and session.alive:
            return session
        if session is not None:
            await asyncio.to_thread(session.close)
            self._session = None
        self._session = await asyncio.to_thread(self._backend.connect, self._target.device)
        return self._session

    async def _call(self, operation: Callable[..., None], *args: object) -> None:
        try:
            await asyncio.to_thread(operation, *args)
        except MusicError:
            raise
        except Exception as exc:
            logger.warning("Cast command failed on %s", self._target.id, exc_info=True)
            raise MusicTargetError(
                "The Cast device rejected the command", user_message=_CAST_ERROR
            ) from exc

    async def _start_current(self) -> None:
        session = await self._ready()
        track = self._tracks[self._order[self._position]]
        if track.kind is TrackKind.YOUTUBE:
            source = parse_source(track.location)
            if source.kind is not SourceKind.YOUTUBE or not source.ref:
                raise MusicUnsupportedError(
                    "YouTube playlists without a video cannot be cast",
                    user_message="Für diese YouTube-Playlist bitte ein Video daraus angeben.",
                )
            await self._call(session.play_youtube, source.ref, source.playlist)
            return
        live = track.kind is TrackKind.URL
        url = track.location if live else self._url_for(track.location)
        await self._call(session.play_url, url, content_type_for(track.location), track.title, live)

    async def play(self, request: PlayRequest) -> None:
        if not request.tracks:
            raise MusicTargetError("Nothing to play")
        async with self._lock:
            self._tracks = request.tracks
            self._order = play_order(
                len(request.tracks), shuffle=request.shuffle, seed=self._seed()
            )
            self._position = 0
            self._repeat = request.repeat
            if request.volume is not None:
                self._volume = request.volume
            session = await self._ready()
            await self._call(session.set_volume, self._volume)
            await self._start_current()
            self._ensure_monitor()

    async def pause(self) -> None:
        await self._call((await self._ready()).pause)

    async def resume(self) -> None:
        await self._call((await self._ready()).resume)

    async def stop(self) -> None:
        async with self._lock:
            self._tracks = ()
            self._order = []
        session = self._session
        if session is not None and session.alive:
            await self._call(session.stop)

    async def next(self) -> None:
        async with self._lock:
            await self._move(
                following(self._position, len(self._order), self._repeat, by_user=True)
            )

    async def previous(self) -> None:
        async with self._lock:
            if not self._order:
                return
            await self._move(preceding(self._position, len(self._order), self._repeat))

    async def _move(self, position: int | None) -> None:
        if not self._order:
            return
        if position is None:
            session = await self._ready()
            self._tracks, self._order = (), []
            await self._call(session.stop)
            return
        self._position = position
        await self._start_current()

    async def set_volume(self, percent: int) -> None:
        self._volume = max(0, min(100, percent))
        await self._call((await self._ready()).set_volume, self._volume)

    async def status(self) -> PlayerStatus:
        session = self._session
        if session is None or not session.alive:
            return PlayerStatus(PlayerState.IDLE, "", self._volume)
        try:
            raw = await asyncio.to_thread(session.status)
        except Exception:  # noqa: BLE001 - any device failure becomes a status
            return PlayerStatus(
                PlayerState.ERROR, "", self._volume, _CAST_ERROR, MusicTargetError.code
            )
        title = raw.title or (
            self._tracks[self._order[self._position]].title if self._order else ""
        )
        return PlayerStatus(
            raw.state, title, raw.volume if raw.volume is not None else self._volume
        )

    def _ensure_monitor(self) -> None:
        if self._monitor is None or self._monitor.done():
            self._monitor = asyncio.get_running_loop().create_task(self._watch())

    async def _watch(self) -> None:
        """Advance the queue when a track ended; stops by itself when the queue is empty."""
        while self._order:
            await asyncio.sleep(self._interval)
            session = self._session
            if session is None or not session.alive:
                return
            try:
                raw = await asyncio.to_thread(session.status)
                if not raw.finished:
                    continue
                async with self._lock:
                    if not self._order:
                        return
                    await self._move(following(self._position, len(self._order), self._repeat))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Cast queue could not advance on %s", self._target.id, exc_info=True)
                return

    async def close(self) -> None:
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await monitor
        session, self._session = self._session, None
        if session is not None:
            await asyncio.to_thread(session.close)
