from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from octapihub.application.outbox import OutboxState
from octapihub.core.config import RuleActionConfig, parse_config
from octapihub.core.errors import OutboxError, ValidationError
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox


def action_specs() -> tuple[RuleActionConfig, ...]:
    config = parse_config(
        {
            "schema_version": 2,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 2,
                    "actions": [
                        {"kind": "hue", "action": "scene.set"},
                        {"kind": "hue", "action": "light.toggle"},
                    ],
                }
            ],
        }
    )
    return config.mappings[0].actions


def test_sqlite_outbox_uses_wal_and_persists_ordered_action_batches(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    outbox = SQLiteOutbox(database_path)
    actions = action_specs()
    light_batch = outbox.enqueue(
        "Licht",
        actions,
        created_at=100.0,
        expires_at=200.0,
    )
    next_batch = outbox.enqueue(
        "Licht",
        (actions[0],),
        created_at=100.0,
        expires_at=200.0,
    )
    music_batch = outbox.enqueue(
        "Musik",
        (actions[0],),
        created_at=100.0,
        expires_at=200.0,
    )

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    first = outbox.claim_next("Licht", now=100.0)
    assert first is not None
    assert first.batch_id == light_batch
    assert first.sequence == 0
    assert first.attempts == 1
    assert outbox.claim_next("Licht", now=100.0) is None
    assert (
        outbox.retry(
            first.id,
            now=100.0,
            retry_at=110.0,
            error_code="RuntimeError",
        )
        is OutboxState.PENDING
    )

    assert outbox.claim_next("Licht", now=109.0) is None
    independent = outbox.claim_next("Musik", now=100.0)
    assert independent is not None
    assert independent.batch_id == music_batch
    outbox.complete(independent.id, finished_at=100.0)

    retried = outbox.claim_next("Licht", now=110.0)
    assert retried is not None
    assert retried.id == first.id
    assert retried.attempts == 2
    outbox.complete(retried.id, finished_at=110.0)

    second = outbox.claim_next("Licht", now=110.0)
    assert second is not None
    assert second.batch_id == light_batch
    assert second.sequence == 1
    outbox.complete(second.id, finished_at=110.0)
    following = outbox.claim_next("Licht", now=110.0)
    assert following is not None
    assert following.batch_id == next_batch

    assert [entry.state for entry in outbox.get_batch(light_batch)] == [
        OutboxState.SUCCEEDED,
        OutboxState.SUCCEEDED,
    ]


def test_outbox_expires_retries_at_ttl_and_prunes_only_old_terminal_rows(
    tmp_path: Path,
) -> None:
    outbox = SQLiteOutbox(tmp_path / "state.db")
    batch = outbox.enqueue(
        "Licht",
        (action_specs()[0],),
        created_at=10.0,
        expires_at=15.0,
    )
    item = outbox.claim_next("Licht", now=10.0)
    assert item is not None

    state = outbox.retry(
        item.id,
        now=14.0,
        retry_at=15.0,
        error_code="TimeoutError",
    )

    assert state is OutboxState.EXPIRED
    assert outbox.get_batch(batch)[0].last_error == "TimeoutError"
    assert outbox.claim_next("Licht", now=15.0) is None
    assert outbox.prune_terminal(finished_before=14.0) == 0
    assert outbox.prune_terminal(finished_before=14.1) == 1
    with pytest.raises(OutboxError, match="batch does not exist"):
        outbox.get_batch(batch)


def test_outbox_recovers_in_progress_action_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state.db"
    first_process = SQLiteOutbox(database_path)
    batch = first_process.enqueue(
        "Licht",
        (action_specs()[0],),
        created_at=20.0,
        expires_at=40.0,
    )
    interrupted = first_process.claim_next("Licht", now=20.0)
    assert interrupted is not None

    restarted = SQLiteOutbox(database_path)
    recovered = restarted.claim_next("Licht", now=21.0)

    assert recovered is not None
    assert recovered.id == interrupted.id
    assert recovered.attempts == 2
    assert restarted.get_batch(batch)[0].last_error == "recovered_after_restart"


def test_outbox_rejects_non_finite_timestamps(tmp_path: Path) -> None:
    outbox = SQLiteOutbox(tmp_path / "state.db")

    with pytest.raises(ValidationError, match="finite"):
        outbox.enqueue(
            "Licht",
            (action_specs()[0],),
            created_at=float("nan"),
            expires_at=10.0,
        )


def test_outbox_surfaces_database_open_errors(tmp_path: Path) -> None:
    with pytest.raises(OutboxError, match="initialize") as error:
        SQLiteOutbox(tmp_path / "missing" / "state.db")

    assert isinstance(error.value.__cause__, sqlite3.Error)
