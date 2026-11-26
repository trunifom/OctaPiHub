"""Version history with restore, mobile menu, web app manifest and the unsaved-changes script."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from test_web_music import TOKEN, Rig

from octapihub.core.config import load_config
from octapihub.core.errors import ValidationError
from octapihub.web.app import create_app

STATIC = Path(__file__).parent.parent / "src" / "octapihub" / "web" / "static"


def post_backup(client: TestClient, fields: dict[str, str], token: str = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token:
        body["token"] = token
    return client.post(
        "/backup",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


# ---------- earlier versions ----------


def test_the_page_lists_earlier_versions_and_restores_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asyncio.run(
        rig.settings.apply_cubes(
            [{"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"}]
        )
    )
    asyncio.run(
        rig.settings.apply_mappings(
            [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [{"kind": "music", "action": "stop", "parameters": {}}],
                }
            ]
        )
    )
    versions = rig.settings.list_backups()
    assert versions and versions[0].size > 0
    before = load_config(rig.path)
    with rig.client() as client:
        assert "Frühere Versionen" in client.get("/backup").text
        assert versions[0].name in client.get("/backup").text
        response = post_backup(
            client, {"action": f"restore:{versions[0].name}", "confirm_restore": "1"}
        )
        assert response.status_code == 200, response.text[:300]
        assert "wiederhergestellt" in response.text
    restored = load_config(rig.path)
    assert restored.mappings == ()  # the version before the mapping was saved
    assert before.mappings != restored.mappings
    assert rig.settings.config.mappings == ()
    assert len(rig.settings.list_backups()) >= 1  # the state before the restore was kept too


def test_restore_needs_the_confirmation_the_token_and_a_valid_name(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asyncio.run(
        rig.settings.apply_cubes(
            [{"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"}]
        )
    )
    name = rig.settings.list_backups()[0].name
    text = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        assert post_backup(client, {"action": f"restore:{name}"}).status_code == 422
        assert post_backup(
            client, {"action": f"restore:{name}", "confirm_restore": "1"}, token="x"
        ).status_code in (403, 429)
        for bad in ("../../etc/passwd", "20260101-000000", "abc", ""):
            response = post_backup(client, {"action": f"restore:{bad}", "confirm_restore": "1"})
            assert response.status_code == 422, bad
    assert rig.path.read_text(encoding="utf-8") == text


def test_a_damaged_earlier_version_is_refused_and_nothing_changes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    broken = tmp_path / "octapihub.toml.bak-20200101-000000"
    broken.write_text("schema_version = 7\n[[cubes]]\nname = ", encoding="utf-8")
    text = rig.path.read_text(encoding="utf-8")
    with pytest.raises(Exception) as caught:
        asyncio.run(rig.settings.restore_backup("20200101-000000"))
    assert not isinstance(caught.value, ValidationError) or caught.value.user_message
    assert rig.path.read_text(encoding="utf-8") == text
    assert "20200101-000000" in [b.name for b in rig.settings.list_backups()]


def test_files_that_only_look_like_backups_are_not_listed(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    (tmp_path / "octapihub.toml.bak-evil").write_text("x", encoding="utf-8")
    (tmp_path / "octapihub.toml.bak-20200101-000000.tmp").write_text("x", encoding="utf-8")
    assert rig.settings.list_backups() == []


# ---------- navigation ----------


def test_the_mobile_menu_offers_the_rarely_used_pages(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        page = client.get("/system").text
    assert 'class="nav-more is-active"' in page and "<span>Mehr</span>" in page
    for href in ("/smarthome", "/sense-hat", "/display", "/system", "/settings"):
        assert f'href="{href}"' in page
    assert page.count('aria-current="page"') == 2  # the bar link and the one in the menu
    with rig.client() as client:
        assert 'class="nav-more"' in client.get("/cubes").text


# ---------- web app ----------


def test_the_manifest_and_the_icons_are_served(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = client.get("/manifest.webmanifest")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/manifest+json")
        data = json.loads(response.text)
        assert data["start_url"] == "/" and data["display"] == "standalone"
        for icon in data["icons"]:
            served = client.get(icon["src"])
            assert served.status_code == 200 and served.content.startswith(b"\x89PNG")
        page = client.get("/").text
    assert 'rel="manifest"' in page and 'name="theme-color"' in page


def test_the_icons_have_the_declared_sizes() -> None:
    import struct

    for size in (192, 512):
        header = (STATIC / f"icon-{size}.png").read_bytes()[16:24]
        assert struct.unpack(">II", header) == (size, size)


# ---------- unsaved changes ----------


def test_the_editor_script_guards_every_editor_form() -> None:
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "beforeunload" in script
    for form in ("mapping-form", "light-form", "music-form", "settings-form", "smarthome-form"):
        assert f"#{form}" in script
    assert 'name === "token"' in script  # typing the token is not a change


def test_the_guarded_forms_exist_in_the_templates() -> None:
    templates = STATIC.parent / "templates"
    for form in ("mapping-form", "light-form", "music-form", "settings-form", "smarthome-form"):
        assert any(
            f'id="{form}"' in p.read_text(encoding="utf-8") for p in templates.glob("*.html")
        )


def test_create_app_still_builds_without_an_audit_log(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert TestClient(create_app(rig.backend)).get("/healthz").status_code == 200
