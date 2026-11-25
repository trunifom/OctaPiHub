"""Composition root: builds production services from validated configuration."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from octapihub.application.ble_runtime import BleRuntime
from octapihub.application.cube_service import CubeActivity, CubeService
from octapihub.application.hue_service import HueService
from octapihub.application.matrix.builder import DisplayStateBuilder
from octapihub.application.matrix.service import MatrixDisplayService
from octapihub.application.music.service import MusicService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import (
    ActionPlugin,
    PluginRegistry,
    load_installed_plugins,
)
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.core.config import JOYSTICK_SOURCE, AppConfig, HueConfig
from octapihub.infrastructure.ble.bleak_transport import BleakTransportFactory
from octapihub.infrastructure.ble.protocol import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_battery_level,
    decode_orientation,
    supports_battery_read,
    supports_orientation_notifications,
)
from octapihub.infrastructure.hue import HueBridgeClient, HueBridgeSettings, pair_bridge
from octapihub.infrastructure.music.library import LibraryResolver
from octapihub.infrastructure.music.media import MediaRegistry, MediaUrls
from octapihub.infrastructure.music.players import DefaultPlayerFactory
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.sense_hat import create_sense_hat_port
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory

MUSIC_ACTION_TTL_SECONDS = 60.0  # a pressed play button must not start music minutes later


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


def build_music_service(config: AppConfig, port: int = 8080) -> tuple[MusicService, MediaRegistry]:
    """Create the music service with real players; ``port`` is where this program serves files.

    The registry is returned as well because the web layer serves ``/media/<token>`` from it.
    """
    service: MusicService | None = None

    def library_dir() -> str:
        return service.config.library_dir if service is not None else config.music.library_dir

    def media_host() -> str:
        return service.config.media_host if service is not None else config.music.media_host

    registry = MediaRegistry(library_dir)
    urls = MediaUrls(registry, port=port, configured_host=media_host)
    service = MusicService(config.music, DefaultPlayerFactory(urls), LibraryResolver())
    return service, registry


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
            battery_characteristic_uuid=BATTERY_LEVEL_CHARACTERISTIC_UUID,
            decode_battery=decode_battery_level,
            supports_battery=supports_battery_read,
        )

    extra_plugins = tuple(plugins)

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        registry = PluginRegistry()
        load_installed_plugins(registry)
        registry.register_many(extra_plugins)
        return OutboxDispatcher(
            RuleEngine(settings),
            SQLiteOutbox(directory / "state.sqlite3"),
            registry,
            ttl_by_kind={"music": MUSIC_ACTION_TTL_SECONDS},
        )

    return CubeService(config, make_runtime, make_dispatcher)


def build_matrix_service(
    config: AppConfig,
    sense: SenseHatService,
    cubes: CubeService | None = None,
    hue: HueService | None = None,
    host_level: Callable[[], str] | None = None,
    music: MusicService | None = None,
) -> MatrixDisplayService:
    """Create the LED matrix display and connect cube feedback to it.

    The display shares the Sense HAT port of ``sense`` (one owner for the I2C device) and
    reads its data through a :class:`DisplayStateBuilder`. Cube activity (a face was
    reached, an action succeeded or failed) shows as a short overlay.
    """

    def joystick_faces() -> tuple[int, ...]:
        current = cubes.config if cubes is not None else config
        return tuple(m.face for m in current.mappings if m.cube == JOYSTICK_SOURCE)

    builder = DisplayStateBuilder(
        lambda: config.cubes, sense, cubes, hue, host_level, joystick_faces, music
    )
    service = MatrixDisplayService(
        config.sense_hat.matrix,
        lambda: sense.port,
        builder.build,
        joystick_enabled=config.sense_hat.joystick_enabled,
        joystick_trigger=(
            (lambda face: cubes.trigger(JOYSTICK_SOURCE, face) is not None)
            if cubes is not None
            else None
        ),
    )
    if cubes is not None:

        def on_activity(activity: CubeActivity) -> None:
            if activity.cube_name == JOYSTICK_SOURCE and activity.kind == "face":
                return  # the display already showed the joystick feedback itself
            if activity.kind == "face":
                service.notify_face(activity.cube_name, activity.face)
            else:
                service.notify_action(activity.cube_name, activity.kind == "action_ok")

        cubes.add_listener(on_activity)
    return service
