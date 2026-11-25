# ruff: noqa: E501
from __future__ import annotations

import asyncio
import logging
import re
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octapihub.core import error_catalog
from octapihub.core.config import save_config
from octapihub.core.errors import ConfigurationError, OctaPiHubError
from octapihub.core.resilience import (
    LoadedConfig,
    backups_of,
    describe_fallback,
    install_exception_hooks,
    install_loop_handler,
    load_with_fallback,
)
from octapihub.web import markdown
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend
from octapihub.web.field_help import (
    HELP,
    add_info_buttons,
    key_for,
    missing_help,
    text_for,
)
from octapihub.web.help_routes import DOCS, HELP_DIR, read_doc, search

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "octapihub"


# ---------- error catalogue ----------


def test_every_error_code_in_the_software_is_in_the_catalogue() -> None:
    used = set(
        re.findall(
            r'code = "(CH-[A-Z]+-\d+)"', (SRC / "core" / "errors.py").read_text(encoding="utf-8")
        )
    )
    for path in SRC.rglob("*.py"):
        used.update(re.findall(r'"(CH-[A-Z]+-\d{3})"', path.read_text(encoding="utf-8")))
    used -= {"CH-GENERIC-000"} - set(error_catalog.CATALOG)
    missing = sorted(code for code in used if code not in error_catalog.CATALOG)
    assert missing == []


def test_every_catalogue_entry_is_complete_and_codes_are_unique() -> None:
    codes = [e.code for e in error_catalog._ENTRIES]
    assert len(codes) == len(set(codes))
    for entry in error_catalog.CATALOG.values():
        assert re.fullmatch(r"CH-[A-Z]+-\d{3}", entry.code)
        assert len(entry.title) > 5 and len(entry.meaning) > 20 and len(entry.action) > 15


def test_retry_flags_match_the_error_classes() -> None:
    from octapihub.core import errors

    classes = {
        c.code: c
        for c in vars(errors).values()
        if isinstance(c, type) and issubclass(c, OctaPiHubError) and "code" in vars(c)
    }
    for code, cls in classes.items():
        entry = error_catalog.lookup(code)
        assert entry is not None, code
        if hasattr(cls, "retryable"):
            assert entry.retried == bool(cls.retryable) or code in {
                "CH-HUE-001",
                "CH-HUE-003",
                "CH-HUE-005",
            }, code


def test_lookup_and_grouping() -> None:
    assert error_catalog.lookup("CH-MUS-002") is not None
    assert error_catalog.lookup("CH-NOPE-999") is None
    areas = error_catalog.by_area()
    assert (
        "Musik" in areas
        and "Smart Home" in areas
        and sum(len(v) for v in areas.values()) == len(error_catalog.CATALOG)
    )


# ---------- markdown ----------


def test_markdown_renders_the_features_the_documents_use() -> None:
    html, headings = markdown.render(
        "# Titel\n\nText mit **fett**, *schräg* und `code <b>`.\n\n## Liste\n- eins\n- zwei\n  - innen\n\n1. a\n2. b\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n```\nx < y\n```\n\n> Hinweis\n\n---\n[Doku](faq.md) [Web](https://example.org) [Anker](#liste)"
    )
    assert '<h1 id="titel">Titel</h1>' in html and "<strong>fett</strong>" in html
    assert "<em>schräg</em>" in html and "<code>code &lt;b&gt;</code>" in html
    assert html.count("<ul>") == 2 and "<ol>" in html and "<table>" in html
    assert "x &lt; y" in html and "<blockquote>" in html and "<hr>" in html
    assert 'href="/help/faq"' in html and 'target="_blank"' in html and 'href="#liste"' in html
    assert [(h.level, h.anchor) for h in headings] == [(1, "titel"), (2, "liste")]


