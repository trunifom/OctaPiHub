from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime, BleRuntimeTuning, CubeConnectionState
from octapihub.application.cube_service import CubeActivity, CubeService
from octapihub.application.hue_service import HueState, HueStatus
from octapihub.application.matrix.builder import HISTORY_LENGTH, DisplayStateBuilder
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.composition import build_matrix_service
from octapihub.core.config import AppConfig, SenseHatConfig, parse_config, save_config
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

LIGHT = "AA:BB:CC:DD:EE:01"
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def config(tmp_path: Path, cubes: int = 3, matrix: dict[str, object] | None = None) -> AppConfig:
    roles = ["light", "music", "general"]
    return parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": f"C{i + 1}", "address": f"AA:BB:CC:DD:EE:0{i + 1}", "role": roles[i]}
                for i in range(cubes)
            ],
            "sense_hat": {
                "history": {"database_path": str(tmp_path / "h.sqlite3")},
                "matrix": matrix or {},
            },
        }
    )


def sense_service(cfg: SenseHatConfig, device: FakeSenseHat | None = None) -> SenseHatService:
    board = device or FakeSenseHat()
    return SenseHatService(
        cfg,
        lambda _c: SenseHatAdapter(board),
        lambda c: SQLiteSenseHatHistory(c.history.database_path),
    )


# ---------- state builder ----------


