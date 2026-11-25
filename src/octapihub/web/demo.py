"""Hardware-free demo backend: a simulated Sense HAT with seeded history.

Only for trying out the interface on a PC. Values are simulated and clearly not
measurements; the demo never runs in the production entry point.
"""

from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime
from octapihub.application.cube_service import CubeService
from octapihub.application.hue_lights import HueGroup
from octapihub.application.hue_service import HueGateway, HueService, SceneInfo
from octapihub.application.music.service import MusicService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.sense_hat_worker import SenseHatSample, correct_temperature
from octapihub.application.settings import SettingsManager
from octapihub.composition import build_matrix_service
from octapihub.core.config import (
    AppConfig,
    HueConfig,
    SenseHatConfig,
    parse_config,
    save_config,
)
from octapihub.core.errors import HueLinkButtonError
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    BATTERY_LEVEL_CHARACTERISTIC_UUID,
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_battery_level,
    decode_orientation,
    supports_battery_read,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.music.fake import FakePlayerFactory, FakeResolver
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web.app import WebBackend

DEMO_TOKEN = "demo"  # noqa: S105 - public demo credential, never used in production
RAW_HEAT_C = 3.2  # simulated CPU warming that the configured offset removes
_DAY = 86400.0


def _temperature(epoch: float) -> float:
    return (
        22.0
        + 2.5 * math.sin((epoch % _DAY) / _DAY * 2 * math.pi - 2.0)
        + 0.05 * math.sin(epoch / 37)
    )


def _humidity(epoch: float) -> float:
    return (
        46.0
        - 6.0 * math.sin((epoch % _DAY) / _DAY * 2 * math.pi - 2.0)
        + 0.4 * math.sin(epoch / 53)
    )


def _pressure(epoch: float) -> float:
    return 1015.0 + 6.0 * math.sin(epoch / (3 * _DAY) * 2 * math.pi) + 0.3 * math.sin(epoch / 91)


class DemoSenseHat(FakeSenseHat):
    """Fake board whose environment values drift like a real room."""

    def get_temperature(self) -> float:
        super().get_temperature()
        return _temperature(time.time()) + RAW_HEAT_C

    def get_humidity(self) -> float:
        super().get_humidity()
        return _humidity(time.time())

    def get_pressure(self) -> float:
        super().get_pressure()
        return _pressure(time.time())


def _seed_history(store: SQLiteSenseHatHistory, config: SenseHatConfig, days: int = 30) -> None:
    now = datetime.now(UTC)
    samples: list[SenseHatSample] = []
    step = timedelta(minutes=10)
    moment = now - timedelta(days=days)
    while moment < now - timedelta(minutes=1):
        epoch = moment.timestamp()
        raw = _temperature(epoch) + RAW_HEAT_C
        samples.append(
            SenseHatSample(
                "temperature",
                SenseHatReading(SenseHatMetric.TEMPERATURE, raw, "°C"),
                moment,
                correct_temperature(
                    raw, config.temperature_correction_factor, config.temperature_offset_c
                ),
            )
        )
        samples.append(
            SenseHatSample(
                "humidity",
                SenseHatReading(SenseHatMetric.HUMIDITY, _humidity(epoch), "%RH"),
                moment,
            )
        )
        samples.append(
            SenseHatSample(
                "pressure",
                SenseHatReading(SenseHatMetric.PRESSURE, _pressure(epoch), "mbar"),
                moment,
            )
        )
        moment += step
    store.append_many(samples)


