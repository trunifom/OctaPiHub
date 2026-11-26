"""Read basic health data of the host (Linux/Raspberry Pi); every value may be unavailable."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_THROTTLE_BITS = {
    0: "under_voltage_now",
    1: "frequency_capped_now",
    2: "throttled_now",
    3: "soft_temperature_limit_now",
    16: "under_voltage_occurred",
    17: "frequency_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temperature_limit_occurred",
}
_MAX_REASONABLE_TEMP_C = 150.0

type CommandRunner = Callable[[Sequence[str]], str]


@dataclass(frozen=True, slots=True)
class ThrottleStatus:
    """Raspberry Pi power and thermal flags from ``vcgencmd get_throttled``."""

    raw: int
    under_voltage_now: bool
    throttled_now: bool
    frequency_capped_now: bool
    soft_temperature_limit_now: bool
    under_voltage_occurred: bool
    throttled_occurred: bool
    frequency_capped_occurred: bool
    soft_temperature_limit_occurred: bool

    @property
    def any_now(self) -> bool:
        """Whether a problem is active right now."""
        return (
            self.under_voltage_now
            or self.throttled_now
            or self.frequency_capped_now
            or self.soft_temperature_limit_now
        )

    @property
    def any_occurred(self) -> bool:
        """Whether a problem happened since boot."""
        return (
            self.under_voltage_occurred
            or self.throttled_occurred
            or self.frequency_capped_occurred
            or self.soft_temperature_limit_occurred
        )


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Host health at one moment; ``None`` means the value is not available here."""

    taken_at: datetime
    uptime_seconds: float | None
    load_average: tuple[float, float, float] | None
    memory_total_kb: int | None
    memory_available_kb: int | None
    cpu_temperature_c: float | None
    throttle: ThrottleStatus | None
    disk_total_bytes: int | None
    disk_free_bytes: int | None
    python_version: str
    platform: str
    time_synced: bool | None = None  # NTP: is the clock set from the network?

    @property
    def memory_used_percent(self) -> float | None:
        """Used memory in percent, or ``None`` if unknown."""
        if not self.memory_total_kb or self.memory_available_kb is None:
            return None
        used = self.memory_total_kb - self.memory_available_kb
        return max(0.0, min(100.0, used * 100.0 / self.memory_total_kb))


def parse_throttled(output: str) -> ThrottleStatus | None:
    """Parse ``throttled=0x50005``; returns ``None`` for anything unexpected."""
    key, separator, value = output.strip().partition("=")
    if key != "throttled" or not separator:
        return None
    try:
        raw = int(value.strip(), 16)
    except ValueError:
        return None
    if raw < 0 or raw > 0xFFFFFFFF:
        return None
    flags = {name: bool(raw & (1 << bit)) for bit, name in _THROTTLE_BITS.items()}
    return ThrottleStatus(raw=raw, **flags)


def _run_command(command: Sequence[str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        list(command), capture_output=True, text=True, timeout=2.0, check=True
    )
    return completed.stdout


class SystemInfoReader:
    """Collect a :class:`SystemSnapshot`; each source failing leaves only its value empty.

    Source locations and the command runner are injectable so tests can use temporary
    files instead of a real ``/proc`` and ``/sys``.
    """

    def __init__(
        self,
        data_dir: str | Path = ".",
        *,
        proc: str | Path = "/proc",
        thermal_file: str | Path = "/sys/class/thermal/thermal_zone0/temp",
        runner: CommandRunner = _run_command,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._data_dir = Path(data_dir)
        self._proc = Path(proc)
        self._thermal_file = Path(thermal_file)
        self._runner = runner
        self._clock = clock

    def read(self) -> SystemSnapshot:
        """Take a snapshot; never raises."""
        total, available = self._memory()
        disk_total, disk_free = self._disk()
        return SystemSnapshot(
            taken_at=self._clock(),
            uptime_seconds=self._uptime(),
            load_average=self._load(),
            memory_total_kb=total,
            memory_available_kb=available,
            cpu_temperature_c=self._temperature(),
            throttle=self._throttle(),
            disk_total_bytes=disk_total,
            disk_free_bytes=disk_free,
            time_synced=self._time_synced(),
            python_version=sys.version.split()[0],
            platform=platform.platform(),
        )

    def _read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _uptime(self) -> float | None:
        text = self._read_text(self._proc / "uptime")
        try:
            value = float(text.split()[0]) if text else None
        except (ValueError, IndexError):
            return None
        return value if value is not None and value >= 0 else None

    def _load(self) -> tuple[float, float, float] | None:
        text = self._read_text(self._proc / "loadavg")
        try:
            parts = text.split()[:3] if text else []
            one, five, fifteen = (float(part) for part in parts)
        except ValueError:
            return None
        return (one, five, fifteen)

    def _memory(self) -> tuple[int | None, int | None]:
        text = self._read_text(self._proc / "meminfo")
        if not text:
            return None, None
        values: dict[str, int] = {}
        for line in text.splitlines():
            name, _, rest = line.partition(":")
            fields = rest.split()
            if name in {"MemTotal", "MemAvailable"} and fields:
                try:
                    values[name] = int(fields[0])
                except ValueError:
                    continue
        return values.get("MemTotal"), values.get("MemAvailable")

    def _temperature(self) -> float | None:
        text = self._read_text(self._thermal_file)
        try:
            millidegrees = int(text.strip()) if text else None
        except ValueError:
            return None
        if millidegrees is None:
            return None
        celsius = millidegrees / 1000.0
        return celsius if -40.0 <= celsius <= _MAX_REASONABLE_TEMP_C else None

    def _time_synced(self) -> bool | None:
        """Whether the system clock is synchronised (``timedatectl``); ``None`` if unknown."""
        try:
            answer = (
                self._runner(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
                .strip()
                .lower()
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return {"yes": True, "no": False}.get(answer)

    def _throttle(self) -> ThrottleStatus | None:
        try:
            return parse_throttled(self._runner(["vcgencmd", "get_throttled"]))
        except (OSError, subprocess.SubprocessError):
            return None

    def _disk(self) -> tuple[int | None, int | None]:
        try:
            usage = shutil.disk_usage(os.fspath(self._data_dir))
        except OSError:
            return None, None
        return usage.total, usage.free
