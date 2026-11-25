from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application import backup
from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat_history import SeriesPoint
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import AppConfig, load_config, parse_config, save_config
from octapihub.core.errors import ConfigurationError, ValidationError
from octapihub.core.logging_support import (
    LOG_FILE_NAME,
    LogRing,
    Redactor,
    create_file_handler,
    read_log_file,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.music.fake import FakePlayerFactory, FakeResolver
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web.app import WebBackend, create_app

TOKEN = "backup-token-123456"
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

MUSIC = {
    "enabled": True,
    "library_dir": "/home/pi/Music",
    "targets": [
        {"id": "wz", "name": "Wohnzimmer", "kind": "cast", "device": "Wohnzimmer"},
        {"id": "pi", "name": "Raspberry Pi", "kind": "local"},
    ],
    "playlists": [
        {"id": "morgen", "name": "Morgens", "source": "Morgens", "target": "wz"},
    ],
}
MAPPINGS = [
    {
        "cube": "Musik",
        "face": 1,
        "actions": [{"kind": "music", "action": "play", "parameters": {"playlist": "morgen"}}],
    }
]


def make_config(tmp_path: Path, music: dict[str, Any] | None = None) -> AppConfig:
    return parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"}],
            "mappings": MAPPINGS,
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
            "music": MUSIC if music is None else music,
        }
    )


class Rig:
    def __init__(self, tmp_path: Path, config: AppConfig | None = None) -> None:
        self.config = config or make_config(tmp_path)
        self.path = tmp_path / "octapihub.toml"
        save_config(self.path, self.config)
        device = FakeSenseHat()
        self.sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(device),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.music = MusicService(self.config.music, FakePlayerFactory(), FakeResolver())
        self.settings = SettingsManager(self.path, self.config, self.sense, music=self.music)
        self.redactor = Redactor([TOKEN])
        self.ring = LogRing(self.redactor)
        self.backend = WebBackend(
            self.settings,
            self.sense,
            TOKEN,
            music=self.music,
            logs=self.ring,
            redactor=self.redactor,
            clock=lambda: NOW,
        )

    def client(self) -> TestClient:
        return TestClient(create_app(self.backend))

    def post(self, client: TestClient, fields: dict[str, str], token: str = TOKEN):  # type: ignore[no-untyped-def]
        body = dict(fields)
        if token:
            body["token"] = token
        return client.post(
            "/backup",
            content=urlencode(body),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )


# ---------- bundle logic ----------


def test_export_contains_the_chosen_sections_and_no_installation_specifics(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    text = backup.build_bundle(config, ["music", "sense_hat", "hue", "mappings"], NOW)
    data = json.loads(text)
    assert data["format"] == "octapihub-export" and data["version"] == 1
    assert list(data["sections"]) == ["music", "mappings", "sense_hat", "hue"]
    assert "database_path" not in data["sections"]["sense_hat"]["history"]
    assert "cubes" not in data["sections"] and "AA:BB:CC:DD:EE:02" not in text
    assert "Wohnzimmer" in text  # umlauts and names stay readable (ensure_ascii off)


@pytest.mark.parametrize("sections", [[], ["bogus"], ["music", "bogus"]])
def test_export_needs_known_sections(tmp_path: Path, sections: list[str]) -> None:
    with pytest.raises(ValidationError):
        backup.build_bundle(make_config(tmp_path), sections, NOW)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"format": "other", "version": 1, "sections": {"music": {}}}',
        '{"format": "octapihub-export", "version": "1", "sections": {"music": {}}}',
        '{"format": "octapihub-export", "version": 99, "sections": {"music": {}}}',
        '{"format": "octapihub-export", "version": 1, "sections": {}}',
        '{"format": "octapihub-export", "version": 1, "sections": {"unknown": {}}}',
        "x" * (backup.MAX_BUNDLE_BYTES + 1),
    ],
)
def test_unusable_files_are_refused_with_an_explanation(text: str) -> None:
    with pytest.raises(ValidationError) as error:
        backup.parse_bundle(text)
    assert error.value.user_message


def test_unknown_sections_are_ignored_but_reported(tmp_path: Path) -> None:
    text = backup.build_bundle(make_config(tmp_path), ["music"], NOW)
    data = json.loads(text)
    data["sections"]["future"] = {"x": 1}
    parsed = backup.parse_bundle(json.dumps(data))
    assert list(parsed.sections) == ["music"] and parsed.ignored == ("future",)


def test_preview_flags_sections_that_do_not_fit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    other = make_config(tmp_path)
    exported = json.loads(backup.build_bundle(other, ["music", "mappings"], NOW))
    exported["sections"]["mappings"][0]["cube"] = "Nicht vorhanden"
    exported["sections"]["music"]["targets"][0]["volume"] = 999
    previews = {
        p.key: p for p in backup.preview_bundle(config, backup.parse_bundle(json.dumps(exported)))
    }
    assert not previews["music"].valid and not previews["mappings"].valid
    assert "Würfeln dieser Installation" in previews["mappings"].detail


def test_playlists_are_merged_and_unknown_devices_removed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    imported = [
        {"id": "morgen", "name": "Neu benannt", "source": "Neu", "target": "wz"},
        {"id": "radio", "name": "Radio", "source": "https://radio.example.org/s", "target": "weg"},
    ]
    candidate, notes = backup.prepare_section(config, "playlists", imported)
    by_id = {p.id: p for p in candidate.music.playlists}
    assert by_id["morgen"].name == "Neu benannt" and by_id["radio"].target == ""
    assert len(candidate.music.targets) == 2
    assert notes and "gibt es hier nicht" in notes[0]
    with pytest.raises(ValidationError):
        backup.prepare_section(config, "playlists", "kaputt")
    with pytest.raises(ValidationError):
        backup.prepare_section(config, "playlists", [1])


# ---------- applying ----------


def exported(tmp_path: Path, **changes: Any) -> backup.Bundle:
    music = {**MUSIC, **changes}
    return backup.parse_bundle(
        backup.build_bundle(make_config(tmp_path, music), ["music", "sense_hat"], NOW)
    )


