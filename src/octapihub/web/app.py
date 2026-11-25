"""FastAPI application: server-rendered pages, JSON series, and an SSE live stream."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from octapihub.application.cube_service import CubeService
from octapihub.application.hue_service import HueService
from octapihub.application.matrix.service import MatrixDisplayService
from octapihub.application.matrix.views import VIEWS
from octapihub.application.music.service import MusicService
from octapihub.application.sense_hat import JoystickAction, JoystickDirection
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import JOYSTICK_FACE_LABELS, JOYSTICK_SOURCE
from octapihub.core.errors import (
    ConfigurationError,
    HueLinkButtonError,
    HueNotConfiguredError,
    OctaPiHubError,
    ValidationError,
)
from octapihub.core.logging_support import LogRing, Redactor
from octapihub.infrastructure.music.media import MediaRegistry, content_type_for
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web import forms
from octapihub.web.backup_routes import register_backup_routes
from octapihub.web.diagnostics import build_zip, collect_diagnostics
from octapihub.web.music_routes import register_music_routes
from octapihub.web.presenter import (
    CHARTABLE_GROUPS,
    GROUP_INFO,
    SECTIONS,
    ViewCard,
    battery_warnings,
    build_cube_tiles,
    build_cube_views,
    build_group_views,
    build_hue_view,
    build_log_rows,
    build_mapping_views,
    build_music_summary,
    build_notices,
    build_system_items,
    chart_groups,
    correction_active,
    cube_summary,
    describe_display_state,
    describe_hardware,
    display_unit,
    frame_to_fills,
    frame_to_hex,
    local_time_text,
    overall_level,
)

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
MAX_FORM_BYTES = 64 * 1024
BACKUP_FORM_BYTES = 640 * 1024  # an export file pasted into the import form
DISPLAY_TIMEZONE = "Europe/Zurich"

# range name -> (label, window, bucket seconds); ~120-180 points per chart.
SERIES_RANGES: Mapping[str, tuple[str, timedelta, int]] = {
    "1h": ("1 Stunde", timedelta(hours=1), 30),
    "24h": ("24 Stunden", timedelta(hours=24), 600),
    "7d": ("7 Tage", timedelta(days=7), 3600),
    "30d": ("30 Tage", timedelta(days=30), 14400),
}
# Per-group chart hints: smallest visible span, or a fixed axis.
_CHART_HINTS: Mapping[str, dict[str, Any]] = {
    "temperature": {"min_span": 2.0, "fixed": None},
    "humidity": {"min_span": 10.0, "fixed": None},
    "pressure": {"min_span": 4.0, "fixed": None},
    "compass": {"min_span": 360.0, "fixed": [0, 360]},
}


def display_timezone() -> tzinfo:
    """Europe/Zurich for display, falling back to UTC when tz data is missing."""
    try:
        return ZoneInfo(DISPLAY_TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("Time zone data for %s not found; showing UTC", DISPLAY_TIMEZONE)
        return UTC


@dataclass(slots=True)
class WebBackend:
    """Everything the web layer may touch; injected so tests need no hardware."""

    settings: SettingsManager
    sense_hat: SenseHatService
    token: str | None = None
    cubes: CubeService | None = None
    hue: HueService | None = None
    system: SystemInfoReader | None = None
    matrix: MatrixDisplayService | None = None
    music: MusicService | None = None
    media: MediaRegistry | None = None
    discover_cast: Callable[[], Awaitable[list[str]]] | None = None
    list_audio_devices: Callable[[], Awaitable[list[tuple[str, str]]]] | None = None
    logs: LogRing | None = None
    log_dir: str | None = None
    redactor: Redactor | None = None
    tz: tzinfo = field(default_factory=display_timezone)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


def _token_matches(expected: str | None, supplied: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(expected.encode()).digest(), hashlib.sha256(supplied.encode()).digest()
    )


def _access_problem(token: str | None, supplied: str) -> tuple[int, str, str, str] | None:
    """Return (status, title, detail, code) when a write request may not proceed."""
    if not token:
        return (
            403,
            "Speichern ist ausgeschaltet",
            "Es ist kein Zugriffs-Token eingerichtet. Setzen Sie OCTAPIHUB_WEB_TOKEN "
            "und starten Sie den Dienst neu.",
            "CH-WEB-003",
        )
    if not _token_matches(token, supplied):
        return (
            403,
            "Das Zugriffs-Token stimmt nicht",
            "Es wurde nichts geändert. Prüfen Sie das Token und versuchen Sie es erneut.",
            "CH-WEB-004",
        )
    return None


async def _read_form(request: Request, limit: int = MAX_FORM_BYTES) -> tuple[dict[str, str], str]:
    """Parse a small urlencoded body; returns (fields without token, supplied token)."""
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="Request too large")
    fields = dict(parse_qsl(raw.decode("utf-8", errors="replace"), keep_blank_values=True))
    supplied = fields.pop("token", "") or request.headers.get("x-octapihub-token", "")
    fields.pop("_", None)
    return fields, supplied


async def display_stream(
    backend: WebBackend,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    interval: float = 0.25,
    max_events: int | None = None,
) -> AsyncIterator[str]:
    """SSE messages with the current LED frame whenever it (or the view) changes."""
    yield "retry: 3000\n\n"
    last = ""
    idle = 0.0
    sent = 0
    while not await is_disconnected():
        matrix = backend.matrix
        if matrix is not None:
            status = matrix.status
            payload = json.dumps(
                {
                    "view": status.view_id,
                    "label": VIEWS[status.view_id].label,
                    "state": status.state,
                    "on": status.display_on,
                    "night": status.night,
                    "brightness": status.brightness,
                    "frame": frame_to_hex(status.frame),
                },
                separators=(",", ":"),
            )
            if payload != last:
                last, idle = payload, 0.0
                sent += 1
                yield f"event: display\ndata: {payload}\n\n"
                if max_events is not None and sent >= max_events:
                    return
        if idle >= 15.0:
            idle = 0.0
            yield ": keep-alive\n\n"
        await asyncio.sleep(interval)
        idle += interval


MUSIC_POLL_SECONDS = 5.0


async def _watch_music(music: MusicService) -> None:
    """Ask connected players for their state while something plays (a track may end by itself)."""
    while True:
        await asyncio.sleep(MUSIC_POLL_SECONDS)
        try:
            if any(
                t.state.value in ("playing", "paused", "buffering") for t in music.status.targets
            ):
                await music.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Music status refresh failed", exc_info=True)


def forms_label(source: str, face: int) -> str:
    """Human label of a cube face or joystick input for messages."""
    if source == JOYSTICK_SOURCE:
        return JOYSTICK_FACE_LABELS[face - 1].lower()
    return f"Seite {face}"


class LiveView:
    """Renders the fragments that SSE pushes and pages embed on first load."""

    def __init__(self, backend: WebBackend, templates: Jinja2Templates) -> None:
        self._backend = backend
        self._templates = templates

    def context(self) -> dict[str, Any]:
        """Template context shared by pages and live fragments."""
        backend = self._backend
        config = backend.settings.config.sense_hat
        service = backend.sense_hat
        statuses = service.statuses
        notices = build_notices(
            config,
            statuses,
            hardware_error=service.hardware_error,
            hardware_error_code=service.hardware_error_code,
            history_error=service.history_error,
            history_error_code=service.history_error_code,
            running=service.running,
        )
        views = build_group_views(config, statuses, backend.tz)
        cubes = backend.cubes
        cube_views = build_cube_views(
            backend.settings.config,
            cubes.statuses if cubes is not None else (),
            backend.tz,
        )
        cube_level, cube_text = cube_summary(cube_views)
        if cubes is None:
            cube_level, cube_text = "off", "Die Würfel-Steuerung ist in diesem Modus nicht aktiv."
        elif cubes.error:
            cube_level = "error"
        hue = backend.hue
        hue_view = build_hue_view(hue.status, backend.tz) if hue is not None else None
        return {
            "hue_active": hue is not None,
            "hue_view": hue_view,
            "hue_scenes": hue.scenes if hue is not None else (),
            "hue_groups": getattr(hue, "groups", ()) if hue is not None else (),
            "cubes_active": cubes is not None,
            "cube_views": cube_views,
            "battery_warnings": battery_warnings(cube_views),
            "cube_level": cube_level,
            "cube_text": cube_text,
            "cube_error": cubes.error if cubes is not None else None,
            "cube_error_code": cubes.error_code if cubes is not None else None,
            "mapping_views": build_mapping_views(backend.settings.config),
            "music_summary": build_music_summary(
                backend.music.status if backend.music is not None else None,
                len(backend.settings.config.music.playlists),
            ),
            "sense_config": config,
            "views": views,
            "notices": notices,
            "level": overall_level(notices),
            "sections": SECTIONS,
            "group_info": GROUP_INFO,
        }

    def fragments(self) -> dict[str, str]:
        """Rendered HTML keyed by the id of the element that shows it."""
        context = self.context()
        render = self._templates.get_template
        return {
            "live-cards": render("_cards.html").render(context),
            "live-notices": render("_notices.html").render(context),
            "live-notices-quiet": render("_notices.html").render({**context, "quiet_ok": True}),
            "live-mini": render("_mini.html").render(context),
            "live-badge": render("_badge.html").render(context),
            "live-cubes": render("_cubes.html").render(context),
            "live-cubes-mini": render("_cubes_mini.html").render(context),
            "live-hue": render("_hue.html").render(context),
            "live-hue-mini": render("_hue_mini.html").render(context),
            "live-music-mini": render("_music_mini.html").render(context),
        }

    async def stream(
        self,
        is_disconnected: Callable[[], Awaitable[bool]],
        *,
        interval: float = 1.0,
        heartbeat: float = 15.0,
        max_events: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield SSE messages when fragments change; a comment keeps proxies awake."""
        last = ""
        sent = 0
        idle = 0.0
        yield "retry: 3000\n\n"
        while not await is_disconnected():
            try:
                payload = json.dumps(self.fragments(), separators=(",", ":"))
            except Exception:
                logger.exception("Could not render live fragments")
                payload = last
            if payload and payload != last:
                last = payload
                idle = 0.0
                sent += 1
                yield f"event: fragments\ndata: {payload}\n\n"
                if max_events is not None and sent >= max_events:
                    return
            elif idle >= heartbeat:
                idle = 0.0
                yield ": keep-alive\n\n"
            await asyncio.sleep(interval)
            idle += interval


