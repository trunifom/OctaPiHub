from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from octapihub.application.sense_hat import (
    ColorSensorReading,
    JoystickAction,
    JoystickDirection,
    Orientation,
    SenseHatMetric,
    SenseHatVersionSource,
    Vector3,
)
from octapihub.core.config import SenseHatVersion
from octapihub.core.errors import SenseHatError, SenseHatFeatureUnavailable, ValidationError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter, create_sense_hat_port


@pytest.mark.parametrize(
    ("metric", "expected_value", "expected_unit", "operation"),
    [
        (SenseHatMetric.TEMPERATURE, 21.123456, "°C", "get_temperature"),
        (
            SenseHatMetric.TEMPERATURE_HUMIDITY,
            21.234567,
            "°C",
            "get_temperature_from_humidity",
        ),
        (
            SenseHatMetric.TEMPERATURE_PRESSURE,
            22.345678,
            "°C",
            "get_temperature_from_pressure",
        ),
        (SenseHatMetric.HUMIDITY, 45.6789, "%RH", "get_humidity"),
        (SenseHatMetric.PRESSURE, 1013.256, "mbar", "get_pressure"),
        (SenseHatMetric.COMPASS, 123.456, "degrees", "get_compass"),
    ],
)
def test_adapter_reads_environmental_and_scalar_imu_values(
    metric: SenseHatMetric,
    expected_value: float,
    expected_unit: str,
    operation: str,
) -> None:
    device = FakeSenseHat()
    adapter = SenseHatAdapter(device)

    reading = adapter.read_metric(metric)

    assert reading.metric is metric
    assert reading.value == expected_value
    assert reading.unit == expected_unit
    assert device.operations[-1] == operation


@pytest.mark.parametrize(
    ("metric", "expected_value", "expected_unit"),
    [
        (
            SenseHatMetric.ORIENTATION,
            (1.25, 2.5, 3.75),
            "degrees",
        ),
        (
            SenseHatMetric.ORIENTATION_DEGREES,
            (1.25, 2.5, 3.75),
            "degrees",
        ),
        (
            SenseHatMetric.ORIENTATION_RADIANS,
            (0.1, 0.2, 0.3),
            "radians",
        ),
        (SenseHatMetric.COMPASS_RAW, (1.125, 2.25, 3.5), "µT"),
        (SenseHatMetric.GYROSCOPE, (4.0, 5.0, 6.0), "degrees"),
        (SenseHatMetric.GYROSCOPE_RAW, (0.25, 0.5, 0.75), "rad/s"),
        (SenseHatMetric.ACCELEROMETER, (7.0, 8.0, 9.0), "degrees"),
        (SenseHatMetric.ACCELEROMETER_RAW, (0.1, 0.2, 0.3), "G"),
    ],
)
def test_adapter_reads_imu_values_without_rounding(
    metric: SenseHatMetric,
    expected_value: tuple[float, float, float],
    expected_unit: str,
) -> None:
    adapter = SenseHatAdapter(FakeSenseHat())

    reading = adapter.read_metric(metric)

    if isinstance(reading.value, Orientation):
        actual_value = (reading.value.pitch, reading.value.roll, reading.value.yaw)
    else:
        assert isinstance(reading.value, Vector3)
        actual_value = (reading.value.x, reading.value.y, reading.value.z)
    assert actual_value == expected_value
    assert reading.unit == expected_unit


def test_auto_version_detection_uses_color_sensor_evidence() -> None:
    v2_adapter = SenseHatAdapter(FakeSenseHat(color_sensor_available=True))
    unknown_adapter = SenseHatAdapter(FakeSenseHat(color_sensor_available=False))

    assert v2_adapter.capabilities.hardware_version is SenseHatVersion.V2
    assert v2_adapter.capabilities.version_source is SenseHatVersionSource.COLOR_SENSOR
    assert v2_adapter.capabilities.color_sensor
    assert unknown_adapter.capabilities.hardware_version is None
    assert unknown_adapter.capabilities.version_source is SenseHatVersionSource.UNKNOWN
    assert not unknown_adapter.capabilities.color_sensor


