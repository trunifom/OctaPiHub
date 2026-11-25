from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import (
    BleRuntime,
    BleRuntimeTuning,
    CubeConnectionState,
    CubeRuntimeStatus,
)
from octapihub.application.cube_service import CubeService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import AppConfig, parse_config, save_config
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_battery_level,
    decode_orientation,
    supports_battery_read,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web.app import WebBackend, create_app
from octapihub.web.presenter import (
    BATTERY_CRITICAL_PERCENT,
    BATTERY_LOW_PERCENT,
    battery_warnings,
    build_cube_views,
    describe_battery,
)

ADDRESSES = ("AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03")
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
ORIENTATION = CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"}))
BATTERY = CharacteristicInfo(BATTERY_LEVEL_CHARACTERISTIC_UUID, frozenset({"read"}))


def _status(**changes: object) -> CubeRuntimeStatus:
    base: dict[str, object] = {
        "name": "Licht",
        "role": "light",
        "state": CubeConnectionState.READY,
        "connected": True,
        "reconnect_attempt": 0,
        "last_value": None,
        "last_error": None,
        "dropped_notifications": 0,
        "battery_percent": 80,
        "battery_checked_at": NOW.timestamp(),
        "battery_supported": True,
        "battery_error": None,
    }
    base.update(changes)
    return CubeRuntimeStatus(**base)  # type: ignore[arg-type]


# ---------- presenter ----------


@pytest.mark.parametrize(
    ("percent", "level", "hint"),
    [
        (100, "ok", ""),
        (BATTERY_LOW_PERCENT + 1, "ok", ""),
        (BATTERY_LOW_PERCENT, "warn", "Bald laden."),
        (BATTERY_CRITICAL_PERCENT + 1, "warn", "Bald laden."),
        (BATTERY_CRITICAL_PERCENT, "error", "Bitte jetzt laden."),
        (0, "error", "Bitte jetzt laden."),
    ],
)
def test_battery_levels_and_hints(percent: int, level: str, hint: str) -> None:
    result = describe_battery(_status(battery_percent=percent), UTC)
    assert (result[0], result[1], result[2], result[3]) == (percent, level, f"{percent} %", hint)
    assert result[4] == "Geprüft um 12:00"


def test_unknown_and_unsupported_batteries_are_explained() -> None:
    assert describe_battery(None, UTC)[1:3] == ("off", "Unbekannt")
    disconnected = describe_battery(_status(connected=False, battery_percent=None), UTC)
    assert disconnected[2] == "Unbekannt"
    unsupported = describe_battery(
        _status(battery_percent=None, battery_supported=False, battery_checked_at=None), UTC
    )
    assert unsupported[1:4] == ("off", "Nicht abrufbar", "Dieser Würfel meldet keinen Akkustand.")
    waiting = describe_battery(
        _status(battery_percent=None, battery_supported=True, battery_checked_at=None), UTC
    )
    assert waiting[2] == "Wird gelesen …"
    failing = describe_battery(
        _status(battery_percent=None, battery_error="Nicht lesbar!", battery_checked_at=None), UTC
    )
    assert failing[1:4] == ("warn", "Nicht lesbar", "Nicht lesbar!")


def test_last_known_value_stays_visible_with_a_note_when_reading_fails() -> None:
    percent, level, text, hint, _ = describe_battery(
        _status(battery_percent=55, battery_error="x"), UTC
    )
    assert (percent, level, text) == (55, "ok", "55 %")
    assert "Letzter Wert" in hint


def test_warnings_list_only_cubes_that_need_charging() -> None:
    config = parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": f"C{i}", "address": ADDRESSES[i], "role": "general"} for i in range(3)
            ],
        }
    )
    statuses = [
        _status(name="C0", battery_percent=90),
        _status(name="C1", battery_percent=15),
        _status(name="C2", battery_percent=3),
    ]
    warnings = battery_warnings(build_cube_views(config, statuses, UTC))
    assert [level for level, _ in warnings] == ["warn", "error"]
    assert "„C1“" in warnings[0][1]
    assert "Bitte jetzt laden" in warnings[1][1]


# ---------- page with three cubes ----------


