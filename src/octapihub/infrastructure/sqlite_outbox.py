"""SQLite WAL implementation of the durable action outbox."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from octapihub.application.outbox import OutboxEntry, OutboxState
from octapihub.core.config import RuleActionConfig
from octapihub.core.errors import OutboxError, ValidationError

_OUTBOX_SCHEMA_VERSION = 1


class SQLiteOutbox:
    """Persist action steps and recover interrupted work when the process restarts.

    The application must run one active outbox owner process. Actions are at-least-once:
    a process failure after an external side effect but before completion can replay it.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            with closing(self._connect()) as connection:
                journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                if journal_mode is None or str(journal_mode[0]).lower() != "wal":
                    raise sqlite3.DatabaseError("Could not enable SQLite WAL mode")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > _OUTBOX_SCHEMA_VERSION:
                    raise sqlite3.DatabaseError("Outbox database schema is newer than supported")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_id TEXT NOT NULL,
                        cube TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        action TEXT NOT NULL,
                        parameters_json TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        available_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('pending', 'in_progress', 'succeeded', 'expired')
                        ),
                        last_error TEXT,
                        finished_at REAL,
                        UNIQUE(batch_id, sequence)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outbox_cube_order ON outbox(cube, id)"
                )
                connection.execute(f"PRAGMA user_version={_OUTBOX_SCHEMA_VERSION}")
                with connection:
                    connection.execute(
                        """
                        UPDATE outbox
                        SET status = 'pending',
                            available_at = MIN(available_at, ?),
                            last_error = 'recovered_after_restart'
                        WHERE status = 'in_progress'
                        """,
                        (self._clock_now(),),
                    )
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not initialize the SQLite action outbox",
                details={"reason": str(exc)},
            ) from exc

    def enqueue(
        self,
        cube: str,
        actions: Sequence[RuleActionConfig],
        *,
        created_at: float,
        expires_at: float,
    ) -> str:
        """Insert an ordered batch atomically."""
        _validate_times(created_at, expires_at)
        if not cube.strip():
            raise ValidationError("Outbox cube name must not be empty")
        if not actions:
            raise ValidationError("Outbox action batch must not be empty")
        serialized = [
            (
                action,
                _serialize_parameters(action.parameters),
            )
            for action in actions
        ]
        batch_id = uuid4().hex
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.executemany(
                        """
                        INSERT INTO outbox (
                            batch_id, cube, sequence, kind, action, parameters_json,
                            attempts, created_at, available_at, expires_at, status
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'pending')
                        """,
                        [
                            (
                                batch_id,
                                cube,
                                sequence,
                                action.kind,
                                action.action,
                                parameters_json,
                                created_at,
                                created_at,
                                expires_at,
                            )
                            for sequence, (action, parameters_json) in enumerate(serialized)
                        ],
                    )
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not enqueue action batch",
                details={"reason": str(exc)},
            ) from exc
        return batch_id

    def claim_next(self, cube: str, *, now: float) -> OutboxEntry | None:
        """Claim only the oldest non-terminal action for the requested cube."""
        _validate_time(now, "now")
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """
                        UPDATE outbox
                        SET status = 'expired', last_error = 'expired', finished_at = ?
                        WHERE cube = ? AND status = 'pending' AND expires_at <= ?
                        """,
                        (now, cube, now),
                    )
                    row = connection.execute(
                        """
                        SELECT * FROM outbox
                        WHERE cube = ? AND status IN ('pending', 'in_progress')
                        ORDER BY id
                        LIMIT 1
                        """,
                        (cube,),
                    ).fetchone()
                    if row is None or row["status"] != OutboxState.PENDING.value:
                        return None
                    if float(row["available_at"]) > now:
                        return None
                    cursor = connection.execute(
                        """
                        UPDATE outbox
                        SET status = 'in_progress', attempts = attempts + 1
                        WHERE id = ? AND status = 'pending'
                        """,
                        (int(row["id"]),),
                    )
                    if cursor.rowcount != 1:
                        return None
                    claimed = connection.execute(
                        "SELECT * FROM outbox WHERE id = ?",
                        (int(row["id"]),),
                    ).fetchone()
                    if claimed is None:
                        raise sqlite3.DatabaseError("Claimed outbox row disappeared")
                    return _row_to_entry(claimed)
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not claim the next outbox action",
                details={"reason": str(exc)},
            ) from exc

    def complete(self, entry_id: int, *, finished_at: float) -> None:
        """Mark a claimed action as completed."""
        _validate_time(finished_at, "finished_at")
        try:
            with closing(self._connect()) as connection:
                with connection:
                    cursor = connection.execute(
                        """
                        UPDATE outbox
                        SET status = 'succeeded', finished_at = ?, last_error = NULL
                        WHERE id = ? AND status = 'in_progress'
                        """,
                        (finished_at, entry_id),
                    )
                    if cursor.rowcount != 1:
                        raise OutboxError(
                            "Outbox action is not in progress",
                            details={"entry_id": entry_id},
                        )
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not complete outbox action",
                details={"reason": str(exc)},
            ) from exc

    def retry(
        self,
        entry_id: int,
        *,
        now: float,
        retry_at: float,
        error_code: str,
    ) -> OutboxState:
        """Schedule a retry, or make the action terminal when its TTL has elapsed."""
        _validate_time(now, "now")
        _validate_time(retry_at, "retry_at")
        if not error_code:
            raise ValidationError("Outbox error code must not be empty")
        try:
            with closing(self._connect()) as connection:
                with connection:
                    row = connection.execute(
                        """
                        SELECT expires_at FROM outbox
                        WHERE id = ? AND status = 'in_progress'
                        """,
                        (entry_id,),
                    ).fetchone()
                    if row is None:
                        raise OutboxError(
                            "Outbox action is not in progress",
                            details={"entry_id": entry_id},
                        )
                    state = (
                        OutboxState.EXPIRED
                        if now >= float(row["expires_at"]) or retry_at >= float(row["expires_at"])
                        else OutboxState.PENDING
                    )
                    connection.execute(
                        """
                        UPDATE outbox
                        SET status = ?, available_at = ?, last_error = ?, finished_at = ?
                        WHERE id = ? AND status = 'in_progress'
                        """,
                        (
                            state.value,
                            retry_at,
                            error_code,
                            now if state is OutboxState.EXPIRED else None,
                            entry_id,
                        ),
                    )
                    return state
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not update outbox retry state",
                details={"reason": str(exc)},
            ) from exc

    def get_entry(self, entry_id: int) -> OutboxEntry:
        """Read a persisted action and its current status."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM outbox WHERE id = ?",
                    (entry_id,),
                ).fetchone()
                if row is None:
                    raise OutboxError(
                        "Outbox action does not exist",
                        details={"entry_id": entry_id},
                    )
                return _row_to_entry(row)
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not read outbox action",
                details={"reason": str(exc)},
            ) from exc

    def get_batch(self, batch_id: str) -> tuple[OutboxEntry, ...]:
        """Return persisted statuses for one event batch in execution order."""
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM outbox WHERE batch_id = ? ORDER BY sequence",
                    (batch_id,),
                ).fetchall()
                if not rows:
                    raise OutboxError(
                        "Outbox batch does not exist",
                        details={"batch_id": batch_id},
                    )
                return tuple(_row_to_entry(row) for row in rows)
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not read outbox batch",
                details={"reason": str(exc)},
            ) from exc

    def prune_terminal(self, *, finished_before: float) -> int:
        """Delete only succeeded or expired records older than the given timestamp."""
        _validate_time(finished_before, "finished_before")
        try:
            with closing(self._connect()) as connection:
                with connection:
                    cursor = connection.execute(
                        """
                        DELETE FROM outbox
                        WHERE status IN ('succeeded', 'expired') AND finished_at < ?
                        """,
                        (finished_before,),
                    )
                    return cursor.rowcount
        except sqlite3.Error as exc:
            raise OutboxError(
                "Could not prune terminal outbox records",
                details={"reason": str(exc)},
            ) from exc

    @staticmethod
    def _clock_now() -> float:
        return time.time()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA synchronous=FULL")
        except sqlite3.Error:
            connection.close()
            raise
        return connection


def _validate_time(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationError(f"{field} must be finite")


def _validate_times(created_at: float, expires_at: float) -> None:
    _validate_time(created_at, "created_at")
    _validate_time(expires_at, "expires_at")
    if expires_at <= created_at:
        raise ValidationError("Outbox expiry must be later than creation")


def _serialize_parameters(parameters: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            _plain_json_value(parameters),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError("Outbox action parameters are not valid JSON") from exc


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json_value(item) for item in value]
    return value


def _row_to_entry(row: sqlite3.Row) -> OutboxEntry:
    try:
        raw_parameters = json.loads(str(row["parameters_json"]))
        parameters = _freeze_json_value(raw_parameters)
        if not isinstance(parameters, Mapping):
            raise ValueError("Action parameters are not an object")
        return OutboxEntry(
            id=int(row["id"]),
            batch_id=str(row["batch_id"]),
            cube=str(row["cube"]),
            sequence=int(row["sequence"]),
            action=RuleActionConfig(
                kind=str(row["kind"]),
                action=str(row["action"]),
                parameters=parameters,
            ),
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            available_at=float(row["available_at"]),
            expires_at=float(row["expires_at"]),
            state=OutboxState(str(row["status"])),
            last_error=None if row["last_error"] is None else str(row["last_error"]),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise OutboxError("Stored outbox action is invalid") from exc


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON object keys must be strings")
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Stored action parameter contains an invalid JSON value")