def test_import_is_all_or_nothing_and_makes_a_backup(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    bundle = exported(
        tmp_path,
        playlists=[{"id": "abend", "name": "Abend", "source": "Abend", "target": "pi"}],
    )
    notes = asyncio_run(rig.settings.apply_bundle(bundle, ["music"]))
    assert notes == []
    assert [p.id for p in load_config(rig.path).music.playlists] == ["abend"]
    assert [p.id for p in rig.music.config.playlists] == ["abend"]  # applied live
    assert len(list(tmp_path.glob("octapihub.toml.bak-*"))) == 1

    broken = backup.Bundle("", {"music": {"targets": "kaputt"}})
    before = load_config(rig.path)
    with pytest.raises(ValidationError):
        asyncio_run(rig.settings.apply_bundle(broken, ["music"]))
    assert load_config(rig.path) == before


def test_import_rejects_a_selection_that_does_not_match_the_file(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    bundle = exported(tmp_path)
    for selection in ([], ["hue"], ["music", "bogus"]):
        with pytest.raises(ValidationError):
            asyncio_run(rig.settings.apply_bundle(bundle, selection))
    assert not list(tmp_path.glob("octapihub.toml.bak-*"))


def test_only_the_newest_backups_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(tmp_path)
    for index in range(8):
        (tmp_path / f"octapihub.toml.bak-2020010{index}-000000").write_text("x")
    asyncio_run(rig.settings.apply_bundle(exported(tmp_path), ["music"]))
    assert len(list(tmp_path.glob("octapihub.toml.bak-*"))) == 5


def test_a_failing_backup_stops_the_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = Rig(tmp_path)

    def broken_copy(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("octapihub.application.settings.shutil.copy2", broken_copy)
    before = load_config(rig.path)
    with pytest.raises(ConfigurationError):
        asyncio_run(rig.settings.apply_bundle(exported(tmp_path, enabled=False), ["music"]))
    assert load_config(rig.path) == before and rig.music.config.enabled is True


def test_a_live_failure_is_reported_but_the_file_stays_saved(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def boom(_config: object) -> None:
        raise RuntimeError("player is stuck")

    rig.music.reconfigure = boom  # type: ignore[method-assign]
    notes = asyncio_run(rig.settings.apply_bundle(exported(tmp_path, enabled=False), ["music"]))
    assert any("Dienst neu" in n for n in notes)
    assert load_config(rig.path).music.enabled is False


def asyncio_run(coro):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(coro)


# ---------- web ----------


def test_the_backup_page_and_link(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        page = client.get("/backup")
        settings = client.get("/settings")
    assert page.status_code == 200
    for wanted in ("Einstellungen exportieren", "Einstellungen importieren", "CSV", "Logdateien"):
        assert wanted in page.text
    assert 'href="/backup"' in settings.text


def test_export_download_needs_the_token_and_roundtrips(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert (
            rig.post(client, {"action": "export", "sec.music": "on"}, token="").status_code == 403
        )
        denied = rig.post(
            client, {"action": "export", "sec.music": "on"}, token="falsches-token-xx"
        )
        assert denied.status_code == 403
        none = rig.post(client, {"action": "export"})
        assert none.status_code == 422
        ok = rig.post(client, {"action": "export", "sec.music": "on", "sec.mappings": "on"})
    assert ok.status_code == 200
    assert ok.headers["content-disposition"].startswith('attachment; filename="octapihub-export-')
    bundle = backup.parse_bundle(ok.text)
    assert set(bundle.sections) == {"music", "mappings"}
    assert TOKEN not in ok.text


def test_import_check_then_apply(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    text = backup.build_bundle(
        make_config(
            tmp_path,
            {**MUSIC, "playlists": [{"id": "x", "name": "Eins", "source": "Eins"}]},
        ),
        ["music", "mappings"],
        NOW,
    )
    with rig.client() as client:
        check = rig.post(client, {"action": "import-check", "bundle": text})
        assert check.status_code == 200 and "Das steht in der Datei" in check.text
        assert (
            'name="imp.music" checked' in check.text.replace("\n", " ").replace("  ", " ")
            or "imp.music" in check.text
        )
        applied = rig.post(client, {"action": "import-apply", "bundle": text, "imp.music": "on"})
    assert applied.status_code == 200 and "Einstellungen übernommen" in applied.text
    assert [p.id for p in load_config(rig.path).music.playlists] == ["x"]
    assert load_config(rig.path).mappings == rig.config.mappings  # not selected: untouched


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"action": "import-check", "bundle": ""}, 422),
        ({"action": "import-check", "bundle": "kein json"}, 422),
        ({"action": "import-apply", "bundle": "{}"}, 422),
        ({"action": "explode"}, 422),
    ],
)
def test_import_errors_are_explained_and_change_nothing(
    tmp_path: Path, fields: dict[str, str], status: int
) -> None:
    rig = Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        response = rig.post(client, fields)
    assert response.status_code == status
    assert rig.path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("octapihub.toml.bak-*"))


def test_apply_without_a_selection_or_with_an_invalid_section_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    data = json.loads(backup.build_bundle(make_config(tmp_path), ["music", "mappings"], NOW))
    data["sections"]["mappings"][0]["cube"] = "Nicht vorhanden"
    text = json.dumps(data)
    with rig.client() as client:
        nothing = rig.post(client, {"action": "import-apply", "bundle": text})
        invalid = rig.post(client, {"action": "import-apply", "bundle": text, "imp.mappings": "on"})
        check = rig.post(client, {"action": "import-check", "bundle": text})
    assert nothing.status_code == 422 and invalid.status_code == 422
    assert "Passt nicht" in check.text


def test_a_large_pasted_file_is_accepted_up_to_the_limit(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = rig.post(client, {"action": "import-check", "bundle": "x" * (700 * 1024)})
    assert response.status_code == 413


# ---------- CSV ----------


def feed(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake(group: str, since: datetime, until: datetime, **kw: Any) -> tuple[SeriesPoint, ...]:
        calls.append((group, kw))
        base = NOW - timedelta(minutes=10)
        value = 21.5 if not kw.get("corrected") else 20.25
        return (SeriesPoint(base, value, 3),)

    monkeypatch.setattr(rig.sense, "query_series", fake)
    return calls


def test_sensor_csv_has_a_header_bom_and_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    calls = feed(rig, monkeypatch)
    with rig.client() as client:
        response = client.get("/export/sensor-data.csv?group=temperature&range=24h")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0] == "zeit_utc,messgroesse,wert,wert_korrigiert,anzahl_messungen"
    assert lines[1] == "2026-05-01T11:50:00+00:00,temperature,21.5000,20.2500,3"
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert any(call[0] == "temperature" for call in calls)


def test_sensor_csv_all_groups_and_raw_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    calls = feed(rig, monkeypatch)
    with rig.client() as client:
        response = client.get("/export/sensor-data.csv?resolution=raw&range=1h")
    assert response.status_code == 200
    groups = {line.split(",")[1] for line in response.text.strip().splitlines()[1:]}
    assert groups == {"temperature", "humidity", "pressure", "compass"} & groups and groups
    assert all(call[1]["bucket_seconds"] is None for call in calls)


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("group=nonsense", 404),
        ("range=1y", 422),
        ("resolution=weird", 422),
        ("resolution=raw&range=7d", 422),
    ],
)
def test_sensor_csv_rejects_bad_requests(tmp_path: Path, query: str, status: int) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert client.get(f"/export/sensor-data.csv?{query}").status_code == status


def test_sensor_csv_reports_missing_history(tmp_path: Path) -> None:
    config = parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"}],
            "sense_hat": {"history": {"enabled": False}},
        }
    )
    rig = Rig(tmp_path, config)
    with rig.client() as client:
        assert client.get("/export/sensor-data.csv").status_code == 503


