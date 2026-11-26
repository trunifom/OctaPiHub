from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import SenseHatConfig, parse_config, save_config
from octapihub.core.logging_support import LogRing, Redactor
from octapihub.core.timezone import TimeSettings
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.secrets import SecretsFile
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.infrastructure.system_info import SystemInfoReader, SystemSnapshot, ThrottleStatus
from octapihub.web.__main__ import install_logging, register_secrets
from octapihub.web.app import WebBackend, create_app
from octapihub.web.presenter import build_system_items

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
SECRET = "my-very-secret-web-token"


def _reader(
    tmp_path: Path,
    *,
    temp: str = "52400\n",
    mem_available: int = 512000,
    throttled: str | Exception = "throttled=0x0\n",
    uptime: str = "273600.5 1.0\n",
) -> SystemInfoReader:
    proc = tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    (proc / "uptime").write_text(uptime, encoding="utf-8")
    (proc / "loadavg").write_text("0.21 0.30 0.28 1/2 3\n", encoding="utf-8")
    (proc / "meminfo").write_text(
        f"MemTotal: 948000 kB\nMemAvailable: {mem_available} kB\n", encoding="utf-8"
    )
    thermal = tmp_path / "temp"
    thermal.write_text(temp, encoding="utf-8")

    def runner(_command: object) -> str:
        if isinstance(throttled, Exception):
            raise throttled
        return throttled

    return SystemInfoReader(
        tmp_path, proc=proc, thermal_file=thermal, runner=runner, clock=lambda: NOW
    )


def _backend(tmp_path: Path, reader: SystemInfoReader | None, ring: LogRing | None) -> WebBackend:
    config = parse_config(
        {
            "schema_version": 4,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
        }
    )
    path = tmp_path / "octapihub.toml"
    save_config(path, config)
    sense = SenseHatService(
        config.sense_hat,
        lambda _c: SenseHatAdapter(FakeSenseHat()),
        lambda c: SQLiteSenseHatHistory(c.history.database_path),
    )
    return WebBackend(
        SettingsManager(path, config, sense),
        sense,
        SECRET,
        system=reader,
        logs=ring,
        clock=lambda: NOW,
        time=TimeSettings("Europe/Zurich"),
    )


