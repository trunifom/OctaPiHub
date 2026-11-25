from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application.matrix.views import VIEWS
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.composition import build_matrix_service
from octapihub.core.config import (
    MATRIX_VIEW_IDS,
    AppConfig,
    SenseHatConfig,
    load_config,
    parse_config,
    save_config,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import forms
from octapihub.web.app import WebBackend, create_app, display_stream
from octapihub.web.presenter import frame_to_fills, frame_to_hex

TOKEN = "display-token-123456"


def _config(tmp_path: Path, matrix: dict[str, object] | None = None) -> AppConfig:
    return parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "sense_hat": {
                "history": {"database_path": str(tmp_path / "h.sqlite3")},
                "matrix": {"brightness": 60, **(matrix or {})},
            },
        }
    )


class Rig:
    def __init__(
        self, tmp_path: Path, matrix: dict[str, object] | None = None, *, with_matrix: bool = True
    ) -> None:
        self.config = _config(tmp_path, matrix)
        self.path = tmp_path / "octapihub.toml"
        save_config(self.path, self.config)
        self.device = FakeSenseHat()
        self.sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(self.device),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.matrix = build_matrix_service(self.config, self.sense) if with_matrix else None
        self.settings = SettingsManager(self.path, self.config, self.sense, matrix=self.matrix)
        self.backend = WebBackend(self.settings, self.sense, TOKEN, matrix=self.matrix)

    def client(self) -> TestClient:
        return TestClient(create_app(self.backend, manage_service=True))

    def wait_running(self) -> None:
        assert self.matrix is not None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.matrix.status.state == "running" and self.device.calls:
                return
            time.sleep(0.01)
        raise AssertionError("matrix never started")


def post(
    client: TestClient, fields: dict[str, str], token: str | None = TOKEN, header: bool = False
):  # type: ignore[no-untyped-def]
    headers = {"content-type": "application/x-www-form-urlencoded"}
    body = dict(fields)
    if token is not None and header:
        headers["x-octapihub-token"] = token
    elif token is not None:
        body["token"] = token
    return client.post("/display/control", content=urlencode(body), headers=headers)


# ---------- page ----------


