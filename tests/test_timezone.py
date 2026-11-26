from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from octapihub.application.matrix.builder import DisplayStateBuilder
from octapihub.application.matrix.views import VIEWS, ViewContext, ViewUi
from octapihub.core.config import load_config, parse_config, save_config, with_timezone
from octapihub.core.errors import ValidationError
from octapihub.core.timezone import (
    LiveTimeZone,
    TimeSettings,
    detect_system_name,
    is_valid_name,
    known_names,
    resolve,
)
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend
from octapihub.web.presenter import build_system_items

NOON_UTC = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)  # summer: Zurich is UTC+2


# ---------- names and detection ----------


@pytest.mark.parametrize("name", ["Europe/Zurich", "UTC", "America/New_York", "Asia/Kolkata"])
def test_known_zones_are_valid(name: str) -> None:
    assert is_valid_name(name)


@pytest.mark.parametrize(
    "name", ["", "Mars/Olympus", "../etc/passwd", "Europe/Zurich\n", "a" * 80, "Europe Zurich"]
)
def test_other_names_are_invalid(name: str) -> None:
    assert not is_valid_name(name)


def test_the_selection_list_is_sorted_and_contains_common_zones() -> None:
    names = known_names()
    assert names == sorted(names) and "Europe/Zurich" in names and len(names) > 100


def test_detection_prefers_the_environment_then_the_files(tmp_path: Path) -> None:
    file = tmp_path / "timezone"
    file.write_text("Europe/Berlin\n", encoding="utf-8")
    nothing = tmp_path / "no-link"
    assert detect_system_name({"TZ": ":Asia/Tokyo"}, file, nothing) == "Asia/Tokyo"
    assert detect_system_name({}, file, nothing) == "Europe/Berlin"
    assert detect_system_name({"TZ": "Mars/Base"}, file, nothing) == "Europe/Berlin"
    file.write_text("Mars/Base\n", encoding="utf-8")
    assert detect_system_name({}, file, nothing) == ""
    assert detect_system_name({}, tmp_path / "missing", nothing) == ""


@pytest.mark.skipif(os.name != "posix", reason="symbolic links need POSIX")
def test_detection_follows_the_localtime_link(tmp_path: Path) -> None:
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/Europe/Zurich")
    assert detect_system_name({}, tmp_path / "missing", link) == "Europe/Zurich"


# ---------- resolve and live settings ----------


def test_resolve_uses_the_setting_then_the_system_then_utc() -> None:
    assert resolve("Europe/Zurich")[0::2] == ("Europe/Zurich", "setting")
    name, _zone, source = resolve("", lambda: "Europe/Berlin")
    assert (name, source) == ("Europe/Berlin", "auto")
    name, zone, source = resolve("", lambda: "")
    assert (name, zone, source) == ("UTC", UTC, "fallback")
    name, _zone, source = resolve("Mars/Base", lambda: "Asia/Tokyo")  # unknown setting: system
    assert (name, source) == ("Asia/Tokyo", "auto")


def test_settings_can_change_while_running() -> None:
    settings = TimeSettings("Europe/Zurich")
    assert settings.now().utcoffset() is not None
    assert (settings.name, settings.source) == ("Europe/Zurich", "setting")
    settings.apply("")
    assert settings.source in {"auto", "fallback"}
    settings.apply("Asia/Tokyo")
    assert NOON_UTC.astimezone(settings.zone).hour == 21


def test_the_live_zone_follows_the_setting_for_every_conversion() -> None:
    settings = TimeSettings("Europe/Zurich")
    live = LiveTimeZone(settings)
    assert NOON_UTC.astimezone(live).strftime("%H:%M") == "14:00"  # summer time
    assert NOON_UTC.astimezone(live).tzname() == "CEST"
    settings.apply("America/New_York")
    assert NOON_UTC.astimezone(live).strftime("%H:%M") == "08:00"
    winter = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    settings.apply("Europe/Zurich")
    assert winter.astimezone(live).strftime("%H:%M") == "13:00"  # winter time
    assert "Europe/Zurich" in repr(live)


# ---------- configuration ----------


def base(**extra: object):  # type: ignore[no-untyped-def]
    return parse_config(
        {
            "schema_version": 7,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
            **extra,
        }
    )


