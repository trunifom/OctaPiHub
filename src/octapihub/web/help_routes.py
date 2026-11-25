"""The built-in help: handbook, installation guide, troubleshooting, glossary and error codes."""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from octapihub.core import error_catalog
from octapihub.web import markdown

HELP_DIR = Path(__file__).parent / "help"
MAX_QUERY_LENGTH = 80
MAX_HITS_PER_DOC = 4

Page = Callable[..., Response]


@dataclass(frozen=True, slots=True)
class HelpDoc:
    """One help document."""

    slug: str
    title: str
    summary: str


# Order and descriptions of the documents; the texts live in ``help/<slug>.md``.
DOCS = (
    HelpDoc("start", "Schnellstart", "Was OctaPiHub ist und wie Sie in zehn Minuten loslegen."),
    HelpDoc("handbuch", "Benutzerhandbuch", "Jede Seite und jede Funktion der Oberfläche erklärt."),
    HelpDoc("installation", "Installation", "Schritt für Schritt auf dem Raspberry Pi einrichten."),
    HelpDoc("smarthome", "Smart Home", "Webhooks, MQTT und Home Assistant (Matter) einrichten."),
    HelpDoc(
        "fehlersuche",
        "Fehlersuche und Protokolle",
        "Wenn etwas nicht klappt: Ursache finden und beheben.",
    ),
    HelpDoc(
        "sicherheit", "Sicherheit und Datenschutz", "Wie Ihre Daten und Ihr Netz geschützt sind."
    ),
    HelpDoc("glossar", "Glossar", "Fachbegriffe in einfachen Worten."),
    HelpDoc("faq", "Häufige Fragen", "Kurze Antworten auf typische Fragen."),
)
_SLUGS = {doc.slug: doc for doc in DOCS}


def read_doc(slug: str) -> str:
    """The Markdown text of a document (empty if the file is missing)."""
    if slug not in _SLUGS:
        return ""
    try:
        return (HELP_DIR / f"{slug}.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _snippets(text: str, query: str) -> list[str]:
    needle = query.casefold()
    found: list[str] = []
    for line in text.splitlines():
        plain = re.sub(r"[`*#|>]", "", line).strip()
        position = plain.casefold().find(needle)
        if position < 0:
            continue
        start = max(0, position - 60)
        end = min(len(plain), position + len(query) + 90)
        before = html.escape(plain[start:position])
        match = html.escape(plain[position : position + len(query)])
        after = html.escape(plain[position + len(query) : end])
        found.append(
            f"{'…' if start else ''}{before}<mark>{match}</mark>{after}"
            f"{'…' if end < len(plain) else ''}"
        )
        if len(found) >= MAX_HITS_PER_DOC:
            break
    return found


def search(query: str) -> list[dict[str, Any]]:
    """Documents and error codes that contain ``query`` (case-insensitive)."""
    query = query.strip()[:MAX_QUERY_LENGTH]
    if len(query) < 2:
        return []
    hits: list[dict[str, Any]] = []
    for doc in DOCS:
        snippets = _snippets(read_doc(doc.slug), query)
        if snippets:
            hits.append({"title": doc.title, "href": f"/help/{doc.slug}", "snippets": snippets})
    codes = []
    for entry in error_catalog.CATALOG.values():
        haystack = f"{entry.code} {entry.title} {entry.meaning} {entry.action}"
        if query.casefold() in haystack.casefold():
            codes.append(
                f'<a href="/help/errors#{entry.code}">{html.escape(entry.code)}</a> '
                f"– {html.escape(entry.title)}"
            )
    if codes:
        hits.append({"title": "Fehlercodes", "href": "/help/errors", "snippets": codes[:8]})
    return hits


def register_help_routes(app: FastAPI, page: Page) -> None:
    """Add the ``/help`` pages."""

    @app.get("/help", response_class=HTMLResponse)
    def help_index(request: Request, q: str = "") -> Response:
        query = q.strip()[:MAX_QUERY_LENGTH]
        return page(
            request,
            "help.html",
            "help",
            docs=DOCS,
            query=query,
            hits=search(query) if query else None,
            live=False,
        )

    @app.get("/help/errors", response_class=HTMLResponse)
    def help_errors(request: Request) -> Response:
        return page(request, "help_errors.html", "help", areas=error_catalog.by_area(), live=False)

    @app.get("/help/{slug}", response_class=HTMLResponse)
    def help_doc(request: Request, slug: str) -> Response:
        doc = _SLUGS.get(slug)
        text = read_doc(slug)
        if doc is None or not text:
            raise HTTPException(status_code=404, detail="Not found")
        body, headings = markdown.render(text)
        return page(
            request,
            "help_doc.html",
            "help",
            doc=doc,
            body=body,
            toc=[h for h in headings if h.level in (2, 3)],
            docs=DOCS,
            live=False,
        )