def _ring() -> tuple[LogRing, logging.Logger]:
    ring = LogRing(Redactor([SECRET]), 50)
    logger = logging.getLogger(f"octapihub.test-system-{id(ring)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(ring)
    return ring, logger


def _page(backend: WebBackend, path: str = "/system") -> tuple[int, str]:
    with TestClient(create_app(backend)) as client:
        response = client.get(path)
        return response.status_code, response.text


# ---------- presenter thresholds ----------


def _snapshot(**changes: object) -> SystemSnapshot:
    base: dict[str, object] = {
        "taken_at": NOW,
        "uptime_seconds": 90000.0,
        "load_average": (0.1, 0.2, 0.3),
        "memory_total_kb": 1000000,
        "memory_available_kb": 500000,
        "cpu_temperature_c": 50.0,
        "throttle": None,
        "disk_total_bytes": 10_000_000_000,
        "disk_free_bytes": 5_000_000_000,
        "python_version": "3.12.3",
        "platform": "test",
    }
    base.update(changes)
    return SystemSnapshot(**base)  # type: ignore[arg-type]


def _levels(snapshot: SystemSnapshot) -> dict[str, str]:
    return {item.key: item.level for item in build_system_items(snapshot, UTC)}


@pytest.mark.parametrize(
    ("temperature", "level"),
    [(45.0, "ok"), (69.9, "ok"), (70.0, "warn"), (79.9, "warn"), (80.0, "error"), (85.0, "error")],
)
def test_temperature_levels(temperature: float, level: str) -> None:
    assert _levels(_snapshot(cpu_temperature_c=temperature))["temperature"] == level


@pytest.mark.parametrize(
    ("available", "level"),
    [(500000, "ok"), (201000, "ok"), (190000, "warn"), (99000, "error")],
)
def test_memory_levels(available: int, level: str) -> None:
    assert _levels(_snapshot(memory_available_kb=available))["memory"] == level


@pytest.mark.parametrize(("free", "level"), [(5e9, "ok"), (1e9, "warn"), (2e8, "error")])
def test_disk_levels(free: float, level: str) -> None:
    assert _levels(_snapshot(disk_free_bytes=int(free)))["disk"] == level


def _throttle(raw: int) -> ThrottleStatus:
    from octapihub.infrastructure.system_info import parse_throttled

    status = parse_throttled(f"throttled={raw:#x}")
    assert status is not None
    return status


@pytest.mark.parametrize(
    ("raw", "level", "text"),
    [
        (0x0, "ok", "In Ordnung"),
        (0x50000, "warn", "In Ordnung"),
        (0x1, "error", "Unterspannung"),
        (0x4, "warn", "Gedrosselt"),
    ],
)
def test_power_levels_distinguish_now_from_earlier(raw: int, level: str, text: str) -> None:
    items = {i.key: i for i in build_system_items(_snapshot(throttle=_throttle(raw)), UTC)}
    assert (items["power"].level, items["power"].value) == (level, text)


def test_unavailable_values_are_explained_not_hidden() -> None:
    items = {
        i.key: i
        for i in build_system_items(
            _snapshot(
                uptime_seconds=None,
                cpu_temperature_c=None,
                memory_total_kb=None,
                memory_available_kb=None,
                load_average=None,
                disk_total_bytes=None,
                disk_free_bytes=None,
            ),
            UTC,
        )
    }
    for key in ("uptime", "temperature", "memory", "power"):
        assert items[key].value == "Nicht verfügbar"
        assert "Raspberry Pi" in items[key].detail
        assert items[key].level == "off"
    assert "load" not in items
    assert "disk" not in items


def test_uptime_formats() -> None:
    def uptime(seconds: float) -> str:
        return next(
            i.value
            for i in build_system_items(_snapshot(uptime_seconds=seconds), UTC)
            if i.key == "uptime"
        )

    assert uptime(90) == "1 Min."
    assert uptime(3 * 3600 + 5 * 60) == "3 Std., 5 Min."
    assert uptime(2 * 86400 + 3 * 3600) == "2 Tage, 3 Std."


# ---------- page ----------


def test_page_shows_values_with_guidance(tmp_path: Path) -> None:
    ring, logger = _ring()
    logger.warning("Bridge nicht erreichbar")
    backend = _backend(
        tmp_path, _reader(tmp_path, temp="83000\n", throttled="throttled=0x1\n"), ring
    )
    status, text = _page(backend)
    assert status == 200
    assert "83.0 °C" in text
    assert "drosselt sich" in text
    assert "Unterspannung" in text
    assert "3 Tage, 4 Std." in text
    assert "<progress" in text
    assert "Bridge nicht erreichbar" in text
    assert "25.11.2026 13:00:00" in text  # shown in Europe/Zurich (UTC+1)


def test_page_without_reader_or_logs_explains_instead_of_failing(tmp_path: Path) -> None:
    status, text = _page(_backend(tmp_path, None, None))
    assert status == 200
    assert "nicht verfügbar" in text
    assert "Das Protokoll ist in diesem Modus nicht verfügbar" in text


def test_missing_sources_on_non_pi_hosts_render_cleanly(tmp_path: Path) -> None:
    reader = SystemInfoReader(
        tmp_path,
        proc=tmp_path / "nope",
        thermal_file=tmp_path / "nope-temp",
        runner=lambda _c: (_ for _ in ()).throw(FileNotFoundError("vcgencmd")),
    )
    status, text = _page(_backend(tmp_path, reader, None))
    assert status == 200
    assert "Nicht verfügbar" in text
    assert "Traceback" not in text


def test_log_levels_filter_and_unknown_level_is_rejected(tmp_path: Path) -> None:
    ring, logger = _ring()
    logger.info("nur-info-zeile")
    logger.warning("warn-zeile")
    logger.error("fehler-zeile")
    backend = _backend(tmp_path, _reader(tmp_path), ring)
    _, everything = _page(backend)
    _, warnings = _page(backend, "/system?level=WARNING")
    _, errors = _page(backend, "/system?level=ERROR")
    assert "nur-info-zeile" in everything
    assert "nur-info-zeile" not in warnings
    assert "warn-zeile" in warnings
    assert "warn-zeile" not in errors
    assert "fehler-zeile" in errors
    status, text = _page(backend, "/system?level=DEBUG")
    assert status == 422
    assert "CH-WEB-000" in text


def test_empty_log_level_says_so(tmp_path: Path) -> None:
    ring, _ = _ring()
    _, text = _page(_backend(tmp_path, _reader(tmp_path), ring), "/system?level=ERROR")
    assert "Keine Meldungen in dieser Stufe" in text


def test_log_page_never_shows_secrets_or_raw_addresses_and_escapes_html(tmp_path: Path) -> None:
    ring, logger = _ring()
    logger.error("token %s failed for AA:BB:CC:DD:EE:01 <script>alert(1)</script>", SECRET)
    _, text = _page(_backend(tmp_path, _reader(tmp_path), ring))
    assert SECRET not in text
    assert "AA:**:**:**:**:01" in text
    assert "AA:BB:CC:DD:EE:01" not in text
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text


def test_overview_and_navigation_link_to_system_page(tmp_path: Path) -> None:
    _, text = _page(_backend(tmp_path, _reader(tmp_path), None), "/")
    assert 'href="/system"' in text


# ---------- startup helpers ----------


def test_install_logging_masks_secrets_in_ring_and_console(
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    redactor = Redactor([SECRET])
    try:
        ring = install_logging(redactor)
        logging.getLogger("octapihub.test").warning("login with %s", SECRET)
        assert all(SECRET not in line.message for line in ring.lines())
        assert any("login with" in line.message for line in ring.lines())
        assert SECRET not in capsys.readouterr().err
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
        root.setLevel(level)


def test_register_secrets_masks_stored_values_and_survives_bad_files(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    SecretsFile(path).set("HUE_APPLICATION_KEY", "stored-hue-key-123456")
    redactor = Redactor()
    register_secrets(redactor, str(path))
    assert "stored-hue-key-123456" not in redactor.redact("x stored-hue-key-123456 y")

    bad = tmp_path / "bad.env"
    bad.write_text("not valid\n", encoding="utf-8")
    if os.name == "posix":
        bad.chmod(0o600)
    register_secrets(Redactor(), str(bad))  # must not raise


def test_default_sense_hat_config_is_untouched_by_system_wiring() -> None:
    assert SenseHatConfig().enabled
