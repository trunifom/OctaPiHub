"""Help buttons (ⓘ) for every input field.

Pages are written normally; after rendering, :func:`add_info_buttons` finds each label (and each
toggle or tick box) and appends a small button whose text comes from :data:`HELP`. A button opens a
short explanation: what the field is, what to enter, an example, and what happens if it is wrong.
A test checks that every field of every page has a text, so a new field cannot be forgotten.
"""

from __future__ import annotations

import html
import re

from octapihub.web.field_help_texts import TEXTS

_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_LABEL_FOR = re.compile(
    r'(<label\b(?P<attrs>[^>]*?)\bfor="f-(?P<name>[^"]+)"[^>]*>(?P<body>.*?)</label>)', re.S
)
_TOGGLE = re.compile(
    r'(<label class="toggle">\s*<input[^>]*\bname="(?P<name>[^"]+)"[^>]*>.*?'
    r"<strong>(?P<body>.*?)</strong>.*?</label>)",
    re.S,
)
_CHECK = re.compile(
    r'(<label class="check"><input type="checkbox"[^>]*\bname="(?P<name>[^"]+)"[^>]*>'
    r"\s*<span>(?P<body>.*?)</span></label>)",
    re.S,
)
_TAG = re.compile(r"<[^>]+>")


def key_for(name: str) -> str:
    """The help key of a field name: row numbers, ``new`` and device addresses become ``*``."""
    parts: list[str] = []
    pieces = name.split(".")
    if pieces[0] == "sensor" and len(pieces) > 2:
        pieces[1] = "*"  # one text for every sensor group
    for index, part in enumerate(pieces):
        if (index > 0 and (part.isdigit() or part == "new" or _MAC.match(part))) or ":" in part:
            parts.append("*")
        else:
            parts.append(part)
    return ".".join(parts)


def text_for(name: str) -> str | None:
    """The help text for a field name, or ``None`` if there is none."""
    return HELP.get(key_for(name))


def _button(name: str, label: str, counter: list[int]) -> tuple[str, str]:
    """The button (placed inside the label, next to its text) and its hidden explanation."""
    text = text_for(name)
    if text is None:
        return "", ""
    counter[0] += 1
    ident = f"info-{counter[0]}"
    plain = html.escape(_TAG.sub("", label).strip(), quote=True)
    button = (
        f'<button type="button" class="info-btn" data-info="{ident}" aria-expanded="false" '
        f'aria-controls="{ident}" aria-label="Hilfe zu: {plain}" title="Hilfe zu diesem Feld">'
        f'<span aria-hidden="true">i</span></button>'
    )
    pop = f'<span class="info-pop" id="{ident}" role="note" hidden>{html.escape(text)}</span>'
    return button, pop


def add_info_buttons(page: str) -> str:
    """Return ``page`` with a help button inside every field label that has a help text.

    The button sits at the end of the label text; its explanation follows the label.
    """
    counter = [0]

    def with_button(match: re.Match[str]) -> str:
        button, pop = _button(match.group("name"), match.group("body"), counter)
        whole = match.group(1)
        return whole[: -len("</label>")] + button + "</label>" + pop if button else whole

    result = _LABEL_FOR.sub(with_button, page)
    result = _TOGGLE.sub(with_button, result)
    return _CHECK.sub(with_button, result)


def missing_help(page: str) -> list[str]:
    """Field names on ``page`` that have a label but no help text (for the completeness test)."""
    names: list[str] = []
    for pattern in (_LABEL_FOR, _TOGGLE, _CHECK):
        names.extend(m.group("name") for m in pattern.finditer(page))
    return sorted({n for n in names if text_for(n) is None})


HELP: dict[str, str] = TEXTS
