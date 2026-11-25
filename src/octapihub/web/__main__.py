"""Run the web interface: ``python -m octapihub.web --config octapihub.toml``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

import uvicorn

from octapihub.application.settings import SettingsManager
from octapihub.composition import build_sense_hat_service
from octapihub.core.config import load_config
from octapihub.core.errors import OctaPiHubError
from octapihub.web.app import WebBackend, create_app

TOKEN_VARIABLE = "OCTAPIHUB_WEB_TOKEN"  # noqa: S105 - name of the variable, not a secret


def main(argv: Sequence[str] | None = None) -> int:
    """Start the interface; returns a process exit code."""
    parser = argparse.ArgumentParser(prog="octapihub.web", description=__doc__)
    parser.add_argument("--config", default="octapihub.toml", help="Pfad zur TOML-Konfiguration")
    parser.add_argument("--host", default="127.0.0.1", help="Adresse (nur im LAN öffnen)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Simuliertes Sense HAT mit Beispieldaten (Token: demo), ohne Hardware",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    if args.demo:
        from octapihub.web.demo import build_demo_backend

        backend = build_demo_backend()
    else:
        try:
            config = load_config(args.config)
        except OctaPiHubError as exc:
            print(f"{exc.error_code}: {exc.user_message}", file=sys.stderr)  # noqa: T201
            return 2
        service = build_sense_hat_service(config)
        backend = WebBackend(
            SettingsManager(args.config, config, service),
            service,
            os.environ.get(TOKEN_VARIABLE) or None,
        )
    uvicorn.run(
        create_app(backend, manage_service=True),
        host=args.host,
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
