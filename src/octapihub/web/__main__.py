"""Run the web interface: ``python -m octapihub.web --config octapihub.toml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC
from pathlib import Path

import uvicorn

from octapihub.application.settings import SettingsManager
from octapihub.composition import (
    build_cube_service,
    build_hue_service,
    build_matrix_service,
    build_music_service,
    build_sense_hat_service,
    build_smarthome_service,
)
from octapihub.core.errors import OctaPiHubError, SecretsError
from octapihub.core.logging_support import (
    LOG_FORMAT,
    LogRing,
    RedactingFilter,
    Redactor,
    create_file_handler,
)
from octapihub.core.resilience import (
    describe_fallback,
    install_exception_hooks,
    load_with_fallback,
)
from octapihub.infrastructure.ble.bleak_transport import BleakDeviceScanner
from octapihub.infrastructure.music.cast_player import PyChromecastBackend
from octapihub.infrastructure.music.mpv_player import list_audio_devices
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web.app import WebBackend, create_app

TOKEN_VARIABLE = "OCTAPIHUB_WEB_TOKEN"  # noqa: S105 - name of the variable, not a secret


def install_logging(
    redactor: Redactor, level: int = logging.INFO, log_dir: str | None = None
) -> LogRing:
    """Log (redacted) to the console and a rotating file; keep recent lines for the System page."""
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    console.addFilter(RedactingFilter(redactor))
    ring = LogRing(redactor)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console)
    root_logger.addHandler(ring)
    if log_dir:
        file_handler = create_file_handler(log_dir, redactor, level=level)
        if file_handler is not None:
            root_logger.addHandler(file_handler)
    return ring


def register_secrets(redactor: Redactor, secrets_path: str) -> None:
    """Make every stored secret value unprintable in logs; a bad file is not fatal here."""
    try:
        for value in SecretsFile(secrets_path).read().values():
            redactor.add_secret(value)
    except SecretsError as exc:
        logging.getLogger(__name__).warning(
            "Secrets could not be registered for log masking: %s", exc
        )


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ungültiger Port: {text!r}") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Der Port muss zwischen 1 und 65535 liegen")
    return port


def _log_level(text: str) -> int:
    level = logging.getLevelNamesMapping().get(text.strip().upper())
    if level not in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
        raise argparse.ArgumentTypeError(
            f"ungültige Ausführlichkeit: {text!r} (DEBUG, INFO, WARNING oder ERROR)"
        )
    return level


MIN_TOKEN_LENGTH = 12
_PLACEHOLDER_PREFIX = "HIER-EIN"


def token_problem(token: str | None) -> str | None:
    """Explain why a configured web token is unsafe, or return ``None`` when it is fine.

    No token is allowed (the interface is then read-only); a weak or copied placeholder
    token is not, because it would protect nothing.
    """
    if token is None:
        return None
    if token.startswith(_PLACEHOLDER_PREFIX):
        return "Das Token ist noch der Platzhalter aus der Beispieldatei. Bitte ein eigenes setzen."
    if len(token) < MIN_TOKEN_LENGTH:
        return f"Das Token ist zu kurz (mindestens {MIN_TOKEN_LENGTH} Zeichen)."
    return None


async def discover_cast() -> list[str]:
    """Names of the Google Cast devices in the network (the search takes a few seconds)."""
    return await asyncio.to_thread(PyChromecastBackend().discover, 5.0)


def host_level_from(reader: SystemInfoReader) -> Callable[[], str]:
    """Severity of the host (temperature, memory, power) for the LED matrix."""
    from octapihub.web.presenter import build_system_items

    def level() -> str:
        items = build_system_items(reader.read(), UTC)
        order = {"error": 3, "warn": 2, "ok": 1, "off": 0}
        worst = max(
            (item.level for item in items), key=lambda lvl: order.get(lvl, 0), default="off"
        )
        return worst

    return level


def build_parser(environ: Mapping[str, str] | None = None) -> argparse.ArgumentParser:
    """Command-line parser; host and port may also come from OCTAPIHUB_HOST / OCTAPIHUB_PORT."""
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="octapihub", description=__doc__)
    parser.add_argument("--config", default="octapihub.toml", help="Pfad zur TOML-Konfiguration")
    parser.add_argument(
        "--host",
        default=env.get("OCTAPIHUB_HOST") or "127.0.0.1",
        help="Adresse, an die gebunden wird (Standard: nur dieses Gerät)",
    )
    parser.add_argument("--port", type=_port, default=_port(env.get("OCTAPIHUB_PORT") or "8080"))
    parser.add_argument("--secrets", default="secrets.env", help="Datei mit Zugangsdaten")
    parser.add_argument("--data-dir", default="data", help="Ordner für Zustandsdatenbanken")
    parser.add_argument(
        "--log-dir",
        default=None,
        help='Ordner für die Logdatei (Standard: <data-dir>/logs; "-" schaltet die Datei aus)',
    )
    parser.add_argument(
        "--log-level",
        default=env.get("OCTAPIHUB_LOG_LEVEL") or "INFO",
        type=_log_level,
        help="Ausführlichkeit: DEBUG, INFO, WARNING oder ERROR (Standard: INFO)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Konfiguration und Zugangsdatei prüfen und beenden (0 = in Ordnung)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Simuliertes Sense HAT mit Beispieldaten (Token: demo), ohne Hardware",
    )
    return parser


def run_check(config_path: str, secrets_path: str) -> int:
    """Check the setup without starting anything; returns 0 (fine), 1 (warnings) or 2 (unusable)."""
    out = sys.stdout.write
    try:
        loaded = load_with_fallback(config_path)
    except OctaPiHubError as exc:
        sys.stderr.write(f"{exc.error_code}: {exc.user_message}\n")
        return 2
    config = loaded.config
    status = 0
    if loaded.fallback_from is not None:
        out(f"WARNUNG: {describe_fallback(loaded)}\n")
        status = 1
    out(
        f"Einstellungen: in Ordnung (Version {config.schema_version}, "
        f"{len(config.cubes)} Würfel, {len(config.music.targets)} Wiedergabegeräte, "
        f"{len(config.smarthome.actions)} Smart-Home-Aktionen)\n"
    )
    try:
        SecretsFile(secrets_path).read()
        out("Zugangsdaten: in Ordnung\n")
    except SecretsError as exc:
        out(f"WARNUNG: {exc.error_code}: {exc.user_message}\n")
        status = max(status, 1)
    problem = token_problem(os.environ.get(TOKEN_VARIABLE) or None)
    if problem is not None:
        out(f"WARNUNG: CH-WEB-008: {problem}\n")
        status = max(status, 1)
    elif not os.environ.get(TOKEN_VARIABLE):
        out("Hinweis: Kein Zugriffs-Token gesetzt, die Oberfläche ist nur lesend.\n")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Start the interface; returns a process exit code."""
    args = build_parser().parse_args(argv)
    redactor = Redactor()
    log_dir = None if args.log_dir == "-" else (args.log_dir or str(Path(args.data_dir) / "logs"))
    logs = install_logging(redactor, args.log_level, None if args.demo else log_dir)

    install_exception_hooks()
    fallback_warning = ""
    if args.check:
        return run_check(args.config, args.secrets)
    if args.demo:
        from octapihub.web.demo import build_demo_backend, demo_system_reader

        backend = build_demo_backend()
        backend.system = demo_system_reader()
    else:
        try:
            loaded = load_with_fallback(args.config)
        except OctaPiHubError as exc:
            print(f"{exc.error_code}: {exc.user_message}", file=sys.stderr)  # noqa: T201
            return 2
        config = loaded.config
        fallback_warning = describe_fallback(loaded)
        service = build_sense_hat_service(config)
        hue = build_hue_service(config, args.secrets)
        music, media = build_music_service(config, args.port)
        smarthome = build_smarthome_service(config, args.secrets)
        cubes = build_cube_service(config, args.data_dir, plugins=(hue, music, smarthome))
        system = SystemInfoReader(args.data_dir)
        matrix = build_matrix_service(config, service, cubes, hue, host_level_from(system), music)
        backend = WebBackend(
            SettingsManager(
                args.config,
                config,
                service,
                hue,
                cubes,
                matrix,
                music,
                smarthome,
                SecretsFile(args.secrets),
            ),
            service,
            os.environ.get(TOKEN_VARIABLE) or None,
            cubes,
            hue,
            system,
            matrix=matrix,
            music=music,
            smarthome=smarthome,
            media=media,
            scanner=BleakDeviceScanner(),
            discover_cast=discover_cast,
            list_audio_devices=list_audio_devices,
        )
    if not args.demo and fallback_warning:
        backend.startup_warnings.append(fallback_warning)
    problem = token_problem(backend.token) if not args.demo else None
    if problem is not None:
        print(f"CH-WEB-008: {problem}", file=sys.stderr)  # noqa: T201
        return 2
    backend.logs = logs
    backend.log_dir = None if args.demo else log_dir
    if backend.system is None:
        backend.system = SystemInfoReader(args.data_dir)
    redactor.add_secret(backend.token)
    if not args.demo:
        register_secrets(redactor, args.secrets)
    uvicorn.run(
        create_app(backend, manage_service=True),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
