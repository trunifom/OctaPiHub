"""Joystick-driven navigation: a small, pure state machine without any I/O.

Controls (relative to the picture on the matrix):

* ← / →   previous / next view (in *Lampe* edit mode: colour)
* ↑ / ↓   the view's own selection (cube, measurement, palette ...), or brightness
* Press   short: scroll a text describing the view (in *Lampe*: edit mode on/off)
* Hold    long press: display off / on
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from octapihub.application.matrix.state import DisplayState
from octapihub.application.matrix.views import (
    BRIGHTNESS_STEPS,
    LAMP_PRESETS,
    VIEWS,
    ViewUi,
)
from octapihub.application.sense_hat import JoystickAction, JoystickDirection, SenseHatInputEvent
from octapihub.core.config import SenseHatMatrixConfig

HOLD_REPEAT_SECONDS = 0.18
AUTO_CYCLE_PAUSE_SECONDS = 60.0
# Clockwise rotation of the picture -> where the user's "up" lies on the board's joystick.
_ROTATION_ORDER = (
    JoystickDirection.UP,
    JoystickDirection.RIGHT,
    JoystickDirection.DOWN,
    JoystickDirection.LEFT,
)


class Effect(StrEnum):
    """Something the display service must do besides re-rendering."""

    NONE = "none"
    SHOW_INFO = "show_info"
    TOGGLE_DISPLAY = "toggle_display"
    VIEW_CHANGED = "view_changed"


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    """Read-only view of the controller for the service, web, and tests."""

    view_id: str
    display_on: bool
    brightness: int
    edit_mode: bool


def logical_direction(direction: JoystickDirection, rotation: int) -> JoystickDirection:
    """Map a physical joystick direction to the direction in the rotated picture.

    The Sense HAT reports physical directions. When the picture is rotated (so it reads
    correctly for the user), "up" in the picture is another physical direction.
    """
    if direction not in _ROTATION_ORDER or rotation % 360 == 0:
        return direction
    steps = (rotation // 90) % 4
    return _ROTATION_ORDER[(_ROTATION_ORDER.index(direction) - steps) % 4]


class DisplayController:
    """Navigation state plus the rules for every joystick input."""

    def __init__(self, matrix: SenseHatMatrixConfig, *, now: float = 0.0) -> None:
        self._views = matrix.views
        self._rotation = matrix.rotation
        self._cycle_seconds = matrix.auto_cycle_seconds
        self._index = self._views.index(matrix.default_view)
        self.ui = ViewUi(brightness=_nearest_step(matrix.brightness))
        self.display_on = True
        self._last_input = -AUTO_CYCLE_PAUSE_SECONDS
        self._last_cycle = now
        self._last_repeat = -1.0
        self._middle_down = False
        self._middle_long = False

    # ----- read access -----

    @property
    def view_id(self) -> str:
        """Id of the view currently shown."""
        return self._views[self._index]

    @property
    def views(self) -> tuple[str, ...]:
        """Enabled views in navigation order."""
        return self._views

    @property
    def snapshot(self) -> ControllerSnapshot:
        """Current navigation state."""
        return ControllerSnapshot(
            self.view_id, self.display_on, self.ui.brightness, self.ui.edit_mode
        )

    # ----- commands (also used by the web interface) -----

    def show(self, view_id: str) -> bool:
        """Switch to ``view_id`` if it is enabled; returns whether that worked."""
        if view_id not in self._views:
            return False
        self._index = self._views.index(view_id)
        self.ui.edit_mode = False
        return True

    def step_view(self, delta: int) -> None:
        """Move to the previous (-1) or next (+1) enabled view, wrapping around."""
        self._index = (self._index + delta) % len(self._views)
        self.ui.edit_mode = False

    def set_brightness(self, percent: int) -> None:
        """Set the brightness to the nearest supported step."""
        self.ui.brightness = _nearest_step(percent)

    def adjust_brightness(self, direction: int) -> None:
        """Step the brightness up (+1) or down (-1)."""
        index = BRIGHTNESS_STEPS.index(self.ui.brightness)
        self.ui.brightness = BRIGHTNESS_STEPS[
            max(0, min(len(BRIGHTNESS_STEPS) - 1, index + direction))
        ]

    def set_display(self, on: bool) -> None:
        """Switch the display on or off (the joystick can switch it back on)."""
        self.display_on = on

    # ----- input -----

    def handle(self, event: SenseHatInputEvent, state: DisplayState, now: float) -> Effect:
        """Apply one joystick event; ``now`` is a monotonic clock in seconds."""
        self._last_input = now
        if event.direction in (JoystickDirection.MIDDLE, JoystickDirection.ANY):
            return self._handle_middle(event)
        if not self.display_on:
            return Effect.NONE  # a sleeping display only reacts to the middle button
        direction = logical_direction(event.direction, self._rotation)
        if event.action is JoystickAction.RELEASED:
            return Effect.NONE
        if event.action is JoystickAction.HELD:
            if direction not in (JoystickDirection.UP, JoystickDirection.DOWN):
                return Effect.NONE
            if now - self._last_repeat < HOLD_REPEAT_SECONDS:
                return Effect.NONE
        self._last_repeat = now
        view = VIEWS[self.view_id]
        if direction in (JoystickDirection.LEFT, JoystickDirection.RIGHT):
            delta = -1 if direction is JoystickDirection.LEFT else 1
            if self.ui.edit_mode and view.has_edit_mode:
                self.ui.lamp_index = (self.ui.lamp_index + delta) % len(LAMP_PRESETS)
                return Effect.NONE
            self.step_view(delta)
            return Effect.VIEW_CHANGED
        if direction in (JoystickDirection.UP, JoystickDirection.DOWN):
            self._vertical(
                view.id, view.sub_count(state), +1 if direction is JoystickDirection.UP else -1
            )
        return Effect.NONE

    def _vertical(self, view_id: str, sub_count: int, direction: int) -> None:
        if self.ui.edit_mode and view_id == "lamp":
            self.ui.lamp_value = max(10, min(100, self.ui.lamp_value + 10 * direction))
            return
        if sub_count > 0:
            # up moves to the previous item, down to the next (like a list)
            current = self.ui.selected(view_id, sub_count)
            self.ui.sub[view_id] = (current - direction) % sub_count
            return
        self.adjust_brightness(direction)

    def _handle_middle(self, event: SenseHatInputEvent) -> Effect:
        if event.action is JoystickAction.PRESSED:
            self._middle_down = True
            self._middle_long = False
            return Effect.NONE
        if event.action is JoystickAction.HELD:
            if self._middle_down and not self._middle_long:
                self._middle_long = True
                self.display_on = not self.display_on
                return Effect.TOGGLE_DISPLAY
            return Effect.NONE
        # released
        was_short = self._middle_down and not self._middle_long
        self._middle_down = False
        if not was_short or not self.display_on:
            return Effect.NONE
        view = VIEWS[self.view_id]
        if view.has_edit_mode:
            self.ui.edit_mode = not self.ui.edit_mode
            return Effect.NONE
        return Effect.SHOW_INFO

    # ----- time -----

    def tick(self, now: float) -> bool:
        """Advance the optional auto-cycle; returns whether the view changed."""
        if (
            self._cycle_seconds <= 0
            or not self.display_on
            or self.ui.edit_mode
            or len(self._views) < 2
            or now - self._last_input < AUTO_CYCLE_PAUSE_SECONDS
            or now - self._last_cycle < self._cycle_seconds
        ):
            return False
        self._last_cycle = now
        self.step_view(1)
        return True


def _nearest_step(percent: int) -> int:
    return min(BRIGHTNESS_STEPS, key=lambda step: abs(step - percent))