def test_markdown_never_lets_markup_or_unsafe_links_through() -> None:
    html, _ = markdown.render(
        "<script>alert(1)</script>\n\n[x](javascript:alert(1)) [y](data:text/html,x) ![z](a.png)\n\n"
        "`<img src=x onerror=1>` **<b>**"
    )
    assert "<script" not in html and "javascript:" not in html and "data:" not in html
    assert "&lt;script&gt;" in html and "onerror" not in html.replace(
        "&lt;img src=x onerror=1&gt;", ""
    )


def test_duplicate_headings_get_unique_anchors_and_umlauts_are_handled() -> None:
    _, headings = markdown.render("## Größe\n\n## Größe\n\n## Äpfel & Birnen")
    assert [h.anchor for h in headings] == ["groesse", "groesse-2", "aepfel-birnen"]
    assert markdown.slugify("???") == "abschnitt"


# ---------- documents ----------


def test_every_document_exists_renders_and_links_to_existing_documents() -> None:
    slugs = {d.slug for d in DOCS}
    for doc in DOCS:
        text = read_doc(doc.slug)
        assert len(text) > 800, doc.slug
        body, headings = markdown.render(text)
        assert headings and "&lt;script" not in body
        for target in re.findall(r"\]\(([^)]+\.md)(?:#[^)]*)?\)", text):
            assert target.rsplit("/", 1)[-1].removesuffix(".md") in slugs, (doc.slug, target)
    assert read_doc("gibtsnicht") == "" and read_doc("../../etc/passwd") == ""
    assert {p.stem for p in HELP_DIR.glob("*.md")} == slugs


def test_error_codes_mentioned_in_the_documents_exist() -> None:
    for doc in DOCS:
        for code in re.findall(r"CH-[A-Z]+-\d{3}", read_doc(doc.slug)):
            assert code in error_catalog.CATALOG, (doc.slug, code)


def test_the_installation_guide_in_the_help_is_the_one_in_deploy() -> None:
    deploy = (ROOT / "deploy" / "INSTALL.md").read_text(encoding="utf-8")
    assert read_doc("installation") == deploy


def test_documents_state_the_hardware_limit_honestly() -> None:
    assert "nicht auf einem echten raspberry pi" in re.sub(
        r"[\s>*]+", " ", read_doc("installation").lower()
    )
    assert "nicht mit echter hardware" in re.sub(r"[\s>*]+", " ", read_doc("start").lower())


# ---------- help pages and search ----------


def client() -> TestClient:
    return TestClient(create_app(build_demo_backend(), manage_service=True))


def test_help_pages_are_served_and_linked() -> None:
    with client() as c:
        index = c.get("/help")
        assert (
            index.status_code == 200
            and "Fehlercodes" in index.text
            and 'href="/help"' in index.text
        )
        for doc in DOCS:
            page = c.get(f"/help/{doc.slug}")
            assert page.status_code == 200 and doc.title in page.text and "Inhalt" in page.text
        errors = c.get("/help/errors")
        assert (
            errors.status_code == 200
            and 'id="CH-MUS-002"' in errors.text
            and "wird wiederholt" in errors.text
        )
        assert c.get("/help/gibtsnicht").status_code == 404
        assert c.get("/help/..%2F..%2Fetc").status_code == 404


def test_search_finds_documents_and_error_codes() -> None:
    hits = search("Token")
    titles = {h["title"] for h in hits}
    assert "Benutzerhandbuch" in titles and "Sicherheit und Datenschutz" in titles
    assert any(h["title"] == "Fehlercodes" for h in search("CH-MUS-002"))
    assert search("x") == [] and search("   ") == [] and search("qqqqzzzzxx") == []
    assert all("<script" not in s for h in search("<script>") for s in h["snippets"])
    with client() as c:
        page = c.get("/help", params={"q": "Bluetooth"})
        assert page.status_code == 200 and "<mark>" in page.text and "Ergebnisse für" in page.text
        assert "Dazu wurde nichts gefunden" in c.get("/help", params={"q": "qqqqzzzzxx"}).text
        evil = c.get("/help", params={"q": "<script>alert(1)</script>"})
        assert "<script>alert(1)</script>" not in evil.text
        assert c.get("/help", params={"q": "a" * 5000}).status_code == 200


