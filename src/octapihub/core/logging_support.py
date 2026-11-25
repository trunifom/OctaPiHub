"""Log redaction and an in-memory ring of recent log lines for the interface."""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

_MAC = re.compile(r"\b([0-9A-Fa-f]{2}):(?:[0-9A-Fa-f]{2}:){4}([0-9A-Fa-f]{2})\b")
_ASSIGNMENT = re.compile(
    r"(?i)\b(hue-application-key|application[_ -]?key|hue_application_key|token|password|"
    r"passwd|secret|api[_-]?key|authorization)(\s*[:=]\s*)(?:Bearer\s+)?[^\s,;'\"]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_MIN_SECRET_LENGTH = 6
REDACTED = "***"


class Redactor:
    """Mask credentials and shorten device addresses in text.

    Beyond pattern-based masking, exact secret values can be registered (for example the
    web token); those are removed wherever they appear.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()
        for secret in secrets:
            self.add_secret(secret)

    def add_secret(self, value: str | None) -> None:
        """Register a literal secret; very short values are ignored to avoid mangling text."""
        if value and len(value) >= _MIN_SECRET_LENGTH:
            with self._lock:
                self._secrets.add(value)

    def redact(self, text: str) -> str:
        """Return ``text`` with credentials masked and MAC addresses shortened."""
        result = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
        result = _BEARER.sub(f"Bearer {REDACTED}", result)
        result = _MAC.sub(
            lambda m: f"{m.group(1).upper()}:**:**:**:**:{m.group(2).upper()}", result
        )
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            result = result.replace(secret, REDACTED)
        return result


class RedactingFilter(logging.Filter):
    """Redact the fully formatted message of every record passing through a handler."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.redact(record.getMessage())
        record.args = None
        if record.exc_info is not None:
            # Keep the exception type for diagnosis but never log the (possibly secret) text.
            record.exc_text = f"{record.exc_info[0].__name__ if record.exc_info[0] else ''}"
            record.exc_info = None
        return True


@dataclass(frozen=True, slots=True)
class LogLine:
    """One recorded log message."""

    time: datetime
    level: str
    level_number: int
    logger: str
    message: str


class LogRing(logging.Handler):
    """Keep the newest log lines in memory, already redacted."""

    def __init__(self, redactor: Redactor, capacity: int = 500) -> None:
        super().__init__(level=logging.INFO)
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._redactor = redactor
        self._lines: deque[LogLine] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self._redactor.redact(record.getMessage())
            if record.exc_info and record.exc_info[0] is not None:
                message += f" [{record.exc_info[0].__name__}]"
            elif record.exc_text:
                message += f" [{record.exc_text}]"
            self._lines.append(
                LogLine(
                    datetime.fromtimestamp(record.created, UTC),
                    record.levelname,
                    record.levelno,
                    record.name,
                    message,
                )
            )
        except Exception:  # noqa: BLE001 - logging must never raise into the caller
            self.handleError(record)

    def lines(self, minimum_level: int = logging.INFO, limit: int = 200) -> tuple[LogLine, ...]:
        """Newest-first lines at or above ``minimum_level``."""
        selected = [line for line in reversed(self._lines) if line.level_number >= minimum_level]
        return tuple(selected[:limit])
