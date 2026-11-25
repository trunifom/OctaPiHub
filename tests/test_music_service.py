from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import octapihub.application.music.service as service_module
from octapihub.application.music.models import PlayerState
from octapihub.application.music.service import MusicService
from octapihub.application.outbox import OutboxDispatcher, OutboxState
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import (
    AppConfig,
    MusicConfig,
    MusicTargetConfig,
    MusicTargetKind,
    PlaylistConfig,
    RepeatMode,
    parse_config,
)
from octapihub.core.errors import (
    MusicNotConfiguredError,
    MusicSourceError,
    MusicTargetError,
)
from octapihub.infrastructure.music.fake import FakePlayer, FakePlayerFactory, FakeResolver
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox

WZ = MusicTargetConfig("wz", "Wohnzimmer", MusicTargetKind.CAST, "Wohnzimmer", volume=35)
PI = MusicTargetConfig("pi", "Raspberry Pi", MusicTargetKind.LOCAL, "", volume=60)
BOX = MusicTargetConfig("box", "Box", MusicTargetKind.BLUETOOTH, "pulse/bt", "AA:BB:CC:DD:EE:FF")
MORGEN = PlaylistConfig(
    "morgen", "Morgenmusik", "Morgens", target="wz", shuffle=True, repeat=RepeatMode.ALL
)
JAZZ = PlaylistConfig("jazz", "Jazz", "/home/pi/Music/Jazz", volume=25)


def make(
    targets: tuple[MusicTargetConfig, ...] = (WZ, PI),
    playlists: tuple[PlaylistConfig, ...] = (MORGEN, JAZZ),
    *,
    enabled: bool = True,
    factory: FakePlayerFactory | None = None,
    resolver: FakeResolver | None = None,
) -> tuple[MusicService, FakePlayerFactory, FakeResolver]:
    factory = factory or FakePlayerFactory()
    resolver = resolver or FakeResolver()
    config = MusicConfig(enabled, "/home/pi/Music", "", targets, playlists)
    return MusicService(config, factory, resolver), factory, resolver


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def status_of(service: MusicService, target_id: str):  # type: ignore[no-untyped-def]
    return next(t for t in service.status.targets if t.id == target_id)


# ---------- play ----------


def test_playing_a_playlist_uses_its_settings_and_updates_the_status() -> None:
    service, factory, resolver = make()
    changes: list[int] = []
    service.add_listener(lambda: changes.append(1))

    async def scenario() -> None:
        await service.execute("play", {"playlist": "morgen"})

    run(scenario())
    (_, request) = next(c for c in factory.players["wz"].calls if c[0] == "play")
    assert [t.title for t in request.tracks] == ["Titel 1", "Titel 2", "Titel 3"]  # type: ignore[attr-defined]
    assert request.shuffle and request.repeat is RepeatMode.ALL  # type: ignore[attr-defined]
    assert request.volume == 35  # no playlist volume -> the target's start volume  # type: ignore[attr-defined]
    assert request.title == "Morgenmusik"  # type: ignore[attr-defined]
    assert resolver.requests == [("local:Morgens", "/home/pi/Music")]
    wz = status_of(service, "wz")
    assert (wz.state, wz.title, wz.current, wz.volume) == (
        PlayerState.PLAYING,
        "Morgenmusik",
        True,
        35,
    )
    assert wz.track_title.startswith("Titel")
    assert status_of(service, "pi").state is PlayerState.IDLE
    assert service.status.playing
    assert changes


def test_a_playlist_volume_beats_the_target_volume_and_params_beat_both() -> None:
    service, factory, _ = make(targets=(PI,))

    async def scenario() -> None:
        await service.execute("play", {"playlist": "jazz"})
        assert status_of(service, "pi").volume == 25
        await service.execute("play", {"playlist": "jazz", "volume": 80})
        assert status_of(service, "pi").volume == 80

    run(scenario())


def test_explicit_parameters_override_the_playlist() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.execute(
            "play",
            {"playlist": "morgen", "target": "pi", "shuffle": False, "repeat": "one", "volume": 10},
        )

    run(scenario())
    request = next(c[1] for c in factory.players["pi"].calls if c[0] == "play")
    assert not request.shuffle and request.repeat is RepeatMode.ONE and request.volume == 10  # type: ignore[attr-defined]
    assert "wz" not in factory.players  # the playlist's own target was not used


def test_a_source_can_be_played_without_a_playlist() -> None:
    service, factory, resolver = make(targets=(PI,), playlists=())

    async def scenario() -> None:
        await service.execute("play", {"source": "https://radio.example.org/live"})

    run(scenario())
    assert resolver.requests[0][0] == "url:https://radio.example.org/live"
    assert status_of(service, "pi").title == "Radio- oder Stream-Adresse"


