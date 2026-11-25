from __future__ import annotations

from fastapi.testclient import TestClient

from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend


def test_the_demo_includes_working_music_with_fake_players() -> None:
    backend = build_demo_backend()
    assert backend.music is not None and backend.music.config.enabled
    with TestClient(create_app(backend, manage_service=True)) as client:
        page = client.get("/music")
        assert page.status_code == 200 and "Morgenmusik" in page.text
        played = client.post(
            "/music",
            data={"action": "play:morgens", "token": DEMO_TOKEN},
        )
        assert played.status_code == 200 and "Wiedergabe gestartet" in played.text
        assert "Spielt" in client.get("/music").text
        found = client.post("/music", data={"action": "discover", "token": DEMO_TOKEN})
        assert "Nest Mini" in found.text
        mapping = client.get("/cubes")
        assert mapping.status_code == 200 and 'value="music:play:morgens" selected' in mapping.text
        assert client.get("/backup").status_code == 200


def test_the_overview_shows_live_music_state() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend, manage_service=True)) as client:
        before = client.get("/")
        assert "Noch nicht angebunden" not in before.text and "Bereit" in before.text
        client.post("/music", data={"action": "play:morgens", "token": DEMO_TOKEN})
        after = client.get("/")
        assert "Spielt" in after.text and "Morgenmusik" in after.text and "Wohnzimmer" in after.text
        client.post("/music", data={"action": "ctl:wohnzimmer:toggle", "token": DEMO_TOKEN})
        assert "Pausiert" in client.get("/").text


def test_music_summary_covers_every_state() -> None:
    from octapihub.application.music.models import PlayerState
    from octapihub.application.music.service import MusicStatus, TargetStatus
    from octapihub.core.config import MusicTargetKind
    from octapihub.web.presenter import build_music_summary

    def target(state: PlayerState, error: str | None = None) -> TargetStatus:
        return TargetStatus(
            "a",
            "Küche",
            MusicTargetKind.CAST,
            state,
            "Liste",
            "Titel",
            30,
            error,
            "CH-MUS-002",
            True,
        )

    assert build_music_summary(None).badge == "Nicht aktiv"
    assert build_music_summary(MusicStatus(False, ())).badge == "Aus"
    assert build_music_summary(MusicStatus(True, ())).badge == "Kein Gerät"
    err = build_music_summary(MusicStatus(True, (target(PlayerState.IDLE, "Aus"),)))
    assert (err.level, err.badge) == ("warn", "Fehler") and "CH-MUS-002" in err.detail
    # a device that plays beats an error elsewhere
    mixed = MusicStatus(True, (target(PlayerState.IDLE, "Aus"), target(PlayerState.PLAYING)))
    assert build_music_summary(mixed).badge == "Spielt"
    assert build_music_summary(MusicStatus(True, (target(PlayerState.IDLE),)), 2).headline.endswith(
        "2 Playlists."
    )
