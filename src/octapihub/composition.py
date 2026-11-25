"""Composition root: builds production services from validated configuration."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from octapihub.application.ble_runtime import BleRuntime
from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import HueService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import (
    ActionPlugin,
    PluginRegistry,
    load_installed_plugins,
)
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import AppConfig, HueConfig
from octapihub.infrastructure.ble.bleak_transport import BleakTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.hue import HueBridgeClient, HueBridgeSettings, pair_bridge
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.sense_hat import create_sense_hat_port
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory


def build_sense_hat_service(config: AppConfig) -> SenseHatService:
    """Create the Sense HAT service with the real adapter and SQLite history."""
    return SenseHatService(
        config.sense_hat,
        lambda settings: create_sense_hat_port(settings.hardware_version),
        lambda settings: SQLiteSenseHatHistory(settings.history.database_path),
    )


def build_hue_service(config: AppConfig, secrets_path: str | Path = "secrets.env") -> HueService:
    """Create the Hue service with the real bridge client and ``secrets.env`` storage."""

    def make_gateway(settings: HueConfig, key: str) -> HueBridgeClient:
        return HueBridgeClient(
            HueBridgeSettings(
                host=settings.host,
                application_key=key,
                ca_file=Path(settings.ca_file) if settings.ca_file else None,
                port=settings.port,
                tls_server_name=settings.tls_server_name or None,
            )
        )

    async def pair(settings: HueConfig) -> str:
        return await pair_bridge(
            settings.host,
            port=settings.port,
            ca_file=Path(settings.ca_file) if settings.ca_file else None,
            tls_server_name=settings.tls_server_name or None,
        )

    return HueService(config.hue, SecretsFile(secrets_path), make_gateway, pair)


def build_cube_service(
    config: AppConfig,
    state_dir: str | Path = "data",
    plugins: Iterable[ActionPlugin] = (),
) -> CubeService:
    """Create the cube pipeline with real Bluetooth, the SQLite outbox, and installed plugins.

    Dependencies are created inside the factories so that a missing plugin or an unusable
    database is reported by the service instead of failing hub start-up.
    """

    def make_runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            BleakTransportFactory(),
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
        )

    extra_plugins = tuple(plugins)

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        registry = PluginRegistry()
        load_installed_plugins(registry)
        registry.register_many(extra_plugins)
        return OutboxDispatcher(
            RuleEngine(settings), SQLiteOutbox(directory / "state.sqlite3"), registry
        )

    return CubeService(config, make_runtime, make_dispatcher)