@pytest.mark.parametrize(
    ("targets", "params", "expected"),
    [
        ((WZ, PI), {"playlist": "morgen"}, "wz"),  # playlist's own target
        ((WZ, PI), {"playlist": "morgen", "target": "pi"}, "pi"),  # explicit wins
        ((PI,), {"playlist": "jazz"}, "pi"),  # the only target
        ((WZ, PI), {"playlist": "jazz", "target": "wz"}, "wz"),
    ],
)
def test_target_selection(
    targets: tuple[MusicTargetConfig, ...], params: dict[str, object], expected: str
) -> None:
    service, factory, _ = make(targets=targets)
    run(service.execute("play", params))
    assert set(factory.players) == {expected}


def test_without_a_target_the_last_used_one_is_reused_otherwise_an_explanation_is_given() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        with pytest.raises(MusicNotConfiguredError, match="No music target"):
            await service.execute("play", {"playlist": "jazz"})
        assert factory.created == []
        await service.execute("play", {"playlist": "morgen"})  # wz
        await service.execute("play", {"playlist": "jazz"})  # reuses wz
        assert sum(1 for c in factory.players["wz"].calls if c[0] == "play") == 2

    run(scenario())


def test_starting_music_elsewhere_stops_the_other_target_first() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.execute("play", {"playlist": "morgen"})
        await service.execute("play", {"playlist": "jazz", "target": "pi"})

    run(scenario())
    assert factory.players["wz"].calls[-2][0] == "stop"  # then its status read
    assert status_of(service, "wz").state is PlayerState.IDLE
    assert status_of(service, "pi").state is PlayerState.PLAYING
    assert status_of(service, "pi").current


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"playlist": "ghost"}, "Playlist"),
        ({}, "weder eine Playlist noch eine Quelle"),
        ({"source": "spotify:track:4uLU6hMCjMI75M1A2tKUQC", "target": "pi"}, "Spotify"),
        ({"source": "ftp://x/y", "target": "pi"}, "Erkannt werden"),
        ({"playlist": "jazz", "target": "ghost"}, "Wiedergabegerät"),
        ({"playlist": "jazz", "target": "pi", "repeat": "forever"}, "Wiederholen"),
        ({"playlist": "jazz", "target": "pi", "volume": 101}, "0 bis 100"),
        ({"playlist": "jazz", "target": "pi", "volume": True}, "0 bis 100"),
        ({"playlist": "jazz", "target": "pi", "shuffle": "yes"}, "shuffle"),
        ({"playlist": 5}, "ungültig"),
        ({"playlist": "jazz", "target": 5}, "ungültig"),
    ],
)
def test_invalid_play_requests_change_nothing_and_are_not_retried(
    params: dict[str, object], message: str
) -> None:
    service, factory, resolver = make()

    async def scenario() -> None:
        with pytest.raises((MusicNotConfiguredError, MusicSourceError)) as error:
            await service.execute("play", params)
        assert error.value.retryable is False
        assert message in error.value.user_message

    run(scenario())
    assert all(not p.calls for p in factory.players.values())
    assert not service.status.playing
    assert service.status.targets[0].error is None


def test_missing_sources_are_reported_before_any_player_is_touched() -> None:
    service, factory, _ = make(resolver=FakeResolver(missing={"local:Morgens"}))

    async def scenario() -> None:
        with pytest.raises(MusicSourceError, match="not found"):
            await service.execute("play", {"playlist": "morgen"})

    run(scenario())
    assert factory.created == []


def test_disabled_music_refuses_everything_without_creating_players() -> None:
    service, factory, _ = make(enabled=False)

    async def scenario() -> None:
        for action in ("play", "stop", "stop.all", "volume.up", "pause"):
            with pytest.raises(MusicNotConfiguredError) as error:
                await service.execute(action, {"playlist": "jazz"})
            assert error.value.retryable is False

    run(scenario())
    assert factory.created == []


def test_unknown_actions_are_rejected() -> None:
    service, _, _ = make()

    async def scenario() -> None:
        with pytest.raises(MusicNotConfiguredError, match="Unsupported music action"):
            await service.execute("rewind", {})

    run(scenario())


# ---------- transport ----------


def test_stop_and_pause_without_a_target_affect_only_active_targets() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.execute("stop", {})  # nothing plays: a silent no-op, no players created
        assert factory.created == []
        await service.execute("play", {"playlist": "morgen"})
        await service.execute("pause", {})
        assert status_of(service, "wz").state is PlayerState.PAUSED
        await service.execute("stop", {})
        assert status_of(service, "wz").state is PlayerState.IDLE
        await service.execute("stop", {})  # again: nothing active any more
        assert [c[0] for c in factory.players["wz"].calls].count("stop") == 1

    run(scenario())


