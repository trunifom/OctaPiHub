"""Builds the matrix' display snapshot from the live services."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from types import MappingProxyType

from octapihub.application.ble_runtime import CubeConnectionState, CubeRuntimeStatus
from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import HueService, HueState
from octapihub.application.matrix.state import CubeDisplay, DisplayState, MusicDisplay, Reading
from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat import ColorSensorReading, Orientation, Vector3
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.sense_hat_worker import SensorGroupState
from octapihub.core.config import CubeConfig

HISTORY_LENGTH = 8
_HISTORY_GROUPS = ("temperature", "humidity", "pressure")
_CUBE_LEVELS = {
    CubeConnectionState.READY: "ok",
    CubeConnectionState.WAITING_FOR_BASELINE: "ok",
    CubeConnectionState.WAITING_FOR_RADIO: "info",
    CubeConnectionState.CONNECTING: "info",
    CubeConnectionState.DISCOVERING: "info",
    CubeConnectionState.SUBSCRIBING: "info",
    CubeConnectionState.RECONNECT_WAIT: "warn",
    CubeConnectionState.UNSUPPORTED: "error",
    CubeConnectionState.FAILED: "error",
    CubeConnectionState.STOPPED: "off",
}

type Clock = Callable[[], datetime]
type HostLevel = Callable[[], str]


class DisplayStateBuilder:
    """Collect cube, Hue, sensor and host data into a :class:`DisplayState`.

    Cheap enough to call ten times per second; the host level (which may run a command)
    is cached for ``host_interval`` seconds. Any single source that fails is shown as
    ``off``/missing, never as an exception.
    """

    def __init__(
        self,
        cubes_config: Callable[[], tuple[CubeConfig, ...]],
        sense: SenseHatService,
        cubes: CubeService | None = None,
        hue: HueService | None = None,
        host_level: HostLevel | None = None,
        joystick_faces: Callable[[], tuple[int, ...]] | None = None,
        music: MusicService | None = None,
        *,
        clock: Clock = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        host_interval: float = 30.0,
    ) -> None:
        self._cubes_config = cubes_config
        self._sense = sense
        self._cubes = cubes
        self._hue = hue
        self._host_level = host_level
        self._joystick_faces = joystick_faces
        self._music = music
        self._clock = clock
        self._monotonic = monotonic
        self._host_interval = host_interval
        self._host_cached = "off"
        self._host_checked = -1e9
        self._history: dict[str, deque[float]] = {
            group: deque(maxlen=HISTORY_LENGTH) for group in _HISTORY_GROUPS
        }
        self._last_sample_time: dict[str, datetime] = {}

    def build(self) -> DisplayState:
        """Snapshot the hub; never raises."""
        readings = self._readings()
        return DisplayState(
            now=self._clock(),
            cubes=self._cube_displays(),
            hue_level=self._hue_level(),
            sense_level=self._sense_level(),
            host_level=self._host(),
            joystick_faces=self._faces(),
            music=self._music_display(),
            readings=MappingProxyType(readings),
            history=MappingProxyType({g: tuple(v) for g, v in self._history.items()}),
        )

    # ----- parts -----

    def _cube_displays(self) -> tuple[CubeDisplay, ...]:
        statuses: dict[str, CubeRuntimeStatus] = {}
        if self._cubes is not None:
            statuses = {status.name: status for status in self._cubes.statuses}
        displays: list[CubeDisplay] = []
        for cube in self._cubes_config():
            status = statuses.get(cube.name)
            if status is None:
                displays.append(CubeDisplay(cube.name, cube.role.value, "off", False))
                continue
            displays.append(
                CubeDisplay(
                    cube.name,
                    cube.role.value,
                    _CUBE_LEVELS.get(status.state, "off"),
                    status.connected,
                    status.last_value,
                    status.battery_percent,
                )
            )
        return tuple(displays)

    def _hue_level(self) -> str:
        if self._hue is None:
            return "off"
        status = self._hue.status
        if status.state is HueState.DISABLED:
            return "off"
        if status.state is HueState.NOT_PAIRED:
            return "warn"
        return "error" if status.last_error else "ok"

    def _sense_level(self) -> str:
        sense = self._sense
        if sense.hardware_error is not None:
            return "error"
        statuses = sense.statuses
        if not sense.running or not statuses:
            return "off"
        ok = [s for s in statuses if s.state is SensorGroupState.OK]
        failing = [s for s in statuses if s.state is SensorGroupState.ERROR]
        if failing:
            return "warn" if ok else "error"
        return "ok" if ok else "info"

    def _music_display(self) -> MusicDisplay | None:
        if self._music is None:
            return None
        try:
            status = self._music.status
            if not status.enabled:
                return None
            ranked = {"playing": 0, "buffering": 1, "paused": 2, "error": 3}
            active = sorted(
                status.targets,
                key=lambda t: (ranked.get("error" if t.error else t.state.value, 9), not t.current),
            )
            if not active:
                return MusicDisplay()
            best = active[0]
            state = "error" if best.error else best.state.value
            if state not in ranked:
                state = "idle"
            return MusicDisplay(state, best.volume, best.track_title or best.title, best.name)
        except Exception:  # noqa: BLE001 - the display must never fail because of music
            return None

    def _faces(self) -> tuple[int, ...]:
        if self._joystick_faces is None:
            return ()
        try:
            return tuple(self._joystick_faces())
        except Exception:  # noqa: BLE001 - optional information
            return ()

    def _host(self) -> str:
        if self._host_level is None:
            return "off"
        now = self._monotonic()
        if now - self._host_checked >= self._host_interval:
            self._host_checked = now
            try:
                self._host_cached = self._host_level()
            except Exception:  # noqa: BLE001 - host data is optional
                self._host_cached = "off"
        return self._host_cached

    def _readings(self) -> dict[str, Reading]:
        readings: dict[str, Reading] = {}
        for status in self._sense.statuses:
            sample = status.last_sample
            if sample is None or status.state is not SensorGroupState.OK:
                continue
            value = sample.reading.value
            group = status.group
            if isinstance(value, float):
                shown = (
                    sample.corrected_temperature_c
                    if group == "temperature" and sample.corrected_temperature_c is not None
                    else value
                )
                readings[group] = Reading(value=shown)
                if (
                    group in self._history
                    and self._last_sample_time.get(group) != sample.measured_at
                ):
                    self._last_sample_time[group] = sample.measured_at
                    self._history[group].append(shown)
            elif isinstance(value, Orientation):
                readings[group] = Reading(vector=(value.pitch, value.roll, value.yaw))
            elif isinstance(value, Vector3):
                readings[group] = Reading(vector=(value.x, value.y, value.z))
            elif isinstance(value, ColorSensorReading):
                readings[group] = Reading(rgba=value.scaled_rgba)
        return readings
