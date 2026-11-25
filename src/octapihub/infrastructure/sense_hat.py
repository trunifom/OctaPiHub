"""Adapter for the official ``sense_hat`` Python library."""

from __future__ import annotations

import importlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, TypeVar, cast

from octapihub.application.sense_hat import (
    ColorSensorReading,
    JoystickAction,
    JoystickCallback,
    JoystickDirection,
    Orientation,
    PixelBuffer,
    RGBColor,
    SenseHatCapabilities,
    SenseHatInputEvent,
    SenseHatMetric,
    SenseHatPort,
    SenseHatReading,
    SenseHatVersionSource,
    Vector3,
)
from octapihub.core.config import SenseHatVersion
from octapihub.core.errors import SenseHatError, SenseHatFeatureUnavailable, ValidationError

T = TypeVar("T")


class _InputEventApi(Protocol):
    @property
    def timestamp(self) -> float: ...

    @property
    def direction(self) -> str: ...

    @property
    def action(self) -> str: ...


class _StickApi(Protocol):
    direction_up: Callable[[_InputEventApi], None] | None
    direction_down: Callable[[_InputEventApi], None] | None
    direction_left: Callable[[_InputEventApi], None] | None
    direction_right: Callable[[_InputEventApi], None] | None
    direction_middle: Callable[[_InputEventApi], None] | None
    direction_any: Callable[[_InputEventApi], None] | None

    def get_events(self) -> Sequence[_InputEventApi]: ...

    def wait_for_event(self, emptybuffer: bool = False) -> _InputEventApi | None: ...


class _ColorSensorApi(Protocol):
    colour: Sequence[int]
    colour_raw: Sequence[int]
    gain: int
    integration_cycles: int
    integration_time: float
    max_raw: int


class SenseHatLibraryApi(Protocol):
    """Public library surface used by the adapter; never imported on non-Pi systems."""

    stick: _StickApi
    rotation: int
    low_light: bool
    gamma: Sequence[int]

    @property
    def colour(self) -> _ColorSensorApi | None: ...

    def has_colour_sensor(self) -> bool: ...

    def get_temperature(self) -> float: ...

    def get_temperature_from_humidity(self) -> float: ...

    def get_temperature_from_pressure(self) -> float: ...

    def get_humidity(self) -> float: ...

    def get_pressure(self) -> float: ...

    def set_imu_config(
        self,
        compass_enabled: bool,
        gyro_enabled: bool,
        accel_enabled: bool,
    ) -> None: ...

    def get_orientation(self) -> Mapping[str, float]: ...

    def get_orientation_degrees(self) -> Mapping[str, float]: ...

    def get_orientation_radians(self) -> Mapping[str, float]: ...

    def get_compass(self) -> float: ...

    def get_compass_raw(self) -> Mapping[str, float]: ...

    def get_gyroscope(self) -> Mapping[str, float]: ...

    def get_gyroscope_raw(self) -> Mapping[str, float]: ...

    def get_accelerometer(self) -> Mapping[str, float]: ...

    def get_accelerometer_raw(self) -> Mapping[str, float]: ...

    def set_rotation(self, r: int = 0, redraw: bool = True) -> None: ...

    def flip_h(self, redraw: bool = True) -> Sequence[Sequence[int]]: ...

    def flip_v(self, redraw: bool = True) -> Sequence[Sequence[int]]: ...

    def set_pixels(self, pixel_list: Sequence[Sequence[int]]) -> None: ...

    def get_pixels(self) -> Sequence[Sequence[int]]: ...

    def set_pixel(self, x: int, y: int, *args: int | Sequence[int]) -> None: ...

    def get_pixel(self, x: int, y: int) -> Sequence[int]: ...

    def load_image(self, file_path: str, redraw: bool = True) -> Sequence[Sequence[int]]: ...

    def clear(self, *args: int | Sequence[int]) -> None: ...

    def show_message(
        self,
        text_string: str,
        scroll_speed: float = 0.1,
        text_colour: Sequence[int] = (255, 255, 255),
        back_colour: Sequence[int] = (0, 0, 0),
    ) -> None: ...

    def show_letter(
        self,
        s: str,
        text_colour: Sequence[int] = (255, 255, 255),
        back_colour: Sequence[int] = (0, 0, 0),
    ) -> None: ...

    def gamma_reset(self) -> None: ...