def test_stop_with_an_explicit_target_and_stop_all() -> None:
    service, factory, _ = make(targets=(WZ, PI, BOX))

    async def scenario() -> None:
        await service.execute("play", {"playlist": "jazz", "target": "pi"})
        await service.execute("stop", {"target": "wz"})  # an explicit target is always asked
        assert [c[0] for c in factory.players["wz"].calls].count("stop") == 1
        assert status_of(service, "pi").state is PlayerState.PLAYING
        await service.execute("stop.all", {})
        assert {t.state for t in service.status.targets} == {PlayerState.IDLE}
        assert all(any(c[0] == "stop" for c in p.calls) for p in factory.players.values())
        assert not any(t.current for t in service.status.targets)

    run(scenario())


def test_toggle_pauses_resumes_and_can_start_a_playlist() -> None:
    service, _, _ = make(targets=(PI,))

    async def scenario() -> None:
        with pytest.raises(MusicNotConfiguredError, match="Nothing to toggle"):
            await service.execute("toggle", {})
        await service.execute("toggle", {"playlist": "jazz"})  # idle + playlist: start it
        assert status_of(service, "pi").state is PlayerState.PLAYING
        await service.execute("toggle", {})
        assert status_of(service, "pi").state is PlayerState.PAUSED
        await service.execute("toggle", {})
        assert status_of(service, "pi").state is PlayerState.PLAYING

    run(scenario())


def test_next_and_previous_walk_the_queue_with_repeat_rules() -> None:
    playlist = PlaylistConfig("p", "P", "x", repeat=RepeatMode.OFF)
    service, _, _ = make(targets=(PI,), playlists=(playlist,))

    async def scenario() -> None:
        await service.execute("play", {"playlist": "p"})
        titles = [status_of(service, "pi").track_title]
        for _ in range(2):
            await service.execute("next", {})
            titles.append(status_of(service, "pi").track_title)
        assert titles == ["Titel 1", "Titel 2", "Titel 3"]
        await service.execute("next", {})  # past the end with repeat off
        assert status_of(service, "pi").state is PlayerState.IDLE
        await service.execute("play", {"playlist": "p"})
        await service.execute("previous", {})
        assert status_of(service, "pi").track_title == "Titel 1"  # the first track starts over

    run(scenario())


def test_volume_actions_clamp_and_use_known_volumes() -> None:
    service, _, _ = make(targets=(PI,))

    async def scenario() -> None:
        await service.execute("volume.up", {})  # unknown volume: the target's 60 + 10
        assert status_of(service, "pi").volume == 70
        await service.execute("volume.down", {"step": 25})
        assert status_of(service, "pi").volume == 45
        await service.execute("volume.set", {"level": 0})
        assert status_of(service, "pi").volume == 0
        await service.execute("volume.down", {})
        assert status_of(service, "pi").volume == 0
        await service.execute("volume.set", {"level": 100})
        await service.execute("volume.up", {"step": 50})
        assert status_of(service, "pi").volume == 100

    run(scenario())


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("volume.set", {}),
        ("volume.set", {"level": 101}),
        ("volume.set", {"level": -1}),
        ("volume.set", {"level": "loud"}),
        ("volume.up", {"step": 0}),
        ("volume.up", {"step": 51}),
        ("volume.up", {"step": 1.5}),
    ],
)
def test_invalid_volume_parameters_are_rejected(action: str, params: dict[str, object]) -> None:
    service, factory, _ = make(targets=(PI,))

    async def scenario() -> None:
        with pytest.raises(MusicNotConfiguredError):
            await service.execute(action, params)

    run(scenario())
    assert all(not p.calls for p in factory.players.values())


@pytest.mark.parametrize("action", ["resume", "next", "previous", "volume.up", "toggle"])
def test_transport_actions_need_a_resolvable_target(action: str) -> None:
    service, factory, _ = make()  # two targets, nothing used yet

    async def scenario() -> None:
        with pytest.raises(MusicNotConfiguredError) as error:
            await service.execute(action, {})
        assert "Wiedergabegerät" in error.value.user_message

    run(scenario())
    assert factory.created == []


# ---------- errors ----------


