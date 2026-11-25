"""Exception hierarchy with stable codes and German user messages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar


class OctaPiHubError(Exception):
    """A deliberate OctaPiHub failure."""

    code: ClassVar[str] = "CH-GENERIC-000"
    default_user_message: ClassVar[str] = (
        "Ein unerwarteter Fehler ist aufgetreten. Details stehen im Log."
    )

    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        details: Mapping[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.user_message = user_message or self.default_user_message
        self.details = dict(details) if details else {}
        self._code = code

    @property
    def error_code(self) -> str:
        """Return the effective error code."""
        return self._code or self.code

    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class ConfigurationError(OctaPiHubError):
    """Configuration is missing, unreadable or invalid."""

    code = "CH-CFG-001"
    default_user_message = "Die Einstellungen sind ungültig oder konnten nicht gelesen werden."


class ValidationError(OctaPiHubError, ValueError):
    """Input data violates a documented rule."""

    code = "CH-CFG-002"
    default_user_message = "Die Eingabe ist ungültig. Bitte prüfen Sie Ihre Angaben."


class ProtocolError(ValidationError):
    """A BLE payload does not match the documented orientation protocol."""

    code = "CH-BLE-001"
    default_user_message = "Der Würfel hat unerwartete Daten gesendet."


class BleConnectionError(OctaPiHubError):
    """Scanning or connecting to a cube failed."""

    code = "CH-BLE-002"
    default_user_message = (
        "Die Bluetooth-Verbindung zum Würfel ist fehlgeschlagen. "
        "Bitte prüfen Sie Bluetooth und die Entfernung zum Würfel."
    )


class UnsupportedDeviceError(OctaPiHubError):
    """A device does not expose the required BLE capability."""

    code = "CH-BLE-003"
    default_user_message = "Der Würfel bietet die benötigte Bluetooth-Funktion nicht an."


class PluginError(OctaPiHubError):
    """A plugin could not be loaded, validated or resolved."""

    code = "CH-PLUG-001"
    default_user_message = "Ein Erweiterungsmodul ist ungültig oder konnte nicht geladen werden."


class OutboxError(OctaPiHubError):
    """The durable action queue could not be accessed or updated."""

    code = "CH-DB-001"
    default_user_message = "Die Aktionswarteschlange konnte nicht gelesen oder gespeichert werden."