def test_builder_maps_sensors_cubes_hue_and_history(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    sense = sense_service(cfg.sense_hat)
    statuses: list[object] = []
    cubes = SimpleNamespace(
        statuses=statuses,
    )
    hue = SimpleNamespace(
        status=HueStatus(HueState.PAIRED, "", None, None, None),
    )
    builder = DisplayStateBuilder(
        lambda: cfg.cubes,
        sense,
        cubes,
        hue,
        lambda: "warn",
        clock=lambda: NOW,  # type: ignore[arg-type]
    )

    async def scenario() -> None:
        await sense.start()
        await until(lambda: all(s.last_sample for s in sense.statuses))
        state = builder.build()
        assert state.now == NOW
        assert state.readings["temperature"].value == pytest.approx(21.123456)
        assert state.readings["humidity"].value == pytest.approx(45.6789)
        assert state.sense_level == "ok"
        assert state.hue_level == "ok"
        assert state.host_level == "warn"
        assert [c.name for c in state.cubes] == ["C1", "C2", "C3"]
        assert all(c.level == "off" and not c.connected for c in state.cubes)
        assert len(state.history["temperature"]) >= 1
        await sense.stop()
        assert builder.build().sense_level == "off"

    asyncio.run(scenario())


def test_builder_history_grows_only_with_new_samples_and_is_bounded() -> None:
    from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading
    from octapihub.application.sense_hat_worker import (
        SenseHatSample,
        SensorGroupState,
        SensorGroupStatus,
    )

    holder: dict[str, list[SensorGroupStatus]] = {"statuses": []}
    sense = SimpleNamespace(
        hardware_error=None,
        running=True,
        statuses=property(lambda _s: holder["statuses"]),
    )

    class Sense:
        hardware_error = None
        running = True

        @property
        def statuses(self) -> list[SensorGroupStatus]:
            return holder["statuses"]

    builder = DisplayStateBuilder(lambda: (), Sense(), clock=lambda: NOW)  # type: ignore[arg-type]
    del sense

    def put(value: float, at: datetime, corrected: float | None = None) -> None:
        sample = SenseHatSample(
            "temperature",
            SenseHatReading(SenseHatMetric.TEMPERATURE, value, "°C"),
            at,
            corrected,
        )
        holder["statuses"] = [
            SensorGroupStatus("temperature", SensorGroupState.OK, sample, None, 0)
        ]

    put(20.0, NOW, corrected=18.0)
    first = builder.build()
    builder.build()
    builder.build()  # same sample: no duplicate history entries
    assert first.readings["temperature"].value == 18.0  # corrected value is what is shown
    assert builder.build().history["temperature"] == (18.0,)
    for step in range(1, 15):
        put(20.0 + step, NOW + timedelta(seconds=step), corrected=18.0 + step)
        builder.build()
    history = builder.build().history["temperature"]
    assert len(history) == HISTORY_LENGTH
    assert history[-1] == 32.0
    assert history == tuple(sorted(history))  # oldest first


def test_builder_hides_failed_sensors_and_reports_levels() -> None:
    from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading
    from octapihub.application.sense_hat_worker import (
        SenseHatSample,
        SensorGroupState,
        SensorGroupStatus,
    )

    sample = SenseHatSample("humidity", SenseHatReading(SenseHatMetric.HUMIDITY, 40.0, "%RH"), NOW)

    class Sense:
        hardware_error: str | None = None
        running = True
        statuses = [SensorGroupStatus("humidity", SensorGroupState.ERROR, sample, "x", 3)]

    sense = Sense()
    builder = DisplayStateBuilder(lambda: (), sense, clock=lambda: NOW)  # type: ignore[arg-type]
    state = builder.build()
    assert "humidity" not in state.readings  # stale values are not shown as current
    assert state.sense_level == "error"
    sense.statuses.append(SensorGroupStatus("temperature", SensorGroupState.OK, None, None, 0))
    assert builder.build().sense_level == "warn"
    sense.hardware_error = "gone"
    assert builder.build().sense_level == "error"


def test_builder_translates_cube_connection_states(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    from octapihub.application.ble_runtime import CubeRuntimeStatus

    def status(
        name: str, state: CubeConnectionState, connected: bool, **kw: object
    ) -> CubeRuntimeStatus:
        return CubeRuntimeStatus(
            name, "x", state, connected, 0, kw.get("face"), None, 0, kw.get("battery")
        )  # type: ignore[arg-type]

    cubes = SimpleNamespace(
        statuses=[
            status("C1", CubeConnectionState.READY, True, face=4, battery=77),
            status("C2", CubeConnectionState.RECONNECT_WAIT, False),
            status("C3", CubeConnectionState.UNSUPPORTED, False),
        ]
    )
    builder = DisplayStateBuilder(lambda: cfg.cubes, sense_service(cfg.sense_hat), cubes)  # type: ignore[arg-type]
    displays = builder.build().cubes
    assert [(c.level, c.connected) for c in displays] == [
        ("ok", True),
        ("warn", False),
        ("error", False),
    ]
    assert (displays[0].last_face, displays[0].battery_percent) == (4, 77)
    assert displays[0].role == "light"


def test_builder_hue_levels_and_failing_host_provider(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    holder = {"status": HueStatus(HueState.DISABLED, "", None, None, None)}
    hue = SimpleNamespace(status=property(lambda _s: holder["status"]))

    class Hue:
        @property
        def status(self) -> HueStatus:
            return holder["status"]

    del hue

    def broken() -> str:
        raise RuntimeError("no vcgencmd")

    calls = {"n": 0}

    def counting() -> str:
        calls["n"] += 1
        return "ok"

    clock = {"t": 0.0}
    builder = DisplayStateBuilder(
        lambda: cfg.cubes,
        sense_service(cfg.sense_hat),
        None,
        Hue(),  # type: ignore[arg-type]
        counting,
        monotonic=lambda: clock["t"],
        host_interval=30.0,
    )
    levels = []
    for state in (
        HueStatus(HueState.DISABLED, "", None, None, None),
        HueStatus(HueState.NOT_PAIRED, "h", None, None, None),
        HueStatus(HueState.PAIRED, "h", None, None, None),
        HueStatus(HueState.PAIRED, "h", "bad", "CH-HUE-001", None),
    ):
        holder["status"] = state
        levels.append(builder.build().hue_level)
    assert levels == ["off", "warn", "ok", "error"]
    builder.build()
    builder.build()
    assert calls["n"] == 1  # the host check is cached
    clock["t"] = 31.0
    builder.build()
    assert calls["n"] == 2
    failing = DisplayStateBuilder(
        lambda: cfg.cubes, sense_service(cfg.sense_hat), None, None, broken
    )
    assert failing.build().host_level == "off"  # a failing check never raises


# ---------- cube activity and dispatcher observer ----------


class _Plugin:
    kind = "fake"

    def __init__(self) -> None:
        self.fail = False

    async def execute(self, action: str, parameters: object) -> None:
        if self.fail:
            raise RuntimeError("boom")


def _pipeline(tmp_path: Path, plugin: _Plugin) -> tuple[CubeService, FakeTransportFactory]:
    cfg = parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "C1", "address": LIGHT, "role": "light"}],
            "mappings": [
                {
                    "cube": "C1",
                    "face": 4,
                    "actions": [{"kind": "fake", "action": "ping", "parameters": {}}],
                }
            ],
        }
    )
    factory = FakeTransportFactory(
        DeviceProfile((CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),))
    )

    def runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            tuning=BleRuntimeTuning(health_poll_seconds=0.01, debounce_ms=0),
        )

    def dispatcher(settings: AppConfig) -> OutboxDispatcher:
        registry = PluginRegistry()
        registry.register(plugin)
        return OutboxDispatcher(
            RuleEngine(settings),
            SQLiteOutbox(tmp_path / "o.db"),
            registry,
            backoff_base_seconds=0.01,
            backoff_cap_seconds=0.02,
        )

    return CubeService(cfg, runtime, dispatcher, poll_interval_seconds=0.005), factory


