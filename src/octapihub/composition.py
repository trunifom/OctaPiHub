"""Composition root: builds production services from validated configuration."""

from __future__ import annotations

from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import AppConfig
from octapihub.infrastructure.sense_hat import create_sense_hat_port
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory


def build_sense_hat_service(config: AppConfig) -> SenseHatService:
    """Create the Sense HAT service with the real adapter and SQLite history."""
    sense_hat = config.sense_hat
    return SenseHatService(
        sense_hat,
        lambda: create_sense_hat_port(sense_hat.hardware_version),
        lambda: SQLiteSenseHatHistory(sense_hat.history.database_path),
    )
