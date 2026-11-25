from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading, Vector3
from octapihub.application.sense_hat_history import SenseHatHistoryRecorder, SeriesPoint
from octapihub.application.sense_hat_worker import SenseHatSample
from octapihub.core.config import SenseHatConfig
from octapihub.core.errors import HistoryError
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _temp(offset_s: int, value: float) -> SenseHatSample:
    return SenseHatSample(
        "temperature",
        SenseHatReading(SenseHatMetric.TEMPERATURE, value, "°C"),
        T0 + timedelta(seconds=offset_s),
        value + 1,
    )


def _count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0])


def test_store_keeps_raw_values_and_queries_series_and_buckets(tmp_path: Path) -> None:
    store = SQLiteSenseHatHistory(tmp_path / "sub" / "h.sqlite3")
    store.append_many([_temp(0, 20.123456789), _temp(30, 22.0), _temp(70, 24.0)])

    raw = store.query_series("temperature", T0, T0 + HOUR)
    assert [p.value for p in raw] == [20.123456789, 22.0, 24.0]
    corrected = store.query_series("temperature", T0, T0 + HOUR, corrected=True)
    assert corrected[0].value == 21.123456789
    buckets = store.query_series("temperature", T0, T0 + HOUR, bucket_seconds=60)
    assert [(p.count, round(p.value, 3)) for p in buckets] == [(2, 21.062), (1, 24.0)]
    assert store.query_series("humidity", T0, T0 + HOUR) == ()


def test_composite_values_are_stored_as_components(tmp_path: Path) -> None:
    store = SQLiteSenseHatHistory(tmp_path / "h.sqlite3")
    sample = SenseHatSample(
        "accelerometer",
        SenseHatReading(SenseHatMetric.ACCELEROMETER_RAW, Vector3(1.0, 2.0, 3.0), "g"),
        T0,
    )
    store.append_many([sample])
    with sqlite3.connect(tmp_path / "h.sqlite3") as connection:
        row = connection.execute("SELECT scalar, components_json FROM samples").fetchone()
    assert row == (None, '{"x": 1.0, "y": 2.0, "z": 3.0}')


def test_prune_removes_only_older_samples(tmp_path: Path) -> None:
    store = SQLiteSenseHatHistory(tmp_path / "h.sqlite3")
    store.append_many([_temp(0, 1.0), _temp(100, 2.0)])
    assert store.prune(T0 + timedelta(seconds=50)) == 1
    assert _count(tmp_path / "h.sqlite3") == 1


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(HistoryError):
        SQLiteSenseHatHistory(path)


def test_recorder_batches_writes_and_respects_history_flags(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    base = SenseHatConfig()
    sensors = dict(base.sensors)
    sensors["humidity"] = replace(sensors["humidity"], history_enabled=False)
    clock = [0.0]
    recorder = SenseHatHistoryRecorder(
        replace(base, sensors=sensors),
        SQLiteSenseHatHistory(path),
        batch_size=3,
        flush_interval_seconds=100,
        monotonic=lambda: clock[0],
        wall_clock=lambda: T0,
    )
    humidity = SenseHatSample("humidity", SenseHatReading(SenseHatMetric.HUMIDITY, 40.0, "%RH"), T0)
    recorder.record(humidity)
    recorder.record(_temp(0, 1.0))
    recorder.record(_temp(1, 2.0))
    assert _count(path) == 0
    assert recorder.pending == 2
    recorder.record(_temp(2, 3.0))
    assert _count(path) == 3
    assert recorder.pending == 0

    recorder.record(_temp(3, 4.0))
    clock[0] = 101.0
    recorder.record(_temp(4, 5.0))
    assert _count(path) == 5


def test_recorder_disabled_history_stores_nothing(tmp_path: Path) -> None:
    config = SenseHatConfig()
    config = replace(config, history=replace(config.history, enabled=False))
    recorder = SenseHatHistoryRecorder(
        config, SQLiteSenseHatHistory(tmp_path / "h.sqlite3"), batch_size=1
    )
    recorder.record(_temp(0, 1.0))
    assert recorder.pending == 0
    assert _count(tmp_path / "h.sqlite3") == 0


class _FlakyStore:
    def __init__(self) -> None:
        self.fail = True
        self.saved: list[SenseHatSample] = []
        self.pruned: list[datetime] = []

    def append_many(self, samples: Sequence[SenseHatSample]) -> None:
        if self.fail:
            raise HistoryError("disk full")
        self.saved.extend(samples)

    def prune(self, older_than: datetime) -> int:
        self.pruned.append(older_than)
        return 0

    def query_series(
        self,
        group: str,
        since: datetime,
        until: datetime,
        *,
        bucket_seconds: int | None = None,
        corrected: bool = False,
    ) -> tuple[SeriesPoint, ...]:
        return ()


def test_recorder_survives_store_failure_keeps_buffer_and_prunes_by_retention() -> None:
    store = _FlakyStore()
    config = SenseHatConfig()
    recorder = SenseHatHistoryRecorder(
        config, store, batch_size=2, max_buffer=3, wall_clock=lambda: T0
    )
    for i in range(5):
        recorder.record(_temp(i, float(i)))
    assert recorder.pending == 3
    assert recorder.dropped_samples == 2
    store.fail = False
    assert recorder.flush() is True
    assert [s.reading.value for s in store.saved] == [2.0, 3.0, 4.0]
    assert store.pruned == [T0 - timedelta(days=config.history.retention_days)]