def test_cube_service_reports_faces_and_action_results_to_listeners(tmp_path: Path) -> None:
    plugin = _Plugin()
    service, factory = _pipeline(tmp_path, plugin)
    seen: list[CubeActivity] = []
    service.add_listener(seen.append)

    async def scenario() -> None:
        await service.start()
        await until(lambda: len(factory.created) == 1 and service.statuses[0].connected)
        transport = factory.created[0][2]
        await until(lambda: service.statuses[0].state is CubeConnectionState.WAITING_FOR_BASELINE)
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await until(lambda: any(a.kind == "action_ok" for a in seen))
        plugin.fail = True
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x02")  # unmapped face
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await until(lambda: any(a.kind == "action_failed" for a in seen))
        await service.stop()

    asyncio.run(scenario())
    kinds = [(a.kind, a.cube_name, a.face) for a in seen]
    assert ("face", "C1", 4) in kinds
    assert ("face", "C1", 2) in kinds  # even unmapped faces give feedback
    assert ("action_ok", "C1", None) in kinds
    assert ("face", "C1", 1) not in kinds  # the baseline is not an action


def test_a_failing_listener_never_disturbs_the_pipeline(tmp_path: Path) -> None:
    plugin = _Plugin()
    service, factory = _pipeline(tmp_path, plugin)
    good: list[CubeActivity] = []

    def bad(_activity: CubeActivity) -> None:
        raise RuntimeError("display crashed")

    service.add_listener(bad)
    service.add_listener(good.append)

    async def scenario() -> None:
        await service.start()
        await until(
            lambda: (
                service.statuses
                and service.statuses[0].state is CubeConnectionState.WAITING_FOR_BASELINE
            )
        )
        transport = factory.created[0][2]
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await until(lambda: any(a.kind == "action_ok" for a in good))
        await service.stop()

    asyncio.run(scenario())


