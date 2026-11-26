# ruff: noqa: E501 - hints are prose; long lines read better than broken ones
"""``octapihub --selftest``: checks that the Raspberry Pi is ready, and says what to fix.

Every check reports one of four results and, when something is wrong, a concrete next step. All
access to the system (files, programs, network) goes through :class:`Probe`, so the checks run in
tests without any hardware. A check that cannot run on this system is *skipped*, never failed.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from octapihub.core.config import AppConfig, SmartConnectionKind
from octapihub.core.errors import OctaPiHubError, SecretsError


class Status(StrEnum):
    """Outcome of one check."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True, slots=True)
class Result:
    """What one check found."""

    area: str
    name: str
    status: Status
    detail: str = ""
    hint: str = ""


class Probe:
    """Everything a check may ask the system; replaced by a fake in tests."""

    def run(self, command: Sequence[str]) -> str | None:
        """Output of a program, or ``None`` if it is missing or fails."""
        try:
            done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                list(command), capture_output=True, text=True, timeout=5.0, check=True
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout

    def which(self, program: str) -> str | None:
        """Path of an installed program."""
        return shutil.which(program)

    def exists(self, path: str) -> bool:
        """Whether a file or device exists."""
        return Path(path).exists()

    def read_text(self, path: str) -> str | None:
        """Text of a small file, or ``None``."""
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def has_module(self, name: str) -> bool:
        """Whether a Python package can be imported."""
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Whether a TCP connection can be opened."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def port_free(self, host: str, port: int) -> bool:
        """Whether this program could listen on the address."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return False
        return True

    def is_posix(self) -> bool:
        """Whether this is Linux or another Unix."""
        return os.name == "posix"

    def free_bytes(self, path: str) -> int | None:
        """Free space at ``path``."""
        try:
            return shutil.disk_usage(path).free
        except OSError:
            return None

    def writable(self, path: str) -> bool:
        """Whether a directory can be written to."""
        return os.access(path, os.W_OK)

    def python_version(self) -> tuple[int, int]:
        """The running Python version."""
        return sys.version_info[0], sys.version_info[1]


@dataclass(frozen=True, slots=True)
class Context:
    """What is being checked."""

    config: AppConfig
    config_note: str
    secrets_path: str
    data_dir: str
    host: str
    port: int
    token: str | None
    probe: Probe


Check = Callable[[Context], list[Result]]


def _ok(area: str, name: str, detail: str = "") -> Result:
    return Result(area, name, Status.OK, detail)


def _warn(area: str, name: str, detail: str, hint: str) -> Result:
    return Result(area, name, Status.WARN, detail, hint)


def _fail(area: str, name: str, detail: str, hint: str) -> Result:
    return Result(area, name, Status.FAIL, detail, hint)


def _skip(area: str, name: str, detail: str) -> Result:
    return Result(area, name, Status.SKIP, detail)


# ---------------------------------------------------------------- the checks


def check_system(ctx: Context) -> list[Result]:
    version = ctx.probe.python_version()
    results = [
        _ok("System", "Python", f"{version[0]}.{version[1]}")
        if version >= (3, 12)
        else _fail(
            "System", "Python", f"{version[0]}.{version[1]}", "Python 3.12 oder neuer installieren."
        )
    ]
    if not ctx.probe.is_posix():
        return [
            *results,
            _skip(
                "System",
                "Linux-Prüfungen",
                "Dieses System ist kein Linux; Hardware-Prüfungen entfallen.",
            ),
        ]
    groups = (ctx.probe.run(["id", "-nG"]) or "").split()
    if groups:
        missing = [g for g in ("bluetooth", "i2c", "input", "audio") if g not in groups]
        results.append(
            _ok("System", "Gruppen des Benutzers", ", ".join(sorted(groups)))
            if not missing
            else _warn(
                "System",
                "Gruppen des Benutzers",
                "fehlt: " + ", ".join(missing),
                f"sudo usermod -aG {','.join(missing)} <Benutzer> (der Dienst-Benutzer ist meist octapihub), dann neu starten.",
            )
        )
    return results


def check_config(ctx: Context) -> list[Result]:
    config = ctx.config
    detail = f"{len(config.cubes)} Würfel, {len(config.music.targets)} Wiedergabegeräte, {len(config.smarthome.actions)} Smart-Home-Aktionen"
    results = [
        _warn(
            "Einstellungen",
            "Konfiguration",
            ctx.config_note,
            "Prüfen Sie die Einstellungen und speichern Sie sie einmal neu.",
        )
        if ctx.config_note
        else _ok("Einstellungen", "Konfiguration", detail)
    ]
    try:
        from octapihub.infrastructure.secrets import SecretsFile

        SecretsFile(ctx.secrets_path).read()
        results.append(_ok("Einstellungen", "Zugangsdaten (secrets.env)", "lesbar und geschützt"))
    except SecretsError as exc:
        results.append(
            _warn(
                "Einstellungen",
                "Zugangsdaten (secrets.env)",
                exc.user_message,
                "Rechte auf 0600 setzen: chmod 600 secrets.env",
            )
        )
    token = ctx.token
    if token is None:
        results.append(
            _warn(
                "Einstellungen",
                "Zugriffs-Token",
                "nicht gesetzt: die Oberfläche ist nur lesend",
                "OCTAPIHUB_WEB_TOKEN in octapihub.env setzen.",
            )
        )
    elif len(token) < 12 or token.startswith("HIER-EIN"):
        results.append(
            _fail(
                "Einstellungen",
                "Zugriffs-Token",
                "zu kurz oder noch der Platzhalter",
                "Ein langes, zufälliges Token setzen (siehe Installation, Abschnitt 6).",
            )
        )
    else:
        results.append(_ok("Einstellungen", "Zugriffs-Token", f"{len(token)} Zeichen"))
    return results


def check_storage(ctx: Context) -> list[Result]:
    path = ctx.data_dir
    if not ctx.probe.exists(path):
        return [
            _fail(
                "Speicher",
                "Datenordner",
                f"{path} existiert nicht",
                f"sudo install -d -o octapihub -g octapihub {path}",
            )
        ]
    if not ctx.probe.writable(path):
        return [
            _fail(
                "Speicher",
                "Datenordner",
                f"{path} ist nicht beschreibbar",
                "Besitzer und Rechte des Ordners prüfen (Benutzer octapihub).",
            )
        ]
    free = ctx.probe.free_bytes(path)
    if free is not None and free < 200 * 1024 * 1024:
        return [
            _warn(
                "Speicher",
                "Datenordner",
                f"nur noch {free // (1024 * 1024)} MB frei",
                "Speicherplatz schaffen (SD-Karte voll?).",
            )
        ]
    return [
        _ok(
            "Speicher",
            "Datenordner",
            "beschreibbar" + (f", {free // (1024 * 1024)} MB frei" if free is not None else ""),
        )
    ]


def check_network(ctx: Context) -> list[Result]:
    if ctx.probe.port_free(ctx.host, ctx.port):
        return [_ok("Netzwerk", f"Port {ctx.port}", f"frei auf {ctx.host}")]
    return [
        _warn(
            "Netzwerk",
            f"Port {ctx.port}",
            "bereits belegt (läuft OctaPiHub schon?)",
            "Dienst stoppen oder einen anderen Port wählen (OCTAPIHUB_PORT).",
        )
    ]


def check_bluetooth(ctx: Context) -> list[Result]:
    if not ctx.probe.is_posix():
        return [_skip("Bluetooth", "Adapter", "nur unter Linux prüfbar")]
    adapter = ctx.config.bluetooth.adapter
    if not ctx.probe.exists(f"/sys/class/bluetooth/{adapter}"):
        return [
            _fail(
                "Bluetooth",
                f"Adapter {adapter}",
                "nicht gefunden",
                "Bluetooth-Adapter prüfen (bluetoothctl list, rfkill list); den Namen in den Einstellungen anpassen.",
            )
        ]
    shown = ctx.probe.run(["bluetoothctl", "show"])
    if shown is None:
        return [
            _warn(
                "Bluetooth",
                f"Adapter {adapter}",
                "vorhanden, bluetoothctl nicht nutzbar",
                "sudo apt install bluez; sudo systemctl enable --now bluetooth",
            )
        ]
    if "Powered: yes" not in shown:
        return [
            _fail(
                "Bluetooth",
                f"Adapter {adapter}",
                "ausgeschaltet",
                "bluetoothctl power on (und rfkill unblock bluetooth)",
            )
        ]
    return [_ok("Bluetooth", f"Adapter {adapter}", "eingeschaltet")]


def check_sense_hat(ctx: Context) -> list[Result]:
    if not ctx.config.sense_hat.enabled:
        return [_skip("Sense HAT", "Aufsatz", "in den Einstellungen ausgeschaltet")]
    if not ctx.probe.is_posix():
        return [_skip("Sense HAT", "Aufsatz", "nur unter Linux prüfbar")]
    results: list[Result] = []
    if ctx.probe.exists("/dev/i2c-1"):
        results.append(_ok("Sense HAT", "I2C", "/dev/i2c-1 vorhanden"))
    else:
        results.append(
            _fail(
                "Sense HAT",
                "I2C",
                "/dev/i2c-1 fehlt",
                "sudo raspi-config nonint do_i2c 0, dann neu starten.",
            )
        )
    results.append(
        _ok("Sense HAT", "Bibliothek sense_hat", "installiert")
        if ctx.probe.has_module("sense_hat")
        else _fail(
            "Sense HAT",
            "Bibliothek sense_hat",
            "nicht installiert",
            "sudo apt install sense-hat (im selben Python-Umfeld wie OctaPiHub).",
        )
    )
    devices = ctx.probe.read_text("/proc/bus/input/devices") or ""
    if "Sense HAT Joystick" in devices:
        results.append(_ok("Sense HAT", "Joystick", "gefunden"))
    elif ctx.config.sense_hat.joystick_enabled:
        results.append(
            _warn(
                "Sense HAT",
                "Joystick",
                "nicht gefunden",
                "Aufsatz fest aufstecken; Gruppe input des Dienst-Benutzers prüfen.",
            )
        )
    return results


def check_music(ctx: Context) -> list[Result]:
    music = ctx.config.music
    if not music.enabled:
        return [_skip("Musik", "Wiedergabe", "in den Einstellungen ausgeschaltet")]
    results: list[Result] = []
    kinds = {t.kind.value for t in music.targets}
    if kinds & {"local", "bluetooth"}:
        results.append(
            _ok("Musik", "mpv", ctx.probe.which("mpv") or "")
            if ctx.probe.which("mpv")
            else _fail("Musik", "mpv", "nicht installiert", "sudo apt install mpv")
        )
        results.append(
            _ok("Musik", "yt-dlp (YouTube)", "installiert")
            if ctx.probe.which("yt-dlp")
            else _warn(
                "Musik",
                "yt-dlp (YouTube)",
                "nicht installiert",
                "sudo apt install yt-dlp (nur für YouTube-Links nötig)",
            )
        )
        cards = ctx.probe.run(["aplay", "-l"])
        if cards is None:
            results.append(
                _warn("Musik", "Audiogeräte", "aplay nicht nutzbar", "sudo apt install alsa-utils")
            )
        elif "card" in cards:
            results.append(_ok("Musik", "Audiogeräte", f"{cards.count('card ')} Karte(n) gefunden"))
        else:
            results.append(
                _warn(
                    "Musik",
                    "Audiogeräte",
                    "keine Tonkarte gefunden",
                    "Kopfhörerbuchse, HDMI oder USB-Soundkarte anschließen.",
                )
            )
    if "cast" in kinds:
        results.append(
            _ok("Musik", "pychromecast (Google-Geräte)", "installiert")
            if ctx.probe.has_module("pychromecast")
            else _fail(
                "Musik",
                "pychromecast (Google-Geräte)",
                "nicht installiert",
                'pip install "octapihub[music]"',
            )
        )
    if music.library_dir:
        folder = music.library_dir
        if not ctx.probe.exists(folder):
            results.append(
                _fail(
                    "Musik",
                    "Musikordner",
                    f"{folder} nicht gefunden",
                    "Ordner anlegen (z. B. /srv/music) oder den Pfad anpassen.",
                )
            )
        elif folder.startswith("/home"):
            results.append(
                _warn(
                    "Musik",
                    "Musikordner",
                    f"{folder} liegt unter /home",
                    "Der Dienst darf /home nicht lesen: nach /srv/music verlegen.",
                )
            )
        else:
            results.append(_ok("Musik", "Musikordner", folder))
    return results


def check_devices(ctx: Context) -> list[Result]:
    results: list[Result] = []
    hue = ctx.config.hue
    if hue.enabled and hue.host:
        results.append(
            _ok("Geräte", "Hue Bridge", f"{hue.host}:{hue.port} erreichbar")
            if ctx.probe.tcp(hue.host, hue.port)
            else _warn(
                "Geräte",
                "Hue Bridge",
                f"{hue.host}:{hue.port} nicht erreichbar",
                "Adresse und Netzwerk prüfen (Hue-App → Bridges).",
            )
        )
    for connection in ctx.config.smarthome.connections:
        if connection.kind is SmartConnectionKind.MQTT:
            host, _, port = connection.address.rpartition(":")
            target = (host, int(port))
        else:
            parts = urlsplit(connection.address)
            target = (parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))
        results.append(
            _ok("Geräte", connection.name, f"{target[0]}:{target[1]} erreichbar")
            if ctx.probe.tcp(*target)
            else _warn(
                "Geräte",
                connection.name,
                f"{target[0]}:{target[1]} nicht erreichbar",
                "Adresse, Port und ob das Gerät läuft prüfen.",
            )
        )
    if not results:
        results.append(_skip("Geräte", "Hue und Smart Home", "nichts eingerichtet"))
    return results


def check_time(ctx: Context) -> list[Result]:
    if not ctx.probe.is_posix():
        return [_skip("Zeit", "Uhr", "nur unter Linux prüfbar")]
    answer = (
        (ctx.probe.run(["timedatectl", "show", "--property=NTPSynchronized", "--value"]) or "")
        .strip()
        .lower()
    )
    if answer == "yes":
        return [_ok("Zeit", "Uhr", "über das Internet abgeglichen (NTP)")]
    if answer == "no":
        return [
            _warn(
                "Zeit",
                "Uhr",
                "noch nicht abgeglichen",
                "sudo timedatectl set-ntp true; Netzwerk prüfen. Ein Raspberry Pi hat keine Batterie-Uhr.",
            )
        ]
    return [_skip("Zeit", "Uhr", "timedatectl nicht verfügbar")]


CHECKS: tuple[Check, ...] = (
    check_system,
    check_config,
    check_storage,
    check_network,
    check_bluetooth,
    check_sense_hat,
    check_music,
    check_devices,
    check_time,
)

_MARK = {Status.OK: "[ OK ]", Status.WARN: "[WARN]", Status.FAIL: "[FEHL]", Status.SKIP: "[ -- ]"}


def run_checks(ctx: Context, checks: Sequence[Check] = CHECKS) -> list[Result]:
    """Run every check; a check that itself breaks is reported as a failure, never raised."""
    results: list[Result] = []
    for check in checks:
        try:
            results.extend(check(ctx))
        except (OctaPiHubError, OSError, ValueError) as exc:
            results.append(
                _fail(
                    "Selbsttest",
                    check.__name__,
                    f"Prüfung abgebrochen: {type(exc).__name__}",
                    "Bitte melden (Diagnose-Paket).",
                )
            )
    return results


def exit_code(results: Sequence[Result]) -> int:
    """0 = everything fine, 1 = warnings, 2 = at least one failure."""
    if any(r.status is Status.FAIL for r in results):
        return 2
    return 1 if any(r.status is Status.WARN for r in results) else 0


def format_report(results: Sequence[Result]) -> str:
    """The report as text, grouped by area, with the next step under every problem."""
    lines: list[str] = ["OctaPiHub Selbsttest", "=" * 20]
    area = ""
    for result in results:
        if result.area != area:
            area = result.area
            lines.append(f"\n{area}")
        text = f" {_MARK[result.status]} {result.name}"
        if result.detail:
            text += f": {result.detail}"
        lines.append(text)
        if result.hint and result.status in (Status.WARN, Status.FAIL):
            lines.append(f"        → {result.hint}")
    counts = {s: sum(1 for r in results if r.status is s) for s in Status}
    lines.append(
        f"\nErgebnis: {counts[Status.OK]} in Ordnung, {counts[Status.WARN]} Hinweise, "
        f"{counts[Status.FAIL]} Fehler, {counts[Status.SKIP]} übersprungen."
    )
    code = exit_code(results)
    lines.append(
        {
            0: "Alles bereit.",
            1: "Läuft, aber die Hinweise sollten Sie ansehen.",
            2: "Es fehlt etwas Wichtiges: bitte die Fehler beheben.",
        }[code]
    )
    return "\n".join(lines) + "\n"
