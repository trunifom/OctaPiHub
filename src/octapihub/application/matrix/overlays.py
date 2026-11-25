"""Short-lived overlays (feedback, alerts, info text) and the state-change detector."""

from __future__ import annotations

from dataclasses import dataclass, field

from octapihub.application.matrix.canvas import (
    GRAY,
    GREEN,
    ICONS,
    RED,
    SIZE,
    WHITE,
    YELLOW,
    Canvas,
    Frame,
    Rgb,
    scale,
    text_columns,
)
from octapihub.application.matrix.state import DisplayState, battery_level
from octapihub.application.matrix.views import marquee

INFO_SPEED = 7.0  # columns per second
ALERT_SECONDS = 3.0
FACE_SECONDS = 0.9


@dataclass(frozen=True, slots=True)
class Overlay:
    """Something drawn on top of the current view for a short time."""

    kind: str  # "info" | "icon" | "face"
    started: float
    duration: float
    text: str = ""
    icon: str = ""
    color: Rgb = WHITE
    face: int | None = None
    cube_index: int = 0
    cube_count: int = 1
    priority: int = 1  # higher wins when a newer overlay arrives

    @staticmethod
    def info(text: str, now: float) -> Overlay:
        """Scroll ``text`` once from right to left."""
        columns = len(text_columns(text))
        return Overlay("info", now, (columns + SIZE) / INFO_SPEED + 0.3, text=text, priority=1)

    @staticmethod
    def icon_flash(icon: str, color: Rgb, now: float, seconds: float, priority: int = 2) -> Overlay:
        """Show a coloured icon for ``seconds``."""
        return Overlay("icon", now, seconds, icon=icon, color=color, priority=priority)

    @staticmethod
    def face_flash(cube_index: int, cube_count: int, face: int, now: float) -> Overlay:
        """Show the face a cube was just put on, with the cube's position marked."""
        return Overlay(
            "face",
            now,
            FACE_SECONDS,
            face=face,
            cube_index=cube_index,
            cube_count=cube_count,
            priority=1,
        )

    def expired(self, now: float) -> bool:
        """Whether the overlay is over."""
        return now - self.started >= self.duration

    def render(self, now: float) -> Frame:
        """Draw the overlay at ``now``."""
        canvas = Canvas()
        age = max(0.0, now - self.started)
        if self.kind == "info":
            marquee(canvas, self.text, WHITE, age, INFO_SPEED)
        elif self.kind == "icon":
            canvas.mask(ICONS.get(self.icon, ICONS["question"]), self.color)
        elif self.kind == "face":
            for dot in range(max(1, self.cube_count)):
                canvas.set(dot * 2, 0, WHITE if dot == self.cube_index else GRAY)
            canvas.text("-" if self.face is None else str(self.face), 2, 2, WHITE)
        return canvas.frame()


@dataclass(frozen=True, slots=True)
class Alert:
    """A noteworthy change between two snapshots."""

    key: str
    icon: str
    color: Rgb
    seconds: float = ALERT_SECONDS
    priority: int = 3


@dataclass(slots=True)
class AlertDetector:
    """Compare consecutive snapshots and report what the user should notice.

    Alerts fire once per change (a cube that stays disconnected does not alert again),
    and the very first snapshot only establishes the baseline.
    """

    _previous: DisplayState | None = None
    _announced: set[str] = field(default_factory=set)

    def detect(self, state: DisplayState) -> list[Alert]:
        """Return new alerts for ``state``."""
        previous, self._previous = self._previous, state
        if previous is None:
            return []
        alerts: list[Alert] = []
        before = {cube.name: cube for cube in previous.cubes}
        for cube in state.cubes:
            old = before.get(cube.name)
            if old is None:
                continue
            if old.connected and not cube.connected:
                alerts.append(Alert(f"lost:{cube.name}", "bluetooth", RED))
            elif not old.connected and cube.connected:
                alerts.append(Alert(f"back:{cube.name}", "bluetooth", GREEN, 1.5, 2))
            old_level, new_level = (
                battery_level(old.battery_percent),
                battery_level(cube.battery_percent),
            )
            key = f"battery:{cube.name}"
            if new_level == "ok" or new_level == "off":
                self._announced.discard(key + ":warn")
                self._announced.discard(key + ":error")
            elif new_level != old_level and key + ":" + new_level not in self._announced:
                self._announced.add(key + ":" + new_level)
                alerts.append(
                    Alert(key, "battery", RED if new_level == "error" else YELLOW, 3.5, 3)
                )
        if previous.hue_level != "error" and state.hue_level == "error":
            alerts.append(Alert("hue", "bulb", RED))
        if previous.sense_level != "error" and state.sense_level == "error":
            alerts.append(Alert("sense", "warning", RED))
        if previous.host_level != "error" and state.host_level == "error":
            alerts.append(Alert("host", "thermometer", RED))
        return alerts


def alert_overlay(alert: Alert, now: float) -> Overlay:
    """Turn an alert into a drawable overlay."""
    return Overlay.icon_flash(
        alert.icon, scale(alert.color, 1.0), now, alert.seconds, alert.priority
    )