def test_page_shows_preview_gallery_legend_and_controls(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.wait_running()
        response = client.get("/display")
    text = response.text
    assert response.status_code == 200
    assert text.count("<circle") >= 64 * (1 + len(MATRIX_VIEW_IDS))  # live grid + a thumbnail each
    assert text.count("data-view-card=") == len(MATRIX_VIEW_IDS)
    for view in VIEWS.values():
        assert view.label in text
    assert 'id="led-live"' in text
    assert "So bedienen Sie den Joystick" in text
    assert "Gedrückt halten" in text
    assert 'data-joy="left:press"' in text
    assert 'data-joy="middle:long"' in text
    assert "Läuft" in text
    assert "<script>" not in text  # no inline scripts (CSP)


def test_only_enabled_views_offer_a_show_button(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {"views": ["overview", "clock"], "default_view": "overview"})
    with rig.client() as client:
        rig.wait_running()
        text = client.get("/display").text
    assert 'data-view="overview"' in text
    assert 'data-view="clock"' in text
    assert 'data-view="rainbow"' not in text
    assert "Nicht aktiviert" in text


def test_page_without_matrix_service_explains(tmp_path: Path) -> None:
    rig = Rig(tmp_path, with_matrix=False)
    with rig.client() as client:
        response = client.get("/display")
        assert response.status_code == 200
        assert "nicht aktiv" in response.text
        assert post(client, {"action": "power", "value": "off"}).status_code == 409


def test_page_explains_a_missing_board_but_still_previews(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    def no_board(_c: SenseHatConfig) -> SenseHatAdapter:
        from octapihub.core.errors import SenseHatError

        raise SenseHatError("no board")

    rig.sense._port_factory = no_board
    with rig.client() as client:
        deadline = time.monotonic() + 3
        while (
            rig.matrix and rig.matrix.status.state != "unavailable" and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        text = client.get("/display").text
    assert "Sense HAT ist nicht erreichbar" in text
    assert "CH-SENSE-001" in text
    assert text.count("<circle") >= 64


def test_disabled_matrix_is_explained_and_not_controllable(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {"enabled": False})
    with rig.client() as client:
        text = client.get("/display").text
        assert "ausgeschaltet" in text
        response = post(client, {"action": "view", "value": "clock"})
    assert response.status_code == 409
    assert response.json()["code"] == "CH-WEB-009"


# ---------- control endpoint ----------


def test_controls_change_view_brightness_and_power(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {"views": ["overview", "clock", "lamp"], "default_view": "overview"})
    with rig.client() as client:
        rig.wait_running()
        shown = post(client, {"action": "view", "value": "clock"})
        assert shown.json() == {
            "ok": True,
            "view": "clock",
            "label": "Uhr",
            "display_on": True,
            "brightness": 60,
        }
        bright = post(client, {"action": "brightness", "value": "90"}, header=True)
        assert bright.json()["brightness"] == 90
        off = post(client, {"action": "power", "value": "off"})
        assert off.json()["display_on"] is False
        assert rig.matrix is not None
        assert rig.matrix.status.display_on is False
        assert post(client, {"action": "power", "value": "on"}).json()["display_on"] is True


def test_virtual_joystick_navigates_like_the_real_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {"views": ["overview", "cubes", "clock"], "default_view": "overview"})
    with rig.client() as client:
        rig.wait_running()
        assert (
            post(client, {"action": "joystick", "value": "right:press"}).json()["view"] == "cubes"
        )
        assert (
            post(client, {"action": "joystick", "value": "right:press"}).json()["view"] == "clock"
        )
        assert post(client, {"action": "joystick", "value": "left:press"}).json()["view"] == "cubes"
        up = post(client, {"action": "joystick", "value": "up:press"}).json()
        assert up["ok"]
        assert post(client, {"action": "joystick", "value": "middle:press"}).json()["ok"]
        long = post(client, {"action": "joystick", "value": "middle:long"}).json()
        assert long["display_on"] is False
        again = post(client, {"action": "joystick", "value": "middle:long"}).json()
        assert again["display_on"] is True


@pytest.mark.parametrize(
    ("fields", "status"),
    [
        ({"action": "view", "value": "nonexistent"}, 422),
        ({"action": "view", "value": "rainbow"}, 422),  # real view, but not enabled
        ({"action": "brightness", "value": "55"}, 422),
        ({"action": "brightness", "value": "abc"}, 422),
        ({"action": "brightness", "value": "-5"}, 422),
        ({"action": "power", "value": "maybe"}, 422),
        ({"action": "joystick", "value": "sideways:press"}, 422),
        ({"action": "joystick", "value": "up:smash"}, 422),
        ({"action": "joystick", "value": "up"}, 422),
        ({"action": "joystick", "value": "any:press"}, 422),
        ({"action": "reboot", "value": ""}, 422),
        ({}, 422),
    ],
)
def test_invalid_controls_are_rejected_without_changing_anything(
    tmp_path: Path, fields: dict[str, str], status: int
) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.wait_running()
        assert rig.matrix is not None
        before = (
            rig.matrix.status.view_id,
            rig.matrix.status.brightness,
            rig.matrix.status.display_on,
        )
        response = post(client, fields)
        assert response.status_code == status
        assert response.json()["code"] == "CH-WEB-010"
        after = (
            rig.matrix.status.view_id,
            rig.matrix.status.brightness,
            rig.matrix.status.display_on,
        )
    assert before == after


@pytest.mark.parametrize("token", [None, "", "wrong-zzz"])
def test_controls_need_the_token(tmp_path: Path, token: str | None) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.wait_running()
        response = post(client, {"action": "view", "value": "overview"}, token=token)
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "CH-WEB-004"
    assert "wrong-zzz" not in response.text


def test_controls_are_refused_without_a_configured_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.backend.token = None
    with rig.client() as client:
        response = post(client, {"action": "view", "value": "overview"}, token="anything")
    assert response.status_code == 403
    assert response.json()["code"] == "CH-WEB-003"


def test_oversized_control_requests_are_rejected(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        response = client.post(
            "/display/control",
            content="a=" + "x" * 70_000,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 413


# ---------- live stream ----------


async def _collect(backend: WebBackend, count: int) -> list[str]:
    async def connected() -> bool:
        return False

    return [
        message
        async for message in display_stream(backend, connected, interval=0.01, max_events=count)
        if message.startswith("event:")
    ]


def test_display_stream_pushes_frames_and_changes(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {"views": ["brightness", "overview"], "default_view": "brightness"})
    assert rig.matrix is not None

    async def scenario() -> list[str]:
        await rig.sense.start()
        await rig.matrix.start()  # type: ignore[union-attr]
        try:
            first = await _collect(rig.backend, 1)
            rig.matrix.show_view("overview")  # type: ignore[union-attr]
            await asyncio.sleep(0.05)
            second = await _collect(rig.backend, 1)
            return first + second
        finally:
            await rig.matrix.stop()  # type: ignore[union-attr]
            await rig.sense.stop()

    first, second = asyncio.run(scenario())
    one = json.loads(first.split("data: ", 1)[1])
    two = json.loads(second.split("data: ", 1)[1])
    assert one["view"] == "brightness"
    assert two["view"] == "overview"
    assert len(one["frame"]) == 384
    assert all(c in "0123456789abcdef" for c in one["frame"])
    assert {"label", "state", "on", "night", "brightness"} <= set(one)


def test_display_stream_without_matrix_sends_nothing_but_keeps_alive(tmp_path: Path) -> None:
    rig = Rig(tmp_path, with_matrix=False)

    async def scenario() -> list[str]:
        calls = {"n": 0}

        async def disconnected() -> bool:
            calls["n"] += 1
            return calls["n"] > 3

        return [m async for m in display_stream(rig.backend, disconnected, interval=0.01)]

    messages = asyncio.run(scenario())
    assert messages == ["retry: 3000\n\n"]


def test_frame_helpers() -> None:
    frame = tuple([(0, 0, 0)] * 63 + [(255, 128, 1)])
    assert frame_to_hex(frame) == "000000" * 63 + "ff8001"
    fills = frame_to_fills(frame)
    assert fills[:63] == [""] * 63
    assert fills[63] == "#ff8001"


# ---------- settings form ----------


def _form(rig: Rig, **changes: str) -> dict[str, str]:
    values = forms.config_to_form(rig.config.sense_hat)
    values.update(changes)
    return values


def test_matrix_form_round_trips_the_saved_settings(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    values = forms.config_to_form(rig.config.sense_hat)
    result = forms.parse_form(values, rig.config.sense_hat)
    assert result.errors == {}
    assert result.table is not None
    from octapihub.core.config import with_sense_hat_table

    assert with_sense_hat_table(rig.config, result.table).sense_hat == rig.config.sense_hat


def test_matrix_form_collects_views_in_canonical_order(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    values = {k: v for k, v in _form(rig).items() if not k.startswith("matrix.views.")}
    values["matrix.views.lamp"] = "on"
    values["matrix.views.overview"] = "on"
    values["matrix.views.clock"] = "on"
    values["matrix.default_view"] = "clock"
    result = forms.parse_form(values, rig.config.sense_hat)
    assert result.table is not None
    matrix = result.table["matrix"]
    assert isinstance(matrix, dict)
    assert matrix["views"] == ["overview", "clock", "lamp"]  # canonical, not click order


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"matrix.brightness": "55"}, "matrix.brightness"),
        ({"matrix.brightness": "abc"}, "matrix.brightness"),
        ({"matrix.auto_cycle_seconds": "7"}, "matrix.auto_cycle_seconds"),
        ({"matrix.night_start": "25:00"}, "matrix.night_start"),
        ({"matrix.night_end": ""}, "matrix.night_end"),
        ({"matrix.night_end": "7:00"}, "matrix.night_end"),
        ({"matrix.night_mode": "blink"}, "matrix.night_mode"),
        ({"matrix.default_view": "rainbow"}, "matrix.default_view"),
        ({"matrix.default_view": "nonexistent"}, "matrix.default_view"),
    ],
)
def test_matrix_form_reports_german_field_errors(
    tmp_path: Path, changes: dict[str, str], field: str
) -> None:
    rig = Rig(tmp_path)
    result = forms.parse_form(_form(rig, **changes), rig.config.sense_hat)
    assert result.table is None
    assert field in result.errors
    assert result.errors[field]


def test_matrix_form_needs_at_least_one_view(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    values = {k: v for k, v in _form(rig).items() if not k.startswith("matrix.views.")}
    result = forms.parse_form(values, rig.config.sense_hat)
    assert result.errors["matrix.views"] == "Mindestens eine Ansicht muss aktiviert sein."


def test_settings_page_lists_every_view_and_new_options(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        text = client.get("/settings").text
    for view_id in MATRIX_VIEW_IDS:
        assert f'name="matrix.views.{view_id}"' in text
    for name in (
        "matrix.brightness",
        "matrix.default_view",
        "matrix.auto_cycle_seconds",
        "matrix.night_start",
        "matrix.night_end",
        "matrix.night_mode",
        "matrix.night_enabled",
    ):
        assert f'name="{name}"' in text
    assert "Noch ohne Funktion" not in text
    assert 'href="/display"' in text


def test_saving_matrix_settings_applies_them_to_the_running_display(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        rig.wait_running()
        values = {k: v for k, v in _form(rig).items() if not k.startswith("matrix.views.")}
        values.update(
            {
                "matrix.views.overview": "on",
                "matrix.views.rainbow": "on",
                "matrix.views.lamp": "on",
                "matrix.default_view": "rainbow",
                "matrix.brightness": "30",
                "matrix.auto_cycle_seconds": "20",
                "matrix.night_enabled": "on",
                "matrix.night_start": "21:30",
                "matrix.night_end": "06:15",
                "matrix.night_mode": "dim",
                "token": TOKEN,
            }
        )
        response = client.post(
            "/settings/sense-hat",
            content=urlencode(values),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        assert "Gespeichert und angewendet" in response.text
        assert rig.matrix is not None
        deadline = time.monotonic() + 3
        while rig.matrix.status.state != "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        status = rig.matrix.status
        assert rig.matrix.controller.views == ("overview", "lamp", "rainbow")
        assert status.view_id == "rainbow"
        assert status.brightness == 30
    saved = load_config(rig.path).sense_hat.matrix
    assert saved.views == ("overview", "lamp", "rainbow")
    assert (saved.night_enabled, saved.night_start, saved.night_end, saved.night_mode) == (
        True,
        "21:30",
        "06:15",
        "dim",
    )
    assert saved.auto_cycle_seconds == 20


def test_invalid_matrix_save_changes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    before = rig.path.read_text(encoding="utf-8")
    with rig.client() as client:
        values = _form(rig, **{"matrix.night_start": "99:99"})
        values["token"] = TOKEN
        response = client.post(
            "/settings/sense-hat",
            content=urlencode(values),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 422
    assert 'id="err-matrix.night_start"' in response.text
    assert rig.path.read_text(encoding="utf-8") == before


def test_navigation_links_to_the_display_page(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.client() as client:
        assert 'href="/display"' in client.get("/").text


def _wait(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
