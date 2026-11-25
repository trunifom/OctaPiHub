from __future__ import annotations

import asyncio
import functools
from pathlib import Path
from typing import Any

import pytest

from octapihub.application.music.models import (
    PlayerState,
    PlayRequest,
    Track,
    TrackKind,
)
from octapihub.application.music.sources import parse_source
from octapihub.core.config import MusicTargetConfig, MusicTargetKind, RepeatMode
from octapihub.core.errors import (
    MusicNotConfiguredError,
    MusicSourceError,
    MusicTargetError,
)
from octapihub.infrastructure.music.library import LibraryResolver
from octapihub.infrastructure.music.media import (
    MediaRegistry,
    MediaUrls,
    content_type_for,
)
from octapihub.infrastructure.music.mpv_player import (
    MpvIpc,
    MpvPlayer,
    build_mpv_command,
    parse_audio_devices,
    validate_device,
)


def aio(test: Any) -> Any:
    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> None:
        asyncio.run(test(*args, **kwargs))

    return run


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


# ---------- library ----------


def test_folder_is_expanded_in_natural_order_without_hidden_files(tmp_path: Path) -> None:
    for name in ("10 b.mp3", "2 a.mp3", ".hidden.mp3", "notes.txt"):
        touch(tmp_path / "Jazz" / name)
    touch(tmp_path / "Jazz" / "Sub" / "z_track.flac")
    tracks = LibraryResolver().resolve(parse_source("Jazz"), str(tmp_path))
    assert [t.title for t in tracks] == ["2 a", "10 b", "z track"]
    assert all(t.kind is TrackKind.FILE for t in tracks)


def test_single_file_and_stream_sources(tmp_path: Path) -> None:
    touch(tmp_path / "a.mp3")
    resolver = LibraryResolver()
    assert len(resolver.resolve(parse_source("a.mp3"), str(tmp_path))) == 1
    url = resolver.resolve(parse_source("https://radio.example.org/live"), "")
    assert url[0].kind is TrackKind.URL and url[0].title == "radio.example.org"
    video = resolver.resolve(parse_source("https://youtu.be/dQw4w9WgXcQ"), "")
    assert video[0].kind is TrackKind.YOUTUBE


@pytest.mark.parametrize("library", ["", "   ", "missing-folder"])
def test_missing_library_is_a_source_error(tmp_path: Path, library: str) -> None:
    value = str(tmp_path / library) if library.startswith("missing") else library
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source("a.mp3"), value)


def test_paths_outside_the_library_are_refused(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()
    outside = touch(tmp_path / "secret.mp3")
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source(str(outside)), str(library))


def test_symlink_leading_out_is_refused(tmp_path: Path) -> None:
    library = tmp_path / "lib"
    library.mkdir()
    outside = touch(tmp_path / "out" / "secret.mp3")
    try:
        (library / "link.mp3").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source("link.mp3"), str(library))


def test_empty_folder_and_unsupported_file(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    touch(tmp_path / "doc.txt")
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source("empty"), str(tmp_path))
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source("doc.txt"), str(tmp_path))
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source("nothing.mp3"), str(tmp_path))


def test_m3u_playlist_keeps_inside_entries_only(tmp_path: Path) -> None:
    touch(tmp_path / "a.mp3")
    touch(tmp_path / "sub" / "b.mp3")
    (tmp_path / "list.m3u").write_text(
        "#EXTM3U\n#EXTINF:1,Erster\na.mp3\nsub/b.mp3\n../evil.mp3\nmissing.mp3\n"
        "https://radio.example.org/s\nftp://x/y.mp3\nfile:///etc/passwd\n",
        encoding="utf-8",
    )
    tracks = LibraryResolver().resolve(parse_source("list.m3u"), str(tmp_path))
    assert [t.title for t in tracks] == ["Erster", "b", "radio.example.org"]


