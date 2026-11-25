from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octapihub.application.ble_scan import FoundDevice
from octapihub.core.config import (
    face_labels_table,
    load_config,
    parse_config,
    save_config,
    with_cubes_table,
    with_face_labels_table,
)
from octapihub.core.errors import BleConnectionError, ValidationError
from octapihub.infrastructure.ble.fake_scanner import FakeScanner
from octapihub.web import forms
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend
from octapihub.web.face_detail import describe_face


def base_config(extra: dict[str, object] | None = None):  # type: ignore[no-untyped-def]
    data: dict[str, object] = {
        "schema_version": 7,
        "bluetooth": {"adapter": "hci0"},
        "cubes": [
            {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
            {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
        ],
        "mappings": [
            {
                "cube": "Musik",
                "face": 1,
                "actions": [{"kind": "music", "action": "stop.all", "parameters": {}}],
            }
        ],
    }
    data.update(extra or {})
    return parse_config(data)


# ---------- face labels in the configuration ----------


def test_face_labels_round_trip_through_the_file(tmp_path: Path) -> None:
    config = base_config(
        {
            "face_labels": [
                {"cube": "licht", "face": 0, "name": "Halterung", "color": "#2FBF71"},
                {"cube": "Joystick", "face": 5, "color": "#ef4444"},
            ]
        }
    )
    assert [(la.cube, la.face, la.name, la.color) for la in config.face_labels] == [
        ("Licht", 0, "Halterung", "#2fbf71"),
        ("Joystick", 5, "", "#ef4444"),
    ]
    path = tmp_path / "c.toml"
    save_config(path, config)
    assert load_config(path).face_labels == config.face_labels
    assert "[[face_labels]]" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "label",
    [
        {"cube": "Nope", "face": 1, "name": "x"},
        {"cube": "Licht", "face": 9, "name": "x"},
        {"cube": "Joystick", "face": 0, "name": "x"},
        {"cube": "Licht", "face": 1},
        {"cube": "Licht", "face": 1, "name": "  "},
        {"cube": "Licht", "face": 1, "color": "red"},
        {"cube": "Licht", "face": 1, "name": "x" * 41},
        {"cube": "Licht", "face": 1, "name": "a\nb"},
        {"cube": "Licht", "face": True, "name": "x"},
        {"cube": "Licht", "face": 1, "name": "x", "extra": 1},
    ],
)
def test_invalid_face_labels_are_rejected(label: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        base_config({"face_labels": [label]})


def test_duplicate_labels_and_old_schema_files() -> None:
    twice = [{"cube": "Licht", "face": 1, "name": "a"}, {"cube": "licht", "face": 1, "name": "b"}]
    with pytest.raises(ValidationError):
        base_config({"face_labels": twice})
    old = base_config()
    assert old.face_labels == () and old.schema_version == 7
    migrated = parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "light"}],
        }
    )
    assert migrated.schema_version == 7
    with pytest.raises(ValidationError):  # v6 files cannot have the new table
        parse_config(
            {
                "schema_version": 6,
                "bluetooth": {"adapter": "hci0"},
                "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "light"}],
                "face_labels": [],
            }
        )


def test_with_face_labels_table_replaces_the_labels() -> None:
    config = base_config()
    updated = with_face_labels_table(config, [{"cube": "Licht", "face": 2, "name": "Hell"}])
    assert face_labels_table(updated) == [{"cube": "Licht", "face": 2, "name": "Hell"}]


# ---------- cube list editing ----------


def test_with_cubes_table_validates_and_prunes_dependent_entries() -> None:
    config = base_config({"face_labels": [{"cube": "Musik", "face": 3, "name": "Jazz"}]})
    only_licht = with_cubes_table(
        config, [{"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"}]
    )
    assert [c.name for c in only_licht.cubes] == ["Licht"]
    assert only_licht.mappings == () and only_licht.face_labels == ()
    kept = with_cubes_table(
        config,
        [
            {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
            {"name": "Musik", "address": "AA:BB:CC:DD:EE:09", "role": "music"},
        ],
    )
    assert len(kept.mappings) == 1 and len(kept.face_labels) == 1  # same name: kept


@pytest.mark.parametrize(
    "cubes",
    [
        [],
        "x",
        [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "light"}] * 2,
        [{"name": "Joystick", "address": "AA:BB:CC:DD:EE:01", "role": "light"}],
        [{"name": "A", "address": "kaputt", "role": "light"}],
        [
            {"name": f"C{i}", "address": f"AA:BB:CC:DD:EE:0{i}", "role": "light"}
            for i in range(1, 5)
        ],
    ],
)
def test_with_cubes_table_rejects_bad_lists(cubes: object) -> None:
    with pytest.raises(ValidationError):
        with_cubes_table(base_config(), cubes)  # type: ignore[arg-type]


