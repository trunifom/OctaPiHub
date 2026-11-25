"""Hardware-free demo backend: a simulated Sense HAT with seeded history.

Only for trying out the interface on a PC. Values are simulated and clearly not
measurements; the demo never runs in the production entry point.
"""

from __future__ import annotations

import math
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime
from octapihub.application.cube_service import CubeService
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat import SenseHatMetric, SenseHatReading
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.sense_hat_worker import SenseHatSample, correct_temperature
from octapihub.application.settings import SettingsManager
from octapihub.core.config import AppConfig, SenseHatConfig, parse_config, save_config
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
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


def build_demo_backend(token: str | None = DEMO_TOKEN) -> WebBackend:
    """Create a self-contained backend in a temporary directory."""
    directory = Path(tempfile.mkdtemp(prefix="octapihub-demo-"))
    base: AppConfig = parse_config(
        {
            "schema_version": 3,
            "bluetooth": {"adapter": "hci0"},
            "debounce_ms": 300,
            "mappings": [
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
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
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
    cubes = _demo_cubes(base, directory)
    return WebBackend(SettingsManager(config_path, base, service), service, token, cubes)


class _DemoHue:
    """Accepts every action without contacting any bridge."""

    kind = "hue"

    async def execute(self, action: str, parameters: object) -> None:
        return None


def _demo_cubes(config: AppConfig, directory: Path) -> CubeService:
    """Two simulated cubes that connect instantly; no Bluetooth is used."""
    profile = DeviceProfile(
        (CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),)
    )

    def make_runtime(settings: AppConfig) -> BleRuntime:
        return BleRuntime(
            settings,
            FakeTransportFactory(profile),
            orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
            decode_orientation=decode_orientation,
            supports_orientation=supports_orientation_notifications,
        )

    def make_dispatcher(settings: AppConfig) -> OutboxDispatcher:
        plugins = PluginRegistry()
        plugins.register(_DemoHue())
        return OutboxDispatcher(
            RuleEngine(settings), SQLiteOutbox(directory / "state.sqlite3"), plugins
        )

    return CubeService(config, make_runtime, make_dispatcher)