def test_pls_playlist(tmp_path: Path) -> None:
    touch(tmp_path / "a.mp3")
    (tmp_path / "x.pls").write_text(
        "[playlist]\nFile1=a.mp3\nTitle1=Eins\nFile2=gone.mp3\nNumberOfEntries=2\n",
        encoding="utf-8",
    )
    tracks = LibraryResolver().resolve(parse_source("x.pls"), str(tmp_path))
    assert [t.title for t in tracks] == ["Eins"]


def test_too_many_files_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octapihub.infrastructure.music.library.MAX_TRACKS", 2)
    for i in range(3):
        touch(tmp_path / f"{i}.mp3")
    with pytest.raises(MusicSourceError):
        LibraryResolver().resolve(parse_source(str(tmp_path)), str(tmp_path))


# ---------- media registry ----------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_registry_serves_only_library_files_and_expires(tmp_path: Path) -> None:
    song = touch(tmp_path / "lib" / "a.mp3")
    other = touch(tmp_path / "other.mp3")
    text = touch(tmp_path / "lib" / "a.txt")
    clock = Clock()
    registry = MediaRegistry(lambda: str(tmp_path / "lib"), ttl_seconds=10, clock=clock)
    token = registry.register(str(song))
    assert registry.register(str(song)) == token  # stable per file
    assert registry.resolve(token) == song.resolve()
    assert registry.resolve("unknown") is None
    for bad in (other, text, tmp_path / "lib" / "none.mp3"):
        with pytest.raises(MusicSourceError):
            registry.register(str(bad))
    clock.now = 11
    assert registry.resolve(token) is None


def test_registry_needs_a_library_and_rechecks_on_resolve(tmp_path: Path) -> None:
    song = touch(tmp_path / "a.mp3")
    folder = {"value": ""}
    registry = MediaRegistry(lambda: folder["value"])
    with pytest.raises(MusicNotConfiguredError):
        registry.register(str(song))
    folder["value"] = str(tmp_path)
    token = registry.register(str(song))
    folder["value"] = str(tmp_path / "elsewhere")  # library moved: token is dead
    assert registry.resolve(token) is None
    folder["value"] = str(tmp_path)
    song.unlink()
    assert registry.resolve(token) is None


def test_registry_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octapihub.infrastructure.music.media.MAX_TOKENS", 2)
    counter = iter(range(100))
    registry = MediaRegistry(lambda: str(tmp_path), token_factory=lambda: f"t{next(counter)}")
    tokens = [registry.register(str(touch(tmp_path / f"{i}.mp3"))) for i in range(3)]
    assert registry.resolve(tokens[0]) is None
    assert registry.resolve(tokens[2]) is not None


def test_media_urls(tmp_path: Path) -> None:
    song = touch(tmp_path / "a.mp3")
    registry = MediaRegistry(lambda: str(tmp_path), token_factory=lambda: "TOK")
    configured = MediaUrls(registry, port=8080, configured_host=lambda: "192.168.1.5")
    assert configured.url_for(str(song)) == "http://192.168.1.5:8080/media/TOK"
    detected = MediaUrls(registry, port=80, detect=lambda: "fe80::1")
    assert detected.url_for(str(song)) == "http://[fe80::1]:80/media/TOK"
    unknown = MediaUrls(registry, port=80, detect=lambda: None)
    with pytest.raises(MusicNotConfiguredError):
        unknown.url_for(str(song))


def test_content_types() -> None:
    assert content_type_for("a.MP3") == "audio/mpeg"
    assert content_type_for("a.flac") == "audio/flac"
    assert content_type_for("a.unknownext") == "audio/mpeg"


# ---------- mpv ----------


def target(**overrides: Any) -> MusicTargetConfig:
    values: dict[str, Any] = {
        "id": "pi",
        "name": "Pi",
        "kind": MusicTargetKind.LOCAL,
        "device": "",
        "bluetooth_address": "",
        "volume": 40,
    }
    values.update(overrides)
    return MusicTargetConfig(**values)


def test_command_line_is_an_argument_list_with_the_device() -> None:
    command = build_mpv_command(target(device="alsa/plughw:1,0"), "/tmp/s", 150)
    assert command[0] == "mpv"
    assert "--audio-device=alsa/plughw:1,0" in command
    assert "--input-ipc-server=/tmp/s" in command
    assert "--volume=100" in command
    assert not any("audio-device" in part for part in build_mpv_command(target(), "/s", 5))


