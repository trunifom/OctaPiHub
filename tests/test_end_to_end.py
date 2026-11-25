from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import (
    BleRuntime,
    BleRuntimeTuning,
    CubeConnectionState,
    CubeEvent,
)
from octapihub.application.outbox import OutboxDispatcher, OutboxState
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import AppConfig, parse_config
from octapihub.domain.cube import OrientationKind
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox


def integration_config() -> AppConfig:
    return parse_config(
        {
            "schema_version": 2,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 4,
                    "actions": [
                        {
                            "kind": "hue",
                            "action": "scene.activate",
                            "parameters": {"scene_id": "test.living-room"},
                        },
                        {
                            "kind": "hue",
                            "action": "light.brightness",
                            "parameters": {"level": 0.65},
                        },
                    ],
                }
            ],
        }
    )


class FakeHuePlugin:
    kind = "hue"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.active_scene: str | None = None
        self.brightness: float | None = None

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.calls.append((action, dict(parameters)))
        if action == "scene.activate":
            scene_id = parameters.get("scene_id")
            if not isinstance(scene_id, str) or not scene_id:
                raise ValueError("A scene ID is required")
            self.active_scene = scene_id
        elif action == "light.brightness":
            level = parameters.get("level")
            if (
                isinstance(level, bool)
                or not isinstance(level, (int, float))
                or not 0 <= level <= 1
            ):
                raise ValueError("Brightness must be between zero and one")
            self.brightness = float(level)
        else:
            raise ValueError(f"Unsupported fake Hue action: {action}")


def fake_device_profile() -> DeviceProfile:
    return DeviceProfile(
        (
            CharacteristicInfo(
                ORIENTATION_CHARACTERISTIC_UUID,
                frozenset({"notify"}),
            ),
        )
    )


async def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError("End-to-end condition was not reached")
        await asyncio.sleep(0.001)


def test_fake_cube_event_reaches_fake_hue_through_persistent_outbox(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = integration_config()
        factory = FakeTransportFactory(fake_device_profile())
        runtime = BleRuntime(
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
        hue = FakeHuePlugin()
        plugins = PluginRegistry()
        plugins.register(hue)
        outbox = SQLiteOutbox(tmp_path / "state.db")
        dispatcher = OutboxDispatcher(RuleEngine(config), outbox, plugins)
        observed_events: asyncio.Queue[CubeEvent] = asyncio.Queue()
        batch_ids: list[str] = []
        stop_dispatcher = asyncio.Event()

        async def route_events() -> None:
            while True:
                event = await runtime.next_event()
                batch_id = dispatcher.enqueue_event(event)
                if batch_id is not None:
                    batch_ids.append(batch_id)
                await observed_events.put(event)

        await runtime.start()
        router_task = asyncio.create_task(route_events())
        worker_task = asyncio.create_task(
            dispatcher.run_cube("Licht", stop_dispatcher, poll_interval_seconds=0.001)
        )
        try:
            await wait_until(
                lambda: (
                    len(factory.created) == 2
                    and all(
                        status.state is CubeConnectionState.WAITING_FOR_BASELINE
                        for status in runtime.statuses
                    )
                )
            )
            light_transport = next(
                transport
                for address, _, transport in factory.created
                if address == "AA:BB:CC:DD:EE:01"
            )

            light_transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x01")
            baseline = await asyncio.wait_for(observed_events.get(), timeout=1)
            assert baseline.orientation.kind is OrientationKind.BASELINE
            assert not batch_ids
            assert not hue.calls

            light_transport.emit_notification(ORIENTATION_CHARACTERISTIC_UUID, b"\x04")
            face_change = await asyncio.wait_for(observed_events.get(), timeout=1)
            assert face_change.cube_name == "Licht"
            assert face_change.orientation.kind is OrientationKind.FACE_CHANGE
            assert face_change.orientation.value == 4
            await wait_until(
                lambda: (
                    bool(batch_ids)
                    and all(
                        entry.state is OutboxState.SUCCEEDED
                        for entry in outbox.get_batch(batch_ids[0])
                    )
                )
            )

            assert [action for action, _ in hue.calls] == [
                "scene.activate",
                "light.brightness",
            ]
            assert hue.active_scene == "test.living-room"
            assert hue.brightness == 0.65
            assert [entry.sequence for entry in outbox.get_batch(batch_ids[0])] == [0, 1]
        finally:
            stop_dispatcher.set()
            await asyncio.wait_for(worker_task, timeout=1)
            router_task.cancel()
            try:
                await router_task
            except asyncio.CancelledError:
                pass
            await runtime.stop()

    asyncio.run(scenario())