def create_sense_hat_port(
    hardware_version: SenseHatVersion = SenseHatVersion.AUTO,
    *,
    device_factory: Callable[[], SenseHatLibraryApi] | None = None,
) -> SenseHatPort:
    """Create the hardware adapter, importing the Pi-only dependency lazily."""
    factory = device_factory
    if factory is None:
        try:
            module = importlib.import_module("sense_hat")
        except ImportError as exc:
            raise SenseHatError(
                "The sense_hat package is not installed",
                details={"dependency": "sense-hat"},
            ) from exc
        constructor = getattr(module, "SenseHat", None)
        if not callable(constructor):
            raise SenseHatError("The sense_hat package does not expose SenseHat")
        factory = cast(Callable[[], SenseHatLibraryApi], constructor)
    try:
        device = factory()
    except (OSError, RuntimeError) as exc:
        raise SenseHatError("Could not initialize the Sense HAT") from exc
    return SenseHatAdapter(device, hardware_version=hardware_version)


class SenseHatAdapter(SenseHatPort):
    """Translate the Sense HAT library API to the application port."""

    def __init__(
        self,
        device: SenseHatLibraryApi,
        *,
        hardware_version: SenseHatVersion = SenseHatVersion.AUTO,
    ) -> None:
        self._device = device
        self._configured_version = hardware_version
        color_present = self._hardware_call(
            "has_colour_sensor",
            self._device.has_colour_sensor,
        )
        self._capabilities = self._inspect_capabilities(color_present)

    @property
    def capabilities(self) -> SenseHatCapabilities:
        return self._capabilities

    @property
    def rotation(self) -> int:
        return self._hardware_call("rotation", lambda: self._device.rotation)

    @rotation.setter
    def rotation(self, value: int) -> None:
        self.set_rotation(value)

    @property
    def low_light(self) -> bool:
        return self._hardware_call("low_light", lambda: self._device.low_light)

    @low_light.setter
    def low_light(self, value: bool) -> None:
        if not isinstance(value, bool):
            raise ValidationError("low_light must be a boolean")
        self._hardware_call("low_light", lambda: setattr(self._device, "low_light", value))

    @property
    def gamma(self) -> tuple[int, ...]:
        value = self._hardware_call("gamma", lambda: self._device.gamma)
        return _gamma_table(value)

    @gamma.setter
    def gamma(self, value: Sequence[int]) -> None:
        table = _gamma_table(value)
        self._hardware_call("gamma", lambda: setattr(self._device, "gamma", list(table)))

    def read_metric(self, metric: SenseHatMetric) -> SenseHatReading:
        """Read one documented sensor metric, preserving the original precision."""
        if not isinstance(metric, SenseHatMetric):
            raise ValidationError("Unsupported Sense HAT metric", details={"metric": str(metric)})
        self._require_metric_capability(metric)
        if metric is SenseHatMetric.TEMPERATURE:
            return self._scalar_reading(metric, "°C", self._device.get_temperature)
        if metric is SenseHatMetric.TEMPERATURE_HUMIDITY:
            return self._scalar_reading(metric, "°C", self._device.get_temperature_from_humidity)
        if metric is SenseHatMetric.TEMPERATURE_PRESSURE:
            return self._scalar_reading(metric, "°C", self._device.get_temperature_from_pressure)
        if metric is SenseHatMetric.HUMIDITY:
            return self._scalar_reading(metric, "%RH", self._device.get_humidity)
        if metric is SenseHatMetric.PRESSURE:
            return self._scalar_reading(metric, "mbar", self._device.get_pressure)
        if metric in {
            SenseHatMetric.ORIENTATION,
            SenseHatMetric.ORIENTATION_DEGREES,
        }:
            return self._orientation_reading(
                metric, "degrees", self._device.get_orientation_degrees
            )
        if metric is SenseHatMetric.ORIENTATION_RADIANS:
            return self._orientation_reading(
                metric, "radians", self._device.get_orientation_radians
            )
        if metric is SenseHatMetric.COMPASS:
            return self._scalar_reading(metric, "degrees", self._device.get_compass)
        if metric is SenseHatMetric.COMPASS_RAW:
            return self._vector_reading(metric, "µT", self._device.get_compass_raw)
        if metric is SenseHatMetric.GYROSCOPE:
            return self._orientation_reading(metric, "degrees", self._device.get_gyroscope)
        if metric is SenseHatMetric.GYROSCOPE_RAW:
            return self._vector_reading(metric, "rad/s", self._device.get_gyroscope_raw)
        if metric is SenseHatMetric.ACCELEROMETER:
            return self._orientation_reading(metric, "degrees", self._device.get_accelerometer)
        if metric is SenseHatMetric.ACCELEROMETER_RAW:
            return self._vector_reading(metric, "G", self._device.get_accelerometer_raw)
        if metric is SenseHatMetric.COLOR:
            return SenseHatReading(metric, self._read_color(), "RGBC")
        raise ValidationError("Unsupported Sense HAT metric", details={"metric": str(metric)})

    def set_imu_config(
        self,
        compass_enabled: bool,
        gyro_enabled: bool,
        accel_enabled: bool,
    ) -> None:
        flags = (compass_enabled, gyro_enabled, accel_enabled)
        if any(not isinstance(flag, bool) for flag in flags):
            raise ValidationError("IMU configuration values must be booleans")
        self._hardware_call(
            "set_imu_config",
            lambda: self._device.set_imu_config(*flags),
        )

    def get_joystick_events(self) -> tuple[SenseHatInputEvent, ...]:
        events = self._hardware_call("stick.get_events", self._device.stick.get_events)
        return tuple(_convert_input_event(event) for event in events)

    def wait_for_joystick_event(
        self,
        *,
        empty_buffer: bool = False,
    ) -> SenseHatInputEvent | None:
        if not isinstance(empty_buffer, bool):
            raise ValidationError("empty_buffer must be a boolean")
        event = self._hardware_call(
            "stick.wait_for_event",
            lambda: self._device.stick.wait_for_event(emptybuffer=empty_buffer),
        )
        return None if event is None else _convert_input_event(event)

    def set_joystick_callback(
        self,
        direction: JoystickDirection,
        callback: JoystickCallback | None,
    ) -> None:
        if not isinstance(direction, JoystickDirection):
            raise ValidationError("Unsupported joystick direction")
        if callback is not None and not callable(callback):
            raise ValidationError("Joystick callback must be callable or None")
        if direction is JoystickDirection.ANY:
            attribute = "direction_any"
        else:
            attribute = f"direction_{direction.value}"
        if callback is None:
            handler = None
        else:

            def handler(event: _InputEventApi) -> None:
                callback(_convert_input_event(event))

        self._hardware_call(
            f"stick.{attribute}",
            lambda: setattr(self._device.stick, attribute, handler),
        )

    @property
    def color_gain(self) -> int:
        sensor = self._require_color_sensor()
        return self._hardware_call("colour.gain", lambda: sensor.gain)

    def set_color_gain(self, gain: int) -> None:
        if isinstance(gain, bool) or not isinstance(gain, int) or gain not in {1, 4, 16, 60}:
            raise ValidationError("Color sensor gain must be 1, 4, 16, or 60")
        sensor = self._require_color_sensor()
        self._hardware_call("colour.gain", lambda: setattr(sensor, "gain", gain))

    @property
    def color_integration_cycles(self) -> int:
        sensor = self._require_color_sensor()
        return self._hardware_call("colour.integration_cycles", lambda: sensor.integration_cycles)

    def set_color_integration_cycles(self, cycles: int) -> None:
        if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 256:
            raise ValidationError("Color sensor integration cycles must be from 1 to 256")
        sensor = self._require_color_sensor()
        self._hardware_call(
            "colour.integration_cycles",
            lambda: setattr(sensor, "integration_cycles", cycles),
        )

    def set_rotation(self, rotation: int, *, redraw: bool = True) -> None:
        if (
            isinstance(rotation, bool)
            or not isinstance(rotation, int)
            or rotation not in {0, 90, 180, 270}
        ):
            raise ValidationError("Rotation must be 0, 90, 180, or 270 degrees")
        self._hardware_call(
            "set_rotation",
            lambda: self._device.set_rotation(rotation, redraw),
        )

    def flip_h(self, *, redraw: bool = True) -> PixelBuffer:
        pixels = self._hardware_call("flip_h", lambda: self._device.flip_h(redraw))
        return _pixel_buffer(pixels)

    def flip_v(self, *, redraw: bool = True) -> PixelBuffer:
        pixels = self._hardware_call("flip_v", lambda: self._device.flip_v(redraw))
        return _pixel_buffer(pixels)

    def set_pixels(self, pixels: Sequence[RGBColor]) -> None:
        normalized = _pixel_buffer(pixels)
        self._hardware_call(
            "set_pixels",
            lambda: self._device.set_pixels([list(pixel) for pixel in normalized]),
        )

    def get_pixels(self) -> PixelBuffer:
        pixels = self._hardware_call("get_pixels", self._device.get_pixels)
        return _pixel_buffer(pixels)

    def set_pixel(self, x: int, y: int, color: RGBColor) -> None:
        _validate_coordinates(x, y)
        normalized_color = _rgb_color(color)
        self._hardware_call(
            "set_pixel",
            lambda: self._device.set_pixel(x, y, normalized_color),
        )

    def get_pixel(self, x: int, y: int) -> RGBColor:
        _validate_coordinates(x, y)
        pixel = self._hardware_call("get_pixel", lambda: self._device.get_pixel(x, y))
        return _rgb_color(pixel)

    def load_image(self, path: str, *, redraw: bool = True) -> PixelBuffer:
        if not isinstance(path, str) or not path.strip():
            raise ValidationError("Image path must not be empty")
        pixels = self._hardware_call(
            "load_image",
            lambda: self._device.load_image(path, redraw),
        )
        return _pixel_buffer(pixels)

    def clear(self, color: RGBColor = (0, 0, 0)) -> None:
        normalized_color = _rgb_color(color)
        self._hardware_call(
            "clear",
            lambda: self._device.clear(*normalized_color),
        )

    def show_message(
        self,
        text: str,
        *,
        scroll_speed: float = 0.1,
        text_color: RGBColor = (255, 255, 255),
        background_color: RGBColor = (0, 0, 0),
    ) -> None:
        if not isinstance(text, str):
            raise ValidationError("Message text must be a string")
        if (
            isinstance(scroll_speed, bool)
            or not isinstance(scroll_speed, (int, float))
            or not math.isfinite(scroll_speed)
            or scroll_speed <= 0
        ):
            raise ValidationError("Scroll speed must be a finite positive number")
        foreground = _rgb_color(text_color)
        background = _rgb_color(background_color)
        self._hardware_call(
            "show_message",
            lambda: self._device.show_message(
                text,
                float(scroll_speed),
                foreground,
                background,
            ),
        )

    def show_letter(
        self,
        letter: str,
        *,
        text_color: RGBColor = (255, 255, 255),
        background_color: RGBColor = (0, 0, 0),
    ) -> None:
        if not isinstance(letter, str) or len(letter) != 1:
            raise ValidationError("Sense HAT letters must contain exactly one character")
        foreground = _rgb_color(text_color)
        background = _rgb_color(background_color)
        self._hardware_call(
            "show_letter",
            lambda: self._device.show_letter(letter, foreground, background),
        )

    def gamma_reset(self) -> None:
        self._hardware_call("gamma_reset", self._device.gamma_reset)

    def _inspect_capabilities(self, color_present: bool) -> SenseHatCapabilities:
        version: SenseHatVersion | None = self._configured_version
        version_source = SenseHatVersionSource.CONFIGURATION
        if version is SenseHatVersion.AUTO:
            if color_present:
                version = SenseHatVersion.V2
                version_source = SenseHatVersionSource.COLOR_SENSOR
            else:
                version = None
                version_source = SenseHatVersionSource.UNKNOWN
        color_available = color_present and version is not SenseHatVersion.V1
        return SenseHatCapabilities(
            environmental_sensors=_has_methods(
                self._device,
                (
                    "get_temperature",
                    "get_temperature_from_humidity",
                    "get_temperature_from_pressure",
                    "get_humidity",
                    "get_pressure",
                ),
            ),
            imu=_has_methods(
                self._device,
                (
                    "set_imu_config",
                    "get_orientation",
                    "get_orientation_degrees",
                    "get_orientation_radians",
                    "get_compass",
                    "get_compass_raw",
                    "get_gyroscope",
                    "get_gyroscope_raw",
                    "get_accelerometer",
                    "get_accelerometer_raw",
                ),
            ),
            joystick=callable(getattr(self._device.stick, "get_events", None)),
            led_matrix=_has_methods(
                self._device,
                (
                    "set_rotation",
                    "flip_h",
                    "flip_v",
                    "set_pixels",
                    "get_pixels",
                    "set_pixel",
                    "get_pixel",
                    "load_image",
                    "clear",
                    "show_message",
                    "show_letter",
                    "gamma_reset",
                ),
            ),
            color_sensor=color_available,
            hardware_version=version,
            version_source=version_source,
        )

    def _require_metric_capability(self, metric: SenseHatMetric) -> None:
        if metric is SenseHatMetric.COLOR:
            self._require_color_sensor()
            return
        if metric in {
            SenseHatMetric.TEMPERATURE,
            SenseHatMetric.TEMPERATURE_HUMIDITY,
            SenseHatMetric.TEMPERATURE_PRESSURE,
            SenseHatMetric.HUMIDITY,
            SenseHatMetric.PRESSURE,
        }:
            available = self._capabilities.environmental_sensors
            feature = "environmental sensors"
        else:
            available = self._capabilities.imu
            feature = "IMU"
        if not available:
            raise SenseHatFeatureUnavailable(
                f"The Sense HAT does not expose {feature}",
                details={"metric": metric.value},
            )

    def _scalar_reading(
        self,
        metric: SenseHatMetric,
        unit: str,
        getter: Callable[[], float],
    ) -> SenseHatReading:
        value = self._hardware_call(metric.value, getter)
        return SenseHatReading(metric, _finite_float(value, metric.value), unit)

    def _orientation_reading(
        self,
        metric: SenseHatMetric,
        unit: str,
        getter: Callable[[], Mapping[str, float]],
    ) -> SenseHatReading:
        values = self._hardware_call(metric.value, getter)
        return SenseHatReading(metric, _orientation(values, metric.value), unit)

    def _vector_reading(
        self,
        metric: SenseHatMetric,
        unit: str,
        getter: Callable[[], Mapping[str, float]],
    ) -> SenseHatReading:
        values = self._hardware_call(metric.value, getter)
        return SenseHatReading(metric, _vector(values, metric.value), unit)

    def _read_color(self) -> ColorSensorReading:
        sensor = self._require_color_sensor()
        scaled = _rgba(
            self._hardware_call("colour.colour", lambda: sensor.colour),
            "colour.colour",
        )
        raw = _rgba(
            self._hardware_call("colour.colour_raw", lambda: sensor.colour_raw),
            "colour.colour_raw",
        )
        integration_time = _finite_float(
            self._hardware_call(
                "colour.integration_time",
                lambda: sensor.integration_time,
            ),
            "colour.integration_time",
        )
        max_raw = _positive_int(
            self._hardware_call("colour.max_raw", lambda: sensor.max_raw),
            "colour.max_raw",
        )
        return ColorSensorReading(
            scaled_rgba=scaled,
            raw_rgba=raw,
            gain=_positive_int(
                self._hardware_call("colour.gain", lambda: sensor.gain),
                "colour.gain",
            ),
            integration_cycles=_positive_int(
                self._hardware_call(
                    "colour.integration_cycles",
                    lambda: sensor.integration_cycles,
                ),
                "colour.integration_cycles",
            ),
            integration_time=integration_time,
            max_raw=max_raw,
        )

    def _require_color_sensor(self) -> _ColorSensorApi:
        if not self._capabilities.color_sensor:
            raise SenseHatFeatureUnavailable(
                "The v2 color sensor is not available",
                details={"feature": "color_sensor"},
            )
        sensor = self._hardware_call("colour", lambda: self._device.colour)
        if sensor is None:
            raise SenseHatFeatureUnavailable(
                "The v2 color sensor is not available",
                details={"feature": "color_sensor"},
            )
        return sensor

    @staticmethod
    def _hardware_call(operation: str, action: Callable[[], T]) -> T:
        try:
            return action()
        except (OSError, RuntimeError) as exc:
            raise SenseHatError(
                "Sense HAT hardware operation failed",
                details={"operation": operation},
            ) from exc


