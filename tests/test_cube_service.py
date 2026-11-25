from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import (
    BleRuntime,
    BleRuntimeTuning,
    CubeConnectionState,
    CubeEvent,
)
from octapihub.application.cube_service import CubeService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import AppConfig, parse_config
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox

LIGHT = "AA:BB:CC:DD:EE:01"


def _config() -> AppConfig:
    return parse_config(
        {
            "schema_version": 3,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": LIGHT, "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 4,
                    "actions": [
                        {"kind": "fake", "action": "ping", "parameters": {"n": 1}},
                    ],
                }
            ],
        }
    )


class _Plugin:
    kind = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.calls.append((action, dict(parameters)))


class _FlakyDispatcher(OutboxDispatcher):
    """Fails the first enqueue to prove that routing survives a bad event."""

    failures = 1

    def enqueue_event(self, event: CubeEvent) -> str | None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("database is locked")
        return super().enqueue_event(event)


async def _until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


def _profile() -> DeviceProfile:
    return DeviceProfile(
        (CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),)
    )


def _service(
    tmp_path: Path,
    plugin: _Plugin | None,
    factory: FakeTransportFactory,
    dispatcher_class: type[OutboxDispatcher] = OutboxDispatcher,
) -> CubeService:
    def make_runtime(config: AppConfig) -> BleRuntime:
        return BleRuntime(
            config,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            tuning=BleRuntimeTuning(
                connect_timeout_seconds=0.5,
                operation_timeout_seconds=0.5,
                backoff_base_seconds=0.01,
                backoff_cap_seconds=0.02,
                health_poll_seconds=0.01,
                debounce_ms=0,
            ),
            jitter=lambda delay: delay,
        )

    def make_dispatcher(config: AppConfig) -> OutboxDispatcher:
        plugins = PluginRegistry()
        if plugin is not None:
            plugins.register(plugin)
        return dispatcher_class(
            RuleEngine(config), SQLiteOutbox(tmp_path / "state.sqlite3"), plugins
        )

    return CubeService(_config(), make_runtime, make_dispatcher, poll_interval_seconds=0.005)


def _light_transport(factory: FakeTransportFactory):  # type: ignore[no-untyped-def]
    return next(t for address, _, t in factory.created if address == LIGHT)


def test_face_change_runs_mapped_action_and_stop_releases_connections(tmp_path: Path) -> None:
    plugin = _Plugin()
    factory = FakeTransportFactory(_profile())
    service = _service(tmp_path, plugin, factory)

    async def scenario() -> None:
        await service.start()
        assert service.running
        await _until(
            lambda: (
                len(factory.created) == 2
                and all(
                    s.state is CubeConnectionState.WAITING_FOR_BASELINE for s in service.statuses
                )
            )
        )
        transport = _light_transport(factory)
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await _until(lambda: bool(plugin.calls))
        await service.stop()
        assert not service.running
        assert service.statuses == ()

    asyncio.run(scenario())
    assert plugin.calls == [("ping", {"n": 1})]
    assert all(not t.is_connected for _, _, t in factory.created)


def test_missing_plugin_is_reported_and_nothing_starts(tmp_path: Path) -> None:
    factory = FakeTransportFactory(_profile())
    service = _service(tmp_path, None, factory)

    async def scenario() -> None:
        await service.start()
        await service.stop()

    asyncio.run(scenario())
    assert not service.running
    assert service.error is not None
    assert service.error_code is not None
    assert factory.created == []


def test_unwritable_data_folder_is_reported(tmp_path: Path) -> None:
    def broken(_config: AppConfig) -> OutboxDispatcher:
        raise PermissionError("read-only file system")

    service = CubeService(_config(), lambda _c: pytest.fail("must not start"), broken)

    async def scenario() -> None:
        await service.start()

    asyncio.run(scenario())
    assert service.error_code == "CH-DB-001"
    assert not service.running


def test_routing_survives_a_failing_enqueue(tmp_path: Path) -> None:
    plugin = _Plugin()
    factory = FakeTransportFactory(_profile())
    service = _service(tmp_path, plugin, factory, _FlakyDispatcher)

    async def scenario() -> None:
        await service.start()
        await _until(lambda: len(factory.created) == 2)
        transport = _light_transport(factory)
        await _until(lambda: all(s.connected for s in service.statuses))
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")  # baseline -> boom
        transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
        await _until(lambda: bool(plugin.calls))
        await service.stop()

    asyncio.run(scenario())
    assert plugin.calls == [("ping", {"n": 1})]


def test_double_start_is_rejected_and_stop_without_start_is_safe(tmp_path: Path) -> None:
    factory = FakeTransportFactory(_profile())
    service = _service(tmp_path, _Plugin(), factory)

    async def scenario() -> None:
        await service.stop()
        await service.start()
        with pytest.raises(RuntimeError):
            await service.start()
        await service.stop()

    asyncio.run(scenario())
