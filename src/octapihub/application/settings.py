"""Validated, atomic, and live-applied settings changes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import HueService
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import (
    AppConfig,
    save_config,
    with_hue_table,
    with_mappings_table,
    with_sense_hat_table,
)

logger = logging.getLogger(__name__)


class SettingsManager:
    """Own the current configuration and apply Sense HAT changes safely.

    A change is validated first, then written atomically, and only then applied to the
    running service. Invalid input never touches the file or the running service.
    Changes are serialized so two simultaneous saves cannot interleave.
    """

    def __init__(
        self,
        path: str | Path,
        config: AppConfig,
        service: SenseHatService,
        hue: HueService | None = None,
        cubes: CubeService | None = None,
    ) -> None:
        self._path = Path(path)
        self._config = config
        self._service = service
        self._hue = hue
        self._cubes = cubes
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

    async def apply_hue(self, table: Mapping[str, object]) -> AppConfig:
        """Validate, save, and apply new Hue connection settings.

        Raises:
            ValidationError: If the table is invalid (nothing is saved).
            ConfigurationError: If the file cannot be written (service is unchanged).
        """
        async with self._lock:
            candidate = with_hue_table(self._config, table)
            await asyncio.to_thread(save_config, self._path, candidate)
            self._config = candidate
            if self._hue is not None:
                await self._hue.reconfigure(candidate.hue)
            logger.info("Hue settings saved and applied")
            return candidate

    async def apply_mappings(self, raw_mappings: Sequence[object]) -> AppConfig:
        """Validate, save, and switch the running rules to new cube mappings.

        The file is written first; if the running rules then reject the change (for
        example a plugin is missing) the previous file content is restored, so file and
        running state never disagree.

        Raises:
            ValidationError: If a mapping is invalid (nothing is saved).
            ConfigurationError: If the file cannot be written (nothing changes).
            PluginError: A needed plugin is missing (file and rules stay unchanged).
        """
        async with self._lock:
            candidate = with_mappings_table(self._config, raw_mappings)
            previous = self._config
            if self._cubes is not None:
                # Validate against the running pipeline before touching the file.
                await self._cubes.update_config(candidate)
            try:
                await asyncio.to_thread(save_config, self._path, candidate)
            except Exception:
                if self._cubes is not None:
                    await self._cubes.update_config(previous)
                raise
            self._config = candidate
            logger.info("Cube mappings saved and applied")
            return candidate
