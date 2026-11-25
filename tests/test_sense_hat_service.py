from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from octapihub.application.sense_hat import SenseHatPort
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.sense_hat_worker import SensorGroupState
from octapihub.composition import build_sense_hat_service
from octapihub.core.config import AppConfig, SenseHatConfig
from octapihub.core.errors import HistoryError, SenseHatError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def _config(tmp_path: Path, **overrides: object) -> SenseHatConfig:
    base = SenseHatConfig()
    history = replace(base.history, database_path=str(tmp_path / "h.sqlite3"))
    return replace(base, history=history, **overrides)  # type: ignore[arg-type]


def _all_ok(service: SenseHatService) -> bool:
    statuses = service.statuses
    return len(statuses) == 3 and all(s.state is SensorGroupState.OK for s in statuses)


def test_stop_persists_buffered_samples(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = SenseHatService(
        config,
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda _c: SQLiteSenseHatHistory(config.history.database_path),
    )
    until = datetime.now(UTC) + timedelta(days=1)

    async def scenario() -> None:
        await service.start()
        await _until(lambda: _all_ok(service))
        assert service.running
        # Batch of 20 is not full yet, so nothing is persisted before shutdown.
        assert service.query_series("temperature", EPOCH, until) == ()
        await service.stop()

    asyncio.run(scenario())
    points = SQLiteSenseHatHistory(config.history.database_path).query_series(
        "temperature", EPOCH, until
    )
    assert [p.value for p in points] == [21.123456]
    assert not service.running


def test_missing_hardware_degrades_without_raising(tmp_path: Path) -> None:
    def no_board(_c: SenseHatConfig) -> SenseHatPort:
        raise SenseHatError("no board")

    service = SenseHatService(
        _config(tmp_path), no_board, lambda _c: SQLiteSenseHatHistory(tmp_path / "x.sqlite3")
    )

    async def scenario() -> None:
        await service.start()
        await service.stop()

    asyncio.run(scenario())
    assert service.hardware_error is not None
    assert service.statuses == ()
    assert not service.running


def test_history_failure_keeps_measuring(tmp_path: Path) -> None:
    def broken_store(_c: SenseHatConfig) -> SQLiteSenseHatHistory:
        raise HistoryError("cannot open")

    service = SenseHatService(
        _config(tmp_path), lambda _c: SenseHatAdapter(FakeSenseHat()), broken_store
    )

    async def scenario() -> None:
        await service.start()
        await _until(lambda: _all_ok(service))
        await service.stop()

    asyncio.run(scenario())
    assert service.history_error is not None
    assert service.hardware_error is None
    assert service.query_series("temperature", EPOCH, datetime.now(UTC)) == ()


def test_color_settings_are_applied_when_group_enabled(tmp_path: Path) -> None:
    base = _config(tmp_path, color_gain=4, color_integration_cycles=8)
    sensors = dict(base.sensors)
    sensors["color"] = replace(sensors["color"], enabled=True)
    device = FakeSenseHat()
    service = SenseHatService(
        replace(base, sensors=sensors),
        lambda _c: SenseHatAdapter(device),
        lambda _c: SQLiteSenseHatHistory(base.history.database_path),
    )

    async def scenario() -> None:
        await service.start()
        await service.stop()

    asyncio.run(scenario())
    assert device.colour is not None
    assert device.colour.gain == 4
    assert device.colour.integration_cycles == 8


def test_color_settings_are_not_applied_when_group_disabled(tmp_path: Path) -> None:
    device = FakeSenseHat()
    config = _config(tmp_path, color_gain=4)
    service = SenseHatService(
        config,
        lambda _c: SenseHatAdapter(device),
        lambda _c: SQLiteSenseHatHistory(config.history.database_path),
    )

    async def scenario() -> None:
        await service.start()
        await service.stop()

    asyncio.run(scenario())
    assert device.colour is not None
    assert device.colour.gain == 1


def test_disabled_service_starts_nothing_and_double_start_is_rejected(tmp_path: Path) -> None:
    disabled = SenseHatService(
        _config(tmp_path, enabled=False),
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda _c: SQLiteSenseHatHistory(tmp_path / "x.sqlite3"),
    )
    active = SenseHatService(
        _config(tmp_path),
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda _c: SQLiteSenseHatHistory(tmp_path / "y.sqlite3"),
    )

    async def scenario() -> None:
        await disabled.start()
        assert not disabled.running
        await active.start()
        try:
            await active.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("second start must fail")
        await active.stop()

    asyncio.run(scenario())


def test_composition_builds_service_from_config(tmp_path: Path) -> None:
    config = AppConfig.__new__(AppConfig)
    object.__setattr__(config, "sense_hat", _config(tmp_path))
    assert isinstance(build_sense_hat_service(config), SenseHatService)
