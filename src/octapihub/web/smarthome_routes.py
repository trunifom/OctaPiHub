"""The Smart-Home page: connections, actions, secrets, and test buttons."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from octapihub.core.errors import (
    OctaPiHubError,
    SmartHomeConnectionError,
    SmartHomeNotConfiguredError,
    SmartHomeRejectedError,
    ValidationError,
)
from octapihub.web import smarthome_forms as sf

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

logger = logging.getLogger(__name__)

AccessProblem = Callable[[str | None, str], tuple[int, str, str, str] | None]
ReadForm = Callable[[Request], Awaitable[tuple[dict[str, str], str]]]
Page = Callable[..., Response]


def register_smarthome_routes(
    app: FastAPI,
    backend: WebBackend,
    page: Page,
    read_form: ReadForm,
    access_problem: AccessProblem,
) -> None:
    """Add ``GET /smarthome`` and ``POST /smarthome``."""

    def context(
        values: dict[str, str], errors: dict[str, str], banner: dict[str, str] | None
    ) -> dict[str, Any]:
        values = sf.with_blank_rows(dict(values))
        saved = backend.settings.config.smarthome
        service = backend.smarthome
        results = service.results if service is not None else {}
        saved_actions = {a.id for a in saved.actions}
        saved_connections = {c.id: c for c in saved.connections}
        return {
            "form": values,
            "errors": errors,
            "banner": banner,
            "writable": bool(backend.token),
            "available": service is not None,
            "connection_rows": [
                {
                    "key": key,
                    "has_secret": bool(
                        service and service.has_secret(values.get(f"c.{key}.id", ""))
                    ),
                    "saved": values.get(f"c.{key}.id", "") in saved_connections,
                }
                for key in sf_row_keys(values, "c")
            ],
            "action_rows": [
                {
                    "key": key,
                    "saved": values.get(f"a.{key}.id", "") in saved_actions,
                    "result": results.get(values.get(f"a.{key}.id", "")),
                }
                for key in sf_row_keys(values, "a")
            ],
            "connection_options": {c.id: c.name for c in saved.connections},
            "connection_kinds": {c.id: c.kind.value for c in saved.connections},
            "connection_kind_labels": sf.CONNECTION_KIND_LABELS,
            "action_kind_labels": sf.ACTION_KIND_LABELS,
            "method_labels": sf.METHOD_LABELS,
            "content_type_labels": sf.CONTENT_TYPE_LABELS,
            "qos_labels": sf.QOS_LABELS,
            "live": False,
        }

    def sf_row_keys(values: dict[str, str], group: str) -> list[str]:
        from octapihub.web.music_forms import row_keys

        return row_keys(values, group)

    def current_form() -> dict[str, str]:
        return sf.smarthome_to_form(backend.settings.config.smarthome)

    @app.get("/smarthome", response_class=HTMLResponse)
    def smarthome_page(request: Request) -> Response:
        return page(request, "smarthome.html", "smarthome", **context(current_form(), {}, None))

    @app.post("/smarthome", response_class=HTMLResponse)
    async def smarthome_action(request: Request) -> Response:
        submitted, supplied = await read_form(request)
        action = submitted.get("action", "")
        errors: dict[str, str] = {}

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request, "smarthome.html", "smarthome", status, **context(submitted, errors, banner)
            )

        problem = access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        service = backend.smarthome
        if service is None:
            return respond(
                503,
                "error",
                "Smart Home ist in diesem Modus nicht verfügbar",
                "Starten Sie die Oberfläche mit einer Konfigurationsdatei.",
                "CH-WEB-019",
            )
        try:
            if action == "save":
                result = sf.parse_smarthome_form(submitted, backend.settings.config)
                if result.table is None:
                    errors.update(result.errors)
                    return respond(
                        422,
                        "error",
                        "Bitte korrigieren Sie die markierten Felder",
                        errors.get(sf.FORM_ERROR_KEY, "Es wurde nichts gespeichert."),
                    )
                await backend.settings.apply_smarthome(result.table, result.secrets)
                submitted.clear()
                submitted.update(current_form())
                return respond(200, "ok", "Smart-Home-Einstellungen gespeichert und angewendet")
            if action.startswith("test:"):
                action_id = action.removeprefix("test:")
                if action_id not in {a.id for a in backend.settings.config.smarthome.actions}:
                    return respond(
                        422,
                        "error",
                        "Diese Aktion ist nicht gespeichert",
                        "Speichern Sie zuerst Ihre Änderungen.",
                        "CH-WEB-011",
                    )
                await service.run(action_id)
                return respond(200, "ok", "Gesendet", "Das Gerät hat die Anfrage angenommen.")
            return respond(422, "error", "Unbekannte Aktion", "", "CH-WEB-006")
        except ValidationError as exc:
            return respond(
                422, "error", "Die Eingabe ist ungültig", exc.user_message, exc.error_code
            )
        except SmartHomeNotConfiguredError as exc:
            return respond(409, "warn", "Noch nicht bereit", exc.user_message, exc.error_code)
        except SmartHomeConnectionError as exc:
            return respond(502, "error", "Nicht erreichbar", exc.user_message, exc.error_code)
        except SmartHomeRejectedError as exc:
            return respond(502, "error", "Abgelehnt", exc.user_message, exc.error_code)
        except OctaPiHubError as exc:
            logger.error("Smart-home action %s failed: %s", action.split(":")[0], exc)
            return respond(500, "error", "Das hat nicht geklappt", exc.user_message, exc.error_code)
