"""The music service: targets, playlists, and the ``music`` action plugin.

Cube faces, the cube holder, the joystick, and the web interface all use the same actions:

========================  ===========================================================
``play``                  ``playlist`` (id) or ``source`` (link/path); optional ``target``,
                          ``volume``, ``shuffle``, ``repeat``
``pause`` / ``stop``      one ``target`` or, without it, every target that is active
``stop.all``              every configured target
``resume`` / ``toggle``   ``target`` (default: the one used last, or the only one)
``next`` / ``previous``   dito
``volume.set``            ``level`` 0..100
``volume.up`` / ``down``  ``step`` 1..50 (default 10)
========================  ===========================================================

Starting music on a target stops other targets that are playing, so two speakers never play
different things by accident (use a speaker group for several rooms). Errors the user has to fix
(invalid source, no target) are not retried; an unreachable device is retried briefly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from octapihub.application.music.models import (
    MusicPlayer,
    PlayerFactory,
    PlayerState,
    PlayerStatus,
    PlayRequest,
    TrackResolver,
)
from octapihub.application.music.sources import SourceKind, parse_source
from octapihub.core.config import (
    MusicConfig,
    MusicTargetConfig,
    MusicTargetKind,
    PlaylistConfig,
    RepeatMode,
)
from octapihub.core.errors import (
    MusicError,
    MusicNotConfiguredError,
    MusicSourceError,
    MusicTargetError,
    OctaPiHubError,
)

logger = logging.getLogger(__name__)

COMMAND_TIMEOUT_SECONDS = 20.0
STATUS_TIMEOUT_SECONDS = 5.0
DEFAULT_VOLUME_STEP = 10
_ACTIVE = {PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.BUFFERING}


class MusicAction(StrEnum):
    """The actions of the ``music`` plugin."""

    PLAY = "play"
    PAUSE = "pause"
    RESUME = "resume"
    TOGGLE = "toggle"
    STOP = "stop"
    STOP_ALL = "stop.all"
    NEXT = "next"
    PREVIOUS = "previous"
    VOLUME_SET = "volume.set"
    VOLUME_UP = "volume.up"
    VOLUME_DOWN = "volume.down"


@dataclass(frozen=True, slots=True)
class TargetStatus:
    """What the interface shows for one target."""

    id: str
    name: str
    kind: MusicTargetKind
    state: PlayerState
    title: str
    track_title: str
    volume: int | None
    error: str | None
    error_code: str | None
    current: bool


@dataclass(frozen=True, slots=True)
class MusicStatus:
    """Snapshot of the whole music system; contains no secrets."""

    enabled: bool
    targets: tuple[TargetStatus, ...]

    @property
    def playing(self) -> bool:
        """Whether anything plays right now."""
        return any(t.state is PlayerState.PLAYING for t in self.targets)


type Listener = Callable[[], None]


def _text(params: Mapping[str, object], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MusicNotConfiguredError(
            f"Music parameter {key!r} must be text",
            user_message=f"Die Einstellung „{key}“ der Musik-Aktion ist ungültig.",
        )
    return value.strip() or None


def _flag(params: Mapping[str, object], key: str) -> bool | None:
    if key not in params:
        return None
    value = params[key]
    if not isinstance(value, bool):
        raise MusicNotConfiguredError(
            f"Music parameter {key!r} must be true or false",
            user_message=f"Die Einstellung „{key}“ der Musik-Aktion ist ungültig.",
        )
    return value


def _number(params: Mapping[str, object], key: str, low: int, high: int) -> int | None:
    if key not in params:
        return None
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MusicNotConfiguredError(
            f"Music parameter {key!r} must be an integer from {low} to {high}",
            user_message=f"„{key}“ muss eine Zahl von {low} bis {high} sein.",
        )
    return value


class MusicService:
    """Owns the players and runs music actions; also usable directly by the web interface."""

    kind = "music"

    def __init__(
        self, config: MusicConfig, players: PlayerFactory, resolver: TrackResolver
    ) -> None:
        self._config = config
        self._factory = players
        self._resolver = resolver
        self._players: dict[str, MusicPlayer] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._states: dict[str, PlayerStatus] = {}
        self._titles: dict[str, str] = {}
        self._errors: dict[str, tuple[str, str]] = {}
        self._current: str | None = None
        self._listeners: list[Listener] = []

    # ----- state -----

    @property
    def config(self) -> MusicConfig:
        """Active music settings."""
        return self._config

    @property
    def status(self) -> MusicStatus:
        """Current snapshot (from the last known player states)."""
        targets: list[TargetStatus] = []
        for target in self._config.targets:
            state = self._states.get(target.id, PlayerStatus())
            error = self._errors.get(target.id)
            targets.append(
                TargetStatus(
                    id=target.id,
                    name=target.name,
                    kind=target.kind,
                    state=state.state,
                    title=self._titles.get(target.id, ""),
                    track_title=state.track_title,
                    volume=state.volume,
                    error=error[0] if error else state.error,
                    error_code=error[1] if error else state.error_code,
                    current=target.id == self._current,
                )
            )
        return MusicStatus(self._config.enabled, tuple(targets))

    def add_listener(self, listener: Listener) -> None:
        """Be told after every state change (used by the LED display)."""
        self._listeners.append(listener)

    def _changed(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:
                logger.exception("Music listener failed")

    # ----- lifecycle -----

    async def close(self) -> None:
        """Release all players (music that plays keeps playing on the device)."""
        players, self._players = self._players, {}
        for player in players.values():
            try:
                await asyncio.wait_for(player.close(), timeout=5)
            except Exception:
                logger.exception("Could not close a music player")

    async def reconfigure(self, config: MusicConfig) -> None:
        """Apply new settings; players of changed or removed targets are closed."""
        old = {t.id: t for t in self._config.targets}
        new = {t.id: t for t in config.targets}
        stale = [tid for tid, target in old.items() if new.get(tid) != target]
        for target_id in stale:
            player = self._players.pop(target_id, None)
            if player is not None:
                try:
                    await asyncio.wait_for(player.close(), timeout=5)
                except Exception:
                    logger.exception("Could not close a music player")
            self._states.pop(target_id, None)
            self._titles.pop(target_id, None)
            self._errors.pop(target_id, None)
        self._config = config
        if self._current not in new:
            self._current = None
        self._changed()

    async def refresh(self) -> MusicStatus:
        """Ask every already connected player for its state (never creates players)."""
        for target_id, player in list(self._players.items()):
            await self._refresh_state(target_id, player)
        self._changed()
        return self.status

    # ----- plugin entry point -----

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        """Run one music action (plugin contract)."""
        config = self._require_enabled()
        try:
            kind = MusicAction(action)
        except ValueError as exc:
            raise MusicNotConfiguredError(
                f"Unsupported music action {action!r}",
                user_message="Diese Musik-Aktion gibt es nicht.",
            ) from exc
        if kind is MusicAction.PLAY:
            await self._play(config, parameters)
        elif kind is MusicAction.STOP_ALL:
            for target in config.targets:
                await self._transport(target, "stop", lambda p: p.stop())
            self._current = None
        elif kind in (MusicAction.PAUSE, MusicAction.STOP):
            await self._stop_or_pause(config, kind, parameters)
        elif kind in (MusicAction.RESUME, MusicAction.NEXT, MusicAction.PREVIOUS):
            target = self._pick_transport_target(config, parameters)
            call = {
                MusicAction.RESUME: lambda p: p.resume(),
                MusicAction.NEXT: lambda p: p.next(),
                MusicAction.PREVIOUS: lambda p: p.previous(),
            }[kind]
            await self._transport(target, kind.value, call)
        elif kind is MusicAction.TOGGLE:
            await self._toggle(config, parameters)
        else:
            await self._volume(config, kind, parameters)

    # ----- implementations -----

    def _require_enabled(self) -> MusicConfig:
        if not self._config.enabled:
            raise MusicNotConfiguredError(
                "Music is disabled", user_message="Musik ist ausgeschaltet. Schalten Sie sie ein."
            )
        return self._config

    def _target_by_id(self, config: MusicConfig, target_id: str) -> MusicTargetConfig:
        for target in config.targets:
            if target.id == target_id:
                return target
        raise MusicNotConfiguredError(
            f"Unknown music target {target_id!r}",
            user_message="Das gewählte Wiedergabegerät gibt es nicht (mehr).",
        )

    def _pick_target(
        self, config: MusicConfig, params: Mapping[str, object], playlist: PlaylistConfig | None
    ) -> MusicTargetConfig:
        explicit = _text(params, "target")
        if explicit:
            return self._target_by_id(config, explicit)
        if playlist is not None and playlist.target:
            return self._target_by_id(config, playlist.target)
        return self._default_target(config)

    def _default_target(self, config: MusicConfig) -> MusicTargetConfig:
        if self._current is not None:
            for target in config.targets:
                if target.id == self._current:
                    return target
        if len(config.targets) == 1:
            return config.targets[0]
        raise MusicNotConfiguredError(
            "No music target chosen",
            user_message="Bitte ein Wiedergabegerät wählen (es sind mehrere eingerichtet).",
        )

    def _pick_transport_target(
        self, config: MusicConfig, params: Mapping[str, object]
    ) -> MusicTargetConfig:
        explicit = _text(params, "target")
        return self._target_by_id(config, explicit) if explicit else self._default_target(config)

    async def _play(self, config: MusicConfig, params: Mapping[str, object]) -> None:
        playlist_id = _text(params, "playlist")
        playlist: PlaylistConfig | None = None
        if playlist_id:
            playlist = next((p for p in config.playlists if p.id == playlist_id), None)
            if playlist is None:
                raise MusicNotConfiguredError(
                    f"Unknown playlist {playlist_id!r}",
                    user_message="Diese Playlist gibt es nicht (mehr).",
                )
        source_text = _text(params, "source") or (playlist.source if playlist else None)
        if not source_text:
            raise MusicNotConfiguredError(
                "No playlist or source given",
                user_message="Es ist weder eine Playlist noch eine Quelle eingestellt.",
            )
        source = parse_source(source_text)
        if source.kind is SourceKind.SPOTIFY:
            raise MusicSourceError(
                "Spotify playback is not supported",
                user_message=(
                    "Spotify-Links können noch nicht abgespielt werden. Verwenden Sie einen "
                    "YouTube-Link, eine Radio-Adresse oder Dateien im Musikordner."
                ),
            )
        target = self._pick_target(config, params, playlist)
        tracks = await asyncio.to_thread(self._resolver.resolve, source, config.library_dir)
        shuffle = _flag(params, "shuffle")
        repeat_text = _text(params, "repeat")
        try:
            repeat = (
                RepeatMode(repeat_text)
                if repeat_text
                else (playlist.repeat if playlist else RepeatMode.OFF)
            )
        except ValueError as exc:
            raise MusicNotConfiguredError(
                f"Invalid repeat mode {repeat_text!r}",
                user_message="Wiederholen muss „off“, „all“ oder „one“ sein.",
            ) from exc
        volume = _number(params, "volume", 0, 100)
        if volume is None:
            volume = playlist.volume if playlist and playlist.volume is not None else target.volume
        request = PlayRequest(
            tracks=tracks,
            source=source,
            title=playlist.name if playlist else source.description,
            shuffle=shuffle if shuffle is not None else (playlist.shuffle if playlist else False),
            repeat=repeat,
            volume=volume,
        )
        for other in config.targets:
            if (
                other.id != target.id
                and self._states.get(other.id, PlayerStatus()).state in _ACTIVE
            ):
                await self._transport(other, "stop", lambda p: p.stop())
        await self._transport(target, "play", lambda p: p.play(request))
        self._titles[target.id] = request.title
        self._current = target.id
        self._changed()

    async def _stop_or_pause(
        self, config: MusicConfig, kind: MusicAction, params: Mapping[str, object]
    ) -> None:
        explicit = _text(params, "target")
        if explicit:
            targets = [self._target_by_id(config, explicit)]
        else:  # whatever is active (nothing active is fine: e.g. a holder action while silent)
            targets = [
                t for t in config.targets if self._states.get(t.id, PlayerStatus()).state in _ACTIVE
            ]
        for target in targets:
            call: Callable[[MusicPlayer], Awaitable[None]] = (
                (lambda p: p.stop()) if kind is MusicAction.STOP else (lambda p: p.pause())
            )
            await self._transport(target, kind.value, call)

    async def _toggle(self, config: MusicConfig, params: Mapping[str, object]) -> None:
        target = self._pick_transport_target(config, params)
        state = self._states.get(target.id, PlayerStatus()).state
        if state is PlayerState.PLAYING:
            await self._transport(target, "pause", lambda p: p.pause())
        elif state in (PlayerState.PAUSED, PlayerState.BUFFERING):
            await self._transport(target, "resume", lambda p: p.resume())
        elif _text(params, "playlist") or _text(params, "source"):
            await self._play(config, params)
        else:
            raise MusicNotConfiguredError(
                "Nothing to toggle",
                user_message="Es läuft nichts, das pausiert oder fortgesetzt werden könnte.",
            )

    async def _volume(
        self, config: MusicConfig, kind: MusicAction, params: Mapping[str, object]
    ) -> None:
        target = self._pick_transport_target(config, params)
        if kind is MusicAction.VOLUME_SET:
            level = _number(params, "level", 0, 100)
            if level is None:
                raise MusicNotConfiguredError(
                    "volume.set needs a level",
                    user_message="Für die Lautstärke fehlt die Angabe „level“ (0 bis 100).",
                )
        else:
            step = _number(params, "step", 1, 50) or DEFAULT_VOLUME_STEP
            current = self._states.get(target.id, PlayerStatus()).volume
            base = current if current is not None else target.volume
            level = base + step if kind is MusicAction.VOLUME_UP else base - step
        level = max(0, min(100, level))
        await self._transport(target, kind.value, lambda p: p.set_volume(level))

    # ----- player access -----

    def _lock(self, target_id: str) -> asyncio.Lock:
        return self._locks.setdefault(target_id, asyncio.Lock())

    def _player(self, target: MusicTargetConfig) -> MusicPlayer:
        player = self._players.get(target.id)
        if player is None:
            try:
                player = self._factory.create(target)
            except MusicError:
                raise
            except Exception as exc:
                logger.exception("Could not create the player for %s", target.id)
                raise MusicTargetError("Could not create the music player") from exc
            self._players[target.id] = player
        return player

    async def _transport(
        self,
        target: MusicTargetConfig,
        name: str,
        call: Callable[[MusicPlayer], Awaitable[None]],
    ) -> None:
        async with self._lock(target.id):
            player = self._player(target)
            try:
                await asyncio.wait_for(call(player), timeout=COMMAND_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                raise
            except MusicError as exc:
                self._errors[target.id] = (exc.user_message, exc.error_code)
                self._changed()
                raise
            except TimeoutError as exc:
                error = MusicTargetError(f"Music command {name!r} timed out")
                self._errors[target.id] = (error.user_message, error.error_code)
                self._changed()
                raise error from exc
            except Exception as exc:
                logger.exception("Music command %s failed on %s", name, target.id)
                error = MusicTargetError(f"Music command {name!r} failed")
                self._errors[target.id] = (error.user_message, error.error_code)
                self._changed()
                raise error from exc
            self._errors.pop(target.id, None)
            await self._refresh_state(target.id, player)
            self._changed()

    async def _refresh_state(self, target_id: str, player: MusicPlayer) -> None:
        try:
            self._states[target_id] = await asyncio.wait_for(
                player.status(), timeout=STATUS_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except (OctaPiHubError, TimeoutError, OSError):
            pass  # keep the last known state; the command itself succeeded
        except Exception:
            logger.exception("Could not read the state of %s", target_id)
