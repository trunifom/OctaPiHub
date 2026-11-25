"""Fallbacks for a damaged setup: use the newest valid backup of the configuration, and log every
exception that would otherwise vanish (threads, asyncio tasks, the main program)."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from octapihub.core.config import AppConfig, load_config
from octapihub.core.errors import OctaPiHubError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """A configuration and, if the file itself was unusable, which backup replaced it."""

    config: AppConfig
    path: Path
    fallback_from: Path | None = None
    reason: str = ""


def backups_of(path: Path) -> list[Path]:
    """Backups ``<name>.bak-<time>`` next to ``path``, newest first."""
    return sorted(path.parent.glob(f"{path.name}.bak-*"), reverse=True)


def load_with_fallback(path: str | Path) -> LoadedConfig:
    """Load the configuration; if it is unusable use the newest valid backup instead.

    The broken file is left untouched (it may be fixable by hand). Saving in the interface later
    replaces it with a good one.

    Raises:
        OctaPiHubError: The file and every backup are unusable (the first error is raised).
    """
    target = Path(path)
    try:
        return LoadedConfig(load_config(target), target)
    except OctaPiHubError as first:
        for backup in backups_of(target):
            try:
                config = load_config(backup)
            except OctaPiHubError:
                logger.warning("Backup %s is not usable either", backup.name)
                continue
            logger.error(
                "Configuration %s is unusable (%s); using backup %s",
                target.name,
                first.error_code,
                backup.name,
            )
            return LoadedConfig(config, backup, target, first.user_message)
        raise


def describe_fallback(loaded: LoadedConfig) -> str:
    """The warning shown in the interface, or an empty text if no fallback was needed."""
    if loaded.fallback_from is None:
        return ""
    return (
        f"Die Einstellungsdatei „{loaded.fallback_from.name}“ ist unbrauchbar "
        f"({loaded.reason}). Es wird die Sicherung „{loaded.path.name}“ verwendet. "
        "Prüfen Sie die Einstellungen und speichern Sie sie einmal neu."
    )


def install_exception_hooks() -> None:
    """Log unhandled exceptions of threads and the main program instead of losing them."""

    def main_hook(
        kind: type[BaseException], value: BaseException, trace: TracebackType | None
    ) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, trace)
            return
        logger.critical("Unhandled exception %s", kind.__name__)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread else "?"
        logger.error("Thread %s stopped with %s", name, args.exc_type.__name__)

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def install_loop_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Log exceptions of tasks nobody awaited."""

    def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        error = context.get("exception")
        logger.error(
            "Background task failed: %s (%s)",
            context.get("message", ""),
            type(error).__name__ if error else "no exception",
        )

    loop.set_exception_handler(handler)
