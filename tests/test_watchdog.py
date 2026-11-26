from __future__ import annotations

import asyncio
import os
import socket
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octapihub.core.watchdog import (
    MAX_LOOP_LAG_SECONDS,
    MAX_MISSED_CHECKS,
    Watchdog,
    notify,
    watchdog_interval,
)
from octapihub.web.app import create_app
from octapihub.web.demo import build_demo_backend

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


# ---------- configuration from systemd ----------


def test_the_interval_is_half_the_configured_time() -> None:
    assert watchdog_interval({"WATCHDOG_USEC": "60000000"}) == 30.0
    assert (
        watchdog_interval({"WATCHDOG_USEC": "60000000", "WATCHDOG_PID": str(os.getpid())}) == 30.0
    )


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"WATCHDOG_USEC": ""},
        {"WATCHDOG_USEC": "0"},
        {"WATCHDOG_USEC": "abc"},
        {"WATCHDOG_USEC": "-5"},
        {"WATCHDOG_USEC": "60000000", "WATCHDOG_PID": "1"},  # meant for another process
    ],
)
def test_no_watchdog_without_a_valid_setting(environ: dict[str, str]) -> None:
    assert watchdog_interval(environ) is None


def test_notify_does_nothing_outside_systemd() -> None:
    assert notify("READY=1", {}) is False
    assert notify("READY=1", {"NOTIFY_SOCKET": ""}) is False


def test_notify_survives_an_unreachable_socket(caplog: pytest.LogCaptureFixture) -> None:
    missing = str(Path(tempfile.gettempdir()) / "octapihub-no-such.sock")
    assert notify("READY=1", {"NOTIFY_SOCKET": missing}) is False


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets")
def test_notify_delivers_the_message_over_a_unix_socket() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "notify.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            server.bind(path)
            server.settimeout(2)
            assert notify("WATCHDOG=1", {"NOTIFY_SOCKET": path}) is True
            assert server.recv(100) == b"WATCHDOG=1"
        finally:
            server.close()


# ---------- the check ----------


class Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str) -> bool:
        self.messages.append(message)
        return True


def test_a_healthy_check_pings() -> None:
    send = Recorder()
    dog = Watchdog(interval=1, send=send)
    assert dog.check_once() is True and send.messages == ["WATCHDOG=1"] and dog.pings == 1


def test_one_or_two_bad_moments_are_tolerated_but_a_stuck_program_stops_pinging() -> None:
    send = Recorder()
    state = {"ok": False}
    dog = Watchdog(interval=1, send=send, healthy=lambda: state["ok"])
    results = [dog.check_once() for _ in range(MAX_MISSED_CHECKS + 2)]
    assert results[: MAX_MISSED_CHECKS - 1] == [True] * (MAX_MISSED_CHECKS - 1)
    assert results[MAX_MISSED_CHECKS - 1 :] == [False] * 3
    assert send.messages.count("WATCHDOG=1") == MAX_MISSED_CHECKS - 1  # then silence
    state["ok"] = True
    assert dog.check_once() is True  # recovery resumes the pings at once


def test_a_late_wake_up_counts_as_a_stuck_loop() -> None:
    send = Recorder()
    dog = Watchdog(interval=1, send=send)
    assert dog.check_once(lag=MAX_LOOP_LAG_SECONDS + 1) is True  # first miss is tolerated
    for _ in range(MAX_MISSED_CHECKS):
        dog.check_once(lag=MAX_LOOP_LAG_SECONDS + 1)
    assert dog.check_once(lag=MAX_LOOP_LAG_SECONDS + 1) is False


def test_a_broken_health_probe_is_unhealthy_not_a_crash(caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> bool:
        raise RuntimeError("geheim123")

    send = Recorder()
    dog = Watchdog(interval=1, send=send, healthy=broken)
    for _ in range(MAX_MISSED_CHECKS + 1):
        dog.check_once()
    assert send.messages.count("WATCHDOG=1") == MAX_MISSED_CHECKS - 1


# ---------- lifecycle ----------


def test_start_announces_ready_and_pings_regularly_and_stop_announces_stopping() -> None:
    async def scenario() -> list[str]:
        send = Recorder()
        dog = Watchdog(interval=0.02, send=send)
        assert dog.active
        await dog.start()
        await dog.start()  # a second start does not create a second task
        await asyncio.sleep(0.15)
        await dog.stop()
        await dog.stop()  # idempotent
        return send.messages

    messages = asyncio.run(scenario())
    assert messages[0] == "READY=1" and messages[-2:] == ["STOPPING=1", "STOPPING=1"]
    assert messages.count("WATCHDOG=1") >= 3


def test_without_an_interval_only_ready_and_stopping_are_sent() -> None:
    async def scenario() -> list[str]:
        send = Recorder()
        dog = Watchdog(interval=None, send=send)
        dog._interval = None
        assert dog.active is False
        await dog.start()
        await asyncio.sleep(0.05)
        await dog.stop()
        return send.messages

    assert asyncio.run(scenario()) == ["READY=1", "STOPPING=1"]


def test_the_web_app_starts_and_stops_the_watchdog() -> None:
    backend = build_demo_backend()
    send = Recorder()
    backend.watchdog = Watchdog(interval=0.02, send=send)
    with TestClient(create_app(backend, manage_service=True)) as client:
        assert client.get("/healthz").status_code == 200
        assert send.messages[0] == "READY=1"
    assert send.messages[-1] == "STOPPING=1"


def test_a_blocked_event_loop_stops_the_pings() -> None:
    """The real danger: something blocks the loop. The ping task cannot run, so none is sent."""
    import time

    async def scenario() -> int:
        send = Recorder()
        dog = Watchdog(interval=0.02, send=send)
        await dog.start()
        await asyncio.sleep(0.1)
        before = send.messages.count("WATCHDOG=1")
        time.sleep(0.3)  # blocks the whole loop, like a deadlock would
        during = send.messages.count("WATCHDOG=1") - before
        await dog.stop()
        return during

    assert asyncio.run(scenario()) <= 1  # at most the ping that was already due


# ---------- deployment ----------


def test_the_unit_uses_notify_with_a_watchdog() -> None:
    text = (DEPLOY / "octapihub.service").read_text(encoding="utf-8")
    assert "Type=notify" in text and "NotifyAccess=main" in text and "WatchdogSec=60" in text
    assert "Type=simple" not in text
