from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from octapihub.application.sense_hat import SenseHatMetric
from octapihub.application.sense_hat_worker import (
    SenseHatMeasurementWorker,
    SenseHatSample,
    SensorGroupState,
    SensorGroupStatus,
    correct_temperature,
)
from octapihub.core.config import SenseHatConfig
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter

FIXED_NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _config(**enabled: bool) -> SenseHatConfig:
    base = SenseHatConfig()
    sensors = {
        name: replace(sensor, enabled=enabled.get(name, sensor.enabled))
        for name, sensor in base.sensors.items()
    }
    return replace(base, sensors=sensors)


def _by_group(worker: SenseHatMeasurementWorker) -> dict[str, SensorGroupStatus]:
    return {status.group: status for status in worker.statuses}


async def _wait_until(predicate: object, timeout: float = 2.0) -> None:
    assert callable(predicate)
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_default_groups_are_sampled_raw_with_correction_alongside() -> None:
    samples: list[SenseHatSample] = []
    config = replace(
        _config(),
        temperature_correction_factor=0.5,
        temperature_offset_c=1.0,
    )
    worker = SenseHatMeasurementWorker(
        config,
        SenseHatAdapter(FakeSenseHat()),
        samples.append,
        clock=lambda: FIXED_NOW,
    )

    async def scenario() -> None:
        await worker.start()
        await _wait_until(lambda: len(samples) >= 3)
        await worker.stop()

    asyncio.run(scenario())

    by_group = {sample.group: sample for sample in samples}
    assert set(by_group) == {"temperature", "humidity", "pressure"}
    temperature = by_group["temperature"]
    assert temperature.reading.value == 21.123456
    assert temperature.corrected_temperature_c == 21.123456 * 0.5 + 1.0
    assert temperature.measured_at == FIXED_NOW
    assert by_group["humidity"].reading.value == 45.6789
    assert by_group["humidity"].corrected_temperature_c is None
    assert all(s.state is SensorGroupState.STOPPED for s in worker.statuses)


def test_failing_group_does_not_stop_other_groups() -> None:
    samples: list[SenseHatSample] = []
    device = FakeSenseHat(failures={"get_humidity": lambda: OSError("i2c gone")})
    worker = SenseHatMeasurementWorker(_config(), SenseHatAdapter(device), samples.append)

    async def scenario() -> None:
        await worker.start()
        await _wait_until(
            lambda: (
                _by_group(worker)["humidity"].state is SensorGroupState.ERROR
                and {s.group for s in samples} >= {"temperature", "pressure"}
            )
        )
        status = _by_group(worker)["humidity"]
        assert status.consecutive_failures == 1
        assert status.last_error is not None
        assert "i2c" not in status.last_error
        assert status.last_sample is None
        await worker.stop()

    asyncio.run(scenario())
    assert "humidity" not in {s.group for s in samples}


def test_failing_sink_is_isolated() -> None:
    calls = 0

    def sink(sample: SenseHatSample) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("sink broke")

    worker = SenseHatMeasurementWorker(_config(), SenseHatAdapter(FakeSenseHat()), sink)

    async def scenario() -> None:
        await worker.start()
        await _wait_until(lambda: calls >= 3)
        assert all(s.state is SensorGroupState.OK for s in worker.statuses)
        await worker.stop()

    asyncio.run(scenario())


def test_unavailable_hardware_group_is_marked_and_not_polled() -> None:
    device = FakeSenseHat(color_sensor_available=False)
    worker = SenseHatMeasurementWorker(
        _config(color=True, temperature=False, humidity=False, pressure=False),
        SenseHatAdapter(device),
    )

    async def scenario() -> None:
        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

    asyncio.run(scenario())

    assert _by_group(worker)["color"].state is SensorGroupState.UNAVAILABLE
    assert "colour" not in device.operations[1:]


def test_opt_in_group_is_polled_and_disabled_group_is_not() -> None:
    samples: list[SenseHatSample] = []
    device = FakeSenseHat()
    worker = SenseHatMeasurementWorker(
        _config(compass=True, humidity=False), SenseHatAdapter(device), samples.append
    )

    async def scenario() -> None:
        await worker.start()
        await _wait_until(lambda: {s.group for s in samples} >= {"temperature", "compass"})
        await worker.stop()

    asyncio.run(scenario())

    assert "get_humidity" not in device.operations
    compass = next(s for s in samples if s.group == "compass")
    assert compass.reading.metric is SenseHatMetric.COMPASS


def test_group_repeats_at_its_interval_and_recovers_after_failure() -> None:
    attempts = 0

    def flaky() -> Exception:
        nonlocal attempts
        attempts += 1
        return OSError("transient")

    device = FakeSenseHat(failures={"get_pressure": flaky})
    pressure = SenseHatConfig().sensors["pressure"]
    sensors = {"pressure": replace(pressure, interval_seconds=1)}
    worker = SenseHatMeasurementWorker(
        replace(SenseHatConfig(), sensors=sensors), SenseHatAdapter(device)
    )

    async def scenario() -> None:
        await worker.start()
        await _wait_until(lambda: _by_group(worker)["pressure"].state is SensorGroupState.ERROR)
        assert attempts >= 1
        device._failures.clear()
        await _wait_until(lambda: _by_group(worker)["pressure"].state is SensorGroupState.OK, 3.0)
        status = _by_group(worker)["pressure"]
        assert status.consecutive_failures == 0
        assert status.last_error is None
        await worker.stop()

    asyncio.run(scenario())


def test_disabled_sense_hat_starts_nothing_and_double_start_is_rejected() -> None:
    worker = SenseHatMeasurementWorker(
        replace(SenseHatConfig(), enabled=False), SenseHatAdapter(FakeSenseHat())
    )

    async def scenario() -> None:
        await worker.start()
        assert worker.statuses == ()
        try:
            await worker.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("second start must fail")
        await worker.stop()

    asyncio.run(scenario())


def test_correct_temperature_is_linear() -> None:
    assert correct_temperature(20.0, 1.0, 0.0) == 20.0
    assert correct_temperature(20.0, 0.9, -1.5) == 20.0 * 0.9 - 1.5
