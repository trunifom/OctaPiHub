"""SQLite WAL implementation of the Sense HAT sample history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from octapihub.application.sense_hat_history import SeriesPoint
from octapihub.application.sense_hat_worker import SenseHatSample
from octapihub.core.errors import HistoryError, ValidationError

_SCHEMA_VERSION = 1


def _components(value: object) -> str:
    """Serialize a non-scalar reading as flat JSON of its numeric fields."""
    if not is_dataclass(value) or isinstance(value, type):
        raise ValidationError("Unsupported sensor value type for history")
    return json.dumps({f.name: getattr(value, f.name) for f in fields(value)}, sort_keys=True)


class SQLiteSenseHatHistory:
    """Store unrounded samples with epoch-second timestamps (UTC).

    Uses WAL with ``synchronous=NORMAL``: a power loss can lose the last commits but not
    corrupt the database, which is acceptable for sensor history and reduces SD wear.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if mode is None or str(mode[0]).lower() != "wal":
                    raise sqlite3.DatabaseError("Could not enable SQLite WAL mode")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > _SCHEMA_VERSION:
                    raise sqlite3.DatabaseError("History database schema is newer than supported")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS samples (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sensor_group TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        measured_at REAL NOT NULL,
                        unit TEXT NOT NULL,
                        scalar REAL,
                        components_json TEXT,
                        corrected REAL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_samples_group_time "
                    "ON samples(sensor_group, measured_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_samples_time ON samples(measured_at)"
                )
                connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        except (sqlite3.Error, OSError) as exc:
            raise HistoryError(
                "Could not initialize the Sense HAT history database",
                details={"reason": str(exc)},
            ) from exc

    def append_many(self, samples: Sequence[SenseHatSample]) -> None:
        rows = []
        for sample in samples:
            value = sample.reading.value
            scalar = float(value) if isinstance(value, float) else None
            rows.append(
                (
                    sample.group,
                    sample.reading.metric.value,
                    sample.measured_at.timestamp(),
                    sample.reading.unit,
                    scalar,
                    None if scalar is not None else _components(value),
                    sample.corrected_temperature_c,
                )
            )
        if not rows:
            return
        try:
            with closing(self._connect()) as connection, connection:
                connection.executemany(
                    "INSERT INTO samples (sensor_group, metric, measured_at, unit, scalar, "
                    "components_json, corrected) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
        except sqlite3.Error as exc:
            raise HistoryError(
                "Could not write Sense HAT history", details={"reason": str(exc)}
            ) from exc

    def prune(self, older_than: datetime) -> int:
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    "DELETE FROM samples WHERE measured_at < ?", (older_than.timestamp(),)
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise HistoryError(
                "Could not prune Sense HAT history", details={"reason": str(exc)}
            ) from exc

    def query_series(
        self,
        group: str,
        since: datetime,
        until: datetime,
        *,
        bucket_seconds: int | None = None,
        corrected: bool = False,
    ) -> tuple[SeriesPoint, ...]:
        if bucket_seconds is not None and bucket_seconds < 1:
            raise ValidationError("bucket_seconds must be positive")
        column = "corrected" if corrected else "scalar"  # fixed identifiers, never user input
        try:
            with closing(self._connect()) as connection:
                if bucket_seconds is None:
                    cursor = connection.execute(
                        f"SELECT measured_at, {column}, 1 FROM samples "  # noqa: S608
                        "WHERE sensor_group = ? AND measured_at >= ? AND measured_at <= ? "
                        f"AND {column} IS NOT NULL ORDER BY measured_at, id",
                        (group, since.timestamp(), until.timestamp()),
                    )
                else:
                    cursor = connection.execute(
                        f"SELECT CAST(measured_at / ? AS INTEGER) * ?, AVG({column}), "  # noqa: S608
                        "COUNT(*) FROM samples WHERE sensor_group = ? AND measured_at >= ? "
                        f"AND measured_at <= ? AND {column} IS NOT NULL GROUP BY 1 ORDER BY 1",
                        (
                            bucket_seconds,
                            bucket_seconds,
                            group,
                            since.timestamp(),
                            until.timestamp(),
                        ),
                    )
                return tuple(
                    SeriesPoint(datetime.fromtimestamp(ts, UTC), float(value), int(count))
                    for ts, value, count in cursor.fetchall()
                )
        except sqlite3.Error as exc:
            raise HistoryError(
                "Could not read Sense HAT history", details={"reason": str(exc)}
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection
