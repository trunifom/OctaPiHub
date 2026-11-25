"""Run the web interface: ``python -m octapihub.web --config octapihub.toml``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC

import uvicorn

from octapihub.application.settings import SettingsManager
from octapihub.composition import (
    build_cube_service,
    build_hue_service,
    build_matrix_service,
    build_music_service,
    build_sense_hat_service,
)
from octapihub.core.config import load_config
from octapihub.core.errors import OctaPiHubError, SecretsError
from octapihub.core.logging_support import LogRing, RedactingFilter, Redactor
from octapihub.infrastructure.music.cast_player import PyChromecastBackend
from octapihub.infrastructure.music.mpv_player import list_audio_devices
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web.app import WebBackend, create_app

TOKEN_VARIABLE = "OCTAPIHUB_WEB_TOKEN"  # noqa: S105 - name of the variable, not a secret


def install_logging(redactor: Redactor) -> LogRing:
    """Send redacted logs to the console and keep recent lines for the System page."""
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console.addFilter(RedactingFilter(redactor))
    ring = LogRing(redactor)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console)
    root_logger.addHandler(ring)
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
        "--demo",
        action="store_true",
        help="Simuliertes Sense HAT mit Beispieldaten (Token: demo), ohne Hardware",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start the interface; returns a process exit code."""
    args = build_parser().parse_args(argv)
    redactor = Redactor()
    logs = install_logging(redactor)

    if args.demo:
        from octapihub.web.demo import build_demo_backend, demo_system_reader

        backend = build_demo_backend()
        backend.system = demo_system_reader()
    else:
        try:
            config = load_config(args.config)
        except OctaPiHubError as exc:
            print(f"{exc.error_code}: {exc.user_message}", file=sys.stderr)  # noqa: T201
            return 2
        service = build_sense_hat_service(config)
        hue = build_hue_service(config, args.secrets)
        music, media = build_music_service(config, args.port)
        cubes = build_cube_service(config, args.data_dir, plugins=(hue, music))
        system = SystemInfoReader(args.data_dir)
        matrix = build_matrix_service(config, service, cubes, hue, host_level_from(system))
        backend = WebBackend(
            SettingsManager(args.config, config, service, hue, cubes, matrix, music),
            service,
            os.environ.get(TOKEN_VARIABLE) or None,
            cubes,
            hue,
            system,
            matrix=matrix,
            music=music,
            media=media,
            discover_cast=discover_cast,
            list_audio_devices=list_audio_devices,
        )
    problem = token_problem(backend.token) if not args.demo else None
    if problem is not None:
        print(f"CH-WEB-008: {problem}", file=sys.stderr)  # noqa: T201
        return 2
    backend.logs = logs
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