@pytest.mark.parametrize("device", ["a b", "x;rm -rf /", "$(id)", "a\nb", "x" * 201])
def test_dangerous_device_names_are_rejected(device: str) -> None:
    with pytest.raises(MusicNotConfiguredError):
        validate_device(device)


def test_audio_device_listing_is_parsed() -> None:
    output = (
        "List of detected audio devices:\n"
        "  'auto' (Autoselect device)\n"
        "  'alsa/default' (Default (alsa))\n"
        "  'bad name;x' (Evil)\n"
    )
    assert parse_audio_devices(output) == [
        ("auto", "Autoselect device"),
        ("alsa/default", "Default (alsa)"),
    ]


class FakeChannel:
    def __init__(self) -> None:
        self.alive = True
        self.calls: list[tuple[object, ...]] = []
        self.properties: dict[str, object] = {"idle-active": False}
        self.fail_on: str | None = None
        self.closed = False

    async def command(self, *arguments: object) -> object:
        self.calls.append(arguments)
        if self.fail_on and arguments[0] == self.fail_on:
            raise MusicTargetError("boom")
        if arguments[0] == "get_property":
            name = str(arguments[1])
            if name not in self.properties:
                raise MusicTargetError("unavailable")
            return self.properties[name]
        return None

    async def close(self) -> None:
        self.closed = True
        self.alive = False


class FakeBackend:
    def __init__(self) -> None:
        self.channels: list[FakeChannel] = []

    async def open(self, target: MusicTargetConfig, volume: int) -> FakeChannel:
        channel = FakeChannel()
        self.channels.append(channel)
        return channel


class FakeBluetooth:
    def __init__(self, fail: bool = False) -> None:
        self.connected: list[str] = []
        self.fail = fail

    async def connect(self, address: str) -> None:
        if self.fail:
            raise MusicTargetError("no")
        self.connected.append(address)


def request(count: int = 3, **kwargs: Any) -> PlayRequest:
    tracks = tuple(Track(f"/m/{i}.mp3", f"t{i}", TrackKind.FILE) for i in range(count))
    return PlayRequest(tracks, parse_source("Jazz"), "Jazz", **kwargs)


@aio
async def test_mpv_play_loads_queue_and_sets_options() -> None:
    backend = FakeBackend()
    player = MpvPlayer(target(), backend)
    await player.play(request(volume=30, repeat=RepeatMode.ALL))
    calls = backend.channels[0].calls
    assert ("set_property", "volume", 30) in calls
    assert ("set_property", "loop-playlist", "inf") in calls
    assert ("loadfile", "/m/0.mp3", "replace") in calls
    assert ("loadfile", "/m/2.mp3", "append") in calls
    await player.play(request(1))
    assert len(backend.channels) == 1  # the process is reused


@aio
async def test_mpv_shuffle_uses_a_permutation() -> None:
    backend = FakeBackend()
    player = MpvPlayer(target(), backend, seed=lambda: 7)
    await player.play(request(8, shuffle=True))
    loaded = [c[1] for c in backend.channels[0].calls if c[0] == "loadfile"]
    assert sorted(loaded) == sorted(f"/m/{i}.mp3" for i in range(8))


@aio
async def test_mpv_controls_and_status() -> None:
    backend = FakeBackend()
    player = MpvPlayer(target(volume=40), backend)
    assert (await player.status()).state is PlayerState.IDLE
    await player.play(request())
    channel = backend.channels[0]
    channel.properties.update({"pause": False, "media-title": "Song", "volume": 55})
    status = await player.status()
    assert (status.state, status.track_title, status.volume) == (PlayerState.PLAYING, "Song", 55)
    channel.properties["pause"] = True
    assert (await player.status()).state is PlayerState.PAUSED
    channel.properties.update({"pause": False, "paused-for-cache": True})
    assert (await player.status()).state is PlayerState.BUFFERING
    channel.properties["idle-active"] = True
    assert (await player.status()).state is PlayerState.IDLE
    await player.pause()
    await player.resume()
    await player.next()
    await player.previous()
    await player.set_volume(150)
    await player.stop()
    assert ("set_property", "volume", 100) in channel.calls
    assert ("playlist-next", "force") in channel.calls
    assert ("stop",) in channel.calls


