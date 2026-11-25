from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import (
    AppConfig,
    load_config,
    parse_config,
    save_config,
)
from octapihub.core.errors import MusicTargetError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.music.fake import FakePlayerFactory, FakeResolver
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import music_forms
from octapihub.web.app import WebBackend, create_app

TOKEN = "music-token-123456"


def make_config(tmp_path: Path, music: dict[str, object] | None = None) -> AppConfig:
    return parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"}],
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
            "music": music
            if music is not None
            else {
                "enabled": True,
                "library_dir": "/home/pi/Music",
                "targets": [
                    {"id": "wz", "name": "Wohnzimmer", "kind": "cast", "device": "Wohnzimmer"},
                    {"id": "pi", "name": "Raspberry Pi", "kind": "local", "volume": 60},
                ],
                "playlists": [
                    {
                        "id": "morgen",
                        "name": "Morgens",
                        "source": "Morgens",
                        "target": "wz",
                        "shuffle": True,
                    }
                ],
            },
        }
    )


class Rig:
    def __init__(self, tmp_path: Path, music: dict[str, object] | None = None) -> None:
        self.config = make_config(tmp_path, music)
        self.path = tmp_path / "octapihub.toml"
        save_config(self.path, self.config)
        self.factory = FakePlayerFactory()
        self.music = MusicService(self.config.music, self.factory, FakeResolver())
        device = FakeSenseHat()
        sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(device),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.settings = SettingsManager(self.path, self.config, sense, music=self.music)

        async def discover() -> list[str]:
            return ["Küche", "Schlafzimmer"]

        async def audio() -> list[tuple[str, str]]:
            return [("alsa/default", "Default (alsa)")]

        self.backend = WebBackend(
            self.settings,
            sense,
            TOKEN,
            music=self.music,
            discover_cast=discover,
            list_audio_devices=audio,
        )

    def client(self) -> TestClient:
        return TestClient(create_app(self.backend))

    def post(
        self,
        client: TestClient,
        action: str,
        fields: dict[str, str] | None = None,
        token: str = TOKEN,
    ):  # type: ignore[no-untyped-def]
        body = {**music_forms.music_to_form(self.settings.config.music), **(fields or {})}
        body["action"] = action
        if token:
            body["token"] = token
        return client.post(
            "/music",
            content=urlencode(body),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )


def test_page_shows_targets_playlists_and_navigation(tmp_path: Path) -> None:
    with Rig(tmp_path).client() as client:
        response = client.get("/music")
    text = response.text
    assert response.status_code == 200
    assert 'href="/music"' in text
    for wanted in (
        "Wohnzimmer",
        "Raspberry Pi",
        "Morgens",
        "Neues Wiedergabegerät",
        "Neue Playlist",
    ):
        assert wanted in text
    assert "Google-Geräte suchen" in text
    assert "Zufällige Reihenfolge" in text


def test_saving_requires_the_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert rig.post(client, "save", token="").status_code == 403
        assert rig.post(client, "save", token="wrong-token-value").status_code == 403
    assert load_config(rig.path).music == rig.config.music


def test_adding_a_target_and_a_playlist_is_saved_and_applied(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "t.new.name": "Küche Nest Hub",
        "t.new.kind": "cast",
        "t.new.device": "Küche",
        "t.new.volume": "30",
        "p.new.name": "Radio Swiss Jazz",
        "p.new.source": "https://stream.srg-ssr.ch/m/rsj/mp3_128",
        "p.new.target": "wz",
        "p.new.repeat": "all",
        "p.new.volume": "25",
    }
    with rig.client() as client:
        response = rig.post(client, "save", fields)
    assert response.status_code == 200
    assert "gespeichert" in response.text
    saved = load_config(rig.path).music
    assert [t.id for t in saved.targets] == ["wz", "pi", "kueche-nest-hub"]
    assert saved.targets[2].volume == 30
    assert [p.id for p in saved.playlists] == ["morgen", "radio-swiss-jazz"]
    assert saved.playlists[1].volume == 25 and saved.playlists[1].repeat.value == "all"
    assert rig.music.config == saved  # applied without a restart


def test_all_mistakes_are_reported_and_nothing_is_saved(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "t.new.name": "Box",
        "t.new.kind": "bluetooth",
        "t.new.bluetooth_address": "kaputt",
        "t.new.volume": "300",
        "p.new.name": "",
        "p.new.source": "../../etc/passwd",
        "p.0.source": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
        "music.media_host": "not a host!",
    }
    with rig.client() as client:
        response = rig.post(client, "save", fields)
    assert response.status_code == 422
    text = response.text
    for message in (
        "Die Adresse muss so aussehen",
        "Die Lautstärke muss eine Zahl",
        "Spotify-Links können noch nicht",
        "Bitte einen Namen eingeben",
    ):
        assert message in text
    assert "Es wurde nichts gespeichert" in text
    assert load_config(rig.path).music == rig.config.music
    assert "not a host!" in text  # the user's input is kept