def _rig(tmp_path: Path, levels: tuple[int | None, ...]) -> tuple[WebBackend, FakeTransportFactory]:
    config: AppConfig = parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": name, "address": ADDRESSES[i], "role": role}
                for i, (name, role) in enumerate(
                    [("Licht", "light"), ("Musik", "music"), ("Extra", "general")]
                )
            ],
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
        }
    )
    path = tmp_path / "octapihub.toml"
    save_config(path, config)
    factory = FakeTransportFactory(DeviceProfile((ORIENTATION, BATTERY)))
    for address, level in zip(ADDRESSES, levels, strict=True):
        if level is not None:
            factory.values[address] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([level])}
        else:
            factory.values[address] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: b""}  # garbage

    def make_runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            tuning=BleRuntimeTuning(
                health_poll_seconds=0.01, debounce_ms=0, operation_timeout_seconds=0.5
            ),
            battery_characteristic_uuid=BATTERY_LEVEL_CHARACTERISTIC_UUID,
            decode_battery=decode_battery_level,
            supports_battery=supports_battery_read,
            wall_clock=lambda: NOW.timestamp(),
        )

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        return OutboxDispatcher(
            RuleEngine(settings), SQLiteOutbox(tmp_path / "s.sqlite3"), PluginRegistry()
        )

    cubes = CubeService(config, make_runtime, make_dispatcher)
    sense = SenseHatService(
        config.sense_hat,
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda c: SQLiteSenseHatHistory(c.history.database_path),
    )
    backend = WebBackend(
        SettingsManager(path, config, sense, None, cubes), sense, "tok-123456789012", cubes
    )
    backend.clock = lambda: NOW
    return backend, factory


def _wait_for_batteries(backend: WebBackend) -> None:
    assert backend.cubes is not None
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        statuses = backend.cubes.statuses
        if len(statuses) == 3 and all(
            s.battery_percent is not None or s.battery_error for s in statuses
        ):
            return
        time.sleep(0.01)
    raise AssertionError("batteries were not read")


def test_cubes_page_shows_each_battery_with_charging_advice(tmp_path: Path) -> None:
    backend, _ = _rig(tmp_path, (95, 18, 4))
    with TestClient(create_app(backend, manage_service=True)) as client:
        _wait_for_batteries(backend)
        text = client.get("/cubes").text
        overview = client.get("/").text
    assert "95 %" in text
    assert "18 %" in text
    assert "4 %" in text
    assert "Bald laden." in text
    assert "Bitte jetzt laden." in text
    assert text.count("<progress") >= 3
    assert "Würfel „Musik“: Akku 18 %" in text
    assert "Würfel „Extra“: Akku 4 %" in text
    assert "Würfel „Licht“: Akku" not in text  # healthy cubes raise no warning
    assert "Würfel „Extra“: Akku 4 %" in overview  # also visible on the overview


def test_unreadable_battery_is_explained_but_cube_stays_usable(tmp_path: Path) -> None:
    backend, _ = _rig(tmp_path, (80, None, 60))
    with TestClient(create_app(backend, manage_service=True)) as client:
        _wait_for_batteries(backend)
        text = client.get("/cubes").text
    assert "Nicht lesbar" in text
    assert "80 %" in text
    assert "60 %" in text


def test_diagnostics_contain_battery_levels_but_no_addresses(tmp_path: Path) -> None:
    import io
    import json
    import zipfile
    from urllib.parse import urlencode

    backend, _ = _rig(tmp_path, (95, 18, 4))
    with TestClient(create_app(backend, manage_service=True)) as client:
        _wait_for_batteries(backend)
        response = client.post(
            "/system/diagnostics",
            content=urlencode({"token": "tok-123456789012"}),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        status = json.loads(archive.read("status.json"))
        everything = "".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert [c["battery_percent"] for c in status["cubes"]["cubes"]] == [95, 18, 4]
    assert all(address not in everything for address in ADDRESSES)


def test_runtime_with_three_cubes_connects_all_of_them(tmp_path: Path) -> None:
    backend, factory = _rig(tmp_path, (50, 50, 50))
    assert backend.cubes is not None

    async def scenario() -> None:
        await backend.cubes.start()  # type: ignore[union-attr]
        async with asyncio.timeout(3):
            while not (
                len(factory.created) == 3 and all(s.connected for s in backend.cubes.statuses)  # type: ignore[union-attr]
            ):
                await asyncio.sleep(0.005)
        await backend.cubes.stop()  # type: ignore[union-attr]

    asyncio.run(scenario())
    assert {address for address, _, _ in factory.created} == set(ADDRESSES)