def demo_system_reader() -> SystemInfoReader:
    """Simulated host values in a temporary folder; clearly not measurements."""
    directory = Path(tempfile.mkdtemp(prefix="octapihub-demo-proc-"))
    (directory / "uptime").write_text("273600.5 1000.0\n", encoding="utf-8")
    (directory / "loadavg").write_text("0.21 0.30 0.28 1/210 4242\n", encoding="utf-8")
    (directory / "meminfo").write_text(
        "MemTotal: 948000 kB\nMemAvailable: 512000 kB\n", encoding="utf-8"
    )
    temperature = directory / "temp"
    temperature.write_text("52400\n", encoding="utf-8")
    return SystemInfoReader(
        directory,
        proc=directory,
        thermal_file=temperature,
        runner=lambda _command: "throttled=0x50000\n",
    )


def build_demo_backend(token: str | None = DEMO_TOKEN) -> WebBackend:
    """Create a self-contained backend in a temporary directory."""
    directory = Path(tempfile.mkdtemp(prefix="octapihub-demo-"))
    base: AppConfig = parse_config(
        {
            "schema_version": 6,
            "hue": {"enabled": True, "host": "192.168.1.20"},
            "bluetooth": {"adapter": "hci0"},
            "debounce_ms": 300,
            "mappings": [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [
                        {
                            "kind": "music",
                            "action": "play",
                            "parameters": {"playlist": "morgens"},
                        }
                    ],
                },
                {
                    "cube": "Musik",
                    "face": 2,
                    "actions": [{"kind": "music", "action": "toggle", "parameters": {}}],
                },
                {
                    "cube": "Musik",
                    "face": 0,
                    "actions": [{"kind": "music", "action": "stop.all", "parameters": {}}],
                },
                {
                    "cube": "Licht",
                    "face": 3,
                    "actions": [
                        {
                            "kind": "hue",
                            "action": "scene.activate",
                            "parameters": {"scene_id": "wohnzimmer-abend"},
                        },
                        {"kind": "hue", "action": "light.brightness", "parameters": {"level": 0.6}},
                    ],
                },
                {
                    "cube": "Licht",
                    "face": 5,
                    "actions": [
                        {
                            "kind": "hue",
                            "action": "scene.activate",
                            "parameters": {"scene_id": "aus"},
                        }
                    ],
                },
            ],
            "music": {
                "enabled": True,
                "library_dir": "/home/pi/Music",
                "targets": [
                    {
                        "id": "wohnzimmer",
                        "name": "Wohnzimmer",
                        "kind": "cast",
                        "device": "Wohnzimmer",
                        "volume": 35,
                    },
                    {"id": "pi", "name": "Raspberry Pi", "kind": "local", "volume": 60},
                ],
                "playlists": [
                    {
                        "id": "morgens",
                        "name": "Morgenmusik",
                        "source": "Morgens",
                        "target": "wohnzimmer",
                        "shuffle": True,
                        "repeat": "all",
                    },
                    {
                        "id": "radio",
                        "name": "Radio Beispiel",
                        "source": "https://radio.example.org/live.mp3",
                        "target": "pi",
                    },
                ],
            },
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
                {"name": "Extra", "address": "AA:BB:CC:DD:EE:03", "role": "general"},
            ],
            "sense_hat": {
                "temperature_offset_c": -RAW_HEAT_C,
                "history": {"database_path": str(directory / "history.sqlite3")},
                "sensors": {
                    "temperature": {"interval_seconds": 2},
                    "humidity": {"interval_seconds": 2},
                    "pressure": {"interval_seconds": 5},
                    "compass": {
                        "enabled": True,
                        "interval_seconds": 2,
                        "presentation": "value",
                    },
                },
            },
        }
    )
    config_path = directory / "octapihub.toml"
    save_config(config_path, base)
    _seed_history(SQLiteSenseHatHistory(base.sense_hat.history.database_path), base.sense_hat)
    service = SenseHatService(
        base.sense_hat,
        lambda _settings: SenseHatAdapter(DemoSenseHat()),
        lambda settings: SQLiteSenseHatHistory(settings.history.database_path),
    )
    hue = _demo_hue(base.hue)
    music = MusicService(base.music, FakePlayerFactory(), FakeResolver())
    cubes = _demo_cubes(base, directory, hue, music)
    matrix = build_matrix_service(base, service, cubes, hue, lambda: "ok", music)
    settings = SettingsManager(config_path, base, service, hue, cubes, matrix, music)

    async def discover() -> list[str]:
        return ["Küche (Nest Mini)", "Schlafzimmer (Nest Hub)"]

    async def audio_devices() -> list[tuple[str, str]]:
        return [("auto", "Automatisch"), ("alsa/default", "Standard (Kopfhörerbuchse)")]

    return WebBackend(
        settings,
        service,
        token,
        cubes,
        hue,
        matrix=matrix,
        music=music,
        discover_cast=discover,
        list_audio_devices=audio_devices,
    )


