from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from octapihub.application.music.models import PlayerState, PlayRequest, Track, TrackKind
from octapihub.application.music.sources import parse_source
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import (
    MusicTargetConfig,
    MusicTargetKind,
    RepeatMode,
    parse_config,
    save_config,
)
from octapihub.core.errors import MusicTargetError, MusicUnsupportedError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.music.cast_player import CastPlayer, CastStatus
from octapihub.infrastructure.music.media import MediaRegistry, MediaUrls
from octapihub.infrastructure.music.mpv_player import MpvPlayer
from octapihub.infrastructure.music.players import DefaultPlayerFactory
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web.app import WebBackend, create_app

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijklmnop"


def aio(test: Any) -> Any:
    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


class FakeSession:
    def __init__(self) -> None:
        self.alive = True
        self.calls: list[tuple[Any, ...]] = []
        self.current = CastStatus(PlayerState.PLAYING, "Song", 30)
        self.fail = False

    def _do(self, *call: Any) -> None:
        if self.fail:
            raise OSError("network")
        self.calls.append(call)

    def play_url(self, url: str, content_type: str, title: str) -> None:
        self._do("url", url, content_type, title)

    def play_youtube(self, video_id: str, playlist_id: str | None) -> None:
        self._do("youtube", video_id, playlist_id)

    def pause(self) -> None:
        self._do("pause")

    def resume(self) -> None:
        self._do("resume")

    def stop(self) -> None:
        self._do("stop")

    def set_volume(self, percent: int) -> None:
        self._do("volume", percent)

    def status(self) -> CastStatus:
        if self.fail:
            raise OSError("network")
        return self.current

    def close(self) -> None:
        self.alive = False


class FakeBackend:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []
        self.refuse = False

    def connect(self, device: str) -> FakeSession:
        if self.refuse:
            raise MusicTargetError("not found")
        session = FakeSession()
        self.sessions.append(session)
        return session

    def discover(self, timeout: float) -> list[str]:
        return ["Kueche"]


def target() -> MusicTargetConfig:
    return MusicTargetConfig(
        id="kueche", name="Kueche", kind=MusicTargetKind.CAST, device="Kueche", volume=35
    )


def tracks(*kinds: TrackKind) -> tuple[Track, ...]:
    out = []
    for i, kind in enumerate(kinds):
        location = {
            TrackKind.FILE: f"/music/{i}.mp3",
            TrackKind.URL: f"https://radio.example.org/{i}",
            TrackKind.YOUTUBE: YOUTUBE,
        }[kind]
        out.append(Track(location, f"t{i}", kind))
    return tuple(out)


def request(items: tuple[Track, ...], **kw: Any) -> PlayRequest:
    return PlayRequest(items, parse_source("Jazz"), "Jazz", **kw)


def make(backend: FakeBackend | None = None) -> tuple[CastPlayer, FakeBackend]:
    backend = backend or FakeBackend()
    player = CastPlayer(target(), backend, lambda p: f"http://pi:8080/media{p}", interval=0.01)
    return player, backend


@aio
async def test_play_sends_volume_and_the_right_kind_of_address() -> None:
    player, backend = make()
    await player.play(request(tracks(TrackKind.FILE, TrackKind.URL), volume=20))
    calls = backend.sessions[0].calls
    assert calls[0] == ("volume", 20)
    assert calls[1] == ("url", "http://pi:8080/media/music/0.mp3", "audio/mpeg", "t0")
    await player.next()
    assert backend.sessions[0].calls[-1][1] == "https://radio.example.org/1"
    await player.close()


@aio
async def test_youtube_track_and_playlist_without_video() -> None:
    player, backend = make()
    await player.play(request(tracks(TrackKind.YOUTUBE)))
    assert ("youtube", "dQw4w9WgXcQ", "PLabcdefghijklmnop") in backend.sessions[0].calls
    other, _ = make()
    only_list = (
        Track("https://www.youtube.com/playlist?list=PLabcdefghijklmnop", "x", TrackKind.YOUTUBE),
    )
    with pytest.raises(MusicUnsupportedError):
        await other.play(request(only_list))
    await player.close()
    await other.close()