def create_app(backend: WebBackend, *, manage_service: bool = False) -> FastAPI:
    """Build the web application around an injected backend.

    With ``manage_service`` the Sense HAT service is started and stopped with the app
    (stopping flushes buffered history); tests usually manage it themselves.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        music_task: asyncio.Task[None] | None = None
        if manage_service:
            await backend.sense_hat.start()
            if backend.hue is not None:
                await backend.hue.start()
            if backend.matrix is not None:
                await backend.matrix.start()
            if backend.cubes is not None:
                await backend.cubes.start()
            if backend.music is not None:
                music_task = asyncio.get_running_loop().create_task(
                    _watch_music(backend.music), name="music-status"
                )
        try:
            yield
        finally:
            if music_task is not None:
                music_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await music_task
            if manage_service:
                if backend.cubes is not None:
                    await backend.cubes.stop()
                if backend.matrix is not None:
                    await backend.matrix.stop()
                if backend.music is not None:
                    await backend.music.close()
                await backend.sense_hat.stop()

    app = FastAPI(
        title="OctaPiHub", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    templates.env.globals["display_unit"] = display_unit
    live = LiveView(backend, templates)
    app.mount("/static", StaticFiles(directory=str(_PACKAGE_DIR / "static")), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Awaitable[Response]]):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def page(request: Request, name: str, active: str, status: int = 200, **extra: Any) -> Response:
        context = {
            "active": active,
            "now_text": local_time_text(backend.clock(), backend.tz),
            "live": True,
            **live.context(),
            **extra,
        }
        return templates.TemplateResponse(request, name, context, status_code=status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        known = {
            404: ("CH-WEB-001", "Diese Seite gibt es nicht."),
            405: ("CH-WEB-002", "Diese Aktion ist hier nicht erlaubt."),
        }
        code, message = known.get(exc.status_code, ("CH-WEB-000", "Die Anfrage war nicht gültig."))
        return page(
            request,
            "error.html",
            "",
            exc.status_code,
            error_code=code,
            error_message=message,
            live=False,
        )

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request) -> Response:
        return page(request, "overview.html", "overview")

    def mapping_context(
        values: dict[str, str], errors: dict[str, str], banner: dict[str, str] | None
    ) -> dict[str, Any]:
        scenes = {scene.id: scene.name for scene in backend.hue.scenes} if backend.hue else {}
        playlists = {p.id: p.name for p in backend.settings.config.music.playlists}
        return {
            "map_form": values,
            "map_errors": errors,
            "banner": banner,
            "writable": bool(backend.token),
            "tiles": build_cube_tiles(backend.settings.config, scenes, values, playlists),
            "scene_options": [{"id": sid, "name": name} for sid, name in scenes.items()],
            "music_groups": forms.music_choice_options(playlists) if backend.music else [],
            "light_groups": forms.hue_choice_options() if backend.hue else [],
            "lamp_groups": {g.id: g.name for g in getattr(backend.hue, "groups", ())}
            if backend.hue
            else {},
            "music_enabled": backend.settings.config.music.enabled,
            "music_targets": {t.id: t.name for t in backend.settings.config.music.targets},
            "option_shuffle": {"": "Wie die Playlist", "yes": "Zufällig", "no": "In Reihenfolge"},
            "option_repeat": {
                "": "Wie die Playlist",
                "off": "Nicht wiederholen",
                "all": "Alles wiederholen",
                "one": "Titel wiederholen",
            },
            "scenes_known": bool(scenes),
            **joystick_readiness(),
        }

    def joystick_readiness() -> dict[str, Any]:
        """Whether programmed joystick actions can actually be triggered, and if not, why."""
        matrix = backend.matrix
        if matrix is None:
            return {
                "joystick_ready": False,
                "joystick_hint": "Die LED-Anzeige ist in diesem Modus nicht aktiv.",
            }
        sense = backend.settings.config.sense_hat
        if not sense.joystick_enabled:
            return {
                "joystick_ready": False,
                "joystick_hint": "Der Joystick ist unter Einstellungen ausgeschaltet.",
            }
        if not sense.matrix.enabled:
            return {
                "joystick_ready": False,
                "joystick_hint": "Die LED-Anzeige ist unter Einstellungen ausgeschaltet.",
            }
        if "control" not in sense.matrix.views:
            return {
                "joystick_ready": False,
                "joystick_hint": "Aktivieren Sie die Ansicht „Steuerung“ unter Einstellungen.",
            }
        return {"joystick_ready": True, "joystick_hint": ""}

    def system_context(level: str, banner: dict[str, str] | None = None) -> dict[str, Any]:
        levels = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}
        if level not in levels:
            raise HTTPException(status_code=422, detail="Unknown log level")
        items = build_system_items(backend.system.read(), backend.tz) if backend.system else ()
        rows = build_log_rows(backend.logs.lines(levels[level]), backend.tz) if backend.logs else ()
        return {
            "live": False,
            "banner": banner,
            "writable": bool(backend.token),
            "system_items": items,
            "system_available": backend.system is not None,
            "log_rows": rows,
            "logs_available": backend.logs is not None,
            "log_level": level,
        }

    @app.post("/system/diagnostics")
    async def diagnostics(request: Request) -> Response:
        _fields, supplied = await _read_form(request)
        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            banner = {"level": "error", "title": title, "detail": detail, "code": code}
            return page(request, "system.html", "system", status, **system_context("INFO", banner))
        now = backend.clock()
        try:
            files = collect_diagnostics(backend, backend.redactor or Redactor(), now)
            data = build_zip(files, now)
        except OctaPiHubError as exc:
            banner = {
                "level": "error",
                "title": "Das Paket konnte nicht erstellt werden",
                "detail": exc.user_message,
                "code": exc.error_code,
            }
            return page(request, "system.html", "system", 500, **system_context("INFO", banner))
        except Exception:
            logger.exception("Unexpected failure while building diagnostics")
            banner = {
                "level": "error",
                "title": "Das Paket konnte nicht erstellt werden",
                "detail": "Unerwarteter Fehler. Details stehen im Protokoll.",
                "code": "CH-WEB-007",
            }
            return page(request, "system.html", "system", 500, **system_context("INFO", banner))
        name = f"octapihub-diagnose-{now.strftime('%Y%m%d-%H%M%S')}.zip"
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    def display_context() -> dict[str, Any]:
        matrix = backend.matrix
        if matrix is None:
            return {"matrix_active": False, "live": False}
        status = matrix.status
        enabled = set(matrix.controller.views)
        cards = [
            ViewCard(
                view.id,
                view.label,
                view.description,
                view.controls,
                view.id in enabled,
                view.id == status.view_id,
                frame_to_fills(matrix.thumbnail(view.id)),
            )
            for view in VIEWS.values()
        ]
        level, text = describe_display_state(status.state)
        sense = backend.sense_hat
        return {
            "live": False,
            "matrix_active": True,
            "matrix_status": status,
            "matrix_level": level,
            "matrix_text": text,
            "matrix_fills": frame_to_fills(status.frame),
            "view_cards": cards,
            "brightness_labels": forms.BRIGHTNESS_LABELS,
            "current_view_label": VIEWS[status.view_id].label,
            "hardware_error": sense.hardware_error,
            "hardware_error_code": sense.hardware_error_code,
            "writable": bool(backend.token),
            "joystick_on": backend.settings.config.sense_hat.joystick_enabled,
        }

    @app.get("/display", response_class=HTMLResponse)
    async def display_page(request: Request) -> Response:
        return page(request, "display.html", "display", **display_context())

    @app.post("/display/control")
    async def display_control(request: Request) -> JSONResponse:
        fields, supplied = await _read_form(request)
        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status_code, title, detail, code = problem
            return JSONResponse(
                {"ok": False, "title": title, "detail": detail, "code": code},
                status_code=status_code,
            )
        matrix = backend.matrix
        if matrix is None or matrix.status.state in {"disabled", "stopped"}:
            return JSONResponse(
                {
                    "ok": False,
                    "title": "Die LED-Anzeige ist nicht aktiv",
                    "detail": "Schalten Sie sie unter Einstellungen ein.",
                    "code": "CH-WEB-009",
                },
                status_code=409,
            )

        def bad(detail: str) -> JSONResponse:
            return JSONResponse(
                {"ok": False, "title": "Ungültige Eingabe", "detail": detail, "code": "CH-WEB-010"},
                status_code=422,
            )

        action = fields.get("action", "")
        value = fields.get("value", "")
        if action == "view":
            if not matrix.show_view(value):
                return bad("Diese Ansicht ist nicht aktiviert.")
        elif action == "brightness":
            if not value.isdigit() or int(value) not in forms.BRIGHTNESS_LABELS:
                return bad("Die Helligkeit muss eine der angebotenen Stufen sein.")
            matrix.set_brightness(int(value))
        elif action == "power":
            if value not in {"on", "off"}:
                return bad("Bitte Ein oder Aus wählen.")
            matrix.set_display(value == "on")
        elif action == "joystick":
            direction, _, kind = value.partition(":")
            try:
                joystick = JoystickDirection(direction)
            except ValueError:
                return bad("Unbekannte Joystick-Richtung.")
            if joystick in {JoystickDirection.ANY} or kind not in {"press", "long"}:
                return bad("Unbekannte Joystick-Eingabe.")
            state = matrix.current_state()
            if kind == "long" and joystick is JoystickDirection.MIDDLE:
                matrix.inject(joystick, JoystickAction.PRESSED, state)
                matrix.inject(joystick, JoystickAction.HELD, state)
                matrix.inject(joystick, JoystickAction.RELEASED, state)
            elif joystick is JoystickDirection.MIDDLE:
                matrix.inject(joystick, JoystickAction.PRESSED, state)
                matrix.inject(joystick, JoystickAction.RELEASED, state)
            else:
                matrix.inject(joystick, JoystickAction.PRESSED, state)
                matrix.inject(joystick, JoystickAction.RELEASED, state)
        else:
            return bad("Unbekannte Aktion.")
        status = matrix.status
        return JSONResponse(
            {
                "ok": True,
                "view": status.view_id,
                "label": VIEWS[status.view_id].label,
                "display_on": status.display_on,
                "brightness": status.brightness,
            }
        )

    @app.get("/events/display")
    async def display_events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            display_stream(backend, request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/system", response_class=HTMLResponse)
    def system_page(request: Request, level: str = "INFO") -> Response:
        return page(request, "system.html", "system", **system_context(level))

    @app.get("/cubes", response_class=HTMLResponse)
    def cubes_page(request: Request) -> Response:
        values = forms.mapping_config_to_form(backend.settings.config)
        return page(request, "cubes.html", "cubes", **mapping_context(values, {}, None))

    @app.post("/cubes/trigger", response_class=HTMLResponse)
    async def trigger_action(request: Request) -> Response:
        submitted, supplied = await _read_form(request)
        errors: dict[str, str] = {}

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            values = forms.mapping_config_to_form(backend.settings.config)
            return page(
                request, "cubes.html", "cubes", status, **mapping_context(values, errors, banner)
            )

        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        sources = forms.mapping_sources(backend.settings.config)
        index_text, _, face_text = submitted.get("trigger", "").partition(".")
        if not (index_text.isdigit() and face_text.isdigit()):
            return respond(422, "error", "Ungültige Auswahl", "", "CH-WEB-011")
        index, face = int(index_text), int(face_text)
        if index >= len(sources) or face not in sources[index][1]:
            return respond(422, "error", "Ungültige Auswahl", "", "CH-WEB-011")
        name = sources[index][0]
        if backend.cubes is None:
            return respond(
                409,
                "warn",
                "Die Aktionssteuerung ist in diesem Modus nicht aktiv",
                "Starten Sie die Oberfläche mit einer Konfigurationsdatei.",
                "CH-WEB-012",
            )
        if backend.cubes.trigger(name, face) is None:
            return respond(
                409,
                "warn",
                "Es wurde nichts ausgelöst",
                "Für diese Seite ist keine Aktion gespeichert, oder die Aktionssteuerung "
                "läuft nicht.",
                "CH-WEB-013",
            )
        return respond(
            200,
            "ok",
            "Aktion ausgelöst",
            f"„{name}“, {forms_label(name, face)}: die Aktion wird gleich ausgeführt.",
        )

    @app.post("/cubes/mapping", response_class=HTMLResponse)
    async def save_mapping(request: Request) -> Response:
        submitted, supplied = await _read_form(request)
        errors: dict[str, str] = {}

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request, "cubes.html", "cubes", status, **mapping_context(submitted, errors, banner)
            )

        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        known = {scene.id for scene in backend.hue.scenes} if backend.hue else set()
        music_config = backend.settings.config.music
        playlist_ids = {p.id for p in music_config.playlists}
        target_ids = {t.id for t in music_config.targets}
        result = forms.parse_mapping_form(
            submitted,
            backend.settings.config,
            known,
            playlist_ids,
            target_ids,
            {g.id for g in getattr(backend.hue, "groups", ())} if backend.hue else (),
        )
        if result.mappings is None:
            errors.update(result.errors)
            return respond(
                422,
                "error",
                "Bitte korrigieren Sie die markierten Würfelseiten",
                "Es wurde nichts gespeichert.",
            )
        try:
            await backend.settings.apply_mappings(result.mappings)
        except ValidationError as exc:
            return respond(
                422, "error", "Die Zuordnung ist ungültig", exc.user_message, exc.error_code
            )
        except OctaPiHubError as exc:
            logger.error("Could not apply mappings: %s", exc)
            return respond(
                500, "error", "Speichern fehlgeschlagen", exc.user_message, exc.error_code
            )
        submitted.update(forms.mapping_config_to_form(backend.settings.config))
        return respond(
            200,
            "ok",
            "Zuordnung gespeichert",
            "Sie gilt sofort, die Würfel müssen nicht neu verbunden werden.",
        )

    register_music_routes(app, backend, page, _read_form, _access_problem)

    async def read_backup_form(request: Request) -> tuple[dict[str, str], str]:
        return await _read_form(request, BACKUP_FORM_BYTES)

    register_backup_routes(
        app, backend, page, read_backup_form, _access_problem, dict(SERIES_RANGES)
    )

    @app.get("/sense-hat", response_class=HTMLResponse)
    def sense_hat_page(request: Request) -> Response:
        config = backend.settings.config.sense_hat
        charts = chart_groups(config)
        return page(
            request,
            "sense_hat.html",
            "sense-hat",
            chart_options=[{"group": g, "label": GROUP_INFO[g].label, "unit": ""} for g in charts],
            ranges=[{"key": k, "label": v[0]} for k, v in SERIES_RANGES.items()],
            history_on=config.history.enabled,
            correction=correction_active(config),
        )

    def settings_context(
        values: dict[str, str],
        errors: dict[str, str],
        banner: dict[str, str] | None,
    ) -> dict[str, Any]:
        config = backend.settings.config.sense_hat
        return {
            "form": values,
            "errors": errors,
            "banner": banner,
            "writable": bool(backend.token),
            "hardware_text": describe_hardware(backend.sense_hat.capabilities, config),
            "capabilities": backend.sense_hat.capabilities,
            "interval_labels": forms.INTERVAL_LABELS,
            "retention_labels": forms.RETENTION_LABELS,
            "precision_labels": forms.PRECISION_LABELS,
            "presentation_labels": forms.PRESENTATION_LABELS,
            "version_labels": forms.VERSION_LABELS,
            "gain_labels": forms.GAIN_LABELS,
            "rotation_labels": forms.ROTATION_LABELS,
            "brightness_labels": forms.BRIGHTNESS_LABELS,
            "cycle_labels": forms.CYCLE_LABELS,
            "night_mode_labels": forms.NIGHT_MODE_LABELS,
            "view_catalog": [
                {"id": view.id, "label": view.label, "description": view.description}
                for view in VIEWS.values()
            ],
            "history_path": config.history.database_path,
            "live": False,
        }

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> Response:
        values = forms.config_to_form(backend.settings.config.sense_hat)
        return page(request, "settings.html", "settings", **settings_context(values, {}, None))

    @app.post("/settings/sense-hat", response_class=HTMLResponse)
    async def save_sense_hat(request: Request) -> Response:
        submitted, supplied = await _read_form(request)

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request,
                "settings.html",
                "settings",
                status,
                **settings_context(submitted, errors_holder, banner),
            )

        errors_holder: dict[str, str] = {}
        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        result = forms.parse_form(submitted, backend.settings.config.sense_hat)
        if result.table is None:
            errors_holder.update(result.errors)
            return respond(
                422,
                "error",
                "Bitte korrigieren Sie die markierten Felder",
                "Es wurde nichts gespeichert.",
            )
        try:
            await backend.settings.apply_sense_hat(result.table)
        except ValidationError as exc:
            return respond(
                422, "error", "Die Einstellungen sind ungültig", exc.user_message, exc.error_code
            )
        except (ConfigurationError, OctaPiHubError) as exc:
            logger.exception("Could not save settings")
            return respond(
                500, "error", "Speichern fehlgeschlagen", exc.user_message, exc.error_code
            )
        submitted = forms.config_to_form(backend.settings.config.sense_hat)
        return respond(
            200,
            "ok",
            "Gespeichert und angewendet",
            "Die Messung läuft mit den neuen Einstellungen.",
        )

    def light_context(
        values: dict[str, str], errors: dict[str, str], banner: dict[str, str] | None
    ) -> dict[str, Any]:
        return {
            "form": values,
            "errors": errors,
            "banner": banner,
            "writable": bool(backend.token),
        }

    @app.get("/light", response_class=HTMLResponse)
    def light_page(request: Request) -> Response:
        values = forms.hue_config_to_form(backend.settings.config.hue)
        return page(request, "light.html", "light", **light_context(values, {}, None))

    @app.post("/light", response_class=HTMLResponse)
    async def light_action(request: Request) -> Response:
        submitted, supplied = await _read_form(request)
        action = submitted.get("action", "")
        errors: dict[str, str] = {}

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request, "light.html", "light", status, **light_context(submitted, errors, banner)
            )

        problem = _access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        hue = backend.hue
        if hue is None:
            return respond(
                503,
                "error",
                "Hue ist in diesem Modus nicht verfügbar",
                "Starten Sie die Oberfläche mit einer Konfigurationsdatei.",
                "CH-WEB-005",
            )
        try:
            if action in {"save", "pair"}:
                result = forms.parse_hue_form(submitted, backend.settings.config)
                if result.table is None:
                    errors.update(result.errors)
                    return respond(
                        422,
                        "error",
                        "Bitte korrigieren Sie die markierten Felder",
                        errors.get(forms.FORM_ERROR_KEY, "Es wurde nichts gespeichert."),
                    )
                if result.table != _current_hue_table():
                    await backend.settings.apply_hue(result.table)
                if action == "save":
                    submitted.update(forms.hue_config_to_form(backend.settings.config.hue))
                    return respond(200, "ok", "Gespeichert und angewendet")
                await hue.pair()
                submitted.update(forms.hue_config_to_form(backend.settings.config.hue))
                try:
                    await hue.refresh_scenes()
                except OctaPiHubError:
                    logger.warning("Scenes could not be read right after pairing")
                return respond(
                    200,
                    "ok",
                    "Mit der Bridge verbunden",
                    "Die Würfel können jetzt Hue-Szenen schalten.",
                )
            if action == "refresh":
                scenes = await hue.refresh_scenes()
                return respond(200, "ok", f"{len(scenes)} Szenen geladen")
            if action == "unpair":
                if not submitted.get("confirm_unpair"):
                    return respond(
                        422,
                        "warn",
                        "Bitte bestätigen Sie das Trennen",
                        "Setzen Sie das Häkchen bei „Ja, Verbindung trennen“.",
                    )
                await hue.unpair()
                return respond(200, "ok", "Verbindung getrennt")
            if action.startswith("light:"):
                _, _, rest = action.partition(":")
                name, _, group_id = rest.partition(":")
                known = {g.id for g in getattr(hue, "groups", ())}
                light = forms.hue_choice_action(f"{forms.HUE_PREFIX}{name}", group_id)
                if light is None or group_id not in known:
                    return respond(
                        422,
                        "error",
                        "Diese Auswahl ist ungültig",
                        "Laden Sie die Szenen und Räume neu und versuchen Sie es erneut.",
                        "CH-WEB-011",
                    )
                params = light["parameters"]
                assert isinstance(params, dict)
                await hue.execute(str(light["action"]), params)
                return respond(200, "ok", "Erledigt")
            if action.startswith("test:"):
                scene_id = action.removeprefix("test:")
                if scene_id not in {scene.id for scene in hue.scenes}:
                    return respond(
                        422,
                        "error",
                        "Diese Szene ist nicht bekannt",
                        "Laden Sie die Szenen neu und versuchen Sie es erneut.",
                    )
                await hue.execute("scene.activate", {"scene_id": scene_id})
                return respond(200, "ok", "Szene wurde aktiviert")
            return respond(422, "error", "Unbekannte Aktion", "", "CH-WEB-006")
        except HueLinkButtonError as exc:
            return respond(
                409,
                "warn",
                "Die Taste an der Bridge wurde nicht gedrückt",
                exc.user_message,
                exc.error_code,
            )
        except HueNotConfiguredError as exc:
            return respond(
                409, "warn", "Hue ist noch nicht eingerichtet", exc.user_message, exc.error_code
            )
        except ValidationError as exc:
            return respond(
                422, "error", "Die Eingabe ist ungültig", exc.user_message, exc.error_code
            )
        except (ConfigurationError, OctaPiHubError) as exc:
            logger.error("Hue action %s failed: %s", action.split(":")[0], exc)
            status = 502 if exc.error_code.startswith("CH-HUE") else 500
            return respond(
                status, "error", "Das hat nicht geklappt", exc.user_message, exc.error_code
            )

    def _current_hue_table() -> dict[str, object]:
        from octapihub.core.config import hue_table

        return hue_table(backend.settings.config.hue)

    @app.get("/api/sense-hat/series/{group}")
    def series(group: str, range: str = "24h", basis: str = "corrected") -> JSONResponse:
        config = backend.settings.config.sense_hat
        if group not in CHARTABLE_GROUPS or group not in config.sensors:
            raise HTTPException(status_code=404, detail="Unknown chartable group")
        if range not in SERIES_RANGES:
            raise HTTPException(status_code=422, detail="Unknown range")
        label, window, bucket = SERIES_RANGES[range]
        until = backend.clock()
        use_corrected = group == "temperature" and basis != "raw"
        sensor = config.sensors[group]
        available = config.history.enabled and sensor.history_enabled
        reason = ""
        points: list[dict[str, float]] = []
        if not available:
            reason = "history_disabled"
        elif backend.sense_hat.history_error is not None or not backend.sense_hat.running:
            available = False
            reason = "history_unavailable"
        else:
            try:
                rows = backend.sense_hat.query_series(
                    group, until - window, until, bucket_seconds=bucket, corrected=use_corrected
                )
            except OctaPiHubError as exc:
                logger.error("Series query failed: %s", exc)
                return JSONResponse(
                    {"error": exc.user_message, "code": exc.error_code}, status_code=503
                )
            points = [{"t": int(p.timestamp.timestamp() * 1000), "v": p.value} for p in rows]
        info = _CHART_HINTS[group]
        unit = {"temperature": "°C", "humidity": "%", "pressure": "hPa", "compass": "°"}[group]
        return JSONResponse(
            {
                "group": group,
                "label": GROUP_INFO[group].label,
                "unit": unit,
                "precision": sensor.precision,
                "range": range,
                "range_label": label,
                "bucket_seconds": bucket,
                "since": int((until - window).timestamp() * 1000),
                "until": int(until.timestamp() * 1000),
                "basis": "corrected" if use_corrected else "raw",
                "correction_active": group == "temperature" and correction_active(config),
                "available": available,
                "reason": reason,
                "min_span": info["min_span"],
                "fixed": info["fixed"],
                "points": points,
            }
        )

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            live.stream(request.is_disconnected),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.api_route("/media/{token}", methods=["GET", "HEAD"])
    def media_file(token: str) -> Response:
        """Serve a music file to a Cast device; the unguessable token is the only access."""
        path = backend.media.resolve(token) if backend.media is not None else None
        if path is None:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type=content_type_for(path.name))

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "sense_hat_running": backend.sense_hat.running,
            "sense_hat_error": backend.sense_hat.hardware_error_code,
            "hue_state": backend.hue.state.value if backend.hue is not None else None,
            "cubes_running": backend.cubes.running if backend.cubes is not None else None,
        }

    return app
