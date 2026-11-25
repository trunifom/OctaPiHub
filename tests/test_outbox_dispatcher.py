from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

import pytest

from octapihub.application.ble_runtime import CubeEvent
from octapihub.application.outbox import OutboxDispatcher, OutboxState
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import AppConfig, parse_config
from octapihub.core.errors import PluginError, RetryableError
from octapihub.domain.cube import OrientationEvent, OrientationKind
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox


def rule_config() -> AppConfig:
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
                    "face": 2,
                    "actions": [
                        {
                            "kind": "test",
                            "action": "lights.scene",
                            "parameters": {"fail_once": True},
                        }
                    ],
                },
                {
                    "cube": "Musik",
                    "face": 3,
                    "actions": [{"kind": "test", "action": "music.play"}],
                },
            ],
        }
    )


def event(cube_name: str, role: str, face: int) -> CubeEvent:
    return CubeEvent(
        cube_name,
        role,
        OrientationEvent(OrientationKind.FACE_CHANGE, face, observed_at_ms=1),
    )


class RecordingPlugin:
    kind = "test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, object]]] = []
        self.light_failures_remaining = 2

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.calls.append((action, parameters))
        if action == "lights.scene" and self.light_failures_remaining:
            self.light_failures_remaining -= 1
            raise RuntimeError("simulated adapter failure")


def test_dispatcher_rejects_unavailable_configured_plugin(tmp_path: Path) -> None:
    with pytest.raises(PluginError) as error:
        OutboxDispatcher(
            RuleEngine(rule_config()),
            SQLiteOutbox(tmp_path / "state.db"),
            PluginRegistry(),
        )
    assert error.value.details["kind"] == "test"


def make_dispatcher(
    database_path: Path,
    plugin: RecordingPlugin,
    now: list[float],
    *,
    ttl_seconds: float = 20.0,
    backoff_seconds: float = 2.0,
) -> tuple[OutboxDispatcher, SQLiteOutbox]:
    registry = PluginRegistry()
    registry.register(plugin)
    outbox = SQLiteOutbox(database_path)
    dispatcher = OutboxDispatcher(
        RuleEngine(rule_config()),
        outbox,
        registry,
        ttl_seconds=ttl_seconds,
        backoff_base_seconds=backoff_seconds,
        backoff_cap_seconds=backoff_seconds * 4,
        clock=lambda: now[0],
        jitter=lambda delay: delay,
    )
    return dispatcher, outbox