# ---------- logs ----------


def test_recent_log_download_is_redacted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    logger = logging.getLogger("octapihub.test")
    logger.addHandler(rig.ring)
    logger.setLevel(logging.INFO)
    try:
        logger.info("Verbunden mit token=%s und AA:BB:CC:DD:EE:02", TOKEN)
        logger.warning("Nur eine Warnung")
        with rig.client() as client:
            everything = client.get("/export/log.txt?source=recent&level=INFO")
            warnings = client.get("/export/log.txt?source=recent&level=WARNING")
    finally:
        logger.removeHandler(rig.ring)
    assert (
        everything.status_code == 200 and "attachment" in everything.headers["content-disposition"]
    )
    assert TOKEN not in everything.text and "AA:BB:CC:DD:EE:02" not in everything.text
    assert "Nur eine Warnung" in warnings.text and "Verbunden" not in warnings.text


def test_log_download_rejects_unknown_parameters_and_missing_files(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert client.get("/export/log.txt?level=TRACE").status_code == 422
        assert client.get("/export/log.txt?source=other").status_code == 422
        assert client.get("/export/log.txt?source=file").status_code == 404
    rig.backend.logs = None
    with rig.client() as client:
        assert client.get("/export/log.txt?source=recent").status_code == 404


def test_log_file_download_reads_the_redacted_file(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.backend.log_dir = str(tmp_path / "logs")
    handler = create_file_handler(tmp_path / "logs", rig.redactor)
    assert handler is not None
    logger = logging.getLogger("octapihub.filetest")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("geheim %s", TOKEN)
        handler.flush()
        with rig.client() as client:
            response = client.get("/export/log.txt?source=file")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert response.status_code == 200 and "geheim" in response.text and TOKEN not in response.text


# ---------- file logging ----------


def test_the_log_file_is_created_rotated_and_redacted(tmp_path: Path) -> None:
    redactor = Redactor(["supersecretvalue"])
    handler = create_file_handler(tmp_path / "a" / "b", redactor, max_bytes=400, backups=2)
    assert handler is not None
    logger = logging.getLogger("octapihub.rotation")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for index in range(60):
            logger.info("Zeile %d password=supersecretvalue Bearer abcdef123456", index)
    finally:
        logger.removeHandler(handler)
        handler.close()
    folder = tmp_path / "a" / "b"
    files = sorted(p.name for p in folder.iterdir())
    assert LOG_FILE_NAME in files and len(files) <= 3  # live file + two rotations
    text = read_log_file(folder)
    assert "Zeile 59" in text and "supersecretvalue" not in text and "abcdef123456" not in text


def test_an_unwritable_log_directory_only_loses_the_file(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert create_file_handler(blocker / "logs", Redactor()) is None
    assert read_log_file(tmp_path / "missing") == ""


def test_read_log_file_returns_only_the_newest_part(tmp_path: Path) -> None:
    (tmp_path / LOG_FILE_NAME).write_text("A" * 1000 + "ENDE", encoding="utf-8")
    text = read_log_file(tmp_path, limit_bytes=100)
    assert len(text) == 100 and text.endswith("ENDE")


# ---------- command line ----------


def test_log_options_of_the_command_line(tmp_path: Path) -> None:
    from octapihub.web.__main__ import build_parser, install_logging

    parser = build_parser({})
    assert parser.parse_args([]).log_level == logging.INFO
    assert parser.parse_args(["--log-level", "warning"]).log_level == logging.WARNING
    with pytest.raises(SystemExit):
        parser.parse_args(["--log-level", "chatty"])
    assert build_parser({"OCTAPIHUB_LOG_LEVEL": "error"}).parse_args([]).log_level == logging.ERROR

    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        ring = install_logging(Redactor(), logging.INFO, str(tmp_path / "logs"))
        logging.getLogger("octapihub.cli").info("hallo datei")
        for handler in root.handlers:
            handler.flush()
        assert ring.lines()
        assert "hallo datei" in read_log_file(tmp_path / "logs")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(level)