# ---------- scanner ----------


def test_found_devices_are_recognised_by_name() -> None:
    assert FoundDevice("Timeular Tracker", "AA:BB:CC:DD:EE:77", -50).looks_like_cube
    assert FoundDevice("ZEI", "AA:BB:CC:DD:EE:78").looks_like_cube
    assert not FoundDevice("Lautsprecher", "AA:BB:CC:DD:EE:79").looks_like_cube
    assert not FoundDevice("", "AA:BB:CC:DD:EE:80").looks_like_cube


def test_fake_scanner_records_and_fails_on_demand() -> None:
    scanner = FakeScanner([FoundDevice("a", "AA:BB:CC:DD:EE:01")])
    assert len(asyncio.run(scanner.scan("hci0", 3))) == 1 and scanner.calls == [("hci0", 3)]
    with pytest.raises(BleConnectionError):
        asyncio.run(FakeScanner(error=BleConnectionError("off")).scan("hci0", 1))


# ---------- web: managing cubes ----------


def client_and_backend() -> tuple[TestClient, object]:
    backend = build_demo_backend()
    client = TestClient(create_app(backend, manage_service=True))
    client.__enter__()
    return client, backend


def manage(client: TestClient, fields: dict[str, str], token: str = DEMO_TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token:
        body["token"] = token
    return client.post("/cubes/manage", data=body)


def test_the_page_shows_the_manage_section_and_counts() -> None:
    client, _ = client_and_backend()
    try:
        page = client.get("/cubes").text
        assert "Würfel verwalten" in page and "3 von 3" in page
        assert "Nach Würfeln suchen" in page and "Name, Farbe, Optionen und Details" in page
        assert 'data-cube-name="Licht"' in page and 'data-face="0"' in page
    finally:
        client.__exit__(None, None, None)


def test_scan_lists_devices_and_marks_cubes() -> None:
    client, backend = client_and_backend()
    try:
        response = manage(client, {"action": "scan"})
        assert response.status_code == 200
        assert "Timeular Tracker" in response.text and "vermutlich Würfel" in response.text
        assert "AA:**:**:**:**:77" in response.text
        backend.scanner = FakeScanner()  # type: ignore[attr-defined]
        assert "Keine Bluetooth-Geräte" in manage(client, {"action": "scan"}).text
        backend.scanner = FakeScanner(error=BleConnectionError("off"))  # type: ignore[attr-defined]
        assert manage(client, {"action": "scan"}).status_code == 502
        assert manage(client, {"action": "scan"}, token="").status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_adding_and_removing_a_cube_updates_file_and_running_service() -> None:
    client, backend = client_and_backend()
    try:
        config_path = backend.settings._path  # type: ignore[attr-defined]
        # full: three cubes already
        full = manage(
            client,
            {"action": "add-manual", "manual_address": "AA:BB:CC:DD:EE:77", "manual_name": "Neu"},
        )
        assert full.status_code == 409 and "höchstens 3" in full.text
        removed = manage(client, {"action": "remove:Extra", "confirm_remove": "on"})
        assert removed.status_code == 200 and "entfernt" in removed.text
        assert [c.name for c in load_config(config_path).cubes] == ["Licht", "Musik"]
        assert [c.name for c in backend.cubes.config.cubes] == ["Licht", "Musik"]  # type: ignore[attr-defined]
        assert len(list(config_path.parent.glob("octapihub.toml.bak-*"))) >= 1
        added = manage(
            client,
            {
                "action": "add:AA:BB:CC:DD:EE:77",
                "newname.AA:BB:CC:DD:EE:77": "Küche",
                "newrole.AA:BB:CC:DD:EE:77": "music",
            },
        )
        assert added.status_code == 200 and "hinzugefügt" in added.text
        names = [c.name for c in load_config(config_path).cubes]
        assert names == ["Licht", "Musik", "Küche"]
        assert [c.name for c in backend.cubes.config.cubes] == names  # type: ignore[attr-defined]
        assert "Küche" in client.get("/cubes").text
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"action": "add-manual", "manual_address": "AA:BB:CC:DD:EE:77", "manual_name": ""}, 422),
        ({"action": "remove:Extra"}, 422),  # no confirmation
        ({"action": "remove:Nope", "confirm_remove": "on"}, 422),
        ({"action": "explode"}, 422),
    ],
)
def test_manage_refusals_change_nothing(fields: dict[str, str], status: int) -> None:
    client, backend = client_and_backend()
    try:
        before = backend.settings.config  # type: ignore[attr-defined]
        assert manage(client, fields).status_code == status
        assert backend.settings.config == before  # type: ignore[attr-defined]
    finally:
        client.__exit__(None, None, None)


