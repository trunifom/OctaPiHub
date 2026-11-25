from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from octapihub.core.logging_support import REDACTED, LogRing, RedactingFilter, Redactor
from octapihub.infrastructure.system_info import SystemInfoReader, parse_throttled

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _proc(tmp_path: Path, **files: str) -> Path:
    proc = tmp_path / "proc"
    proc.mkdir()
    for name, content in files.items():
        (proc / name).write_text(content, encoding="utf-8")
    return proc


def _reader(
    tmp_path: Path,
    proc: Path | None = None,
    thermal: str | None = None,
    command: str | Exception = "throttled=0x0\n",
) -> SystemInfoReader:
    thermal_file = tmp_path / "temp"
    if thermal is not None:
        thermal_file.write_text(thermal, encoding="utf-8")

    def runner(_command: Sequence[str]) -> str:
        if isinstance(command, Exception):
            raise command
        return command

    return SystemInfoReader(
        tmp_path,
        proc=proc or tmp_path / "missing-proc",
        thermal_file=thermal_file,
        runner=runner,
        clock=lambda: NOW,
    )


# ---------- system info ----------


def test_snapshot_reads_all_available_sources(tmp_path: Path) -> None:
    proc = _proc(
        tmp_path,
        uptime="3600.55 12000.1\n",
        loadavg="0.10 0.25 0.50 1/200 999\n",
        meminfo="MemTotal:        1000000 kB\nMemFree: 1 kB\nMemAvailable:    400000 kB\n",
    )
    snapshot = _reader(tmp_path, proc, thermal="48312\n").read()

    assert snapshot.taken_at == NOW
    assert snapshot.uptime_seconds == pytest.approx(3600.55)
    assert snapshot.load_average == (0.10, 0.25, 0.50)
    assert snapshot.memory_total_kb == 1_000_000
    assert snapshot.memory_used_percent == pytest.approx(60.0)
    assert snapshot.cpu_temperature_c == pytest.approx(48.312)
    assert snapshot.throttle is not None
    assert not snapshot.throttle.any_now
    assert snapshot.disk_total_bytes and snapshot.disk_free_bytes is not None


def test_missing_sources_leave_only_their_values_empty(tmp_path: Path) -> None:
    snapshot = _reader(tmp_path, command=FileNotFoundError("no vcgencmd")).read()
    assert snapshot.uptime_seconds is None
    assert snapshot.load_average is None
    assert snapshot.memory_total_kb is None
    assert snapshot.memory_used_percent is None
    assert snapshot.cpu_temperature_c is None
    assert snapshot.throttle is None
    assert snapshot.python_version  # always available
    assert snapshot.disk_total_bytes is not None


@pytest.mark.parametrize(
    "files",
    [
        {"uptime": "", "loadavg": "", "meminfo": ""},
        {"uptime": "abc", "loadavg": "a b c", "meminfo": "MemTotal: lots kB"},
        {"uptime": "-5 0", "loadavg": "1 2", "meminfo": "MemAvailable: 5 kB"},
    ],
)
def test_garbage_in_proc_files_never_raises(tmp_path: Path, files: dict[str, str]) -> None:
    snapshot = _reader(tmp_path, _proc(tmp_path, **files)).read()
    assert snapshot.uptime_seconds is None
    assert snapshot.load_average is None
    assert snapshot.memory_used_percent is None


@pytest.mark.parametrize("thermal", ["", "hot", "999999", "-90000", "\x00"])
def test_implausible_temperatures_are_not_shown(tmp_path: Path, thermal: str) -> None:
    assert _reader(tmp_path, thermal=thermal).read().cpu_temperature_c is None


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("missing"),
        PermissionError("denied"),
        subprocess.TimeoutExpired("vcgencmd", 2),
        subprocess.CalledProcessError(1, "vcgencmd"),
    ],
)
def test_vcgencmd_failures_are_swallowed(tmp_path: Path, failure: Exception) -> None:
    assert _reader(tmp_path, command=failure).read().throttle is None


def test_memory_percent_is_clamped(tmp_path: Path) -> None:
    proc = _proc(tmp_path, meminfo="MemTotal: 100 kB\nMemAvailable: 500 kB\n")
    assert _reader(tmp_path, proc).read().memory_used_percent == 0.0


