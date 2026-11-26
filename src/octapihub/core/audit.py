"""The change log: who changed what, and when (never the content, never a secret).

One JSON object per line in a file in the data folder. The file is rotated at 512 KB (one older
file is kept), so it cannot fill the SD card. Failing to write must never stop a request.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_BYTES = 512 * 1024
_MAX_FIELD = 200


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One line of the change log."""

    time: str  # UTC, ISO 8601
    client: str
    action: str
    result: str  # "ok" or "abgelehnt"


def _clean(text: str) -> str:
    return "".join(c if c.isprintable() else " " for c in text)[:_MAX_FIELD]


class AuditLog:
    """Append-only change log with rotation and a reader for the interface."""

    def __init__(
        self,
        path: str | Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._path = Path(path)
        self._clock = clock
        self._max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(self, client: str, action: str, result: str = "ok") -> None:
        """Add one entry; problems are logged, not raised."""
        entry = AuditEntry(
            self._clock().astimezone(UTC).isoformat(timespec="seconds"),
            _clean(client),
            _clean(action),
            _clean(result),
        )
        line = json.dumps(
            {
                "time": entry.time,
                "client": entry.client,
                "action": entry.action,
                "result": entry.result,
            },
            ensure_ascii=False,
        )
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._path.exists() and self._path.stat().st_size >= self._max_bytes:
                    os.replace(self._path, self._path.with_name(self._path.name + ".1"))
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:
            logger.warning("Change log could not be written: %s", exc)

    def recent(self, limit: int = 50) -> list[AuditEntry]:
        """The newest entries first (unreadable lines are skipped)."""
        entries: list[AuditEntry] = []
        try:
            with self._lock:
                lines = []
                for name in (self._path.name + ".1", self._path.name):
                    candidate = self._path.with_name(name)
                    if candidate.exists():
                        lines.extend(candidate.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            logger.warning("Change log could not be read: %s", exc)
            return []
        for line in reversed(lines):
            try:
                data = json.loads(line)
                entries.append(
                    AuditEntry(
                        str(data["time"]),
                        str(data["client"]),
                        str(data["action"]),
                        str(data["result"]),
                    )
                )
            except (ValueError, KeyError, TypeError):
                continue
            if len(entries) >= limit:
                break
        return entries
