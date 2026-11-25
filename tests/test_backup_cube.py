from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from octapihub.application import backup
from octapihub.core.config import load_config, parse_config
from octapihub.core.errors import ValidationError
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def config():  # type: ignore[no-untyped-def]
    return parse_config(
        {
            "schema_version": 7,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
                {"name": "Neu", "address": "AA:BB:CC:DD:EE:03", "role": "general"},
            ],
            "mappings": [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [{"kind": "music", "action": "stop.all", "parameters": {}}],
                },
                {
                    "cube": "Musik",
                    "face": 2,
                    "actions": [{"kind": "music", "action": "next", "parameters": {}}],
                },
                {
                    "cube": "Licht",
                    "face": 1,
                    "actions": [{"kind": "hue", "action": "light.on", "parameters": {}}],
                },
                {
                    "cube": "Joystick",
                    "face": 1,
                    "actions": [{"kind": "music", "action": "volume.up", "parameters": {}}],
                },
            ],
            "face_labels": [
                {"cube": "Musik", "face": 1, "name": "Stopp", "color": "#ef4444"},
                {"cube": "Licht", "face": 1, "name": "An"},
            ],
        }
    )


def test_export_of_one_cube_has_only_its_data() -> None:
    text = backup.build_bundle(config(), ["cube"], NOW, "musik")
    profile = json.loads(text)["sections"]["cube"]
    assert profile["name"] == "Musik"
    assert [m["face"] for m in profile["mappings"]] == [1, 2]
    assert profile["labels"] == [{"face": 1, "name": "Stopp", "color": "#ef4444"}]
    assert "AA:BB:CC" not in text and "Licht" not in text
    with pytest.raises(ValidationError):
        backup.build_bundle(config(), ["cube"], NOW, "gibtsnicht")
    with pytest.raises(ValidationError):
        backup.build_bundle(config(), ["cube"], NOW, "")


def test_a_cube_profile_is_applied_to_another_cube_and_replaces_only_that_cube() -> None:
    base = config()
    bundle = backup.parse_bundle(backup.build_bundle(base, ["cube"], NOW, "Musik"))
    updated, notes = backup.prepare_section(base, "cube", bundle.sections["cube"], "Neu")
    new_faces = {m.face: m.actions[0].action for m in updated.mappings if m.cube == "Neu"}
    assert new_faces == {1: "stop.all", 2: "next"}
    assert [(la.face, la.name) for la in updated.face_labels if la.cube == "Neu"] == [(1, "Stopp")]
    assert {m.face for m in updated.mappings if m.cube == "Musik"} == {1, 2}  # source untouched
    assert any(m.cube == "Licht" for m in updated.mappings)  # other cubes untouched
    assert "„Neu“" in notes[0]
    # without a chosen target the same-named cube is used
    same, _ = backup.prepare_section(base, "cube", bundle.sections["cube"])
    assert sorted(same.mappings, key=lambda m: (m.cube, m.face)) == sorted(
        base.mappings, key=lambda m: (m.cube, m.face)
    )


def test_a_profile_replaces_the_targets_old_assignments() -> None:
    base = config()
    profile = {
        "name": "x",
        "mappings": [
            {"face": 4, "actions": [{"kind": "music", "action": "stop", "parameters": {}}]}
        ],
    }
    updated, _ = backup.prepare_section(base, "cube", profile, "Musik")
    assert {m.face for m in updated.mappings if m.cube == "Musik"} == {4}
    assert [la for la in updated.face_labels if la.cube == "Musik"] == []


@pytest.mark.parametrize(
    ("profile", "target"),
    [
        ({"mappings": "x"}, "Neu"),
        ("kaputt", "Neu"),
        (
            {
                "mappings": [
                    {"face": 9, "actions": [{"kind": "music", "action": "stop", "parameters": {}}]}
                ]
            },
            "Neu",
        ),
        (
            {
                "mappings": [
                    {"face": 1, "actions": [{"kind": "music", "action": "stop", "parameters": {}}]}
                ],
                "labels": [{"face": 0, "name": "x"}],
            },
            "Joystick",
        ),
        ({"mappings": []}, "Nirgends"),
    ],
)
def test_profiles_that_do_not_fit_are_rejected(profile: object, target: str) -> None:
    with pytest.raises(ValidationError):
        backup.prepare_section(config(), "cube", profile, target)


def test_labels_section_round_trips_and_rejects_foreign_cubes() -> None:
    base = config()
    exported = json.loads(backup.build_bundle(base, ["labels"], NOW))["sections"]["labels"]
    again, _ = backup.prepare_section(base, "labels", exported)
    assert again.face_labels == base.face_labels
    with pytest.raises(ValidationError):
        backup.prepare_section(base, "labels", [{"cube": "Fremd", "face": 1, "name": "x"}])
    with pytest.raises(ValidationError):
        backup.prepare_section(base, "labels", "x")


def test_web_export_and_import_of_one_cube() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend, manage_service=True)) as client:
        page = client.get("/backup").text
        assert "Ein einzelner Würfel" in page and "Welcher Würfel?" in page
        exported = client.post(
            "/backup",
            data={"action": "export", "sec.cube": "on", "cube_name": "Musik", "token": DEMO_TOKEN},
        )
        assert exported.status_code == 200
        bundle_text = exported.text
        check = client.post(
            "/backup",
            data={
                "action": "import-check",
                "bundle": bundle_text,
                "target_cube": "Extra",
                "token": DEMO_TOKEN,
            },
        )
        assert check.status_code == 200 and "Wird auf „Extra“ angewendet" in check.text
        applied = client.post(
            "/backup",
            data={
                "action": "import-apply",
                "bundle": bundle_text,
                "target_cube": "Extra",
                "imp.cube": "on",
                "token": DEMO_TOKEN,
            },
        )
        assert applied.status_code == 200, applied.text[:300]
        saved = load_config(backend.settings._path)  # type: ignore[attr-defined]
        assert {m.face for m in saved.mappings if m.cube == "Extra"} == {0, 1, 2}
        assert {m.face for m in saved.mappings if m.cube == "Musik"} == {0, 1, 2}
