"""Support bundle: a ZIP with status, redacted configuration and recent logs.

The bundle is meant to be sent to someone helping with a problem, so it never contains
credentials, full device addresses or the bridge address.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from octapihub import __version__
from octapihub.application.hue_service import HueState
from octapihub.core.config import config_to_toml
from octapihub.core.errors import OctaPiHubError
from octapihub.core.logging_support import Redactor

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

_HIDDEN_HOST = "hidden.invalid"
_README = """OctaPiHub Diagnose-Paket
========================

Erstellt: {created}
Version: {version}

Inhalt
- status.json   Zustand der Komponenten (ohne Adressen und Zugangsdaten)
- system.json   Werte des Geräts (Temperatur, Speicher, Stromversorgung, ...)
- config.toml   Einstellungen; Geräteadressen sind gekürzt, die Bridge-Adresse entfernt
- logs.txt      Letzte Meldungen, Zugangsdaten unkenntlich gemacht

Nicht enthalten: Zugriffs-Token, Hue-Schlüssel, Passwörter, volle Bluetooth-Adressen.
Prüfen Sie den Inhalt trotzdem, bevor Sie das Paket weitergeben.
"""


def _status(backend: WebBackend) -> dict[str, Any]:
    sense = backend.sense_hat
    status: dict[str, Any] = {
        "version": __version__,
        "sense_hat": {
            "enabled": backend.settings.config.sense_hat.enabled,
            "running": sense.running,
            "hardware_error_code": sense.hardware_error_code,
            "history_error_code": sense.history_error_code,
            "groups": [
                {
                    "group": s.group,
                    "state": s.state.value,
                    "error_code": s.last_error_code,
                    "consecutive_failures": s.consecutive_failures,
                }
                for s in sense.statuses
            ],
        },
        "cubes": None,
        "hue": None,
    }
    if backend.cubes is not None:
        status["cubes"] = {
            "running": backend.cubes.running,
            "error_code": backend.cubes.error_code,
            "cubes": [
                {
                    "name": s.name,
                    "role": s.role,
                    "state": s.state.value,
                    "connected": s.connected,
                    "reconnect_attempt": s.reconnect_attempt,
                    "dropped_notifications": s.dropped_notifications,
                    "last_face": s.last_value,
                    "error": s.last_error,
                }
                for s in backend.cubes.statuses
            ],
        }
    if backend.hue is not None:
        hue = backend.hue.status
        status["hue"] = {
            "state": hue.state.value,
            "paired": hue.state is HueState.PAIRED,
            "bridge_configured": bool(hue.host),
            "error_code": hue.last_error_code,
            "scenes_known": len(backend.hue.scenes),
        }
    return status


def _config_text(backend: WebBackend) -> str:
    config = backend.settings.config
    if config.hue.host:
        config = replace(config, hue=replace(config.hue, host=_HIDDEN_HOST, ca_file=""))
    return config_to_toml(config)


def _json(data: object) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"


def collect_diagnostics(backend: WebBackend, redactor: Redactor, now: datetime) -> dict[str, str]:
    """Gather the text files of the bundle; every file passes through ``redactor`` once more."""
    # The bridge address may also appear inside log messages; remove it from the bundle.
    hue = backend.settings.config.hue
    bundle_redactor = Redactor()
    bundle_redactor.add_secret(hue.host)
    bundle_redactor.add_secret(hue.ca_file)
    files: dict[str, str] = {
        "README.txt": _README.format(
            created=now.isoformat(timespec="seconds"), version=__version__
        ),
        "status.json": _json(_status(backend)),
        "config.toml": _config_text(backend),
    }
    if backend.system is not None:
        snapshot = backend.system.read()
        files["system.json"] = _json(
            {
                "uptime_seconds": snapshot.uptime_seconds,
                "load_average": snapshot.load_average,
                "memory_total_kb": snapshot.memory_total_kb,
                "memory_available_kb": snapshot.memory_available_kb,
                "cpu_temperature_c": snapshot.cpu_temperature_c,
                "throttled_raw": snapshot.throttle.raw if snapshot.throttle else None,
                "disk_total_bytes": snapshot.disk_total_bytes,
                "disk_free_bytes": snapshot.disk_free_bytes,
                "python": snapshot.python_version,
                "platform": snapshot.platform,
            }
        )
    if backend.logs is not None:
        lines = reversed(backend.logs.lines(limit=500))
        files["logs.txt"] = (
            "\n".join(
                f"{line.time.isoformat(timespec='seconds')} {line.level} "
                f"{line.logger}: {line.message}"
                for line in lines
            )
            + "\n"
        )
    return {name: bundle_redactor.redact(redactor.redact(text)) for name, text in files.items()}


def build_zip(files: dict[str, str], now: datetime) -> bytes:
    """Create the ZIP in memory.

    Raises:
        OctaPiHubError: If the archive cannot be created.
    """
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in files.items():
                info = zipfile.ZipInfo(name, date_time=now.timetuple()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, text.encode("utf-8"))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise OctaPiHubError(
            "Could not create the diagnostics archive",
            user_message="Das Diagnose-Paket konnte nicht erstellt werden.",
            code="CH-WEB-007",
        ) from exc
    return buffer.getvalue()
