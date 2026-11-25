"""The Musik page: devices, playlists, playback controls and discovery."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from octapihub.application.music.service import MusicAction
from octapihub.core.config import music_table
from octapihub.core.errors import (
    MusicNotConfiguredError,
    MusicSourceError,
    OctaPiHubError,
    ValidationError,
)
from octapihub.web import music_forms

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

logger = logging.getLogger(__name__)

AccessProblem = Callable[[str | None, str], tuple[int, str, str, str] | None]
ReadForm = Callable[[Request], Awaitable[tuple[dict[str, str], str]]]
Page = Callable[..., Response]

CONTROLS = {
    "pause": MusicAction.PAUSE,
    "resume": MusicAction.RESUME,
    "toggle": MusicAction.TOGGLE,
    "stop": MusicAction.STOP,
    "next": MusicAction.NEXT,
    "previous": MusicAction.PREVIOUS,
    "volume.up": MusicAction.VOLUME_UP,
    "volume.down": MusicAction.VOLUME_DOWN,
}


def register_music_routes(
    app: FastAPI,
    backend: WebBackend,
    page: Page,
    read_form: ReadForm,
    access_problem: AccessProblem,
) -> None:
    """Add ``GET /music`` and ``POST /music`` to ``app``."""

    def context(
        values: dict[str, str],
        errors: dict[str, str],
        banner: dict[str, str] | None,
        found: list[str] | None = None,
        devices: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        values = music_forms.with_blank_rows(dict(values))
        saved = backend.settings.config.music
        status = backend.music.status if backend.music is not None else None
        by_id = {t.id: t for t in status.targets} if status else {}
        saved_playlists = {p.id for p in saved.playlists}
        return {
            "form": values,
            "errors": errors,
            "banner": banner,
            "writable": bool(backend.token),
            "music_available": backend.music is not None,
            "target_rows": [
                {"key": key, "status": by_id.get(values.get(f"t.{key}.id", ""))}
                for key in music_forms.row_keys(values, "t")
            ],
            "playlist_rows": [
                {"key": key, "saved": values.get(f"p.{key}.id", "") in saved_playlists}
                for key in music_forms.row_keys(values, "p")
            ],
            "target_options": {t.id: t.name for t in saved.targets},
            "kind_labels": music_forms.TARGET_KIND_LABELS,
            "repeat_labels": music_forms.REPEAT_LABELS,
            "music_enabled": saved.enabled,
            "found_devices": found or [],
            "audio_devices": devices or [],
            "live": False,
        }

    def current_form() -> dict[str, str]:
        return music_forms.music_to_form(backend.settings.config.music)

    @app.get("/music", response_class=HTMLResponse)
    def music_page(request: Request) -> Response:
        return page(request, "music.html", "music", **context(current_form(), {}, None))

    @app.post("/music", response_class=HTMLResponse)
    async def music_action(request: Request) -> Response:
        submitted, supplied = await read_form(request)
        action = submitted.get("action", "")
        errors: dict[str, str] = {}
        extra: dict[str, Any] = {}

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request,
                "music.html",
                "music",
                status,
                **context(submitted, errors, banner, **extra),
            )

        problem = access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        try:
            return await handle(action, submitted, errors, extra, respond)
        except ValidationError as exc:
            return respond(
                422, "error", "Die Eingabe ist ungültig", exc.user_message, exc.error_code
            )
        except MusicSourceError as exc:
            return respond(
                422, "error", "Diese Quelle geht nicht", exc.user_message, exc.error_code
            )
        except MusicNotConfiguredError as exc:
            return respond(
                409, "warn", "Musik ist noch nicht bereit", exc.user_message, exc.error_code
            )
        except OctaPiHubError as exc:
            logger.error("Music action %s failed: %s", action.split(":")[0], exc)
            status = 502 if exc.error_code.startswith("CH-MUS") else 500
            return respond(
                status, "error", "Das hat nicht geklappt", exc.user_message, exc.error_code
            )

    async def handle(
        action: str,
        submitted: dict[str, str],
        errors: dict[str, str],
        extra: dict[str, Any],
        respond: Callable[..., Response],
    ) -> Response:
        music = backend.music
        if music is None:
            return respond(
                503,
                "error",
                "Musik ist in diesem Modus nicht verfügbar",
                "Starten Sie die Oberfläche mit einer Konfigurationsdatei.",
                "CH-WEB-014",
            )
        if action == "save":
            result = music_forms.parse_music_form(submitted, backend.settings.config)
            if result.config is None:
                errors.update(result.errors)
                return respond(
                    422,
                    "error",
                    "Bitte korrigieren Sie die markierten Felder",
                    errors.get(music_forms.FORM_ERROR_KEY, "Es wurde nichts gespeichert."),
                )
            await backend.settings.apply_music(music_table(result.config.music))
            submitted.clear()
            submitted.update(current_form())
            return respond(200, "ok", "Musik-Einstellungen gespeichert und angewendet")
        if action == "refresh":
            await music.refresh()
            return respond(200, "ok", "Status aktualisiert")
        if action == "discover":
            if backend.discover_cast is None:
                return respond(503, "warn", "Die Suche ist nicht verfügbar", "", "CH-WEB-014")
            extra["found"] = await backend.discover_cast()
            if not extra["found"]:
                return respond(
                    200,
                    "warn",
                    "Keine Google-Geräte gefunden",
                    "Prüfen Sie, ob die Lautsprecher eingeschaltet und im selben WLAN sind, "
                    "und ob das Paket „pychromecast“ installiert ist.",
                )
            return respond(200, "ok", f"{len(extra['found'])} Gerät(e) gefunden")
        if action == "audio-devices":
            if backend.list_audio_devices is None:
                return respond(503, "warn", "Die Suche ist nicht verfügbar", "", "CH-WEB-014")
            extra["devices"] = await backend.list_audio_devices()
            if not extra["devices"]:
                return respond(
                    200,
                    "warn",
                    "Keine Audiogeräte gefunden",
                    "Ist „mpv“ installiert? (sudo apt install mpv)",
                )
            return respond(200, "ok", f"{len(extra['devices'])} Audiogerät(e) gefunden")
        if action == "stop-all":
            await music.execute(MusicAction.STOP_ALL.value, {})
            return respond(200, "ok", "Alle Wiedergaben gestoppt")
        if action.startswith("ctl:"):
            _, _, rest = action.partition(":")
            target_id, _, operation = rest.rpartition(":")
            known = {t.id for t in backend.settings.config.music.targets}
            if target_id not in known or operation not in CONTROLS:
                return respond(422, "error", "Ungültige Auswahl", "", "CH-WEB-011")
            await music.execute(CONTROLS[operation].value, {"target": target_id})
            return respond(200, "ok", "Erledigt")
        if action.startswith("play:"):
            playlist_id = action.removeprefix("play:")
            if playlist_id not in {p.id for p in backend.settings.config.music.playlists}:
                return respond(
                    422,
                    "error",
                    "Diese Playlist ist nicht gespeichert",
                    "Speichern Sie zuerst Ihre Änderungen.",
                    "CH-WEB-011",
                )
            await music.execute(MusicAction.PLAY.value, {"playlist": playlist_id})
            return respond(200, "ok", "Wiedergabe gestartet")
        return respond(422, "error", "Unbekannte Aktion", "", "CH-WEB-006")
