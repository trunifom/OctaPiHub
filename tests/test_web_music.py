from __future__ import annotations

import re
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


# ---------- mapping editor: music actions per face ----------


def post_mapping(rig: Rig, client: TestClient, fields: dict[str, str], token: str = TOKEN):  # type: ignore[no-untyped-def]
    from octapihub.web import forms

    body = {**forms.mapping_config_to_form(rig.settings.config), **fields}
    if token:
        body["token"] = token
    return client.post(
        "/cubes/mapping",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def test_mapping_page_offers_music_actions_and_playlists(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        text = client.get("/cubes").text
    assert "Musik: Playlist abspielen" in text and "▶ Morgens" in text
    assert "Musik: Steuern" in text and "Lauter" in text and "Alle Wiedergaben stoppen" in text
    assert 'value="music:toggle:morgen"' in text


def test_music_actions_can_be_assigned_to_faces_and_the_holder(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "map.0.1": "music:play:morgen",
        "map.0.2": "music:toggle",
        "map.0.3": "music:volume.up",
        "map.0.0": "music:stop.all",
        "map.1.1": "music:toggle:morgen",
    }
    with rig.client() as client:
        response = post_mapping(rig, client, fields)
    assert response.status_code == 200, response.text[:500]
    saved = {(m.cube, m.face): m.actions[0] for m in load_config(rig.path).mappings}
    play = saved[("Musik", 1)]
    assert (play.kind, play.action, dict(play.parameters)) == (
        "music",
        "play",
        {"playlist": "morgen"},
    )
    assert saved[("Musik", 2)].action == "toggle" and dict(saved[("Musik", 2)].parameters) == {}
    assert saved[("Musik", 0)].action == "stop.all"
    assert saved[("Joystick", 1)].action == "toggle"
    with rig.client() as client:
        page = client.get("/cubes").text
    assert 'value="music:play:morgen" selected' in page
    assert 'value="music:stop.all" selected' in page


@pytest.mark.parametrize(
    "choice", ["music:explode", "music:play:", "music:play:gibtsnicht", "music:"]
)
def test_invalid_music_choices_are_refused(tmp_path: Path, choice: str) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = post_mapping(rig, client, {"map.0.1": choice})
    assert response.status_code == 422
    assert load_config(rig.path).mappings == ()


def test_a_removed_playlist_stays_visible_in_an_existing_mapping(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        post_mapping(rig, client, {"map.0.1": "music:play:morgen"})
        rig.post(client, "save", {"p.0.remove": "on"})
        page = client.get("/cubes").text
    assert "nicht mehr vorhanden" in page


# ---------- per-face music options: device, shuffle, repeat ----------


def test_options_per_face_are_saved_loaded_and_used(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "map.0.1": "music:play:morgen",
        "map.0.1.target": "pi",
        "map.0.1.shuffle": "yes",
        "map.0.1.repeat": "one",
        "map.0.2": "music:toggle",
        "map.0.2.target": "wz",
    }
    with rig.client() as client:
        response = post_mapping(rig, client, fields)
        assert response.status_code == 200, response.text[:300]
        page = client.get("/cubes").text
    saved = {
        (m.cube, m.face): dict(m.actions[0].parameters) for m in load_config(rig.path).mappings
    }
    assert saved[("Musik", 1)] == {
        "playlist": "morgen",
        "target": "pi",
        "shuffle": True,
        "repeat": "one",
    }
    assert saved[("Musik", 2)] == {"target": "wz"}
    assert 'value="pi" selected' in page and 'value="yes" selected' in page
    assert 'value="one" selected' in page
    # the service gets exactly these parameters
    import asyncio

    asyncio.run(rig.music.execute("play", saved[("Musik", 1)]))
    play = [c for c in rig.factory.players["pi"].calls if c[0] == "play"][0][1]
    assert play.shuffle is True and play.repeat.value == "one"


def test_a_cube_wide_device_applies_to_all_music_faces(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "map.0.1": "music:play:morgen",
        "map.0.2": "music:next",
        "map.0.3": "music:volume.up",
        "cubetarget.0": "pi",
    }
    with rig.client() as client:
        assert post_mapping(rig, client, fields).status_code == 200
        page = client.get("/cubes").text
    saved = {
        m.face: dict(m.actions[0].parameters)
        for m in load_config(rig.path).mappings
        if m.cube == "Musik"
    }
    assert saved[1]["target"] == saved[2]["target"] == saved[3]["target"] == "pi"
    assert 'name="cubetarget.0"' in page
    assert re.search(r'<option value="pi" selected>', page)
    # changing a single face afterwards keeps the other faces and the cube default
    with rig.client() as client:
        post_mapping(rig, client, {"map.0.2.target": "wz", "cubetarget.0": "pi"})
    again = {
        m.face: dict(m.actions[0].parameters)
        for m in load_config(rig.path).mappings
        if m.cube == "Musik"
    }
    assert again[2]["target"] == "wz" and again[1]["target"] == "pi"


@pytest.mark.parametrize(
    "field",
    [
        {"map.0.1.target": "gibtsnicht"},
        {"map.0.1.shuffle": "vielleicht"},
        {"map.0.1.repeat": "ewig"},
    ],
)
def test_invalid_options_are_refused(tmp_path: Path, field: dict[str, str]) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = post_mapping(rig, client, {"map.0.1": "music:play:morgen", **field})
    assert response.status_code == 422
    assert load_config(rig.path).mappings == ()


def test_the_holder_can_stop_the_music_and_light_cubes_can_ignore_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        post_mapping(rig, client, {"map.0.0": "music:stop.all"})
    config = load_config(rig.path)
    holder = [m for m in config.mappings if m.face == 0]
    assert len(holder) == 1 and holder[0].actions[0].action == "stop.all"
    import asyncio

    asyncio.run(rig.music.execute("play", {"playlist": "morgen"}))
    asyncio.run(
        rig.music.execute(holder[0].actions[0].action, dict(holder[0].actions[0].parameters))
    )
    assert all(t.state.value == "idle" for t in rig.music.status.targets)


# ---------- conditions and delays per face ----------


def test_delay_and_time_condition_are_saved_shown_and_loaded_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    fields = {
        "map.0.1": "music:play:morgen",
        "map.0.1.delay": "2,5",
        "map.0.1.when_start": "18:00",
        "map.0.1.when_end": "23:30",
        "map.0.1.day.0": "1",
        "map.0.1.day.1": "1",
        "map.0.1.day.2": "1",
        "map.0.1.day.3": "1",
        "map.0.1.day.4": "1",
    }
    with rig.client() as client:
        response = post_mapping(rig, client, fields)
        assert response.status_code == 200, response.text[:300]
        page = client.get("/cubes").text
    saved = [m for m in load_config(rig.path).mappings if (m.cube, m.face) == ("Musik", 1)][0]
    assert saved.actions[0].delay_seconds == 2.5
    assert saved.when is not None
    assert (saved.when.start, saved.when.end, saved.when.days) == (
        "18:00",
        "23:30",
        (0, 1, 2, 3, 4),
    )
    assert 'value="2.5"' in page and 'value="18:00"' in page and 'value="23:30"' in page
    assert "Nur Mo–Fr von 18:00 bis 23:30 Uhr" in page and "Wartet zuerst 2.5 Sekunden" in page


def test_without_timing_nothing_is_added(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        post_mapping(rig, client, {"map.0.1": "music:next", "map.0.1.day.0": ""})
    saved = [m for m in load_config(rig.path).mappings if (m.cube, m.face) == ("Musik", 1)][0]
    assert saved.when is None and saved.actions[0].delay_seconds == 0


@pytest.mark.parametrize(
    ("fields", "error_field"),
    [
        ({"map.0.1.delay": "31"}, "map.0.1.delay"),
        ({"map.0.1.delay": "-1"}, "map.0.1.delay"),
        ({"map.0.1.delay": "bald"}, "map.0.1.delay"),
        ({"map.0.1.delay": "nan"}, "map.0.1.delay"),
        ({"map.0.1.when_start": "18:00"}, "map.0.1.when_start"),
        ({"map.0.1.when_end": "18:00"}, "map.0.1.when_start"),
        ({"map.0.1.when_start": "18:00", "map.0.1.when_end": "18:00"}, "map.0.1.when_end"),
        ({"map.0.1.when_start": "25:00", "map.0.1.when_end": "26:00"}, "map.0.1.when_start"),
    ],
)
def test_invalid_timing_is_reported_and_nothing_is_saved(
    tmp_path: Path, fields: dict[str, str], error_field: str
) -> None:
    rig = Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        response = post_mapping(rig, client, {"map.0.1": "music:next", **fields})
    assert response.status_code == 422 or error_field in response.text
    assert rig.path.read_text(encoding="utf-8") == before


def test_a_locked_face_keeps_its_condition_and_delay(tmp_path: Path) -> None:
    import asyncio

    rig = Rig(tmp_path)
    two_steps = {
        "cube": "Musik",
        "face": 4,
        "actions": [
            {"kind": "music", "action": "stop", "parameters": {}},
            {"kind": "music", "action": "resume", "parameters": {}, "delay_seconds": 3},
        ],
        "when": {"start": "08:00", "end": "09:00", "days": [5, 6]},
    }
    asyncio.run(rig.settings.apply_mappings([two_steps]))
    with rig.client() as client:
        response = post_mapping(rig, client, {"map.0.1": "music:next"})
        assert response.status_code == 200, response.text[:300]
    kept = [m for m in load_config(rig.path).mappings if (m.cube, m.face) == ("Musik", 4)]
    assert kept and kept[0].when is not None and kept[0].when.days == (5, 6)
    assert kept[0].actions[1].delay_seconds == 3.0