def test_dispatcher_retries_failed_cube_without_blocking_another_cube(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = [100.0]
        plugin = RecordingPlugin()
        dispatcher, outbox = make_dispatcher(tmp_path / "state.db", plugin, now)

        assert (
            dispatcher.enqueue_event(
                CubeEvent(
                    "Licht",
                    "light",
                    OrientationEvent(OrientationKind.BASELINE, 2, observed_at_ms=0),
                )
            )
            is None
        )
        assert dispatcher.enqueue_event(event("Licht", "light", 5)) is None
        light_batch = dispatcher.enqueue_event(event("Licht", "light", 2))
        music_batch = dispatcher.enqueue_event(event("Musik", "music", 3))
        assert light_batch is not None
        assert music_batch is not None

        assert await dispatcher.dispatch_next("Licht")
        light_entry = outbox.get_batch(light_batch)[0]
        assert light_entry.state is OutboxState.PENDING
        assert light_entry.attempts == 1
        assert light_entry.available_at == 102.0
        assert light_entry.last_error == "RuntimeError"

        assert await dispatcher.dispatch_next("Musik")
        music_entry = outbox.get_batch(music_batch)[0]
        assert music_entry.state is OutboxState.SUCCEEDED

        now[0] = 102.0
        assert await dispatcher.dispatch_next("Licht")
        light_entry = outbox.get_batch(light_batch)[0]
        assert light_entry.state is OutboxState.PENDING
        assert light_entry.attempts == 2
        assert light_entry.available_at == 106.0

        now[0] = 106.0
        assert await dispatcher.dispatch_next("Licht")
        assert outbox.get_batch(light_batch)[0].state is OutboxState.SUCCEEDED
        assert [action for action, _ in plugin.calls] == [
            "lights.scene",
            "music.play",
            "lights.scene",
            "lights.scene",
        ]

    asyncio.run(scenario())


def test_dispatcher_expires_retry_when_backoff_reaches_ttl(tmp_path: Path) -> None:
    class AlwaysFailingPlugin:
        kind = "test"

        async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
            raise RuntimeError("adapter unavailable")

    async def scenario() -> None:
        now = [10.0]
        registry = PluginRegistry()
        registry.register(AlwaysFailingPlugin())
        outbox = SQLiteOutbox(tmp_path / "state.db")
        dispatcher = OutboxDispatcher(
            RuleEngine(rule_config()),
            outbox,
            registry,
            ttl_seconds=1.0,
            backoff_base_seconds=2.0,
            backoff_cap_seconds=2.0,
            clock=lambda: now[0],
            jitter=lambda delay: delay,
        )
        batch = dispatcher.enqueue_event(event("Licht", "light", 2))
        assert batch is not None

        assert await dispatcher.dispatch_next("Licht")

        entry = outbox.get_batch(batch)[0]
        assert entry.state is OutboxState.EXPIRED
        assert entry.last_error == "RuntimeError"
        assert await dispatcher.dispatch_next("Licht") is False

    asyncio.run(scenario())


def test_run_cube_polls_until_retry_succeeds_and_stops_cleanly(tmp_path: Path) -> None:
    class FailsOncePlugin:
        kind = "test"

        def __init__(self) -> None:
            self.attempts = 0

        async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary failure")

    async def scenario() -> None:
        now = [30.0]
        plugin = FailsOncePlugin()
        registry = PluginRegistry()
        registry.register(plugin)
        outbox = SQLiteOutbox(tmp_path / "state.db")
        dispatcher = OutboxDispatcher(
            RuleEngine(rule_config()),
            outbox,
            registry,
            backoff_base_seconds=1.0,
            backoff_cap_seconds=1.0,
            clock=lambda: now[0],
            jitter=lambda delay: delay,
        )
        batch = dispatcher.enqueue_event(event("Licht", "light", 2))
        assert batch is not None
        stop = asyncio.Event()
        task = asyncio.create_task(dispatcher.run_cube("Licht", stop, poll_interval_seconds=0.001))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        while outbox.get_batch(batch)[0].attempts < 1:
            if loop.time() >= deadline:
                raise TimeoutError("First outbox attempt was not made")
            await asyncio.sleep(0.001)

        now[0] = 31.0
        while outbox.get_batch(batch)[0].state is not OutboxState.SUCCEEDED:
            if loop.time() >= deadline:
                stop.set()
                await task
                raise TimeoutError("Outbox retry did not complete")
            await asyncio.sleep(0.001)

        stop.set()
        await asyncio.wait_for(task, timeout=1.0)
        assert plugin.attempts == 2
        assert outbox.get_batch(batch)[0].state is OutboxState.SUCCEEDED

    asyncio.run(scenario())


def test_dispatcher_honors_retry_after_hint_from_adapter(tmp_path: Path) -> None:
    class RateLimitedPlugin:
        kind = "test"

        async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
            raise RetryableError("rate limited", retry_after_seconds=8.0)

    async def scenario() -> None:
        now = [100.0]
        registry = PluginRegistry()
        registry.register(RateLimitedPlugin())
        outbox = SQLiteOutbox(tmp_path / "state.db")
        dispatcher = OutboxDispatcher(
            RuleEngine(rule_config()),
            outbox,
            registry,
            backoff_base_seconds=1.0,
            backoff_cap_seconds=2.0,
            clock=lambda: now[0],
            jitter=lambda delay: delay,
        )
        batch = dispatcher.enqueue_event(event("Licht", "light", 2))
        assert batch is not None

        assert await dispatcher.dispatch_next("Licht")

        entry = outbox.get_batch(batch)[0]
        assert entry.available_at == 108.0
        assert entry.last_error == "RetryableError"
        assert entry.state is OutboxState.PENDING

    asyncio.run(scenario())
