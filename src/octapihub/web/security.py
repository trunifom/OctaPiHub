"""Access protection, the change log and the metrics page of the web interface."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from octapihub.core.auth_guard import FailureLimiter

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

_client: ContextVar[str] = ContextVar("octapihub_client", default="")
_guard: ContextVar[FailureLimiter | None] = ContextVar("octapihub_guard", default=None)

# What a successful POST means, for the change log (everything else shows its path).
ACTIONS: dict[str, str] = {
    "/cubes/mapping": "Zuordnung der Würfelseiten gespeichert",
    "/cubes/manage": "Würfel hinzugefügt oder entfernt",
    "/cubes/trigger": "Würfelaktion von Hand ausgelöst",
    "/cubes/template": "Würfel-Vorlage angewendet",
    "/light": "Licht-Einstellungen oder Licht-Aktion",
    "/music": "Musik-Einstellungen oder Wiedergabe",
    "/smarthome": "Smart-Home-Einstellungen oder Aktion",
    "/settings": "Einstellungen gespeichert",
    "/backup": "Sicherung, Export oder Import",
    "/display": "LED-Anzeige bedient",
    "/time": "Zeitzone geändert",
}
_NOT_A_CHANGE = ("/help",)


def token_problem_for_client(
    matches: Callable[[], bool],
) -> tuple[int, str, str, str] | None:
    """Apply the lock-out rule around a token comparison (``matches`` does the comparing)."""
    guard, client = _guard.get(), _client.get()
    if guard is not None and client:
        wait = guard.retry_after(client)
        if wait:
            return (
                429,
                "Zu viele Fehlversuche",
                f"Es wurde nichts geändert. Bitte warten Sie {wait} Sekunden und versuchen Sie "
                "es dann mit dem richtigen Token erneut.",
                "CH-WEB-021",
            )
    if matches():
        if guard is not None and client:
            guard.record_success(client)
        return None
    if guard is not None and client:
        guard.record_failure(client)
    return (
        403,
        "Das Zugriffs-Token stimmt nicht",
        "Es wurde nichts geändert. Prüfen Sie das Token und versuchen Sie es erneut.",
        "CH-WEB-004",
    )


class Stats:
    """Counters for the metrics page."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.requests: Counter[str] = Counter()


def label_for(path: str) -> str:
    """The change-log wording for a path."""
    for prefix, text in ACTIONS.items():
        if path == prefix or path.startswith(prefix + "/"):
            return text
    return f"Anfrage {path[:80]}"


def register_security(
    app: FastAPI,
    backend: WebBackend,
    matches: Callable[[str | None, str], bool],
    stats: Stats,
) -> None:
    """Install the client/guard context, the change log and ``/metrics``."""

    @app.middleware("http")
    async def guard_and_audit(
        request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        client = request.client.host if request.client else ""
        client_token = _client.set(client)
        guard_token = _guard.set(backend.guard)
        try:
            response = await call_next(request)
        finally:
            _client.reset(client_token)
            _guard.reset(guard_token)
        stats.requests[f"{response.status_code // 100}xx"] += 1
        if request.method == "POST" and backend.audit is not None:
            path = request.url.path
            if not path.startswith(_NOT_A_CHANGE):
                if response.status_code < 400:
                    backend.audit.record(client, label_for(path))
                elif response.status_code in (403, 429):
                    backend.audit.record(client, label_for(path), "abgelehnt")
        return response

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        """Prometheus text format; needs the access token (``Authorization: Bearer ...``)."""
        header = request.headers.get("authorization", "")
        supplied = header.removeprefix("Bearer ").strip() or request.headers.get(
            "x-octapihub-token", ""
        )
        if not backend.token or not matches(backend.token, supplied):
            return PlainTextResponse("Zugriffs-Token erforderlich\n", status_code=403)
        return PlainTextResponse(
            render_metrics(backend, stats), media_type="text/plain; version=0.0.4"
        )


def _line(name: str, help_text: str, value: float, kind: str = "gauge") -> list[str]:
    return [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name} {value:g}"]


def render_metrics(backend: WebBackend, stats: Stats) -> str:
    """The numbers of this hub in the Prometheus text format (no names, no secrets)."""
    lines: list[str] = []
    lines += _line("octapihub_up", "The hub is running", 1)
    uptime = round(time.monotonic() - stats.started)
    lines += _line("octapihub_uptime_seconds", "Seconds since start", uptime)
    sense = backend.sense_hat
    ok = 1 if sense.hardware_error_code is None else 0
    lines += _line("octapihub_sense_hat_ok", "1 if the Sense HAT works", ok)
    if backend.cubes is not None:
        configured = len(backend.settings.config.cubes)
        connected = sum(1 for s in backend.cubes.statuses if s.connected)
        pipeline = 1 if backend.cubes.running and backend.cubes.error is None else 0
        lines += _line("octapihub_cubes_configured", "Configured cubes", configured)
        lines += _line("octapihub_cubes_connected", "Connected cubes", connected)
        lines += _line("octapihub_cubes_ok", "1 if the cube pipeline runs", pipeline)
    if backend.hue is not None:
        status = backend.hue.status
        lines += _line("octapihub_hue_ok", "1 if Hue has no error", int(status.last_error is None))
        lines += _line(
            "octapihub_hue_live", "1 if the Hue event stream is connected", int(status.live)
        )
    guard = backend.guard
    lines += _line(
        "octapihub_login_failures_total", "Wrong access tokens", guard.failures, "counter"
    )
    lines += _line(
        "octapihub_login_lockouts_total", "Clients locked out", guard.lockouts, "counter"
    )
    lines += [
        "# HELP octapihub_http_requests_total Requests by status class",
        "# TYPE octapihub_http_requests_total counter",
    ]
    for klass, count in sorted(stats.requests.items()):
        lines.append(f'octapihub_http_requests_total{{class="{klass}"}} {count}')
    return "\n".join(lines) + "\n"
