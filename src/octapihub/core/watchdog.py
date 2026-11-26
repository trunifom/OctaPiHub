"""systemd watchdog: restart the service if it hangs.

Under ``Type=notify`` with ``WatchdogSec=`` systemd expects a ``WATCHDOG=1`` message within the
interval and restarts the service if none arrives. This module sends the messages over the
``NOTIFY_SOCKET`` itself (no extra package) and only does so while the program is *healthy*: the
event loop must keep responding. A process that is stuck (blocked loop, deadlock) stops reporting
and is restarted; a part that merely has a problem (Bluetooth off, lamp unreachable) is **not** a
reason to restart and is shown in the interface instead.

Outside systemd (no ``NOTIFY_SOCKET``) everything here does nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

MAX_LOOP_LAG_SECONDS = 5.0  # a loop that needs longer than this to wake up is considered stuck
MAX_MISSED_CHECKS = 3  # consecutive unhealthy checks before pings stop


def notify(message: str, environ: Mapping[str, str] | None = None) -> bool:
    """Send ``message`` (e.g. ``READY=1``) to systemd; returns whether it was sent."""
    env = os.environ if environ is None else environ
    path = env.get("NOTIFY_SOCKET", "")
    if not path or not hasattr(socket, "AF_UNIX"):
        return False
    address = "\0" + path[1:] if path.startswith("@") else path  # "@" = abstract socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2.0)
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
    except OSError as exc:
        logger.warning("systemd notification failed (%s)", type(exc).__name__)
        return False
    return True


def watchdog_interval(environ: Mapping[str, str] | None = None) -> float | None:
    """How often to ping: half the configured ``WatchdogSec`` (``None`` = no watchdog)."""
    env = os.environ if environ is None else environ
    raw = env.get("WATCHDOG_USEC", "")
    if not raw.isdigit() or int(raw) <= 0:
        return None
    pid = env.get("WATCHDOG_PID", "")
    if pid and pid.isdigit() and int(pid) != os.getpid():
        return None  # meant for another process
    return int(raw) / 1_000_000 / 2


class Watchdog:
    """Pings systemd while the program responds; stops pinging when it does not."""

    def __init__(
        self,
        *,
        interval: float | None = None,
        send: Callable[[str], bool] = notify,
        healthy: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval if interval is not None else watchdog_interval()
        self._send = send
        self._healthy = healthy
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._missed = 0
        self.pings = 0

    @property
    def active(self) -> bool:
        """Whether a watchdog interval is configured."""
        return self._interval is not None

    async def start(self) -> None:
        """Tell systemd that start-up is finished and begin pinging."""
        self._send("READY=1")
        if self._interval is None or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(self._run(), name="watchdog")

    async def stop(self) -> None:
        """Announce shutdown and end the ping task."""
        self._send("STOPPING=1")
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def check_once(self, lag: float = 0.0) -> bool:
        """One check: ping if healthy and the loop was not stuck. Returns whether it pinged."""
        try:
            ok = lag <= MAX_LOOP_LAG_SECONDS and bool(self._healthy())
        except Exception:
            logger.exception("Watchdog health check failed")
            ok = False
        if ok:
            self._missed = 0
            self.pings += 1
            self._send("WATCHDOG=1")
            return True
        self._missed += 1
        if self._missed == MAX_MISSED_CHECKS:
            logger.error(
                "Watchdog: no sign of life for %d checks; systemd will restart", self._missed
            )
        if self._missed < MAX_MISSED_CHECKS:
            self._send("WATCHDOG=1")  # one bad moment is tolerated
        return self._missed < MAX_MISSED_CHECKS

    async def _run(self) -> None:
        assert self._interval is not None
        while True:
            started = self._clock()
            await asyncio.sleep(self._interval)
            lag = max(0.0, self._clock() - started - self._interval)
            self.check_once(lag)
