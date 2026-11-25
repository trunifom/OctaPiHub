"""The LED matrix display service: one task owns the matrix and the joystick."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import time as clock_time

from octapihub.application.matrix.canvas import BLACK, Frame, apply_brightness
from octapihub.application.matrix.controller import DisplayController, Effect
from octapihub.application.matrix.overlays import (
    AlertDetector,
    Overlay,
    alert_overlay,
)
from octapihub.application.matrix.state import DisplayState
from octapihub.application.matrix.views import VIEWS, ViewContext
from octapihub.application.sense_hat import (
    JoystickAction,
    JoystickDirection,
    SenseHatInputEvent,
    SenseHatPort,
)
from octapihub.core.config import SenseHatMatrixConfig
from octapihub.core.errors import OctaPiHubError

logger = logging.getLogger(__name__)

type PortProvider = Callable[[], SenseHatPort | None]
type StateProvider = Callable[[], DisplayState]
type Monotonic = Callable[[], float]

TICK_SECONDS = 0.1
MAX_BACKOFF_SECONDS = 30.0
NIGHT_WAKE_SECONDS = 30.0
NIGHT_DIM_PERCENT = 5
FAILURE_FLASH_GAP_SECONDS = 10.0
AMBIENT_MIN_FACTOR = 0.25
AMBIENT_FULL_CLEAR = 200


@dataclass(frozen=True, slots=True)
class DisplayStatus:
    """Safe status snapshot for the interface; contains no secrets."""

    state: str  # "stopped" | "running" | "unavailable" | "error" | "disabled"
    view_id: str
    display_on: bool
    brightness: int
    effective_brightness: int
    night: bool
    last_error: str | None
    last_error_code: str | None
    consecutive_failures: int
    frame: Frame


def in_night_window(now: clock_time, start: str, end: str) -> bool:
    """Whether ``now`` lies in the daily window ``start``..``end`` (may cross midnight).

    Equal start and end mean an empty window, so a mistaken setting never blanks the display.
    """
    begin = clock_time.fromisoformat(start)
    finish = clock_time.fromisoformat(end)
    if begin == finish:
        return False
    if begin < finish:
        return begin <= now < finish
    return now >= begin or now < finish


def ambient_factor(rgba: tuple[int, int, int, int] | None) -> float:
    """Brightness multiplier from the colour sensor's clear channel (dim room, dim LEDs)."""
    if rgba is None:
        return 1.0
    clear = max(0, rgba[3])
    return AMBIENT_MIN_FACTOR + (1 - AMBIENT_MIN_FACTOR) * min(1.0, clear / AMBIENT_FULL_CLEAR)


