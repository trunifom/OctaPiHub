"""Time zone: use the system's by default, allow an explicit choice, and change it live.

The Raspberry Pi keeps its time correct through the operating system (NTP over the internet); the
program must not set the clock itself. It only needs to know *which zone to show*. By default that
is the zone the system is set to (``timedatectl`` / ``/etc/localtime``); a setting can override it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
FALLBACK = "UTC"


def is_valid_name(name: str) -> bool:
    """Whether ``name`` is a time zone this system knows (for example ``Europe/Zurich``)."""
    if not _NAME.fullmatch(name):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def known_names() -> list[str]:
    """All zone names, sorted (for the selection list)."""
    return sorted(n for n in available_timezones() if _NAME.fullmatch(n))


def detect_system_name(
    environ: Mapping[str, str] | None = None,
    timezone_file: Path = Path("/etc/timezone"),
    localtime_link: Path = Path("/etc/localtime"),
) -> str:
    """The zone the operating system is set to (``""`` if it cannot be determined)."""
    env = os.environ if environ is None else environ
    candidates: list[str] = []
    tz_env = env.get("TZ", "").lstrip(":")
    if tz_env:
        candidates.append(tz_env)
    with contextlib.suppress(OSError):
        candidates.append(timezone_file.read_text(encoding="utf-8").strip())
    try:
        target = os.readlink(localtime_link)
        marker = "zoneinfo/"
        if marker in target:
            candidates.append(target.split(marker, 1)[1])
    except OSError:
        pass
    for name in candidates:
        if name and is_valid_name(name):
            return name
    return ""


def resolve(name: str, detect: Callable[[], str] = detect_system_name) -> tuple[str, tzinfo, str]:
    """Resolve a setting to ``(zone name, zone, source)``; ``source`` is auto, setting or fallback.

    An empty ``name`` means "use the system's zone". An unknown zone never raises: UTC is used
    and the source says so.
    """
    if name:
        if is_valid_name(name):
            return name, ZoneInfo(name), "setting"
        logger.warning("Time zone %r is unknown; using the system's zone", name)
    detected = detect()
    if detected:
        return detected, ZoneInfo(detected), "auto"
    logger.warning("The system time zone is unknown; showing UTC")
    return FALLBACK, UTC, "fallback"


class TimeSettings:
    """The zone currently in use; it can be changed while the program runs."""

    def __init__(self, name: str = "", detect: Callable[[], str] = detect_system_name) -> None:
        self._detect = detect
        self.name = ""
        self.source = "auto"
        self._zone: tzinfo = UTC
        self.apply(name)

    def apply(self, name: str) -> None:
        """Use ``name`` (``""`` = the system's zone)."""
        self.name, self._zone, self.source = resolve(name, self._detect)

    @property
    def zone(self) -> tzinfo:
        """The zone object in use right now."""
        return self._zone

    def now(self) -> datetime:
        """The current time in the zone in use."""
        return datetime.now(self._zone)


class LiveTimeZone(tzinfo):
    """A time zone that always follows :class:`TimeSettings`, so a change applies everywhere."""

    def __init__(self, settings: TimeSettings) -> None:
        self._settings = settings

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return self._settings.zone.utcoffset(dt)

    def dst(self, dt: datetime | None) -> timedelta | None:
        return self._settings.zone.dst(dt)

    def tzname(self, dt: datetime | None) -> str | None:
        return self._settings.zone.tzname(dt)

    def fromutc(self, dt: datetime) -> datetime:
        local = dt.replace(tzinfo=UTC).astimezone(self._settings.zone)
        return local.replace(tzinfo=self)

    def __repr__(self) -> str:
        return f"LiveTimeZone({self._settings.name})"