def test_an_unreachable_device_is_reported_retryable_and_recovers() -> None:
    failures = {"wz": {"play": lambda: MusicTargetError("offline")}}
    service, factory, _ = make(factory=FakePlayerFactory(failures))

    async def scenario() -> None:
        with pytest.raises(MusicTargetError) as error:
            await service.execute("play", {"playlist": "morgen"})
        assert error.value.retryable is True
        wz = status_of(service, "wz")
        assert wz.error_code == "CH-MUS-002"
        assert "Wiedergabegerät" in (wz.error or "")
        assert not service.status.playing
        factory.players["wz"].failures.clear()
        await service.execute("play", {"playlist": "morgen"})
        assert status_of(service, "wz").error is None

    run(scenario())


def test_unexpected_errors_never_leak_details() -> None:
    failures = {"pi": {"play": lambda: RuntimeError("secret internal detail")}}
    service, _, _ = make(targets=(PI,), factory=FakePlayerFactory(failures))

    async def scenario() -> None:
        with pytest.raises(MusicTargetError) as error:
            await service.execute("play", {"playlist": "jazz"})
        assert "secret" not in error.value.user_message
        assert "secret" not in (status_of(service, "pi").error or "")

    run(scenario())


def test_a_hanging_device_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module, "COMMAND_TIMEOUT_SECONDS", 0.05)
    service, _, _ = make(targets=(PI,), factory=FakePlayerFactory(delay=0.5))

    async def scenario() -> None:
        with pytest.raises(MusicTargetError, match="timed out"):
            await service.execute("play", {"playlist": "jazz"})
        assert status_of(service, "pi").error_code == "CH-MUS-002"

    run(scenario())


def test_a_failing_player_factory_is_retried_on_the_next_command() -> None:
    attempts = {"n": 0}

    class Flaky(FakePlayerFactory):
        def create(self, target: MusicTargetConfig) -> FakePlayer:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("no pychromecast")
            return super().create(target)

    service, factory, _ = make(targets=(PI,), factory=Flaky())

    async def scenario() -> None:
        with pytest.raises(MusicTargetError):
            await service.execute("play", {"playlist": "jazz"})
        await service.execute("play", {"playlist": "jazz"})
        assert status_of(service, "pi").state is PlayerState.PLAYING

    run(scenario())
    assert attempts["n"] == 2


def test_a_failing_status_call_does_not_fail_the_command() -> None:
    failures = {"pi": {"status": lambda: OSError("flaky status")}}
    service, _, _ = make(targets=(PI,), factory=FakePlayerFactory(failures))
    run(service.execute("play", {"playlist": "jazz"}))
    assert status_of(service, "pi").state is PlayerState.IDLE  # last known state is kept


def test_listener_failures_are_isolated() -> None:
    service, _, _ = make(targets=(PI,))
    seen: list[int] = []

    def broken() -> None:
        raise RuntimeError("display crashed")

    service.add_listener(broken)
    service.add_listener(lambda: seen.append(1))
    run(service.execute("play", {"playlist": "jazz"}))
    assert seen


def test_commands_for_one_target_never_interleave() -> None:
    service, factory, _ = make(targets=(PI,), factory=FakePlayerFactory(delay=0.01))

    async def scenario() -> None:
        await asyncio.gather(
            service.execute("play", {"playlist": "jazz"}),
            service.execute("volume.set", {"level": 20}),
            service.execute("play", {"playlist": "jazz", "volume": 90}),
        )

    run(scenario())
    operations = [c[0] for c in factory.players["pi"].calls]
    # every command is followed by its own status read before the next one starts
    assert operations[1::2] == ["status"] * 3
    assert sorted(operations[0::2]) == ["play", "play", "set_volume"]


# ---------- lifecycle ----------


def test_reconfigure_closes_changed_players_and_resets_their_state() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.execute("play", {"playlist": "morgen"})
        first = factory.players["wz"]
        same = MusicConfig(True, "/home/pi/Music", "", (WZ, PI), (MORGEN,))
        await service.reconfigure(same)
        assert not first.closed  # unchanged target keeps its player
        assert status_of(service, "wz").state is PlayerState.PLAYING
        changed = MusicConfig(
            True,
            "/home/pi/Music",
            "",
            (MusicTargetConfig("wz", "Neu", MusicTargetKind.CAST, "Anderes"), PI),
            (),
        )
        await service.reconfigure(changed)
        assert first.closed
        assert status_of(service, "wz").state is PlayerState.IDLE
        assert status_of(service, "wz").name == "Neu"
        removed = MusicConfig(True, "", "", (PI,), ())
        await service.reconfigure(removed)
        assert [t.id for t in service.status.targets] == ["pi"]
        assert not any(t.current for t in service.status.targets)

    run(scenario())


