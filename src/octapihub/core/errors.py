"""Exception hierarchy with stable codes and German user messages."""

from __future__ import annotations

import math
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


class SenseHatError(OctaPiHubError):
    """The Sense HAT could not be initialized or accessed."""

    code = "CH-SENSE-001"
    default_user_message = "Das Sense HAT konnte nicht angesprochen werden."


class SenseHatFeatureUnavailable(SenseHatError):
    """A requested feature is not present on this Sense HAT revision."""

    code = "CH-SENSE-002"
    default_user_message = "Diese Funktion ist auf dem erkannten Sense HAT nicht verfügbar."


class HistoryError(OctaPiHubError):
    """The persistent sensor history could not be accessed or updated."""

    code = "CH-DB-002"
    default_user_message = "Der Sensorverlauf konnte nicht gelesen oder gespeichert werden."


class SecretsError(OctaPiHubError):
    """The secrets file is unreadable, unsafe, malformed, or cannot be written."""

    code = "CH-SEC-001"
    default_user_message = (
        "Die Datei mit den Zugangsdaten (secrets.env) ist ungültig, unsicher oder nicht "
        "beschreibbar."
    )


class MusicError(OctaPiHubError):
    """Music could not be played or controlled."""

    code = "CH-MUS-001"
    default_user_message = "Die Musik konnte nicht abgespielt werden."
    # Errors the user has to fix are not retried by the action outbox.
    retryable: ClassVar[bool] = True


class MusicTargetError(MusicError):
    """A playback target (speaker, display, audio output) did not respond."""

    code = "CH-MUS-002"
    default_user_message = (
        "Das Wiedergabegerät antwortet nicht. Bitte prüfen, ob es eingeschaltet und im "
        "Netzwerk erreichbar ist."
    )


class MusicSourceError(MusicError):
    """A music source (link, file, folder) is invalid, unsupported, or unavailable."""

    code = "CH-MUS-003"
    default_user_message = "Diese Musikquelle kann nicht abgespielt werden."
    retryable = False


class MusicNotConfiguredError(MusicError):
    """Music is switched off, or the action names no usable target or playlist."""

    code = "CH-MUS-004"
    default_user_message = (
        "Musik ist nicht eingerichtet. Schalten Sie sie ein und wählen Sie ein Wiedergabegerät."
    )
    retryable = False


class PluginError(OctaPiHubError):
    """A plugin could not be loaded, validated or resolved."""

    code = "CH-PLUG-001"
    default_user_message = "Ein Erweiterungsmodul ist ungültig oder konnte nicht geladen werden."


class OutboxError(OctaPiHubError):
    """The durable action queue could not be accessed or updated."""

    code = "CH-DB-001"
    default_user_message = "Die Aktionswarteschlange konnte nicht gelesen oder gespeichert werden."


class RetryableError(OctaPiHubError):
    """A failure that can recommend a minimum delay before retrying."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        user_message: str | None = None,
        details: Mapping[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        if retry_after_seconds is not None and (
            isinstance(retry_after_seconds, bool)
            or not isinstance(retry_after_seconds, (int, float))
            or not math.isfinite(retry_after_seconds)
            or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be finite and non-negative")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message,
            user_message=user_message,
            details=details,
            code=code,
        )


class HueBridgeError(RetryableError):
    """A Hue Bridge request failed."""

    code = "CH-HUE-001"
    default_user_message = "Die Hue Bridge konnte nicht sicher angesprochen werden."


class HueLinkButtonError(HueBridgeError):
    """The pairing request was refused because the bridge button was not pressed."""

    code = "CH-HUE-004"
    default_user_message = (
        "Die Taste auf der Hue Bridge wurde noch nicht gedrückt. Drücken Sie die Taste "
        "und versuchen Sie es innerhalb von 30 Sekunden erneut."
    )


class HueNotConfiguredError(HueBridgeError):
    """A Hue action ran before the bridge was configured and paired."""

    code = "CH-HUE-005"
    default_user_message = (
        "Hue ist noch nicht eingerichtet. Tragen Sie die Bridge ein und verbinden Sie sie "
        "auf der Seite Licht."
    )


class HueRateLimitError(HueBridgeError):
    """The Hue Bridge rejected a request because of rate limiting."""

    code = "CH-HUE-002"
    default_user_message = "Die Hue Bridge begrenzt Anfragen. Die Aktion wird später wiederholt."


class HueCertificateError(HueBridgeError):
    """The Hue Bridge TLS certificate could not be verified."""

    code = "CH-HUE-003"
    default_user_message = (
        "Das Zertifikat der Hue Bridge ist nicht vertrauenswürdig oder passt nicht zum Hostnamen."
    )