# ---------- field help ----------


def test_keys_ignore_rows_and_addresses() -> None:
    assert key_for("t.3.device") == "t.*.device" and key_for("map.0.4.target") == "map.*.*.target"
    assert key_for("newname.AA:BB:CC:DD:EE:01") == "newname.*"
    assert key_for("sensor.temperature.interval_seconds") == "sensor.*.interval_seconds"
    assert key_for("music.library_dir") == "music.library_dir"


def test_every_help_text_is_useful_and_nothing_is_blank() -> None:
    for key, text in HELP.items():
        assert len(text) > 40 and text.endswith((".", "”", ")", "}", "!", "?")) or len(text) > 40, (
            key
        )


def test_info_buttons_are_added_after_labels_toggles_and_checkboxes() -> None:
    page = (
        '<label for="f-music.library_dir">Ordner</label><input id="f-music.library_dir">'
        '<label class="toggle"><input type="checkbox" name="music.enabled" id="f-music.enabled">'
        '<span class="tl"><strong>Musik</strong></span></label>'
        '<label class="check"><input type="checkbox" name="t.0.remove" value="on"> <span>Entfernen</span></label>'
        '<label for="f-unbekannt">?</label>'
    )
    result = add_info_buttons(page)
    assert result.count('class="info-btn"') == 3
    assert 'aria-label="Hilfe zu: Ordner"' in result and 'aria-expanded="false"' in result
    assert result.count('<span class="info-pop"') == 3 and " hidden>" in result
    assert missing_help(page) == ["unbekannt"]
    assert text_for("music.library_dir") and text_for("gibt.es.nicht") is None


def test_every_field_of_every_page_has_a_help_button() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend, manage_service=True)) as c:
        for _ in range(2):  # pair the demo bridge so the Licht page shows everything
            c.post(
                "/light",
                data={
                    "action": "pair",
                    "token": DEMO_TOKEN,
                    "hue.enabled": "on",
                    "hue.host": "192.168.1.20",
                    "hue.port": "443",
                },
            )
        missing: dict[str, list[str]] = {}
        for path in (
            "/",
            "/cubes",
            "/light",
            "/music",
            "/smarthome",
            "/sense-hat",
            "/display",
            "/system",
            "/settings",
            "/backup",
            "/help",
        ):
            response = c.get(path)
            assert response.status_code == 200, path
            gaps = missing_help(response.text)
            if gaps:
                missing[path] = gaps
            assert response.text.count('class="info-btn"') >= (
                1
                if path in ("/cubes", "/music", "/smarthome", "/settings", "/backup", "/light")
                else 0
            ), path
        assert missing == {}


def test_help_texts_are_escaped_in_the_page() -> None:
    result = add_info_buttons('<label for="f-token">T</label>')
    assert "<script" not in result and 'role="note"' in result


# ---------- resilience ----------


def good_config_file(tmp_path: Path, name: str = "octapihub.toml") -> Path:
    config = build_demo_backend().settings.config
    path = tmp_path / name
    save_config(path, config)
    return path


def test_a_good_file_is_used_directly(tmp_path: Path) -> None:
    path = good_config_file(tmp_path)
    loaded = load_with_fallback(path)
    assert loaded.path == path and loaded.fallback_from is None and describe_fallback(loaded) == ""


