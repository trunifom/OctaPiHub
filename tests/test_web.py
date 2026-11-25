from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.sense_hat_worker import (
    SenseHatSample,
    SensorGroupState,
    SensorGroupStatus,
)
from octapihub.application.settings import SettingsManager
from octapihub.core.config import (
    AppConfig,
    SenseHatConfig,
    load_config,
    parse_config,
    save_config,
    with_sense_hat_table,
)
from octapihub.core.errors import SenseHatError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import forms
from octapihub.web.app import _PACKAGE_DIR, LiveView, WebBackend, create_app
from octapihub.web.presenter import (
    build_group_view,
    build_notices,
    format_number,
    overall_level,
)

TOKEN = "s3cret-token"
UTC_NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _base_config(tmp_path: Path) -> AppConfig:
    return parse_config(
        {
            "schema_version": 3,
            "bluetooth": {"adapter": "hci0"},
            "debounce_ms": 300,
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "sense_hat": {"history": {"database_path": str(tmp_path / "history.sqlite3")}},
        }
    )


def _backend(tmp_path: Path, *, token: str | None = TOKEN, board: bool = True) -> WebBackend:
    config = _base_config(tmp_path)
    path = tmp_path / "octapihub.toml"
    save_config(path, config)

    def make_port(_settings: SenseHatConfig) -> SenseHatAdapter:
        if not board:
            raise SenseHatError("no board")
        return SenseHatAdapter(FakeSenseHat())

    service = SenseHatService(
        config.sense_hat,
        make_port,
        lambda settings: SQLiteSenseHatHistory(settings.history.database_path),
    )
    return WebBackend(SettingsManager(path, config, service), service, token, clock=lambda: UTC_NOW)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    backend = _backend(tmp_path)
    with TestClient(create_app(backend, manage_service=True)) as test_client:
        _wait_for_values(backend)
        yield test_client


def _wait_for_values(backend: WebBackend) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        statuses = backend.sense_hat.statuses
        if statuses and all(s.state is SensorGroupState.OK for s in statuses):
            return
        time.sleep(0.01)
    raise AssertionError("sensors never became ready")


# ---------- presenter ----------


def test_format_number_rounds_only_text_and_avoids_negative_zero() -> None:
    assert format_number(21.456, 1) == "21.5"
    assert format_number(21.456, 0) == "21"
    assert format_number(-0.04, 1) == "0.0"
    assert format_number(-1.26, 1) == "-1.3"


def _status(
    group: str, state: SensorGroupState, sample: SenseHatSample | None = None
) -> SensorGroupStatus:
    error = "Das Sense HAT konnte nicht angesprochen werden." if state.value == "error" else None
    return SensorGroupStatus(group, state, sample, error, 1 if error else 0, "CH-SENSE-001")


