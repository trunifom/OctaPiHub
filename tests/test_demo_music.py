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