def test_dispatcher_observer_sees_success_and_failure_and_may_crash(tmp_path: Path) -> None:
    plugin = _Plugin()
    registry = PluginRegistry()
    registry.register(plugin)
    cfg = parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "C1", "address": LIGHT, "role": "light"}],
            "mappings": [
                {
                    "cube": "C1",
                    "face": 1,
                    "actions": [{"kind": "fake", "action": "ping", "parameters": {}}],
                }
            ],
        }
    )
    results: list[bool] = []

    def observer(_entry: object, ok: bool) -> None:
        results.append(ok)
        raise RuntimeError("observer bug")

    dispatcher = OutboxDispatcher(
        RuleEngine(cfg),
        SQLiteOutbox(tmp_path / "o.db"),
        registry,
        observer=observer,  # type: ignore[arg-type]
        backoff_base_seconds=0.01,
        backoff_cap_seconds=0.02,
    )

    async def scenario() -> None:
        dispatcher.enqueue_face_change("C1", 1)
        assert await dispatcher.dispatch_next("C1")
        plugin.fail = True
        dispatcher.enqueue_face_change("C1", 1)
        assert await dispatcher.dispatch_next("C1")

    asyncio.run(scenario())
    assert results == [True, False]


# ---------- composition ----------


def test_composition_connects_cube_feedback_to_the_display(tmp_path: Path) -> None:
    cfg = config(tmp_path, cubes=2, matrix={"brightness": 100})
    sense = sense_service(cfg.sense_hat)
    plugin = _Plugin()
    cube_service, _factory = _pipeline(tmp_path, plugin)
    matrix = build_matrix_service(cfg, sense, cube_service)

    async def scenario() -> None:
        await sense.start()
        await matrix.start()
        await until(lambda: matrix.status.state == "running")
        view = matrix.status.view_id
        # activity arrives through the listener registered by the composition
        for listener in cube_service._listeners:
            listener(CubeActivity("face", "C1", 3))
        assert matrix._overlay is not None
        assert matrix._overlay.kind == "face"
        for listener in cube_service._listeners:
            listener(CubeActivity("action_ok", "C1"))
        assert matrix._overlay.kind == "icon"
        assert matrix.status.view_id == view
        await matrix.stop()
        await sense.stop()

    asyncio.run(scenario())


def test_matrix_shares_the_sense_hat_port_and_survives_missing_hardware(tmp_path: Path) -> None:
    cfg = config(tmp_path)

    def no_board(_c: SenseHatConfig) -> SenseHatAdapter:
        from octapihub.core.errors import SenseHatError

        raise SenseHatError("no board")

    sense = SenseHatService(
        cfg.sense_hat, no_board, lambda c: SQLiteSenseHatHistory(c.history.database_path)
    )
    matrix = build_matrix_service(cfg, sense)

    async def scenario() -> None:
        await sense.start()
        await matrix.start()
        await until(lambda: matrix.status.state == "unavailable")
        await matrix.stop()

    asyncio.run(scenario())


# ---------- settings ----------


def test_saving_sense_hat_settings_restarts_the_matrix_with_new_views(tmp_path: Path) -> None:
    from octapihub.core.config import sense_hat_table

    cfg = config(tmp_path, matrix={"brightness": 100})
    path = tmp_path / "octapihub.toml"
    save_config(path, cfg)
    device = FakeSenseHat()
    sense = sense_service(cfg.sense_hat, device)
    matrix = build_matrix_service(cfg, sense)
    manager = SettingsManager(path, cfg, sense, matrix=matrix)

    async def scenario() -> None:
        await sense.start()
        await matrix.start()
        await until(lambda: matrix.status.state == "running")
        table = sense_hat_table(cfg.sense_hat)
        table["matrix"] = {  # type: ignore[index]
            **table["matrix"],  # type: ignore[dict-item]
            "views": ["overview", "clock", "lamp"],
            "default_view": "clock",
            "brightness": 30,
        }
        await manager.apply_sense_hat(table)
        await until(lambda: matrix.status.state == "running")
        assert matrix.controller.views == ("overview", "clock", "lamp")
        assert matrix.status.view_id == "clock"
        assert matrix.status.brightness == 30
        await matrix.stop()
        await sense.stop()

    asyncio.run(scenario())