def test_a_broken_file_falls_back_to_the_newest_valid_backup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = good_config_file(tmp_path)
    older = good_config_file(tmp_path, "octapihub.toml.bak-20260101-000000")
    newest = good_config_file(tmp_path, "octapihub.toml.bak-20260301-000000")
    (tmp_path / "octapihub.toml.bak-20260401-000000").write_text("kaputt = [", encoding="utf-8")
    path.write_text("schema_version = 7\nnonsense", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        loaded = load_with_fallback(path)
    assert loaded.path == newest and loaded.fallback_from == path
    assert older.exists() and path.read_text(encoding="utf-8").startswith(
        "schema_version"
    )  # untouched
    text = describe_fallback(loaded)
    assert "octapihub.toml.bak-20260301-000000" in text and "unbrauchbar" in text
    assert any("unusable" in r.message for r in caplog.records)
    assert backups_of(path)[0].name.endswith("20260401-000000")


def test_without_any_usable_file_the_original_error_is_raised(tmp_path: Path) -> None:
    path = tmp_path / "octapihub.toml"
    with pytest.raises(ConfigurationError):
        load_with_fallback(path)
    path.write_text("x = [", encoding="utf-8")
    (tmp_path / "octapihub.toml.bak-1").write_text("y = [", encoding="utf-8")
    with pytest.raises(OctaPiHubError):
        load_with_fallback(path)


def test_loaded_config_without_fallback_has_no_message() -> None:
    config = build_demo_backend().settings.config
    assert describe_fallback(LoadedConfig(config, Path("x"))) == ""


def test_exception_hooks_log_instead_of_losing_errors(caplog: pytest.LogCaptureFixture) -> None:
    import sys

    old = sys.excepthook, threading.excepthook
    try:
        install_exception_hooks()
        with caplog.at_level(logging.ERROR):
            thread = threading.Thread(target=lambda: 1 / 0, name="probe")
            thread.start()
            thread.join()
            sys.excepthook(RuntimeError, RuntimeError("geheim123"), None)
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "probe" in text and "ZeroDivisionError" in text and "RuntimeError" in text
        assert "geheim123" not in text  # only the type is logged, never the message
    finally:
        sys.excepthook, threading.excepthook = old


def test_the_loop_handler_logs_failed_background_tasks(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        install_loop_handler(asyncio.get_running_loop())

        async def boom() -> None:
            raise ValueError("geheim456")

        task = asyncio.get_running_loop().create_task(boom())
        await asyncio.sleep(0.05)
        del task
        import gc

        gc.collect()
        await asyncio.sleep(0.05)

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "geheim456" not in text


# ---------- web error handling ----------


def test_an_unexpected_error_shows_a_calm_page_with_a_reference_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    backend = build_demo_backend()
    app = create_app(backend, manage_service=True)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("geheimes-token-123")

    @app.get("/api/boom")
    def api_boom() -> None:
        raise RuntimeError("x")

    with TestClient(app, raise_server_exceptions=False) as c, caplog.at_level(logging.ERROR):
        page = c.get("/boom")
        assert page.status_code == 500 and "CH-WEB-500" in page.text and "Kennung" in page.text
        assert "geheimes-token-123" not in page.text and "Traceback" not in page.text
        reference = re.search(r"Kennung <code>([0-9a-f]{8})</code>", page.text)
        assert reference is not None
        assert any(reference.group(1) in r.getMessage() for r in caplog.records)
        assert "geheimes-token-123" not in " ".join(r.getMessage() for r in caplog.records)
        api = c.get("/api/boom")
        assert (
            api.status_code == 500
            and api.json()["code"] == "CH-WEB-500"
            and len(api.json()["reference"]) == 8
        )
        assert c.get("/").status_code == 200  # the app keeps running


def test_an_uncaught_project_error_keeps_its_own_code() -> None:
    from octapihub.core.errors import MusicTargetError

    app = create_app(build_demo_backend(), manage_service=True)

    @app.get("/music-boom")
    def boom() -> None:
        raise MusicTargetError("x", user_message="Die Box ist aus.")

    with TestClient(app, raise_server_exceptions=False) as c:
        page = c.get("/music-boom")
    assert page.status_code == 500 and "CH-MUS-002" in page.text and "Die Box ist aus." in page.text


def test_known_http_errors_get_their_codes_and_a_link_to_the_explanation() -> None:
    with client() as c:
        missing = c.get("/gibt-es-nicht")
        assert missing.status_code == 404 and "CH-WEB-001" in missing.text
        assert 'href="/help/errors#CH-WEB-001"' in missing.text
        too_big = c.post(
            "/music",
            content=b"x" * 70000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert too_big.status_code == 413 and "CH-WEB-020" in too_big.text


def test_a_failing_service_start_is_isolated_and_reported() -> None:
    backend = build_demo_backend()

    async def broken() -> None:
        raise RuntimeError("Bluetooth kaputt")

    backend.cubes.start = broken  # type: ignore[method-assign, union-attr]
    with TestClient(create_app(backend, manage_service=True)) as c:
        page = c.get("/")
        assert page.status_code == 200 and "„Würfel“ konnte nicht gestartet werden" in page.text
        assert "Bluetooth kaputt" not in page.text
        health = c.get("/healthz").json()
        assert health["status"] == "degraded" and health["warnings"] == 1
        assert c.get("/music").status_code == 200  # everything else works


def test_healthz_reports_every_part_without_secrets() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend, manage_service=True)) as c:
        data = c.get("/healthz").json()
        assert set(data["parts"]) >= {"sense_hat", "cubes", "hue", "music", "smarthome"}
        assert data["parts"]["cubes"]["configured"] == 3
        assert data["sense_hat_running"] is True  # the old keys still exist
        assert "token" not in str(data).lower() and "demo-token" not in str(data)
        # a failed smart-home action marks that part as degraded
        with pytest.raises(OctaPiHubError):
            asyncio.run(backend.smarthome.run("gibtsnicht"))  # type: ignore[union-attr]

        async def refused(*_a: object, **_k: object) -> int:
            from octapihub.core.errors import SmartHomeRejectedError

            raise SmartHomeRejectedError("no")

        backend.smarthome._http = refused  # type: ignore[union-attr]
        with pytest.raises(OctaPiHubError):
            asyncio.run(backend.smarthome.run("tor"))  # type: ignore[union-attr]
        degraded = c.get("/healthz").json()
        assert "smarthome" in degraded["degraded"] and degraded["parts"]["smarthome"][
            "error_codes"
        ] == ["CH-HOME-003"]


# ---------- command line ----------


@pytest.fixture
def clean_root_logger():  # type: ignore[no-untyped-def]
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_check_reports_ok_warnings_and_unusable(
    clean_root_logger: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from octapihub.web.__main__ import main

    path = good_config_file(tmp_path)
    secrets = tmp_path / "secrets.env"
    monkeypatch.setenv("OCTAPIHUB_WEB_TOKEN", "ein-langes-zufaelliges-token-1234")
    assert main(["--check", "--config", str(path), "--secrets", str(secrets)]) == 0
    assert "Einstellungen: in Ordnung" in capsys.readouterr().out
    monkeypatch.setenv("OCTAPIHUB_WEB_TOKEN", "kurz")
    assert main(["--check", "--config", str(path), "--secrets", str(secrets)]) == 1
    assert "CH-WEB-008" in capsys.readouterr().out
    monkeypatch.delenv("OCTAPIHUB_WEB_TOKEN")
    assert main(["--check", "--config", str(path), "--secrets", str(secrets)]) == 0
    assert "nur lesend" in capsys.readouterr().out
    path.write_text("kaputt = [", encoding="utf-8")
    good_config_file(tmp_path, "octapihub.toml.bak-20260101-000000")
    assert main(["--check", "--config", str(path), "--secrets", str(secrets)]) == 1
    assert "WARNUNG" in capsys.readouterr().out
    for backup in backups_of(path):
        backup.unlink()
    assert main(["--check", "--config", str(path), "--secrets", str(secrets)]) == 2
    assert "CH-CFG" in capsys.readouterr().err
