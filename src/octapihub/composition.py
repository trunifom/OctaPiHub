"""Composition root: builds production services from validated configuration."""

from __future__ import annotations

from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import AppConfig
from octapihub.infrastructure.sense_hat import create_sense_hat_port
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory


def build_sense_hat_service(config: AppConfig) -> SenseHatService:
    """Create the Sense HAT service with the real adapter and SQLite history."""
    return SenseHatService(
        config.sense_hat,
        lambda settings: create_sense_hat_port(settings.hardware_version),
        lambda settings: SQLiteSenseHatHistory(settings.history.database_path),
    )
