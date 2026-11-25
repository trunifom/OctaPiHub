"""Validated, atomic, and live-applied settings changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path

from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import AppConfig, save_config, with_sense_hat_table

logger = logging.getLogger(__name__)


class SettingsManager:
    """Own the current configuration and apply Sense HAT changes safely.

    A change is validated first, then written atomically, and only then applied to the
    running service. Invalid input never touches the file or the running service.
    Changes are serialized so two simultaneous saves cannot interleave.
    """

    def __init__(self, path: str | Path, config: AppConfig, service: SenseHatService) -> None:
        self._path = Path(path)
        self._config = config
        self._service = service
        self._lock = asyncio.Lock()

    @property
    def config(self) -> AppConfig:
        """The configuration currently saved and applied."""
        return self._config

    async def apply_sense_hat(self, table: Mapping[str, object]) -> AppConfig:
        """Validate, save, and restart the Sense HAT service with ``table``.

        Raises:
            ValidationError: If the table is invalid (nothing is saved).
            ConfigurationError: If the file cannot be written (service is unchanged).
        """
        async with self._lock:
            candidate = with_sense_hat_table(self._config, table)
            await asyncio.to_thread(save_config, self._path, candidate)
            self._config = candidate
            await self._service.restart(candidate.sense_hat)
            logger.info("Sense HAT settings saved and applied")
            return candidate