class MatrixDisplayService:
    """Render the chosen view, handle the joystick, and survive hardware faults.

    Only this service's task touches the matrix and joystick (no calls from BLE callbacks).
    Public control methods must be called on the event loop thread.
    """

    def __init__(
        self,
        config: SenseHatMatrixConfig,
        port_provider: PortProvider,
        state_provider: StateProvider,
        *,
        joystick_enabled: bool = True,
        monotonic: Monotonic = time.monotonic,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._config = config
        self._joystick_enabled = joystick_enabled
        self._port_provider = port_provider
        self._state_provider = state_provider
        self._monotonic = monotonic
        self._tick = tick_seconds
        self._controller = DisplayController(config, now=monotonic())
        self._detector = AlertDetector()
        self._overlay: Overlay | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake_until = 0.0
        self._last_failure_flash: dict[str, float] = {}
        self._port: SenseHatPort | None = None
        self._last_written: Frame | None = None
        self._preview: Frame = (BLACK,) * 64
        self._effective_brightness = 0
        self._night = False
        self._failures = 0
        self._last_error: str | None = None
        self._last_error_code: str | None = None
        self._status_state = "stopped"

    # ----- read access -----

    @property
    def controller(self) -> DisplayController:
        """Navigation state (read it, or change it only on the loop thread)."""
        return self._controller

    @property
    def config(self) -> SenseHatMatrixConfig:
        """Matrix settings in use."""
        return self._config

    @property
    def status(self) -> DisplayStatus:
        """Current status snapshot."""
        snap = self._controller.snapshot
        return DisplayStatus(
            state=self._status_state,
            view_id=snap.view_id,
            display_on=snap.display_on,
            brightness=snap.brightness,
            effective_brightness=self._effective_brightness,
            night=self._night,
            last_error=self._last_error,
            last_error_code=self._last_error_code,
            consecutive_failures=self._failures,
            frame=self._preview,
        )

    # ----- lifecycle -----

    async def start(self) -> None:
        """Start the display task; a missing matrix leaves the service ``unavailable``."""
        if self._task is not None:
            raise RuntimeError("Matrix display service has already been started")
        self._stop = asyncio.Event()
        if not self._config.enabled:
            self._status_state = "disabled"
            return
        self._status_state = "running"
        self._task = asyncio.create_task(self._run(), name="octapihub-matrix")

    async def stop(self) -> None:
        """Stop the task and blank the matrix (best effort)."""
        task, self._task = self._task, None
        if task is None:
            return
        self._stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        port = self._port
        self._port = None
        self._last_written = None
        self._status_state = "stopped"
        if port is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(port.clear)

    async def restart(self, config: SenseHatMatrixConfig, *, joystick_enabled: bool) -> None:
        """Apply new settings.

        The current view is kept when it is still enabled, unless the default view was
        changed: choosing a new default then takes effect immediately.
        """
        current = self._controller.view_id
        default_changed = config.default_view != self._config.default_view
        await self.stop()
        self._config = config
        self._joystick_enabled = joystick_enabled
        self._controller = DisplayController(config, now=self._monotonic())
        if not default_changed:
            self._controller.show(current)  # keep what the user is looking at
        self._overlay = None
        self._detector = AlertDetector()
        await self.start()

    # ----- control (called by the web interface; loop thread only) -----

    def show_view(self, view_id: str) -> bool:
        """Switch to an enabled view."""
        self._overlay = None
        return self._controller.show(view_id)

    def set_brightness(self, percent: int) -> None:
        """Set the brightness (nearest supported step)."""
        self._controller.set_brightness(percent)

    def set_display(self, on: bool) -> None:
        """Switch the display on or off."""
        self._controller.set_display(on)
        self._overlay = None

    def inject(
        self, direction: JoystickDirection, action: JoystickAction, state: DisplayState
    ) -> None:
        """Feed a virtual joystick event (the on-screen joystick uses this)."""
        event = SenseHatInputEvent(self._monotonic(), direction, action)
        self._apply_event(event, state, self._monotonic())

    def notify_face(self, cube_name: str, face: int | None) -> None:
        """Brief feedback that a cube reported a face."""
        if not self._controller.display_on or face is None or self._night_blocks(self._monotonic()):
            return
        state = self._state_provider()
        names = [cube.name for cube in state.cubes]
        index = names.index(cube_name) if cube_name in names else 0
        self._offer(Overlay.face_flash(index, max(1, len(names)), face, self._monotonic()))

    def notify_action(self, cube_name: str, ok: bool) -> None:
        """Feedback that an action finished (green check) or failed (red cross)."""
        now = self._monotonic()
        if not self._controller.display_on or self._night_blocks(now):
            return
        if not ok:
            if now - self._last_failure_flash.get(cube_name, -1e9) < FAILURE_FLASH_GAP_SECONDS:
                return
            self._last_failure_flash[cube_name] = now
        from octapihub.application.matrix.canvas import GREEN, RED

        self._offer(
            Overlay.icon_flash(
                "check" if ok else "cross", GREEN if ok else RED, now, 1.2 if ok else 2.5, 2
            )
        )

    # ----- internals -----

    def _offer(self, overlay: Overlay) -> None:
        current = self._overlay
        if (
            current is not None
            and not current.expired(self._monotonic())
            and current.priority > overlay.priority
        ):
            return
        self._overlay = overlay

    def _night_blocks(self, now: float) -> bool:
        return self._night and self._config.night_mode == "off" and now >= self._wake_until

    def _apply_event(self, event: SenseHatInputEvent, state: DisplayState, now: float) -> None:
        if self._night and now >= self._wake_until:
            self._wake_until = now + NIGHT_WAKE_SECONDS  # any touch wakes the display for a while
        effect = self._controller.handle(event, state, now)
        if effect is Effect.SHOW_INFO:
            text = VIEWS[self._controller.view_id].describe(state)
            self._overlay = Overlay.info(text, now)
        elif effect in (Effect.VIEW_CHANGED, Effect.TOGGLE_DISPLAY):
            self._overlay = None

    async def _run(self) -> None:
        backoff = 0.0
        while not self._stop.is_set():
            try:
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the display must never stop the hub
                self._record_failure(exc)
                backoff = min(MAX_BACKOFF_SECONDS, max(0.5, backoff * 2 or 0.5))
                self._status_state = "error"
                self._last_written = None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                continue
            backoff = 0.0
            self._failures = 0
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick)

    def _record_failure(self, exc: Exception) -> None:
        self._failures += 1
        if isinstance(exc, OctaPiHubError):
            self._last_error, self._last_error_code = exc.user_message, exc.error_code
        else:
            self._last_error = "Unerwarteter Fehler bei der LED-Anzeige. Details stehen im Log."
            self._last_error_code = OctaPiHubError.code
        if self._failures == 1:
            logger.warning("LED matrix failure: %s", exc)
        else:
            logger.debug("LED matrix still failing (%d): %s", self._failures, exc)

    async def _cycle(self) -> None:
        port = self._port_provider()
        if port is None:
            self._status_state = "unavailable"
            self._port = None
            self._last_written = None
            return
        if port is not self._port:
            await self._configure(port)
        now = self._monotonic()
        state = self._state_provider()
        if self._joystick_enabled and port.capabilities.joystick:
            for event in await asyncio.to_thread(port.get_joystick_events):
                self._apply_event(event, state, now)
        if self._controller.tick(now):
            self._overlay = None
        for alert in self._detector.detect(state):
            if self._controller.display_on and not self._night_blocks(now):
                self._offer(alert_overlay(alert, now))
        frame = self._compose(state, now)
        if frame != self._last_written:
            await asyncio.to_thread(port.set_pixels, frame)
            self._last_written = frame
        self._status_state = "running"
        self._last_error = None
        self._last_error_code = None

    async def _configure(self, port: SenseHatPort) -> None:
        if not port.capabilities.led_matrix:
            raise OctaPiHubError("This Sense HAT has no usable LED matrix")
        await asyncio.to_thread(port.set_rotation, self._config.rotation)
        await asyncio.to_thread(setattr, port, "low_light", self._config.low_light)
        self._port = port
        self._last_written = None
        logger.info("LED matrix display started")

    def _compose(self, state: DisplayState, now: float) -> Frame:
        controller = self._controller
        local = state.now.timetz().replace(tzinfo=None)
        self._night = self._config.night_enabled and in_night_window(
            local, self._config.night_start, self._config.night_end
        )
        percent = controller.ui.brightness
        if self._config.ambient_brightness:
            reading = state.readings.get("color")
            percent = round(percent * ambient_factor(reading.rgba if reading else None))
        awake = now < self._wake_until
        if self._night and not awake:
            percent = 0 if self._config.night_mode == "off" else min(percent, NIGHT_DIM_PERCENT)
        elif self._night:
            percent = min(percent, 20)
        self._effective_brightness = percent
        overlay = self._overlay
        if overlay is not None and overlay.expired(now):
            overlay = self._overlay = None
        if not controller.display_on:
            frame: Frame = (BLACK,) * 64
        elif overlay is not None:
            frame = overlay.render(now)
        else:
            view = VIEWS[controller.view_id]
            frame = view.render(ViewContext(state, controller.ui, now))
        self._preview = frame if controller.display_on else (BLACK,) * 64
        return apply_brightness(frame, percent)
