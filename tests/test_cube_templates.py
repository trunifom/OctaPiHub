from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from test_web_music import TOKEN, Rig

from octapihub.application.backup import build_bundle, cube_profile
from octapihub.application.cube_templates import TEMPLATES, template_by_id, template_profile
from octapihub.core.config import load_config, parse_config
from octapihub.web import field_help


def post_template(rig: Rig, client: TestClient, fields: dict[str, str], token: str = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token:
        body["token"] = token
    return client.post(
        "/cubes/template",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def faces_of(rig: Rig) -> dict[int, tuple[str, str]]:
    return {
        m.face: (m.actions[0].kind, m.actions[0].action)
        for m in load_config(rig.path).mappings
        if m.cube == "Musik"
    }


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_every_template_is_a_valid_profile_for_a_cube(template) -> None:  # type: ignore[no-untyped-def]
    config = parse_config(
        {
            "schema_version": 7,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
        }
    )
    from octapihub.application.backup import prepare_section

    updated, notes = prepare_section(config, "cube", template_profile(template), "A")
    assert len(updated.mappings) == len(template.faces)
    assert {label.name for label in updated.face_labels} == {f[3] for f in template.faces}
    assert notes


def test_template_ids_are_unique_and_found() -> None:
    assert len({t.id for t in TEMPLATES}) == len(TEMPLATES)
    assert template_by_id("licht") is not None and template_by_id("nope") is None


def test_the_page_offers_the_templates(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        page = client.get("/cubes").text
    assert "Licht-Würfel" in page and "Musik-Würfel" in page and "/cubes/template" in page


def test_applying_a_template_replaces_the_cube_and_backs_up(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = post_template(
            rig, client, {"template": "musik", "cube": "Musik", "confirm_template": "1"}
        )
        assert response.status_code == 200, response.text[:300]
        assert "angewendet" in response.text
    assert faces_of(rig) == {
        1: ("music", "toggle"),
        2: ("music", "next"),
        3: ("music", "previous"),
        4: ("music", "volume.up"),
        5: ("music", "volume.down"),
        6: ("music", "stop"),
    }
    assert list(tmp_path.glob("octapihub.toml.bak-*"))
    names = {(label.face, label.name) for label in load_config(rig.path).face_labels}
    assert (1, "Pause / Weiter") in names


def test_a_template_replaces_old_faces_of_that_cube_only(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asyncio.run(
        rig.settings.apply_mappings(
            [
                {
                    "cube": "Musik",
                    "face": 8,
                    "actions": [{"kind": "music", "action": "stop", "parameters": {}}],
                }
            ]
        )
    )
    with rig.client() as client:
        post_template(rig, client, {"template": "musik", "cube": "Musik", "confirm_template": "1"})
    assert 8 not in faces_of(rig)


@pytest.mark.parametrize(
    "fields",
    [
        {"template": "unbekannt", "cube": "Musik", "confirm_template": "1"},
        {"template": "musik", "cube": "Gibtsnicht", "confirm_template": "1"},
        {"template": "musik", "cube": "Joystick", "confirm_template": "1"},
        {"template": "musik", "cube": "Musik"},  # not confirmed
        {},
    ],
)
def test_bad_requests_change_nothing(tmp_path: Path, fields: dict[str, str]) -> None:
    rig = Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        response = post_template(rig, client, fields)
    assert response.status_code == 422
    assert rig.path.read_text(encoding="utf-8") == before


def test_a_template_needs_the_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        response = post_template(
            rig,
            client,
            {"template": "musik", "cube": "Musik", "confirm_template": "1"},
            token="",
        )
    assert response.status_code in (401, 403)
    assert rig.path.read_text(encoding="utf-8") == before


def test_the_cube_export_keeps_conditions_and_delays(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asyncio.run(
        rig.settings.apply_mappings(
            [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [
                        {"kind": "music", "action": "stop", "parameters": {}, "delay_seconds": 2}
                    ],
                    "when": {"start": "18:00", "end": "22:00"},
                }
            ]
        )
    )
    profile = cube_profile(rig.settings.config, "Musik")
    assert profile["mappings"][0]["when"] == {"start": "18:00", "end": "22:00"}
    assert profile["mappings"][0]["actions"][0]["delay_seconds"] == 2
    assert build_bundle(rig.settings.config, ["cube"], datetime.now(UTC), "Musik")


def test_the_new_fields_have_help() -> None:
    for name in ("template", "template-cube", "confirm_template", "token-template"):
        assert field_help.text_for(name)