def test_cast_target_needs_a_device_name(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = rig.post(client, "save", {"t.new.name": "Hub", "t.new.kind": "cast"})
    assert response.status_code == 422
    assert "Gerätenamen eintragen" in response.text


def test_removing_a_target_still_used_by_a_playlist_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = rig.post(client, "save", {"t.0.remove": "on"})
    assert response.status_code == 422
    assert "gibt es nicht" in response.text
    assert [t.id for t in load_config(rig.path).music.targets] == ["wz", "pi"]


def test_removing_unused_entries_and_disabling_music(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = rig.post(
            client, "save", {"t.1.remove": "on", "p.0.remove": "on", "music.enabled": ""}
        )
    assert response.status_code == 200
    saved = load_config(rig.path).music
    assert [t.id for t in saved.targets] == ["wz"] and saved.playlists == ()
    assert saved.enabled is False


def test_ids_stay_unique_for_equal_names(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.post(client, "save", {"t.new.name": "Wohnzimmer", "t.new.kind": "local"})
    assert [t.id for t in load_config(rig.path).music.targets] == ["wz", "pi", "wohnzimmer"]


def test_slugify_handles_umlauts_digits_and_collisions() -> None:
    assert music_forms.slugify("Küche Büro", set(), "x") == "kueche-buero"
    assert music_forms.slugify("123", set(), "geraet") == "geraet-123"
    assert music_forms.slugify("!!!", set(), "playlist") == "playlist"
    assert music_forms.slugify("Küche", {"kueche", "kueche-2"}, "x") == "kueche-3"
    assert len(music_forms.slugify("a" * 100, set(), "x")) <= 32


def test_play_pause_and_stop_controls_reach_the_player(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert rig.post(client, "play:morgen").status_code == 200
        assert rig.factory.players["wz"].calls[0][0] == "play"
        page = client.get("/music").text
        assert "Spielt" in page and "Morgens" in page
        assert rig.post(client, "ctl:wz:toggle").status_code == 200
        assert rig.post(client, "ctl:wz:volume.up").status_code == 200
        assert rig.post(client, "ctl:wz:next").status_code == 200
        assert rig.post(client, "ctl:wz:stop").status_code == 200
        calls = [c[0] for c in rig.factory.players["wz"].calls]
    for expected in ("pause", "set_volume", "next", "stop"):
        assert expected in calls


@pytest.mark.parametrize(
    "action", ["ctl:nobody:stop", "ctl:wz:explode", "ctl:wz", "play:missing", "explode"]
)
def test_unknown_controls_are_rejected(tmp_path: Path, action: str) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert rig.post(client, action).status_code == 422
    assert rig.factory.created == []


def test_control_without_token_does_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert rig.post(client, "play:morgen", token="").status_code == 403
    assert rig.factory.created == []


def test_player_failure_is_explained_with_its_code(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.factory = FakePlayerFactory(
        {"wz": {"play": lambda: MusicTargetError("offline", user_message="Box ist aus.")}}
    )
    rig.music = MusicService(rig.config.music, rig.factory, FakeResolver())
    rig.backend.music = rig.music
    with rig.client() as client:
        response = rig.post(client, "play:morgen")
    assert response.status_code == 502
    assert "Box ist aus." in response.text and "CH-MUS-002" in response.text


def test_music_disabled_is_reported_as_a_hint(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.post(client, "save", {"music.enabled": ""})
        response = rig.post(client, "play:morgen")
    assert response.status_code == 409
    assert "CH-MUS-004" in response.text


def test_discovery_lists_devices_and_audio_outputs(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        found = rig.post(client, "discover")
        audio = rig.post(client, "audio-devices")
        refreshed = rig.post(client, "refresh")
        stopped = rig.post(client, "stop-all")
    assert "Schlafzimmer" in found.text and "2 Gerät(e) gefunden" in found.text
    assert "alsa/default" in audio.text
    assert refreshed.status_code == 200 and stopped.status_code == 200


def test_discovery_without_results_explains_what_to_check(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def nothing() -> list[str]:
        return []

    rig.backend.discover_cast = nothing
    with rig.client() as client:
        response = rig.post(client, "discover")
    assert "Keine Google-Geräte gefunden" in response.text


def test_without_a_music_service_the_page_explains_itself(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.backend.music = None
    with rig.client() as client:
        assert client.get("/music").status_code == 200
        response = rig.post(client, "save")
    assert response.status_code == 503 and "CH-WEB-014" in response.text