def test_throttle_flags_distinguish_now_and_earlier() -> None:
    status = parse_throttled("throttled=0x50005\n")
    assert status is not None
    assert status.under_voltage_now and status.throttled_now
    assert status.under_voltage_occurred and status.throttled_occurred
    assert not status.frequency_capped_now
    assert status.any_now and status.any_occurred

    earlier = parse_throttled("throttled=0x50000")
    assert earlier is not None
    assert not earlier.any_now
    assert earlier.any_occurred


@pytest.mark.parametrize(
    "text",
    ["", "throttled", "throttled=zz", "other=0x1", "throttled=-0x1", "throttled=0x1FFFFFFFF"],
)
def test_throttle_parser_rejects_unexpected_output(text: str) -> None:
    assert parse_throttled(text) is None


# ---------- redaction ----------


def test_redactor_masks_credentials_and_shortens_addresses() -> None:
    redactor = Redactor()
    text = (
        "connect AA:bb:CC:dd:EE:01 failed; hue-application-key: abcdef123456, "
        "token=xyz987 Authorization: Bearer tok.en-1 password = hunter2"
    )
    redacted = redactor.redact(text)
    assert "AA:**:**:**:**:01" in redacted
    assert "bb:CC" not in redacted
    for secret in ("abcdef123456", "xyz987", "tok.en-1", "hunter2"):
        assert secret not in redacted
    assert REDACTED in redacted


def test_registered_secrets_are_removed_everywhere() -> None:
    redactor = Redactor(["my-web-token-123"])
    assert "my-web-token-123" not in redactor.redact("GET /x?a=my-web-token-123&b=2")
    redactor.add_secret("short")  # too short to register safely
    assert redactor.redact("a short word") == "a short word"
    redactor.add_secret(None)
    redactor.add_secret("")


def test_plain_text_is_untouched() -> None:
    assert Redactor().redact("Würfel verbunden (Versuch 2)") == "Würfel verbunden (Versuch 2)"


def test_filter_redacts_formatted_message_and_hides_exception_text() -> None:
    redactor = Redactor(["supersecretvalue"])
    record = logging.LogRecord(
        "x",
        logging.ERROR,
        __file__,
        1,
        "key %s for %s",
        ("supersecretvalue", "AA:BB:CC:DD:EE:02"),
        None,
    )
    try:
        raise RuntimeError("leaks supersecretvalue")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    assert RedactingFilter(redactor).filter(record) is True
    assert "supersecretvalue" not in record.msg
    assert "AA:**:**:**:**:02" in record.msg
    assert record.exc_info is None
    assert record.exc_text == "RuntimeError"


# ---------- log ring ----------


def _ring(capacity: int = 3) -> tuple[LogRing, logging.Logger]:
    ring = LogRing(Redactor(["topsecretvalue"]), capacity)
    logger = logging.getLogger(f"test-ring-{id(ring)}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(ring)
    return ring, logger


def test_ring_keeps_newest_lines_redacted_and_newest_first() -> None:
    ring, logger = _ring(capacity=3)
    for number in range(5):
        logger.info("line %d topsecretvalue", number)
    lines = ring.lines()
    assert [line.message for line in lines] == [
        "line 4 ***",
        "line 3 ***",
        "line 2 ***",
    ]


def test_ring_filters_by_level_and_ignores_debug() -> None:
    ring, logger = _ring(capacity=10)
    logger.debug("debug")
    logger.info("info")
    logger.warning("warn")
    logger.error("error")
    assert [line.level for line in ring.lines()] == ["ERROR", "WARNING", "INFO"]
    assert [line.level for line in ring.lines(minimum_level=logging.WARNING)] == [
        "ERROR",
        "WARNING",
    ]
    assert len(ring.lines(limit=1)) == 1


def test_ring_records_exception_type_but_not_its_text() -> None:
    ring, logger = _ring()
    try:
        raise ValueError("password=hunter22 leaked")
    except ValueError:
        logger.exception("boom")
    (line,) = ring.lines()
    assert line.message == "boom [ValueError]"


def test_ring_never_raises_into_the_caller() -> None:
    ring, logger = _ring()

    class Broken:
        def __str__(self) -> str:
            raise RuntimeError("cannot format")

    logging.raiseExceptions = False
    try:
        logger.info("value %s", Broken())
    finally:
        logging.raiseExceptions = True
    assert ring.lines() == ()


def test_ring_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        LogRing(Redactor(), 0)
