"""Application contract for the Sense HAT sensors, joystick, and LED matrix."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from octapihub.core.config import SenseHatVersion

type RGBColor = tuple[int, int, int]
type PixelBuffer = tuple[RGBColor, ...]


class SenseHatMetric(StrEnum):
    """Individual public sensor readings exposed by the Sense HAT API."""

    TEMPERATURE = "temperature"
    TEMPERATURE_HUMIDITY = "temperature_humidity"
    TEMPERATURE_PRESSURE = "temperature_pressure"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    ORIENTATION = "orientation"
    ORIENTATION_DEGREES = "orientation_degrees"
    ORIENTATION_RADIANS = "orientation_radians"
    COMPASS = "compass"
    COMPASS_RAW = "compass_raw"
    GYROSCOPE = "gyroscope"
    GYROSCOPE_RAW = "gyroscope_raw"
    ACCELEROMETER = "accelerometer"
    ACCELEROMETER_RAW = "accelerometer_raw"
    COLOR = "color"


class JoystickDirection(StrEnum):
    """Directions reported by the five-way Sense HAT joystick."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    ANY = "any"


class JoystickAction(StrEnum):
    """Actions reported by the Sense HAT joystick."""

    PRESSED = "pressed"
    RELEASED = "released"
    HELD = "held"


class SenseHatVersionSource(StrEnum):
    """Evidence used to report the Sense HAT hardware version."""

    CONFIGURATION = "configuration"
    COLOR_SENSOR = "color_sensor"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Orientation:
    """Pitch, roll, and yaw in the unit named by the reading."""

    pitch: float
    roll: float
    yaw: float


@dataclass(frozen=True, slots=True)
class Vector3:
    """Three-axis sensor value."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class ColorSensorReading:
    """Scaled and raw RGBC channels plus the active sensor integration settings."""

    scaled_rgba: tuple[int, int, int, int]
    raw_rgba: tuple[int, int, int, int]
    gain: int
    integration_cycles: int
    integration_time: float
    max_raw: int


type SensorValue = float | Orientation | Vector3 | ColorSensorReading


@dataclass(frozen=True, slots=True)
class SenseHatReading:
    """Unrounded sensor value and its unit."""

    metric: SenseHatMetric
    value: SensorValue
    unit: str


@dataclass(frozen=True, slots=True)
class SenseHatInputEvent:
    """Validated joystick input event."""

    timestamp: float
    direction: JoystickDirection
    action: JoystickAction


@dataclass(frozen=True, slots=True)
class SenseHatCapabilities:
    """Runtime-detected Sense HAT hardware and API capabilities."""

    environmental_sensors: bool
    imu: bool
    joystick: bool
    led_matrix: bool
    color_sensor: bool
    hardware_version: SenseHatVersion | None
    version_source: SenseHatVersionSource


type JoystickCallback = Callable[[SenseHatInputEvent], None]


class SenseHatPort(Protocol):
    """Typed application boundary for the public Sense HAT API."""

    @property
    def capabilities(self) -> SenseHatCapabilities:
        """Return the capabilities detected for the connected board."""

    def read_metric(self, metric: SenseHatMetric) -> SenseHatReading:
        """Read one metric without rounding or hiding sensor failures."""

    def set_imu_config(
        self,
        compass_enabled: bool,
        gyro_enabled: bool,
        accel_enabled: bool,
    ) -> None:
        """Enable or disable the IMU contributions used by orientation readings."""

    def get_joystick_events(self) -> tuple[SenseHatInputEvent, ...]:
        """Return and drain pending joystick events."""

    def wait_for_joystick_event(
        self,
        *,
        empty_buffer: bool = False,
    ) -> SenseHatInputEvent | None:
        """Block until a joystick event is available."""

    def set_joystick_callback(
        self,
        direction: JoystickDirection,
        callback: JoystickCallback | None,
    ) -> None:
        """Install or clear one documented direction callback."""

    @property
    def rotation(self) -> int:
        """Current LED matrix rotation."""

    @rotation.setter
    def rotation(self, value: int) -> None: ...

    @property
    def low_light(self) -> bool:
        """Whether LED matrix low-light mode is enabled."""

    @low_light.setter
    def low_light(self, value: bool) -> None: ...

    @property
    def gamma(self) -> tuple[int, ...]:
        """Current 32-entry LED gamma table."""

    @gamma.setter
    def gamma(self, value: Sequence[int]) -> None: ...

    def gamma_reset(self) -> None:
        """Restore the matrix gamma table to its hardware default."""

    def set_rotation(self, rotation: int, *, redraw: bool = True) -> None:
        """Rotate the LED matrix by a supported quarter-turn."""

    def flip_h(self, *, redraw: bool = True) -> PixelBuffer:
        """Return the horizontally flipped pixels and optionally display them."""

    def flip_v(self, *, redraw: bool = True) -> PixelBuffer:
        """Return the vertically flipped pixels and optionally display them."""

    def set_pixels(self, pixels: Sequence[RGBColor]) -> None:
        """Set all 64 matrix pixels."""

    def get_pixels(self) -> PixelBuffer:
        """Read all 64 matrix pixels."""

    def set_pixel(self, x: int, y: int, color: RGBColor) -> None:
        """Set one pixel."""

    def get_pixel(self, x: int, y: int) -> RGBColor:
        """Read one pixel."""

    def load_image(self, path: str, *, redraw: bool = True) -> PixelBuffer:
        """Load an 8x8 image and optionally draw it."""

    def clear(self, color: RGBColor = (0, 0, 0)) -> None:
        """Fill the matrix with one colour."""

    def show_message(
        self,
        text: str,
        *,
        scroll_speed: float = 0.1,
        text_color: RGBColor = (255, 255, 255),
        background_color: RGBColor = (0, 0, 0),
    ) -> None:
        """Scroll text across the matrix."""

    def show_letter(
        self,
        letter: str,
        *,
        text_color: RGBColor = (255, 255, 255),
        background_color: RGBColor = (0, 0, 0),
    ) -> None:
        """Display one character."""

    def set_color_gain(self, gain: int) -> None:
        """Set the v2 colour sensor gain."""

    def set_color_integration_cycles(self, cycles: int) -> None:
        """Set v2 colour sensor integration cycles."""