def test_bad_address_duplicate_and_last_cube_are_explained() -> None:
    client, backend = client_and_backend()
    try:
        manage(client, {"action": "remove:Extra", "confirm_remove": "on"})
        bad = manage(
            client, {"action": "add-manual", "manual_address": "kaputt", "manual_name": "X"}
        )
        assert bad.status_code == 422
        dup = manage(
            client,
            {"action": "add-manual", "manual_address": "AA:BB:CC:DD:EE:01", "manual_name": "Y"},
        )
        assert dup.status_code == 422
        manage(client, {"action": "remove:Musik", "confirm_remove": "on"})
        last = manage(client, {"action": "remove:Licht", "confirm_remove": "on"})
        assert last.status_code == 409 and "Mindestens ein Würfel" in last.text
    finally:
        client.__exit__(None, None, None)


# ---------- web: names, colours, details ----------


def test_names_colours_and_details_are_saved_and_shown() -> None:
    client, backend = client_and_backend()
    try:
        body = {
            **forms.mapping_config_to_form(backend.settings.config),  # type: ignore[attr-defined]
            "map.1.1.label": "Morgenmusik",
            "map.1.1.color": "orange",
            "map.1.0.label": "Aus in der Halterung",
            "map.0.2.color": "blue",
            "token": DEMO_TOKEN,
        }
        response = client.post("/cubes/mapping", data=body)
        assert response.status_code == 200, response.text[:300]
        labels = {
            (la.cube, la.face): (la.name, la.color)
            for la in backend.settings.config.face_labels  # type: ignore[attr-defined]
        }
        assert labels[("Musik", 1)] == ("Morgenmusik", "#f59e0b")
        assert labels[("Musik", 0)] == ("Aus in der Halterung", "")
        assert labels[("Licht", 2)] == ("", "#3b82f6")
        page = client.get("/cubes").text
        assert "Morgenmusik" in page and "fc-orange" in page and 'value="orange" checked' in page
        assert "Spielt die Playlist" in page and "Auf Wohnzimmer" in page
        assert "Reihenfolge: zufällig" in page and "ganze Playlist wiederholen" in page
        # removing name and colour removes the label
        cleared = {**body, "map.1.1.label": "", "map.1.1.color": ""}
        client.post("/cubes/mapping", data=cleared)
        assert ("Musik", 1) not in {
            (la.cube, la.face)
            for la in backend.settings.config.face_labels  # type: ignore[attr-defined]
        }
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("extra", [{"map.0.1.color": "magenta"}, {"map.0.1.label": "x" * 41}])
def test_invalid_names_and_colours_are_refused(extra: dict[str, str]) -> None:
    client, backend = client_and_backend()
    try:
        body = {
            **forms.mapping_config_to_form(backend.settings.config),  # type: ignore[attr-defined]
            **extra,
            "token": DEMO_TOKEN,
        }
        before = backend.settings.config  # type: ignore[attr-defined]
        assert client.post("/cubes/mapping", data=body).status_code == 422
        assert backend.settings.config == before  # type: ignore[attr-defined]
    finally:
        client.__exit__(None, None, None)


def test_custom_colours_from_the_file_survive_a_save() -> None:
    client, backend = client_and_backend()
    try:
        config = with_face_labels_table(
            backend.settings.config,  # type: ignore[attr-defined]
            [{"cube": "Licht", "face": 1, "name": "x", "color": "#123456"}],
        )
        backend.settings._config = config  # type: ignore[attr-defined]
        body = {**forms.mapping_config_to_form(config), "token": DEMO_TOKEN}
        assert body["map.0.1.color"] == "#123456"
        assert client.post("/cubes/mapping", data=body).status_code == 200
        assert backend.settings.config.face_labels[0].color == "#123456"  # type: ignore[attr-defined]
    finally:
        client.__exit__(None, None, None)


def test_face_details_describe_every_kind_of_action() -> None:
    client, backend = client_and_backend()
    try:
        config = backend.settings.config  # type: ignore[attr-defined]
        by_face = {(m.cube, m.face): m for m in config.mappings}
        scenes = {"wohnzimmer-abend": "Wohnzimmer – Abend"}
        play = describe_face(config, by_face[("Musik", 1)], scenes, {})
        assert any("Morgenmusik" in line for line in play)
        assert any("Musikordner" in line for line in play)
        assert any("Auf Wohnzimmer" in line for line in play)
        stop = describe_face(config, by_face[("Musik", 0)], scenes, {})
        assert "allen Geräten" in stop[0]
        scene = describe_face(config, by_face[("Licht", 3)], scenes, {})
        assert scene[0].startswith("Schritt 1:") and "Wohnzimmer – Abend" in scene[1]
        assert describe_face(config, None, scenes, {}) == []
    finally:
        client.__exit__(None, None, None)