def test_the_zone_setting_round_trips_and_is_omitted_when_automatic(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    save_config(path, base())
    assert "timezone" not in path.read_text(encoding="utf-8") and load_config(path).timezone == ""
    save_config(path, base(timezone=" Europe/Zurich "))
    assert 'timezone = "Europe/Zurich"' in path.read_text(encoding="utf-8")
    assert load_config(path).timezone == "Europe/Zurich"


@pytest.mark.parametrize("value", ["Mars/Base", 5, "a\nb", "../x", True])
def test_invalid_zone_settings_are_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        base(timezone=value)
    with pytest.raises(ValidationError):
        with_timezone(base(), value)


def test_old_files_without_the_setting_still_load() -> None:
    old = parse_config(
        {
            "schema_version": 6,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
        }
    )
    assert old.timezone == ""


# ---------- applying the setting ----------


def test_the_manager_saves_and_applies_the_zone(tmp_path: Path) -> None:
    backend = build_demo_backend()
    settings = backend.settings
    path = Path(settings._path)
    asyncio.run(settings.apply_timezone("Asia/Tokyo"))
    assert load_config(path).timezone == "Asia/Tokyo" and backend.time.name == "Asia/Tokyo"
    assert NOON_UTC.astimezone(backend.tz).hour == 21  # the interface follows at once
    with pytest.raises(ValidationError):
        asyncio.run(settings.apply_timezone("Mars/Base"))
    assert load_config(path).timezone == "Asia/Tokyo" and backend.time.name == "Asia/Tokyo"
    asyncio.run(settings.apply_timezone(""))
    assert load_config(path).timezone == "" and backend.time.source in {"auto", "fallback"}


# ---------- web ----------


def post(client: TestClient, zone: str, token: str = DEMO_TOKEN):  # type: ignore[no-untyped-def]
    data = {"timezone": zone}
    if token:
        data["token"] = token
    return client.post("/settings/time", data=data)


def test_the_settings_page_shows_and_changes_the_zone() -> None:
    backend = build_demo_backend()
    backend.clock = lambda: NOON_UTC
    with TestClient(create_app(backend, manage_service=True)) as client:
        page = client.get("/settings").text
        assert "Uhrzeit und Zeitzone" in page and 'id="zone-list"' in page
        assert (
            "Europe/Zurich" in page and "automatisch vom Gerät" in page or "unbekannt (UTC)" in page
        )
        saved = post(client, "Europe/Zurich")
        assert saved.status_code == 200 and "Zeitzone gespeichert" in saved.text
        assert "01.07.2026 14:00:00" in saved.text and "eingestellt" in saved.text
        assert post(client, "Asia/Tokyo").status_code == 200
        assert "01.07.2026 21:00:00" in client.get("/").text  # the footer time follows
        bad = post(client, "Mars/Base")
        assert bad.status_code == 422 and "Diese Zeitzone gibt es nicht" in bad.text
        assert "01.07.2026 21:00:00" in client.get("/").text  # unchanged
        assert post(client, "Europe/Zurich", token="").status_code == 403
        automatic = post(client, "")
        assert automatic.status_code == 200


def test_an_unknown_system_zone_is_reported_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = build_demo_backend()
    backend.time = TimeSettings("", detect=lambda: "")
    with TestClient(create_app(backend, manage_service=True)) as client:
        backend.settings._time = backend.time
        answer = post(client, "")
        assert answer.status_code == 200 and "Zeitzone des Geräts unbekannt" in answer.text


# ---------- clock synchronisation ----------


def make_reader(tmp_path: Path, answer: str | Exception) -> SystemInfoReader:
    def runner(command: object) -> str:
        if isinstance(command, list) and command[0] == "timedatectl":
            if isinstance(answer, Exception):
                raise answer
            return answer
        return "throttled=0x0\n"

    return SystemInfoReader(tmp_path, proc=tmp_path, thermal_file=tmp_path / "none", runner=runner)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("yes\n", True),
        ("no\n", False),
        ("YES", True),
        ("", None),
        ("maybe", None),
        (FileNotFoundError(), None),
    ],
)
def test_the_sync_state_is_read_from_the_system(
    tmp_path: Path, answer: str | Exception, expected: bool | None
) -> None:
    assert make_reader(tmp_path, answer).read().time_synced is expected


def test_the_time_item_explains_each_sync_state(tmp_path: Path) -> None:
    zone = ZoneInfo("Europe/Zurich")
    synced = build_system_items(make_reader(tmp_path, "yes").read(), zone, "Europe/Zurich")[0]
    assert (
        synced.level == "ok"
        and "automatisch gestellt" in synced.detail
        and "Europe/Zurich" in synced.detail
    )
    unsynced = build_system_items(make_reader(tmp_path, "no").read(), zone, "Europe/Zurich")[0]
    assert unsynced.level == "warn" and "noch nicht" in unsynced.detail
    unknown = build_system_items(make_reader(tmp_path, FileNotFoundError()).read(), zone)[0]
    assert unknown.level == "ok" and "Falsche Zeit?" in unknown.detail


# ---------- LED clock and night window ----------


def test_the_led_clock_shows_local_time_not_utc() -> None:
    from octapihub.application.matrix.state import DisplayState

    settings = TimeSettings("Asia/Tokyo")  # UTC+9
    state = DisplayState(now=NOON_UTC.astimezone(settings.zone))
    ctx = ViewContext(state, ViewUi(), 0.0)
    assert VIEWS["clock"].describe(state) == "21:00" and len(VIEWS["clock"].render(ctx)) == 64
    assert VIEWS["clock_binary"].describe(state).endswith("21:00")


def test_the_builder_uses_the_configured_clock(tmp_path: Path) -> None:
    from octapihub.application.sense_hat_service import SenseHatService
    from octapihub.core.config import SenseHatConfig
    from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
    from octapihub.infrastructure.sense_hat import SenseHatAdapter
    from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

    sense = SenseHatService(
        SenseHatConfig(),
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda _c: SQLiteSenseHatHistory(str(tmp_path / "h.sqlite3")),
    )
    settings = TimeSettings("Asia/Tokyo")
    builder = DisplayStateBuilder(lambda: (), sense, clock=settings.now)
    assert builder.build().now.tzinfo is settings.zone
    settings.apply("UTC")
    assert builder.build().now.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