def test_refresh_picks_up_changes_made_outside_and_never_creates_players() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.refresh()
        assert factory.created == []
        await service.execute("play", {"playlist": "morgen"})
        await factory.players["wz"].stop()  # somebody stops it on the device itself
        assert status_of(service, "wz").state is PlayerState.PLAYING  # not known yet
        await service.refresh()
        assert status_of(service, "wz").state is PlayerState.IDLE

    run(scenario())


def test_close_closes_all_players_even_when_one_fails() -> None:
    service, factory, _ = make()

    async def scenario() -> None:
        await service.execute("play", {"playlist": "morgen"})
        await service.execute("stop.all", {})
        factory.players["wz"].failures["close"] = lambda: RuntimeError("x")

        async def failing_close() -> None:
            raise RuntimeError("cannot close")

        factory.players["wz"].close = failing_close  # type: ignore[method-assign]
        await service.close()
        assert factory.players["pi"].closed
        await service.close()  # closing twice is harmless

    run(scenario())


# ---------- through the action outbox ----------


def _dispatcher(
    tmp_path: Path, service: MusicService, **kwargs: object
) -> tuple[OutboxDispatcher, SQLiteOutbox, AppConfig]:
    config = parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "Musik", "address": "AA:BB:CC:DD:EE:01", "role": "music"}],
            "mappings": [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [
                        {"kind": "music", "action": "play", "parameters": {"playlist": "jazz"}}
                    ],
                }
            ],
        }
    )
    registry = PluginRegistry()
    registry.register(service)
    outbox = SQLiteOutbox(tmp_path / "o.db")
    dispatcher = OutboxDispatcher(
        RuleEngine(config),
        outbox,
        registry,
        backoff_base_seconds=0.01,
        backoff_cap_seconds=0.02,
        **kwargs,  # type: ignore[arg-type]
    )
    return dispatcher, outbox, config


def test_the_music_plugin_runs_from_the_outbox(tmp_path: Path) -> None:
    service, factory, _ = make(targets=(PI,))
    dispatcher, outbox, _ = _dispatcher(tmp_path, service)

    async def scenario() -> None:
        batch = dispatcher.enqueue_face_change("Musik", 1)
        assert batch is not None
        assert await dispatcher.dispatch_next("Musik")
        assert outbox.get_batch(batch)[0].state is OutboxState.SUCCEEDED

    run(scenario())
    assert status_of(service, "pi").state is PlayerState.PLAYING


def test_user_errors_are_not_retried_by_the_outbox(tmp_path: Path) -> None:
    service, _, _ = make(targets=(PI,), playlists=())  # the playlist "jazz" does not exist
    dispatcher, outbox, _ = _dispatcher(tmp_path, service)

    async def scenario() -> None:
        batch = dispatcher.enqueue_face_change("Musik", 1)
        assert batch is not None
        assert await dispatcher.dispatch_next("Musik")
        (entry,) = outbox.get_batch(batch)
        assert entry.state is OutboxState.EXPIRED  # given up at once
        assert entry.attempts == 1
        assert not await dispatcher.dispatch_next("Musik")  # nothing left to retry

    run(scenario())


def test_unreachable_devices_are_retried_but_music_does_not_start_late(tmp_path: Path) -> None:
    failures = {"pi": {"play": lambda: MusicTargetError("offline")}}
    service, _, _ = make(targets=(PI,), factory=FakePlayerFactory(failures))
    dispatcher, outbox, _ = _dispatcher(tmp_path, service, ttl_by_kind={"music": 0.3})

    async def scenario() -> None:
        batch = dispatcher.enqueue_face_change("Musik", 1)
        assert batch is not None
        assert await dispatcher.dispatch_next("Musik")
        (entry,) = outbox.get_batch(batch)
        assert entry.state is OutboxState.PENDING  # a short retry window
        await asyncio.sleep(0.4)
        await dispatcher.dispatch_next("Musik")  # the stale action is dropped, not played
        (entry,) = outbox.get_batch(batch)
        assert entry.state is OutboxState.EXPIRED  # too old to be useful: never plays late

    run(scenario())


def test_ttl_by_kind_applies_to_mixed_batches_and_is_validated(tmp_path: Path) -> None:
    service, _, _ = make(targets=(PI,))
    with pytest.raises(Exception, match="TTL"):
        _dispatcher(tmp_path, service, ttl_by_kind={"music": 0})
    dispatcher, outbox, config = _dispatcher(tmp_path, service, ttl_by_kind={"music": 5})
    batch = dispatcher.enqueue_face_change("Musik", 1)
    assert batch is not None
    (entry,) = outbox.get_batch(batch)
    assert entry.expires_at - entry.created_at == pytest.approx(5)
