from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from octapihub.application.matrix.builder import DisplayStateBuilder
from octapihub.application.matrix.state import DisplayState, MusicDisplay, status_messages
from octapihub.application.matrix.views import VIEWS, ViewContext, ViewUi
from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import (
    MATRIX_VIEW_IDS,
    MusicConfig,
    MusicTargetConfig,
    MusicTargetKind,
    PlaylistConfig,
)
from octapihub.core.errors import MusicTargetError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.music.fake import FakePlayerFactory, FakeResolver
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def render(music: MusicDisplay | None, t: float = 0.0) -> tuple[object, ...]:
    state = DisplayState(now=NOW, music=music)
    return tuple(VIEWS["music"].render(ViewContext(state, ViewUi(), t)))


def lit(frame: tuple[object, ...]) -> int:
    return sum(1 for pixel in frame if tuple(pixel) != (0, 0, 0))  # type: ignore[call-overload]


def test_the_music_view_is_registered_next_to_the_control_view() -> None:
    assert "music" in MATRIX_VIEW_IDS
    assert tuple(VIEWS) == MATRIX_VIEW_IDS
    view = VIEWS["music"]
    assert view.label == "Musik" and view.description and view.controls


@pytest.mark.parametrize("state", ["playing", "paused", "buffering", "idle", "error", "bogus"])
def test_every_state_draws_a_frame_with_64_pixels(state: str) -> None:
    frame = render(MusicDisplay(state, 50, "Titel", "Küche"))
    assert len(frame) == 64 and lit(frame) > 0


def test_states_look_different_and_volume_is_a_bar_at_the_right_edge() -> None:
    playing = render(MusicDisplay("playing", 100))
    paused = render(MusicDisplay("paused", 100))
    assert playing != paused
    quiet = render(MusicDisplay("paused", 0))
    assert lit(paused) > lit(quiet)
    right_column = [paused[y * 8 + 7] for y in range(8)]
    assert all(tuple(p) != (0, 0, 0) for p in right_column)  # type: ignore[call-overload]
    assert all(tuple(quiet[y * 8 + 7]) == (0, 0, 0) for y in range(8))  # type: ignore[call-overload]


def test_a_playing_icon_blinks_gently() -> None:
    assert render(MusicDisplay("playing", None), 0.0) != render(MusicDisplay("playing", None), 0.6)


def test_off_and_unknown_volume_are_handled() -> None:
    assert lit(render(None)) > 0
    assert len(render(MusicDisplay("playing", None))) == 64
    assert len(render(MusicDisplay("error", 50))) == 64


def test_the_description_names_state_title_and_volume() -> None:
    view = VIEWS["music"]
    off = DisplayState(now=NOW)
    assert view.describe(off) == "MUSIK AUS"
    text = view.describe(DisplayState(now=NOW, music=MusicDisplay("playing", 40, "Blue", "Pi")))
    assert text == "MUSIK SPIELT Blue 40%"
    assert "FEHLER" in view.describe(DisplayState(now=NOW, music=MusicDisplay("error")))
    assert "BEREIT" in view.describe(DisplayState(now=NOW, music=MusicDisplay("idle")))


def test_the_ticker_mentions_music_only_while_it_plays() -> None:
    playing = DisplayState(now=NOW, music=MusicDisplay("playing", 40, "Blue", "Pi"))
    assert ("MUSIK Blue", "info") in status_messages(playing)
    paused = DisplayState(now=NOW, music=MusicDisplay("paused", 40, "Blue", "Pi"))
    assert not any(text.startswith("MUSIK") for text, _ in status_messages(paused))


# ---------- builder ----------


def make_builder(tmp_path: Path, music: MusicService | None) -> DisplayStateBuilder:
    device = FakeSenseHat()
    from octapihub.core.config import SenseHatConfig

    sense = SenseHatService(
        SenseHatConfig(),
        lambda _c: SenseHatAdapter(device),
        lambda c: SQLiteSenseHatHistory(str(tmp_path / "h.sqlite3")),
    )
    return DisplayStateBuilder(lambda: (), sense, music=music)


def service(enabled: bool = True, factory: FakePlayerFactory | None = None) -> MusicService:
    targets = (
        MusicTargetConfig("a", "Küche", MusicTargetKind.CAST, "Küche"),
        MusicTargetConfig("b", "Pi", MusicTargetKind.LOCAL),
    )
    playlists = (PlaylistConfig("p", "Morgens", "Morgens", target="a"),)
    config = MusicConfig(enabled, "/m", "", targets, playlists)
    return MusicService(config, factory or FakePlayerFactory(), FakeResolver())


def test_builder_without_or_with_disabled_music_reports_none(tmp_path: Path) -> None:
    assert make_builder(tmp_path, None).build().music is None
    assert make_builder(tmp_path, service(enabled=False)).build().music is None


def test_builder_reports_idle_playing_and_errors(tmp_path: Path) -> None:
    music = service()
    builder = make_builder(tmp_path, music)
    idle = builder.build().music
    assert idle is not None and idle.state == "idle"

    async def play() -> None:
        await music.execute("play", {"playlist": "p"})

    asyncio.run(play())
    shown = builder.build().music
    assert shown is not None and shown.state == "playing" and shown.target == "Küche"
    assert shown.title == "Titel 1" and shown.volume is not None

    failing = service(factory=FakePlayerFactory({"a": {"play": lambda: MusicTargetError("x")}}))

    async def broken() -> None:
        with pytest.raises(MusicTargetError):
            await failing.execute("play", {"playlist": "p"})

    asyncio.run(broken())
    error = make_builder(tmp_path, failing).build().music
    assert error is not None and error.state == "error"


def test_builder_never_raises_when_the_service_fails(tmp_path: Path) -> None:
    class Broken:
        @property
        def status(self) -> object:
            raise RuntimeError("boom")

    builder = make_builder(tmp_path, Broken())  # type: ignore[arg-type]
    assert builder.build().music is None