def test_manual_hardware_version_override_is_respected() -> None:
    adapter = SenseHatAdapter(
        FakeSenseHat(color_sensor_available=True),
        hardware_version=SenseHatVersion.V1,
    )

    assert adapter.capabilities.hardware_version is SenseHatVersion.V1
    assert adapter.capabilities.version_source is SenseHatVersionSource.CONFIGURATION
    assert not adapter.capabilities.color_sensor
    with pytest.raises(SenseHatFeatureUnavailable) as error:
        adapter.read_metric(SenseHatMetric.COLOR)
    assert error.value.error_code == "CH-SENSE-002"


def test_color_sensor_reading_keeps_scaled_raw_and_integration_information() -> None:
    adapter = SenseHatAdapter(FakeSenseHat())

    reading = adapter.read_metric(SenseHatMetric.COLOR)

    assert isinstance(reading.value, ColorSensorReading)
    assert reading.value.scaled_rgba == (32, 64, 128, 160)
    assert reading.value.raw_rgba == (128, 256, 512, 640)
    assert reading.value.integration_time == 0.0024
    assert reading.value.max_raw == 1024


def test_joystick_events_and_callbacks_are_translated() -> None:
    device = FakeSenseHat()
    adapter = SenseHatAdapter(device)
    callbacks = []
    adapter.set_joystick_callback(JoystickDirection.UP, callbacks.append)
    device.emit_joystick_event(123.5, "up", "pressed")

    events = adapter.get_joystick_events()

    assert len(events) == 1
    assert events[0].timestamp == 123.5
    assert events[0].direction is JoystickDirection.UP
    assert events[0].action is JoystickAction.PRESSED
    assert callbacks == list(events)
    assert adapter.get_joystick_events() == ()


def test_joystick_wait_and_unknown_event_validation() -> None:
    device = FakeSenseHat()
    adapter = SenseHatAdapter(device)
    device.emit_joystick_event(10.0, "middle", "held")

    event = adapter.wait_for_joystick_event()

    assert event is not None
    assert event.direction is JoystickDirection.MIDDLE
    assert event.action is JoystickAction.HELD

    device.emit_joystick_event(10.0, "diagonal", "pressed")
    with pytest.raises(SenseHatError, match="unknown joystick event"):
        adapter.get_joystick_events()


def test_matrix_methods_validate_inputs_and_delegate_to_device() -> None:
    device = FakeSenseHat()
    adapter = SenseHatAdapter(device)
    red = (255, 0, 0)

    adapter.set_rotation(90)
    adapter.set_pixel(2, 3, red)
    assert adapter.get_pixel(2, 3) == red
    adapter.show_message("Hi", text_color=red)
    adapter.show_letter("A")
    adapter.clear()

    assert adapter.rotation == 90
    assert "show_message" in device.operations
    assert "show_letter" in device.operations
    with pytest.raises(ValidationError, match="coordinates"):
        adapter.get_pixel(8, 3)
    with pytest.raises(ValidationError, match="exactly 32"):
        adapter.gamma = (0, 1)


def test_sensor_io_errors_are_translated_with_original_cause() -> None:
    failure = OSError("I2C unavailable")
    adapter = SenseHatAdapter(FakeSenseHat(failures={"get_temperature": lambda: failure}))

    with pytest.raises(SenseHatError) as error:
        adapter.read_metric(SenseHatMetric.TEMPERATURE)

    assert error.value.__cause__ is failure
    assert error.value.details["operation"] == SenseHatMetric.TEMPERATURE.value


def test_factory_uses_injected_hardware_constructor() -> None:
    device = FakeSenseHat()
    created: list[FakeSenseHat] = []

    def factory() -> FakeSenseHat:
        created.append(device)
        return device

    adapter = create_sense_hat_port(SenseHatVersion.V2, device_factory=factory)

    assert adapter.capabilities.hardware_version is SenseHatVersion.V2
    assert created == [device]


def test_factory_reports_missing_sense_hat_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_module(name: str) -> object:
        assert name == "sense_hat"
        raise ModuleNotFoundError("sense_hat")

    monkeypatch.setattr(importlib, "import_module", missing_module)

    with pytest.raises(SenseHatError, match="package is not installed") as error:
        create_sense_hat_port()

    assert isinstance(error.value.__cause__, ModuleNotFoundError)


def test_fake_image_loading_returns_normalized_matrix(tmp_path: Path) -> None:
    adapter = SenseHatAdapter(FakeSenseHat())

    pixels = adapter.load_image(str(tmp_path / "image.png"), redraw=False)

    assert len(pixels) == 64
    assert pixels[0] == (0, 0, 0)