@aio
async def test_controls_status_and_stop() -> None:
    player, backend = make()
    assert (await player.status()).state is PlayerState.IDLE
    await player.play(request(tracks(TrackKind.URL)))
    status = await player.status()
    assert (status.state, status.track_title, status.volume) == (PlayerState.PLAYING, "Song", 30)
    await player.pause()
    await player.resume()
    await player.set_volume(150)
    await player.stop()
    session = backend.sessions[0]
    assert ("pause",) in session.calls and ("resume",) in session.calls
    assert ("volume", 100) in session.calls and session.calls[-1] == ("stop",)
    session.fail = True
    assert (await player.status()).state is PlayerState.ERROR
    await player.close()
    assert not session.alive


@aio
async def test_monitor_advances_when_a_track_finished_and_repeats() -> None:
    player, backend = make()
    await player.play(request(tracks(TrackKind.URL, TrackKind.URL), repeat=RepeatMode.ALL))
    session = backend.sessions[0]
    session.current = CastStatus(PlayerState.IDLE, finished=True)
    for _ in range(200):
        if sum(1 for c in session.calls if c[0] == "url") >= 3:
            break
        await asyncio.sleep(0.01)
    played = [c[1] for c in session.calls if c[0] == "url"]
    assert played[:3] == [
        "https://radio.example.org/0",
        "https://radio.example.org/1",
        "https://radio.example.org/0",
    ]
    await player.close()


@aio
async def test_queue_end_stops_and_previous_on_empty_queue_is_harmless() -> None:
    player, backend = make()
    await player.play(request(tracks(TrackKind.URL)))
    await player.next()
    assert backend.sessions[0].calls[-1] == ("stop",)
    await player.previous()
    await player.close()


@aio
async def test_errors_are_typed_and_the_connection_is_retried() -> None:
    backend = FakeBackend()
    backend.refuse = True
    player, _ = make(backend)
    with pytest.raises(MusicTargetError):
        await player.play(request(tracks(TrackKind.URL)))
    backend.refuse = False
    await player.play(request(tracks(TrackKind.URL)))
    backend.sessions[0].fail = True
    with pytest.raises(MusicTargetError):
        await player.pause()
    backend.sessions[0].alive = False
    backend.sessions[0].fail = False
    await player.pause()
    assert len(backend.sessions) == 2
    with pytest.raises(MusicTargetError):
        await make()[0].play(request(()))
    await player.close()


def test_factory_picks_the_player_for_the_kind(tmp_path: Path) -> None:
    media = MediaUrls(MediaRegistry(lambda: str(tmp_path)), port=1, configured_host=lambda: "h")
    factory = DefaultPlayerFactory(media, cast_backend=FakeBackend())
    assert isinstance(factory.create(target()), CastPlayer)
    local = MusicTargetConfig(id="pi", name="Pi", kind=MusicTargetKind.LOCAL, volume=10)
    assert isinstance(factory.create(local), MpvPlayer)


# ---------- /media route ----------


def _client(tmp_path: Path) -> tuple[TestClient, MediaRegistry]:
    library = tmp_path / "lib"
    library.mkdir()
    (library / "a.mp3").write_bytes(b"0123456789")
    config = parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "music"}],
        }
    )
    path = tmp_path / "octapihub.toml"
    save_config(path, config)
    device = FakeSenseHat()
    sense = SenseHatService(
        config.sense_hat,
        lambda _c: SenseHatAdapter(device),
        lambda c: SQLiteSenseHatHistory(str(tmp_path / "h.sqlite3")),
    )
    registry = MediaRegistry(lambda: str(library))
    backend = WebBackend(
        SettingsManager(path, config, sense), sense, "token-1234567890", media=registry
    )
    return TestClient(create_app(backend)), registry


def test_media_route_serves_registered_files_only(tmp_path: Path) -> None:
    client, registry = _client(tmp_path)
    token = registry.register(str(tmp_path / "lib" / "a.mp3"))
    ok = client.get(f"/media/{token}")
    assert ok.status_code == 200 and ok.content == b"0123456789"
    assert ok.headers["content-type"] == "audio/mpeg"
    assert client.head(f"/media/{token}").status_code == 200
    partial = client.get(f"/media/{token}", headers={"range": "bytes=2-4"})
    assert partial.status_code == 206 and partial.content == b"234"
    assert client.get("/media/unknown-token").status_code == 404
    assert client.get("/media/..%2Fsecret").status_code == 404
    (tmp_path / "lib" / "a.mp3").unlink()
    assert client.get(f"/media/{token}").status_code == 404
