"""Lifecycle owner for the cube pipeline: BLE runtime -> rules -> outbox -> plugins."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from octapihub.application.ble_runtime import BleRuntime, CubeRuntimeStatus
from octapihub.application.outbox import OutboxDispatcher, OutboxEntry
from octapihub.application.rules import RuleEngine
from octapihub.core.config import AppConfig
from octapihub.core.errors import OctaPiHubError
from octapihub.domain.cube import OrientationKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CubeActivity:
    """Something that happened with a cube, for status displays and feedback.

    ``kind`` is ``face`` (a cube settled on a face), ``action_ok`` or ``action_failed``.
    """

    kind: str
    cube_name: str
    face: int | None = None


type ActivityListener = Callable[[CubeActivity], None]
type RuntimeFactory = Callable[[AppConfig], BleRuntime]
type DispatcherFactory = Callable[[AppConfig], OutboxDispatcher]

_RESTART_DELAY_SECONDS = 5.0


class CubeService:
    """Start and stop cube handling without letting a fault leave the hub.

    If the action pipeline cannot be built (for example a configured plugin is not
    installed or the outbox database is unusable), nothing is started and ``error`` /
    ``error_code`` explain why; the rest of the hub keeps running. Once running, a failing
    event or one cube's dispatcher never stops the others.
    """

    def __init__(
        self,
        config: AppConfig,
        runtime_factory: RuntimeFactory,
        dispatcher_factory: DispatcherFactory,
        *,
        poll_interval_seconds: float = 0.1,
        restart_delay_seconds: float = _RESTART_DELAY_SECONDS,
    ) -> None:
        self._config = config
        self._runtime_factory = runtime_factory
        self._dispatcher_factory = dispatcher_factory
        self._poll_interval = poll_interval_seconds
        self._restart_delay = restart_delay_seconds
        self._runtime: BleRuntime | None = None
        self._dispatcher: OutboxDispatcher | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._listeners: list[ActivityListener] = []
        self._stop = asyncio.Event()
        self.error: str | None = None
        self.error_code: str | None = None

    @property
    def config(self) -> AppConfig:
        """Configuration the service was built with."""
        return self._config

    @property
    def running(self) -> bool:
        """Whether the BLE runtime and dispatchers are active."""
        return self._runtime is not None

    @property
    def statuses(self) -> tuple[CubeRuntimeStatus, ...]:
        """Per-cube status snapshots; empty when not running."""
        return self._runtime.statuses if self._runtime is not None else ()

    async def start(self) -> None:
        """Build the pipeline and start it; failures are recorded, not raised."""
        if self._runtime is not None:
            raise RuntimeError("Cube service has already been started")
        self.error = None
        self.error_code = None
        try:
            dispatcher = await asyncio.to_thread(self._dispatcher_factory, self._config)
            runtime = self._runtime_factory(self._config)
        except OctaPiHubError as exc:
            self.error = exc.user_message
            self.error_code = exc.error_code
            logger.error("Cube pipeline unavailable: %s", exc)
            return
        except OSError as exc:
            self.error = "Der Datenordner ist nicht beschreibbar."
            self.error_code = "CH-DB-001"
            logger.error("Cube pipeline unavailable: %s", exc)
            return
        self._stop = asyncio.Event()
        await runtime.start()
        self._runtime = runtime
        self._dispatcher = dispatcher
        dispatcher.set_observer(self._on_outcome)
        self._tasks = [
            asyncio.create_task(self._route(runtime, dispatcher), name="octapihub-router")
        ]
        self._tasks.extend(
            asyncio.create_task(
                self._dispatch(dispatcher, cube.name), name=f"octapihub-dispatch-{i}"
            )
            for i, cube in enumerate(self._config.cubes)
        )

    def add_listener(self, listener: ActivityListener) -> None:
        """Register a callback for cube activity; failures inside it are logged, not raised."""
        self._listeners.append(listener)

    def _emit(self, activity: CubeActivity) -> None:
        for listener in list(self._listeners):
            try:
                listener(activity)
            except Exception:
                logger.exception("Cube activity listener failed")

    def _on_outcome(self, entry: OutboxEntry, succeeded: bool) -> None:
        self._emit(CubeActivity("action_ok" if succeeded else "action_failed", entry.cube))

    async def update_config(self, config: AppConfig) -> None:
        """Apply changed cube mappings without reconnecting any cube.

        If the pipeline is running, the rules switch atomically; a rejected update (for
        example a missing plugin) raises and keeps the old rules. If the pipeline is not
        running because it could not be built earlier, it is started again with the new
        configuration.

        Raises:
            PluginError: A plugin needed by the new mappings is not registered.
        """
        dispatcher = self._dispatcher
        if dispatcher is not None:
            dispatcher.update_rules(RuleEngine(config))
            self._config = config
            return
        self._config = config
        if self._runtime is None:
            await self.start()

    async def stop(self) -> None:
        """Stop dispatchers and the BLE runtime; always releases Bluetooth connections."""
        runtime = self._runtime
        if runtime is None:
            return
        self._runtime = None
        self._dispatcher = None
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        try:
            await runtime.stop()
        except RuntimeError:
            logger.exception("BLE runtime reported a failure during shutdown")

    async def _route(self, runtime: BleRuntime, dispatcher: OutboxDispatcher) -> None:
        while True:
            event = await runtime.next_event()
            if event.orientation.kind is OrientationKind.FACE_CHANGE:
                self._emit(CubeActivity("face", event.cube_name, event.orientation.value))
            try:
                dispatcher.enqueue_event(event)
            except Exception:
                logger.exception("Could not queue actions for cube %s", event.cube_name)

    async def _dispatch(self, dispatcher: OutboxDispatcher, cube_name: str) -> None:
        while not self._stop.is_set():
            try:
                await dispatcher.run_cube(
                    cube_name, self._stop, poll_interval_seconds=self._poll_interval
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Dispatcher for cube %s failed; restarting", cube_name)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self._restart_delay)