def _has_methods(device: object, method_names: Sequence[str]) -> bool:
    return all(callable(getattr(device, name, None)) for name in method_names)


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SenseHatError("Sense HAT returned a non-numeric value", details={"field": field})
    result = float(value)
    if not math.isfinite(result):
        raise SenseHatError("Sense HAT returned a non-finite value", details={"field": field})
    return result


def _orientation(value: object, field: str) -> Orientation:
    table = _require_value_table(value, field)
    return Orientation(
        _finite_float(table.get("pitch"), f"{field}.pitch"),
        _finite_float(table.get("roll"), f"{field}.roll"),
        _finite_float(table.get("yaw"), f"{field}.yaw"),
    )


def _vector(value: object, field: str) -> Vector3:
    table = _require_value_table(value, field)
    return Vector3(
        _finite_float(table.get("x"), f"{field}.x"),
        _finite_float(table.get("y"), f"{field}.y"),
        _finite_float(table.get("z"), f"{field}.z"),
    )


def _require_value_table(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SenseHatError("Sense HAT returned an invalid axis mapping", details={"field": field})
    return value


def _rgba(value: object, field: str) -> tuple[int, int, int, int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise SenseHatError("Sense HAT returned invalid RGBC data", details={"field": field})
    result = tuple(_bounded_int(channel, 0, 65536, field) for channel in value)
    return cast(tuple[int, int, int, int], result)


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SenseHatError("Sense HAT returned an invalid integer", details={"field": field})
    return value


def _bounded_int(value: object, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise SenseHatError("Sense HAT returned an invalid integer", details={"field": field})
    return value


def _convert_input_event(event: _InputEventApi) -> SenseHatInputEvent:
    timestamp = _finite_float(event.timestamp, "joystick.timestamp")
    try:
        direction = JoystickDirection(event.direction)
        action = JoystickAction(event.action)
    except ValueError as exc:
        raise SenseHatError(
            "Sense HAT returned an unknown joystick event",
            details={"direction": event.direction, "action": event.action},
        ) from exc
    if direction is JoystickDirection.ANY:
        raise SenseHatError("Sense HAT joystick events cannot use the 'any' direction")
    return SenseHatInputEvent(timestamp, direction, action)


def _rgb_color(value: object) -> RGBColor:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValidationError("RGB colors must contain exactly three channels")
    return cast(
        RGBColor,
        tuple(_bounded_int(channel, 0, 255, "color") for channel in value),
    )


def _pixel_buffer(value: object) -> PixelBuffer:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 64:
        raise ValidationError("LED matrix pixel buffers must contain exactly 64 pixels")
    return cast(PixelBuffer, tuple(_rgb_color(pixel) for pixel in value))


def _gamma_table(value: object) -> tuple[int, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 32:
        raise ValidationError("Gamma tables must contain exactly 32 entries")
    return tuple(_bounded_int(entry, 0, 31, "gamma") for entry in value)


def _validate_coordinates(x: int, y: int) -> None:
    if (
        isinstance(x, bool)
        or not isinstance(x, int)
        or not 0 <= x <= 7
        or isinstance(y, bool)
        or not isinstance(y, int)
        or not 0 <= y <= 7
    ):
        raise ValidationError("LED matrix coordinates must be integers from 0 to 7")