def test_temperature_card_shows_corrected_value_with_raw_beside_it() -> None:
    config = with_sense_hat_table(
        parse_config(
            {
                "schema_version": 3,
                "bluetooth": {"adapter": "hci0"},
                "debounce_ms": 300,
                "cubes": [
                    {"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                    {"name": "B", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
                ],
            }
        ),
        {"temperature_offset_c": -3.0},
    ).sense_hat
    sample = SenseHatSample(
        "temperature",
        SenseHatReading(SenseHatMetric.TEMPERATURE, 24.456, "°C"),
        UTC_NOW,
        21.456,
    )
    view = build_group_view(
        "temperature", _status("temperature", SensorGroupState.OK, sample), config, UTC
    )
    assert (view.primary, view.unit) == ("21.5", "°C")
    assert view.secondary == "Rohwert 24.5 °C"
    assert view.state == "ok"


def test_failed_sensor_without_value_says_value_not_available() -> None:
    config = SenseHatConfig()
    view = build_group_view("humidity", _status("humidity", SensorGroupState.ERROR), config, UTC)
    assert view.state == "error"
    assert view.primary == "–"
    assert view.error_code == "CH-SENSE-001"


def test_failed_sensor_with_old_value_is_marked_stale() -> None:
    config = SenseHatConfig()
    sample = SenseHatSample(
        "humidity", SenseHatReading(SenseHatMetric.HUMIDITY, 40.0, "%RH"), UTC_NOW
    )
    view = build_group_view(
        "humidity", _status("humidity", SensorGroupState.ERROR, sample), config, UTC
    )
    assert view.state == "stale"
    assert view.primary == "40.0"
    assert view.unit == "%"


def test_notices_rank_hardware_failure_above_everything() -> None:
    notices = build_notices(
        SenseHatConfig(),
        (),
        hardware_error="Nicht erreichbar.",
        hardware_error_code="CH-SENSE-001",
        history_error=None,
        history_error_code=None,
        running=False,
    )
    assert overall_level(notices) == "error"
    assert notices[0].code == "CH-SENSE-001"
    assert "I2C" in notices[0].hint


# ---------- forms ----------


def test_form_round_trip_reproduces_the_saved_config(tmp_path: Path) -> None:
    current = _base_config(tmp_path).sense_hat
    result = forms.parse_form(forms.config_to_form(current), current)
    assert result.errors == {}
    assert result.table is not None
    rebuilt = with_sense_hat_table(_base_config(tmp_path), result.table).sense_hat
    assert rebuilt == current


def test_form_reports_german_errors_per_field(tmp_path: Path) -> None:
    current = _base_config(tmp_path).sense_hat
    values = forms.config_to_form(current)
    values["sensor.temperature.interval_seconds"] = "0"
    values["history.retention_days"] = "viele"
    values["temperature_correction_factor"] = "-2"
    values["matrix.rotation"] = "45"
    result = forms.parse_form(values, current)
    assert result.table is None
    assert set(result.errors) == {
        "sensor.temperature.interval_seconds",
        "history.retention_days",
        "temperature_correction_factor",
        "matrix.rotation",
    }
    assert "zwischen 1 und 3600" in result.errors["sensor.temperature.interval_seconds"]


def test_form_accepts_decimal_comma(tmp_path: Path) -> None:
    current = _base_config(tmp_path).sense_hat
    values = forms.config_to_form(current)
    values["temperature_offset_c"] = "-1,5"
    result = forms.parse_form(values, current)
    assert result.table is not None
    assert result.table["temperature_offset_c"] == -1.5


# ---------- pages ----------


def test_pages_render_in_german_with_security_headers(client: TestClient) -> None:
    for path, expected in (
        ("/", "Übersicht"),
        ("/sense-hat", "Temperatur"),
        ("/settings", "Einstellungen"),
    ):
        response = client.get(path)
        assert response.status_code == 200
        assert expected in response.text
        assert response.headers["content-security-policy"].startswith("default-src 'self'")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    assert "<script>" not in client.get("/sense-hat").text  # no inline scripts (CSP)


def test_sense_hat_page_shows_live_values_and_chart_controls(client: TestClient) -> None:
    text = client.get("/sense-hat").text
    assert "21.1" in text  # temperature 21.123456 shown with precision 1
    assert "45.7" in text
    assert "Luftdruck" in text
    assert 'id="chart"' in text


def test_unknown_page_gets_friendly_error_with_code(client: TestClient) -> None:
    response = client.get("/gibt-es-nicht")
    assert response.status_code == 404
    assert "CH-WEB-001" in response.text
    assert "Traceback" not in response.text


def test_missing_board_is_explained_not_crashing(tmp_path: Path) -> None:
    backend = _backend(tmp_path, board=False)
    with TestClient(create_app(backend, manage_service=True)) as client:
        page = client.get("/sense-hat")
        assert page.status_code == 200
        assert "nicht erreichbar" in page.text
        assert "CH-SENSE-001" in page.text
        assert "I2C" in page.text
        assert client.get("/").status_code == 200


# ---------- series API ----------


def test_series_returns_history_and_validates_input(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    store = SQLiteSenseHatHistory(backend.settings.config.sense_hat.history.database_path)
    store.append_many(
        [
            SenseHatSample(
                "temperature",
                SenseHatReading(SenseHatMetric.TEMPERATURE, 20.0 + i, "°C"),
                UTC_NOW - timedelta(minutes=i),
                18.0 + i,
            )
            for i in range(5)
        ]
    )
    with TestClient(create_app(backend, manage_service=True)) as client:
        corrected = client.get("/api/sense-hat/series/temperature?range=1h").json()
        raw = client.get("/api/sense-hat/series/temperature?range=1h&basis=raw").json()
        assert corrected["available"] is True
        assert corrected["basis"] == "corrected"
        assert raw["basis"] == "raw"
        assert [
            round(r["v"] - c["v"]) for r, c in zip(raw["points"], corrected["points"], strict=True)
        ] == [2] * 5
        assert corrected["unit"] == "°C"
        assert client.get("/api/sense-hat/series/orientation").status_code == 404
        assert client.get("/api/sense-hat/series/temperature?range=5y").status_code == 422


def test_series_explains_disabled_history(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    table = {**_table(backend), "history": {**_history(backend), "enabled": False}}
    asyncio.run(backend.settings.apply_sense_hat(table))
    with TestClient(create_app(backend)) as client:
        data = client.get("/api/sense-hat/series/temperature").json()
        assert data["available"] is False
        assert data["reason"] == "history_disabled"


def _table(backend: WebBackend) -> dict[str, object]:
    from octapihub.core.config import sense_hat_table

    return sense_hat_table(backend.settings.config.sense_hat)


def _history(backend: WebBackend) -> dict[str, object]:
    history = _table(backend)["history"]
    assert isinstance(history, dict)
    return dict(history)


# ---------- saving settings ----------


def _post(client: TestClient, values: dict[str, str], token: str | None = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(values)
    if token is not None:
        body["token"] = token
    return client.post(
        "/settings/sense-hat",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def test_saving_without_configured_token_is_refused(tmp_path: Path) -> None:
    backend = _backend(tmp_path, token=None)
    with TestClient(create_app(backend)) as client:
        values = forms.config_to_form(backend.settings.config.sense_hat)
        response = _post(client, values, token="anything")
        assert response.status_code == 403
        assert "CH-WEB-003" in response.text
        assert "disabled" in client.get("/settings").text


def test_wrong_token_saves_nothing_and_is_not_echoed(client: TestClient, tmp_path: Path) -> None:
    before = (tmp_path / "octapihub.toml").read_text(encoding="utf-8")
    values = forms.config_to_form(_base_config(tmp_path).sense_hat)
    values["temperature_offset_c"] = "-9"
    response = _post(client, values, token="zzz-bad-token")
    assert response.status_code == 403
    assert "CH-WEB-004" in response.text
    assert "zzz-bad-token" not in response.text
    assert TOKEN not in response.text
    assert (tmp_path / "octapihub.toml").read_text(encoding="utf-8") == before


def test_invalid_values_show_field_errors_and_save_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    before = (tmp_path / "octapihub.toml").read_text(encoding="utf-8")
    values = forms.config_to_form(_base_config(tmp_path).sense_hat)
    values["sensor.humidity.interval_seconds"] = "99999"
    response = _post(client, values)
    assert response.status_code == 422
    assert "Bitte korrigieren Sie die markierten Felder" in response.text
    assert 'aria-invalid="true"' in response.text
    assert "99999" in response.text  # the user's input stays in the form
    assert (tmp_path / "octapihub.toml").read_text(encoding="utf-8") == before


def test_valid_save_persists_and_applies_to_running_service(
    client: TestClient, tmp_path: Path
) -> None:
    values = forms.config_to_form(_base_config(tmp_path).sense_hat)
    values["sensor.temperature.precision"] = "2"
    values["sensor.compass.enabled"] = "on"
    values["sensor.compass.presentation"] = "value"
    values["temperature_offset_c"] = "-1.5"
    response = _post(client, values)
    assert response.status_code == 200
    assert "Gespeichert und angewendet" in response.text

    saved = load_config(tmp_path / "octapihub.toml").sense_hat
    assert saved.sensors["temperature"].precision == 2
    assert saved.sensors["compass"].enabled
    assert saved.temperature_offset_c == -1.5
    assert saved.sensors["humidity"].enabled  # untouched settings survive

    deadline = time.monotonic() + 3
    page = ""
    while time.monotonic() < deadline:
        page = client.get("/sense-hat").text
        if "Kompass" in page and "21.12" in page:
            break
        time.sleep(0.02)
    assert "Kompass" in page
    assert "21.12" in page  # new precision applied to the same raw value


# ---------- live stream ----------


async def _collect(live: LiveView, count: int) -> list[str]:
    async def connected() -> bool:
        return False

    return [
        message
        async for message in live.stream(connected, interval=0.01, max_events=count)
        if message.startswith("event:")
    ]


def test_live_stream_pushes_rendered_fragments(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    live = LiveView(backend, Jinja2Templates(directory=str(_PACKAGE_DIR / "templates")))

    async def scenario() -> list[str]:
        await backend.sense_hat.start()
        try:
            await asyncio.sleep(0.15)
            return await _collect(live, 1)
        finally:
            await backend.sense_hat.stop()

    (message,) = asyncio.run(scenario())
    payload = json.loads(message.split("data: ", 1)[1])
    assert set(payload) >= {"live-cards", "live-notices", "live-badge", "live-mini"}
    assert "Temperatur" in payload["live-cards"]
    assert "In Ordnung" in payload["live-badge"]


# ---------- cubes page ----------


def _cube_config(tmp_path: Path) -> AppConfig:
    base = _base_config(tmp_path)
    return parse_config(
        {
            "schema_version": 3,
            "bluetooth": {"adapter": "hci0"},
            "debounce_ms": 300,
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 3,
                    "actions": [
                        {
                            "kind": "hue",
                            "action": "scene.activate",
                            "parameters": {"scene_id": "abend"},
                        }
                    ],
                }
            ],
            "sense_hat": {"history": {"database_path": base.sense_hat.history.database_path}},
        }
    )


def test_cubes_page_without_cube_service_explains_and_lists_mapping(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.settings._config = _cube_config(tmp_path)
    with TestClient(create_app(backend)) as client:
        text = client.get("/cubes").text
        assert "nicht aktiv" in text
        assert "Szene aktivieren" in text
        assert "scene_id: abend" in text
        assert "AA:BB:CC" not in text  # addresses are never shown
        assert "nicht aktiv" in client.get("/").text


def test_cubes_page_shows_pipeline_error_with_code(tmp_path: Path) -> None:
    from octapihub.application.cube_service import CubeService
    from octapihub.core.errors import PluginError

    def no_plugin(_config: AppConfig) -> OutboxDispatcher:
        raise PluginError("missing hue plugin")

    backend = _backend(tmp_path)
    config = _cube_config(tmp_path)
    backend.settings._config = config
    backend.cubes = CubeService(config, lambda _c: pytest.fail("no runtime"), no_plugin)
    with TestClient(create_app(backend, manage_service=True)) as client:
        text = client.get("/cubes").text
        assert "nicht gestartet werden" in text
        assert "CH-PLUG" in text
        assert client.get("/healthz").json()["cubes_running"] is False


def test_cubes_page_shows_connected_cubes(tmp_path: Path) -> None:
    from octapihub.web.demo import _demo_cubes

    backend = _backend(tmp_path)
    config = _cube_config(tmp_path)
    backend.settings._config = config
    backend.cubes = _demo_cubes(config, tmp_path)
    with TestClient(create_app(backend, manage_service=True)) as client:
        deadline = time.monotonic() + 3
        text = ""
        while time.monotonic() < deadline:
            text = client.get("/cubes").text
            if text.count("Verbunden") >= 2:
                break
            time.sleep(0.02)
        assert text.count("Verbunden") >= 2
        assert "Noch keine Seite erkannt" in text
        assert "2 von 2 Würfeln verbunden" in client.get("/").text
