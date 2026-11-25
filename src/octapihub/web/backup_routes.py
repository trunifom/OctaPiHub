"""Backup page: export and import of settings, sensor data as CSV, and the log files."""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from octapihub.application import backup
from octapihub.core.errors import ConfigurationError, OctaPiHubError, ValidationError
from octapihub.core.logging_support import Redactor, read_log_file

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

logger = logging.getLogger(__name__)

AccessProblem = Callable[[str | None, str], tuple[int, str, str, str] | None]
ReadForm = Callable[[Request], Awaitable[tuple[dict[str, str], str]]]
Page = Callable[..., Response]

CSV_GROUPS = ("temperature", "humidity", "pressure", "compass")
RAW_MAX_HOURS = 24
LEVEL_NUMBERS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR}


def _attachment(name: str) -> str:
    return f'attachment; filename="{name}"'


def register_backup_routes(
    app: FastAPI,
    backend: WebBackend,
    page: Page,
    read_form: ReadForm,
    access_problem: AccessProblem,
    ranges: dict[str, tuple[str, Any, int]],
) -> None:
    """Add ``/backup`` and the download routes to ``app``."""

    def context(
        form: dict[str, str],
        banner: dict[str, str] | None,
        previews: list[backup.SectionPreview] | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        config = backend.settings.config
        return {
            "form": form,
            "banner": banner,
            "writable": bool(backend.token),
            "sections": backup.SECTION_LABELS,
            "previews": previews or [],
            "notes": notes or [],
            "csv_groups": [g for g in CSV_GROUPS if g in config.sense_hat.sensors],
            "ranges": {k: v[0] for k, v in ranges.items()},
            "history_on": config.sense_hat.history.enabled,
            "log_file": bool(backend.log_dir),
            "live": False,
        }

    def default_form() -> dict[str, str]:
        return {f"sec.{key}": "on" for key in backup.SECTION_LABELS if key != "playlists"}

    @app.get("/backup", response_class=HTMLResponse)
    def backup_page(request: Request) -> Response:
        return page(request, "backup.html", "settings", **context(default_form(), None))

    @app.post("/backup", response_class=HTMLResponse)
    async def backup_action(request: Request) -> Response:
        submitted, supplied = await read_form(request)
        action = submitted.get("action", "")
        previews: list[backup.SectionPreview] = []
        notes: list[str] = []

        def respond(
            status: int, level: str, title: str, detail: str = "", code: str = ""
        ) -> Response:
            banner = {"level": level, "title": title, "detail": detail, "code": code}
            return page(
                request,
                "backup.html",
                "settings",
                status,
                **context(submitted, banner, previews, notes),
            )

        problem = access_problem(backend.token, supplied)
        if problem is not None:
            status, title, detail, code = problem
            return respond(status, "error", title, detail, code)
        chosen = [key for key in backup.SECTION_LABELS if submitted.get(f"sec.{key}")]
        try:
            if action == "export":
                text = backup.build_bundle(backend.settings.config, chosen, backend.clock())
                stamp = backend.clock().astimezone(backend.tz).strftime("%Y%m%d-%H%M")
                return Response(
                    text,
                    media_type="application/json",
                    headers={"Content-Disposition": _attachment(f"octapihub-export-{stamp}.json")},
                )
            if action in {"import-check", "import-apply"}:
                text = submitted.get("bundle", "").strip()
                if not text:
                    return respond(
                        422,
                        "warn",
                        "Es wurde nichts eingefügt",
                        "Wählen Sie eine Export-Datei aus oder fügen Sie ihren Inhalt ein.",
                    )
                bundle = backup.parse_bundle(text)
                previews.extend(backup.preview_bundle(backend.settings.config, bundle))
                if action == "import-check":
                    valid = [p for p in previews if p.valid]
                    for p in valid:
                        submitted[f"imp.{p.key}"] = "on"
                    detail = "Wählen Sie unten, was übernommen werden soll."
                    if bundle.ignored:
                        detail += " Unbekannte Bereiche werden ignoriert: " + ", ".join(
                            bundle.ignored
                        )
                    if not valid:
                        return respond(
                            422, "error", "Nichts davon passt zu dieser Installation", detail
                        )
                    return respond(200, "ok", "Datei gelesen", detail)
                picked = [p.key for p in previews if submitted.get(f"imp.{p.key}")]
                invalid = [p.key for p in previews if p.key in picked and not p.valid]
                if not picked or invalid:
                    return respond(
                        422,
                        "warn",
                        "Bitte wählen Sie mindestens einen passenden Bereich",
                        "Bereiche mit Fehlern können nicht übernommen werden.",
                    )
                notes.extend(await backend.settings.apply_bundle(bundle, picked))
                submitted.pop("bundle", None)
                previews.clear()
                return respond(
                    200,
                    "ok",
                    "Einstellungen übernommen",
                    "Die bisherigen Einstellungen wurden als Sicherungskopie neben der "
                    "Konfigurationsdatei abgelegt.",
                )
            return respond(422, "error", "Unbekannte Aktion", "", "CH-WEB-006")
        except ValidationError as exc:
            return respond(422, "error", "Das geht so nicht", exc.user_message, exc.error_code)
        except ConfigurationError as exc:
            logger.error("Backup action %s failed: %s", action, exc)
            return respond(
                500, "error", "Speichern fehlgeschlagen", exc.user_message, exc.error_code
            )
        except OctaPiHubError as exc:
            logger.error("Backup action %s failed: %s", action, exc)
            return respond(500, "error", "Das hat nicht geklappt", exc.user_message, exc.error_code)

    @app.get("/export/sensor-data.csv")
    def sensor_csv(group: str = "all", range: str = "24h", resolution: str = "average") -> Response:
        config = backend.settings.config.sense_hat
        groups = [g for g in CSV_GROUPS if g in config.sensors]
        if group != "all":
            if group not in groups:
                raise HTTPException(status_code=404, detail="Unknown group")
            groups = [group]
        if range not in ranges:
            raise HTTPException(status_code=422, detail="Unknown range")
        if resolution not in {"average", "raw"}:
            raise HTTPException(status_code=422, detail="Unknown resolution")
        _label, window, bucket = ranges[range]
        if resolution == "raw" and window.total_seconds() > RAW_MAX_HOURS * 3600:
            raise HTTPException(
                status_code=422, detail="Raw values are only available up to 24 hours"
            )
        if not config.history.enabled or backend.sense_hat.history_error is not None:
            raise HTTPException(status_code=503, detail="History is not available")
        until = backend.clock()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["zeit_utc", "messgroesse", "wert", "wert_korrigiert", "anzahl_messungen"])
        try:
            for name in groups:
                bucket_seconds = None if resolution == "raw" else bucket
                raw = backend.sense_hat.query_series(
                    name, until - window, until, bucket_seconds=bucket_seconds
                )
                corrected = {}
                if name == "temperature":
                    corrected = {
                        p.timestamp: p.value
                        for p in backend.sense_hat.query_series(
                            name,
                            until - window,
                            until,
                            bucket_seconds=bucket_seconds,
                            corrected=True,
                        )
                    }
                for point in raw:
                    writer.writerow(
                        [
                            point.timestamp.astimezone(UTC).isoformat(timespec="seconds"),
                            name,
                            f"{point.value:.4f}",
                            f"{corrected[point.timestamp]:.4f}"
                            if point.timestamp in corrected
                            else "",
                            point.count,
                        ]
                    )
        except OctaPiHubError as exc:
            logger.error("Sensor export failed: %s", exc)
            raise HTTPException(status_code=503, detail=exc.user_message) from exc
        stamp = until.astimezone(backend.tz).strftime("%Y%m%d-%H%M")
        return Response(
            "\ufeff" + out.getvalue(),  # BOM: Excel reads the umlauts correctly
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="octapihub-sensoren-{stamp}.csv"'
            },
        )

    @app.get("/export/log.txt")
    def log_text(source: str = "recent", level: str = "INFO") -> Response:
        if level not in LEVEL_NUMBERS:
            raise HTTPException(status_code=422, detail="Unknown level")
        redactor = backend.redactor or Redactor()
        if source == "file":
            if not backend.log_dir:
                raise HTTPException(status_code=404, detail="No log file")
            text = redactor.redact(read_log_file(backend.log_dir))
        elif source == "recent":
            if backend.logs is None:
                raise HTTPException(status_code=404, detail="No log")
            lines = backend.logs.lines(LEVEL_NUMBERS[level], limit=500)
            text = "\n".join(
                f"{line.time.astimezone(backend.tz).isoformat(timespec='seconds')} "
                f"{line.level} {line.logger}: {line.message}"
                for line in reversed(lines)
            )
        else:
            raise HTTPException(status_code=422, detail="Unknown source")
        stamp = datetime.now(UTC).astimezone(backend.tz).strftime("%Y%m%d-%H%M")
        return PlainTextResponse(
            text + "\n",
            headers={"Content-Disposition": f'attachment; filename="octapihub-log-{stamp}.txt"'},
        )
