"""Owner-only ``secrets.env`` storage for credentials such as the Hue application key."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

from octapihub.core.errors import SecretsError

_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_VALUE_LENGTH = 512
_MAX_FILE_BYTES = 64 * 1024
_GROUP_OTHER_BITS = 0o077

HUE_APPLICATION_KEY = "HUE_APPLICATION_KEY"


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not _KEY_PATTERN.fullmatch(key):
        raise SecretsError("Secret names must be upper-case words such as HUE_APPLICATION_KEY")


def _validate_value(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_VALUE_LENGTH
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise SecretsError("A secret value must be 1-512 printable ASCII characters without spaces")


class SecretsFile:
    """Read and atomically update a ``KEY=VALUE`` file readable only by its owner.

    The file is refused when other users could read it (POSIX only). Messages and details
    never contain secret values, and updates keep unrelated lines and comments intact.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def read(self) -> dict[str, str]:
        """Return all secrets; a missing file means none are set."""
        return dict(self._parse(self._read_lines()))

    def get(self, key: str) -> str | None:
        """Return one secret or ``None`` when it is not set."""
        _validate_key(key)
        return self.read().get(key)

    def set(self, key: str, value: str) -> None:
        """Create or replace one secret and persist it with owner-only permissions."""
        _validate_key(key)
        _validate_value(value)
        lines = self._read_lines()
        self._parse(lines)  # refuse to rewrite a malformed file
        updated = f"{key}={value}"
        replaced = False
        result: list[str] = []
        for line in lines:
            if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
                result.append(updated)
                replaced = True
            else:
                result.append(line)
        if not replaced:
            result.append(updated)
        self._write(result)

    def delete(self, key: str) -> bool:
        """Remove one secret; returns whether it existed."""
        _validate_key(key)
        lines = self._read_lines()
        self._parse(lines)
        kept = [
            line
            for line in lines
            if line.lstrip().startswith("#") or line.split("=", 1)[0].strip() != key
        ]
        if len(kept) == len(lines):
            return False
        self._write(kept)
        return True

    def _read_lines(self) -> list[str]:
        try:
            status = self._path.stat()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise SecretsError(
                "Could not inspect the secrets file", details={"reason": type(exc).__name__}
            ) from exc
        if os.name == "posix" and status.st_mode & _GROUP_OTHER_BITS:
            raise SecretsError(
                "The secrets file is readable by other users",
                user_message=(
                    "Die Datei secrets.env ist für andere Benutzer lesbar. "
                    "Setzen Sie die Rechte mit: chmod 600 secrets.env"
                ),
                details={"mode": oct(status.st_mode & 0o777)},
            )
        if status.st_size > _MAX_FILE_BYTES:
            raise SecretsError("The secrets file is unexpectedly large")
        try:
            return self._path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise SecretsError(
                "Could not read the secrets file", details={"reason": type(exc).__name__}
            ) from exc

    @staticmethod
    def _parse(lines: list[str]) -> dict[str, str]:
        secrets: dict[str, str] = {}
        for number, raw in enumerate(lines, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not separator or not _KEY_PATTERN.fullmatch(key) or not value:
                raise SecretsError(
                    "The secrets file contains an invalid line", details={"line": number}
                )
            if key in secrets:
                raise SecretsError(
                    "The secrets file defines a name twice", details={"line": number}
                )
            secrets[key] = value
        return secrets

    def _write(self, lines: list[str]) -> None:
        contents = "\n".join(lines) + "\n"
        temporary: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp creates the file with mode 0600, so the secret is never world-readable.
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError as exc:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
            raise SecretsError(
                "Could not write the secrets file", details={"reason": type(exc).__name__}
            ) from exc
