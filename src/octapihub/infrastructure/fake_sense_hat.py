"""In-memory implementation of the official Sense HAT library API."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from octapihub.infrastructure.sense_hat import SenseHatLibraryApi, _InputEventApi

Failure = Callable[[], Exception]


@dataclass(frozen=True, slots=True)
class _FakeInputEvent:
    timestamp: float
    direction: str
    action: str


class _FakeStick:
    def __init__(self, record: Callable[..., None]) -> None:
        self._record = record
        self._events: list[_FakeInputEvent] = []
        self.direction_up: Callable[[_InputEventApi], None] | None = None
        self.direction_down: Callable[[_InputEventApi], None] | None = None
        self.direction_left: Callable[[_InputEventApi], None] | None = None
        self.direction_right: Callable[[_InputEventApi], None] | None = None
        self.direction_middle: Callable[[_InputEventApi], None] | None = None
        self.direction_any: Callable[[_InputEventApi], None] | None = None

    def get_events(self) -> Sequence[_FakeInputEvent]:
        self._record("stick.get_events")
        events = tuple(self._events)
        self._events.clear()
        return events

    def wait_for_event(self, emptybuffer: bool = False) -> _FakeInputEvent | None:
        self._record("stick.wait_for_event", emptybuffer)
        if emptybuffer:
            self._events.clear()
            return None
        return self._events.pop(0) if self._events else None

    def emit(self, event: _FakeInputEvent) -> None:
        self._events.append(event)
        callback = getattr(self, f"direction_{event.direction}", None)
        if callback is not None:
            callback(event)
        if self.direction_any is not None:
            self.direction_any(event)


class _FakeColorSensor:
    def __init__(self) -> None:
        self.colour: Sequence[int] = (32, 64, 128, 160)
        self.colour_raw: Sequence[int] = (128, 256, 512, 640)
        self.gain = 1
        self.integration_cycles = 1
        self.integration_time = 0.0024
        self.max_raw = 1024


class FakeSenseHat(SenseHatLibraryApi):
    """Configurable fake device for sensor, joystick, and matrix adapter tests."""

    def __init__(
        self,
        *,
        color_sensor_available: bool = True,
        failures: Mapping[str, Failure] | None = None,
    ) -> None:
        self._failures = dict(failures or {})
        self.operations: list[str] = []
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self._rotation = 0
        self._low_light = False
        self._gamma = tuple(range(32))
        self._pixels: list[list[int]] = [[0, 0, 0] for _ in range(64)]
        self._colour = _FakeColorSensor() if color_sensor_available else None
        self.stick = _FakeStick(self._record)
        self.imu_config = (True, True, True)

    @property
    def colour(self) -> _FakeColorSensor | None:
        self._record("colour")
        return self._colour

    def has_colour_sensor(self) -> bool:
        self._record("has_colour_sensor")
        return self._colour is not None

    def get_temperature(self) -> float:
        self._record("get_temperature")
        return 21.123456

    def get_temperature_from_humidity(self) -> float:
        self._record("get_temperature_from_humidity")
        return 21.234567

    def get_temperature_from_pressure(self) -> float:
        self._record("get_temperature_from_pressure")
        return 22.345678

    def get_humidity(self) -> float:
        self._record("get_humidity")
        return 45.6789

    def get_pressure(self) -> float:
        self._record("get_pressure")
        return 1013.256

    def set_imu_config(
        self,
        compass_enabled: bool,
        gyro_enabled: bool,
        accel_enabled: bool,
    ) -> None:
        self._record(
            "set_imu_config",
            compass_enabled,
            gyro_enabled,
            accel_enabled,
        )
        self.imu_config = (compass_enabled, gyro_enabled, accel_enabled)

    def get_orientation(self) -> Mapping[str, float]:
        self._record("get_orientation")
        return {"pitch": 1.25, "roll": 2.5, "yaw": 3.75}

    def get_orientation_degrees(self) -> Mapping[str, float]:
        self._record("get_orientation_degrees")
        return {"pitch": 1.25, "roll": 2.5, "yaw": 3.75}

    def get_orientation_radians(self) -> Mapping[str, float]:
        self._record("get_orientation_radians")
        return {"pitch": 0.1, "roll": 0.2, "yaw": 0.3}

    def get_compass(self) -> float:
        self._record("get_compass")
        return 123.456

    def get_compass_raw(self) -> Mapping[str, float]:
        self._record("get_compass_raw")
        return {"x": 1.125, "y": 2.25, "z": 3.5}

    def get_gyroscope(self) -> Mapping[str, float]:
        self._record("get_gyroscope")
        return {"pitch": 4.0, "roll": 5.0, "yaw": 6.0}

    def get_gyroscope_raw(self) -> Mapping[str, float]:
        self._record("get_gyroscope_raw")
        return {"x": 0.25, "y": 0.5, "z": 0.75}

    def get_accelerometer(self) -> Mapping[str, float]:
        self._record("get_accelerometer")
        return {"pitch": 7.0, "roll": 8.0, "yaw": 9.0}

    def get_accelerometer_raw(self) -> Mapping[str, float]:
        self._record("get_accelerometer_raw")
        return {"x": 0.1, "y": 0.2, "z": 0.3}

    @property
    def rotation(self) -> int:
        self._record("rotation.get")
        return self._rotation

    @rotation.setter
    def rotation(self, value: int) -> None:
        self._record("rotation.set", value)
        self._rotation = value

    @property
    def low_light(self) -> bool:
        self._record("low_light.get")
        return self._low_light

    @low_light.setter
    def low_light(self, value: bool) -> None:
        self._record("low_light.set", value)
        self._low_light = value

    @property
    def gamma(self) -> Sequence[int]:
        self._record("gamma.get")
        return self._gamma

    @gamma.setter
    def gamma(self, value: Sequence[int]) -> None:
        self._record("gamma.set", value)
        self._gamma = tuple(value)

    def gamma_reset(self) -> None:
        self._record("gamma_reset")
        self._gamma = tuple(range(32))

    def set_rotation(self, r: int = 0, redraw: bool = True) -> None:
        self._record("set_rotation", r, redraw)
        self._rotation = r

    def flip_h(self, redraw: bool = True) -> Sequence[Sequence[int]]:
        self._record("flip_h", redraw)
        flipped = [
            pixel for row in range(8) for pixel in reversed(self._pixels[row * 8 : (row + 1) * 8])
        ]
        if redraw:
            self._pixels = [list(pixel) for pixel in flipped]
        return flipped

    def flip_v(self, redraw: bool = True) -> Sequence[Sequence[int]]:
        self._record("flip_v", redraw)
        flipped = [
            pixel for row in reversed(range(8)) for pixel in self._pixels[row * 8 : (row + 1) * 8]
        ]
        if redraw:
            self._pixels = [list(pixel) for pixel in flipped]
        return flipped

    def set_pixels(self, pixel_list: Sequence[Sequence[int]]) -> None:
        self._record("set_pixels", pixel_list)
        self._pixels = [list(pixel) for pixel in pixel_list]

    def get_pixels(self) -> Sequence[Sequence[int]]:
        self._record("get_pixels")
        return [list(pixel) for pixel in self._pixels]

    def set_pixel(self, x: int, y: int, *args: int | Sequence[int]) -> None:
        self._record("set_pixel", x, y, *args)
        if len(args) == 1:
            color = args[0]
            if isinstance(color, int):
                raise ValueError("A color must be a sequence of three channels")
            channels = list(color)
        elif len(args) == 3 and all(isinstance(channel, int) for channel in args):
            channels = [channel for channel in args if isinstance(channel, int)]
        else:
            raise ValueError("A pixel requires three RGB channels")
        self._pixels[y * 8 + x] = channels

    def get_pixel(self, x: int, y: int) -> Sequence[int]:
        self._record("get_pixel", x, y)
        return list(self._pixels[y * 8 + x])

    def load_image(self, file_path: str, redraw: bool = True) -> Sequence[Sequence[int]]:
        self._record("load_image", file_path, redraw)
        return self.get_pixels()

    def clear(self, *args: int | Sequence[int]) -> None:
        self._record("clear", *args)
        if not args:
            channels = [0, 0, 0]
        elif len(args) == 1 and not isinstance(args[0], int):
            channels = list(args[0])
        elif len(args) == 3 and all(isinstance(channel, int) for channel in args):
            channels = [channel for channel in args if isinstance(channel, int)]
        else:
            raise ValueError("A color requires three RGB channels")
        self._pixels = [channels.copy() for _ in range(64)]

    def show_message(
        self,
        text_string: str,
        scroll_speed: float = 0.1,
        text_colour: Sequence[int] = (255, 255, 255),
        back_colour: Sequence[int] = (0, 0, 0),
    ) -> None:
        self._record("show_message", text_string, scroll_speed, text_colour, back_colour)

    def show_letter(
        self,
        s: str,
        text_colour: Sequence[int] = (255, 255, 255),
        back_colour: Sequence[int] = (0, 0, 0),
    ) -> None:
        self._record("show_letter", s, text_colour, back_colour)

    def emit_joystick_event(self, timestamp: float, direction: str, action: str) -> None:
        self.stick.emit(_FakeInputEvent(timestamp, direction, action))

    def _record(self, operation: str, *args: object, **kwargs: object) -> None:
        self.operations.append(operation)
        self.calls.append((operation, args, kwargs))
        failure = self._failures.get(operation)
        if failure is not None:
            raise failure()
