"""Validated, atomic, and live-applied settings changes."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from octapihub.application.backup import SECTION_ORDER, Bundle, prepare_section
from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import HueService
from octapihub.application.matrix.service import MatrixDisplayService
from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import (
    AppConfig,
    save_config,
    with_hue_table,
    with_mappings_table,
    with_music_table,
    with_sense_hat_table,
)
from octapihub.core.errors import ConfigurationError, ValidationError

logger = logging.getLogger(__name__)

KEEP_BACKUPS = 5


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
        matrix: MatrixDisplayService | None = None,
        music: MusicService | None = None,
    ) -> None:
        self._path = Path(path)
        self._config = config
        self._service = service
        self._hue = hue
        self._cubes = cubes
        self._matrix = matrix
        self._music = music
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
            await self._live_sense_hat(candidate)
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
            await self._live_hue(candidate)
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

    async def apply_music(self, table: Mapping[str, object]) -> AppConfig:
        """Validate, save, and apply new music settings (targets, playlists, folder).

        Raises:
            ValidationError: If the table is invalid (nothing is saved).
            ConfigurationError: If the file cannot be written (service is unchanged).
        """
        async with self._lock:
            candidate = with_music_table(self._config, table)
            await asyncio.to_thread(save_config, self._path, candidate)
            self._config = candidate
            await self._live_music(candidate)
            logger.info("Music settings saved and applied")
            return candidate

    # ----- live application (shared by single saves and imports) -----

    async def _live_sense_hat(self, candidate: AppConfig) -> None:
        if self._matrix is not None:
            await self._matrix.stop()  # it uses the port that is about to be replaced
        await self._service.restart(candidate.sense_hat)
        if self._matrix is not None:
            await self._matrix.restart(
                candidate.sense_hat.matrix,
                joystick_enabled=candidate.sense_hat.joystick_enabled,
            )

    async def _live_hue(self, candidate: AppConfig) -> None:
        if self._hue is not None:
            await self._hue.reconfigure(candidate.hue)

    async def _live_music(self, candidate: AppConfig) -> None:
        if self._music is not None:
            await self._music.reconfigure(candidate.music)

    # ----- import -----

    def _backup_file(self) -> Path | None:
        """Copy the current file to ``<name>.bak-<time>`` and keep the newest few copies."""
        if not self._path.exists():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self._path.with_name(f"{self._path.name}.bak-{stamp}")
        try:
            shutil.copy2(self._path, target)
            backups = sorted(self._path.parent.glob(f"{self._path.name}.bak-*"))
            for old in backups[:-KEEP_BACKUPS]:
                old.unlink(missing_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                "Could not back up the configuration file",
                user_message="Die bisherigen Einstellungen konnten nicht gesichert werden. "
                "Es wurde nichts importiert.",
                details={"reason": exc.strerror or type(exc).__name__},
            ) from exc
        return target

    async def apply_bundle(self, bundle: Bundle, sections: Sequence[str]) -> list[str]:
        """Import the chosen ``sections`` of an export; all or nothing.

        Every section is validated against the current configuration first. The current file
        is copied to a backup, the new file is written atomically, and the running services
        are switched over. Returns notes for the user.

        Raises:
            ValidationError: A section is missing or does not fit (nothing changes).
            ConfigurationError: The backup or the file could not be written (nothing changes).
        """
        async with self._lock:
            wanted = [key for key in SECTION_ORDER if key in sections]
            missing = [key for key in wanted if key not in bundle.sections]
            if not wanted or missing or set(sections) - set(SECTION_ORDER):
                raise ValidationError(
                    "Import selection does not match the file",
                    user_message="Die Auswahl passt nicht zur Export-Datei.",
                )
            candidate = self._config
            notes: list[str] = []
            for key in wanted:
                candidate, extra = prepare_section(candidate, key, bundle.sections[key])
                notes.extend(extra)
            previous = self._config
            await asyncio.to_thread(self._backup_file)
            if "mappings" in wanted and self._cubes is not None:
                await self._cubes.update_config(candidate)
            try:
                await asyncio.to_thread(save_config, self._path, candidate)
            except Exception:
                if "mappings" in wanted and self._cubes is not None:
                    await self._cubes.update_config(previous)
                raise
            self._config = candidate
            failed: list[str] = []
            for key, live in (
                ("sense_hat", self._live_sense_hat),
                ("hue", self._live_hue),
                ("music", self._live_music),
                ("playlists", self._live_music),
            ):
                if key in wanted:
                    try:
                        await live(candidate)
                    except Exception:
                        logger.exception("Imported %s settings could not be applied live", key)
                        failed.append(key)
            if failed:
                notes.append(
                    "Gespeichert, aber nicht alles ließ sich sofort anwenden. "
                    "Bitte starten Sie den Dienst neu."
                )
            logger.info("Imported settings: %s", ", ".join(wanted))
            return notes