class _DemoHue:
    """Accepts every action without contacting any bridge."""

    kind = "hue"

    async def execute(self, action: str, parameters: object) -> None:
        return None


class _DemoSecrets:
    """In-memory key storage; nothing is written to disk in the demo."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete(self, key: str) -> bool:
        return self._values.pop(key, None) is not None


@dataclass(frozen=True)
class _DemoScene:
    id: str
    name: str
    group_id: str = ""


class _DemoGateway:
    """A bridge that always works and lists a few scenes."""

    async def list_scenes(self) -> tuple[SceneInfo, ...]:
        return (
            _DemoScene("wohnzimmer-abend", "Wohnzimmer – Abend", "room-wz"),
            _DemoScene("wohnzimmer-hell", "Wohnzimmer – Hell", "room-wz"),
            _DemoScene("aus", "Alles aus"),
        )

    async def recall_scene(self, scene_id: str) -> None:
        return None

    async def list_groups(self) -> tuple[HueGroup, ...]:
        return (
            HueGroup("g-alle", "Alle Lampen", "home", "home-1"),
            HueGroup("g-wohnzimmer", "Wohnzimmer", "room", "room-wz"),
            HueGroup("g-kueche", "Küche", "room", "room-ku"),
        )

    async def set_group(self, group_id: str, body: dict[str, object]) -> None:
        return None

    async def group_is_on(self, group_id: str) -> bool | None:
        return True


def _demo_hue(config: HueConfig) -> HueService:
    """Hue with a fake bridge; the first pairing attempt shows the link-button message."""
    attempts = 0

    async def pair(_settings: HueConfig) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise HueLinkButtonError("link button not pressed")
        return "demo-application-key"

    def make_gateway(_settings: HueConfig, _key: str) -> HueGateway:
        return _DemoGateway()

    return HueService(config, _DemoSecrets(), make_gateway, pair)


def _demo_cubes(
    config: AppConfig,
    directory: Path,
    hue: HueService | None = None,
    music: MusicService | None = None,
) -> CubeService:
    """Two simulated cubes that connect instantly; no Bluetooth is used."""
    profile = DeviceProfile(
        (
            CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),
            CharacteristicInfo(BATTERY_LEVEL_CHARACTERISTIC_UUID, frozenset({"read"})),
        )
    )
    factory = FakeTransportFactory(profile)
    # Simulated battery levels so the warnings and the battery view can be tried out.
    for address, level in zip((cube.address for cube in config.cubes), (87, 18, 6), strict=False):
        factory.values[address] = {BATTERY_LEVEL_CHARACTERISTIC_UUID: bytes([level])}

    def make_runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            factory,
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
            battery_characteristic_uuid=BATTERY_LEVEL_CHARACTERISTIC_UUID,
            decode_battery=decode_battery_level,
            supports_battery=supports_battery_read,
        )

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        plugins = PluginRegistry()
        plugins.register(hue if hue is not None else _DemoHue())
        if music is not None:
            plugins.register(music)
        return OutboxDispatcher(
            RuleEngine(settings), SQLiteOutbox(directory / "state.sqlite3"), plugins
        )

    return CubeService(config, make_runtime, make_dispatcher)
