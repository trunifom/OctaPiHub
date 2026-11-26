"""The first-setup guide: what is already done, and what to do next (cube, light, music, a face).

The steps are derived from the real state (nothing is stored), so the guide is always right,
also after a restore or when a step is done on another page.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

if TYPE_CHECKING:
    from octapihub.web.app import WebBackend

Page = Callable[..., Response]


@dataclass(frozen=True, slots=True)
class SetupStep:
    """One step of the guide."""

    key: str
    title: str
    done: bool
    what: str  # what to do, in one or two sentences
    done_text: str  # shown when done
    href: str
    action: str  # label of the link
    optional: bool = False


def build_steps(backend: WebBackend) -> list[SetupStep]:
    """The setup steps with their current state."""
    config = backend.settings.config
    cubes_connected = (
        sum(1 for s in backend.cubes.statuses if s.connected) if backend.cubes is not None else 0
    )
    hue_paired = backend.hue is not None and backend.hue.state.value == "paired"
    music_ready = bool(config.music.enabled and config.music.targets)
    cube_mappings = {m.cube for m in config.mappings}
    return [
        SetupStep(
            "token",
            "Zugriffs-Token festlegen",
            bool(backend.token),
            "Ohne Token ist die Oberfläche nur lesend. Tragen Sie OCTAPIHUB_WEB_TOKEN in "
            "octapihub.env ein und starten Sie den Dienst neu.",
            "Das Token ist eingerichtet, Änderungen sind möglich.",
            "/help/installation",
            "Anleitung lesen",
        ),
        SetupStep(
            "cube",
            "Würfel koppeln",
            cubes_connected > 0,
            "Kippen Sie den Würfel einmal, suchen Sie ihn auf der Seite Würfel und fügen Sie "
            "ihn mit einem Namen hinzu.",
            f"{cubes_connected} Würfel verbunden."
            if cubes_connected
            else f"{len(config.cubes)} Würfel eingerichtet, noch keiner verbunden.",
            "/cubes",
            "Würfel einrichten",
        ),
        SetupStep(
            "light",
            "Licht verbinden (Hue)",
            hue_paired,
            "Tragen Sie die Adresse der Hue Bridge ein, drücken Sie die runde Taste an der "
            "Bridge und klicken Sie auf „Mit Bridge verbinden“.",
            "Die Bridge ist verbunden.",
            "/light",
            "Licht einrichten",
            optional=True,
        ),
        SetupStep(
            "music",
            "Musik einrichten",
            music_ready,
            "Schalten Sie Musik ein, legen Sie ein Wiedergabegerät und eine Playlist an.",
            "Musik ist eingeschaltet und hat ein Wiedergabegerät.",
            "/music",
            "Musik einrichten",
            optional=True,
        ),
        SetupStep(
            "face",
            "Erste Seite belegen",
            bool(cube_mappings),
            "Wählen Sie auf der Seite Würfel für eine Würfelseite eine Aktion, oder wenden Sie "
            "eine Vorlage („Licht-Würfel“, „Musik-Würfel“) mit einem Klick an.",
            "Mindestens eine Seite hat eine Aktion.",
            "/cubes",
            "Seite belegen",
        ),
    ]


def progress(steps: list[SetupStep]) -> tuple[int, int]:
    """(done, total) of the steps that are required or already done."""
    counted = [s for s in steps if not s.optional or s.done]
    return sum(1 for s in counted if s.done), len(counted)


def register_setup(app: FastAPI, backend: WebBackend, page: Page) -> None:
    """Add ``/setup``."""

    @app.get("/setup", response_class=HTMLResponse)
    def setup(request: Request) -> Response:
        steps = build_steps(backend)
        done, total = progress(steps)
        context: dict[str, Any] = {
            "steps": steps,
            "next_key": next((s.key for s in steps if not s.done and not s.optional), ""),
            "done_count": done,
            "total_count": total,
            "complete": all(s.done for s in steps if not s.optional),
        }
        return page(request, "setup.html", "overview", **context)


def overview_hint(backend: WebBackend) -> dict[str, Any] | None:
    """A short note for the overview while required steps are open (``None`` when finished)."""
    steps = build_steps(backend)
    open_required = [s for s in steps if not s.done and not s.optional]
    if not open_required:
        return None
    done, total = progress(steps)
    return {"done": done, "total": total, "next": open_required[0].title}