@aio
async def test_mpv_restarts_a_dead_process_and_closes() -> None:
    backend = FakeBackend()
    player = MpvPlayer(target(), backend)
    await player.play(request())
    backend.channels[0].alive = False
    assert (await player.status()).state is PlayerState.IDLE
    await player.pause()
    assert len(backend.channels) == 2 and backend.channels[0].closed
    await player.close()
    assert backend.channels[1].closed
    await player.close()  # idempotent


@aio
async def test_mpv_errors_and_empty_requests() -> None:
    backend = FakeBackend()
    player = MpvPlayer(target(), backend)
    with pytest.raises(MusicTargetError):
        await player.play(request(0))
    await player.play(request())
    backend.channels[0].fail_on = "playlist-next"
    with pytest.raises(MusicTargetError):
        await player.next()


@aio
async def test_bluetooth_is_connected_before_starting() -> None:
    bluetooth = FakeBluetooth()
    backend = FakeBackend()
    player = MpvPlayer(
        target(kind=MusicTargetKind.BLUETOOTH, bluetooth_address="AA:BB:CC:DD:EE:FF"),
        backend,
        bluetooth,
    )
    await player.play(request())
    assert bluetooth.connected == ["AA:BB:CC:DD:EE:FF"]
    failing = MpvPlayer(
        target(bluetooth_address="AA:BB:CC:DD:EE:FF"), FakeBackend(), FakeBluetooth(fail=True)
    )
    with pytest.raises(MusicTargetError):
        await failing.play(request())


# ---------- mpv IPC protocol ----------


class PipeWriter:
    def __init__(self, reader: asyncio.StreamReader, answer: Any) -> None:
        self.reader = reader
        self.answer = answer
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        import json

        self.sent.append(data)
        message = json.loads(data)
        reply = self.answer(message)
        if reply is not None:
            self.reader.feed_data(reply)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


@aio
async def test_ipc_matches_replies_and_ignores_events() -> None:
    import json

    reader = asyncio.StreamReader()

    def answer(message: dict[str, Any]) -> bytes:
        event = b'{"event":"property-change"}\nnot json\n'
        body = {"request_id": message["request_id"], "error": "success", "data": 42}
        return event + json.dumps(body).encode() + b"\n"

    ipc = MpvIpc(reader, PipeWriter(reader, answer))
    assert await ipc.command("get_property", "volume") == 42
    await ipc.close()
    assert not ipc.alive
    with pytest.raises(MusicTargetError):
        await ipc.command("stop")


@aio
async def test_ipc_reports_mpv_errors_and_lost_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    reader = asyncio.StreamReader()

    def reject(message: dict[str, Any]) -> bytes:
        return (
            json.dumps(
                {"request_id": message["request_id"], "error": "property unavailable"}
            ).encode()
            + b"\n"
        )

    ipc = MpvIpc(reader, PipeWriter(reader, reject))
    with pytest.raises(MusicTargetError):
        await ipc.command("get_property", "x")
    await ipc.close()

    reader2 = asyncio.StreamReader()
    ipc2 = MpvIpc(reader2, PipeWriter(reader2, lambda m: reader2.feed_eof()))
    with pytest.raises(MusicTargetError):
        await ipc2.command("stop")


@aio
async def test_ipc_times_out_when_mpv_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("octapihub.infrastructure.music.mpv_player.REQUEST_TIMEOUT_SECONDS", 0.05)
    reader = asyncio.StreamReader()
    ipc = MpvIpc(reader, PipeWriter(reader, lambda m: None))
    with pytest.raises(MusicTargetError):
        await ipc.command("stop")
    await ipc.close()
