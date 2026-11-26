"""Colour contrast (WCAG 2.1 AA) of the style sheet and basic keyboard and landmark checks."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).parent.parent / "src" / "octapihub" / "web"
CSS = (WEB / "static" / "app.css").read_text(encoding="utf-8")

# (foreground, background, minimum ratio): 4.5 for text, 3 for focus rings and large parts
PAIRS = [
    ("text", "bg", 4.5),
    ("text", "surface", 4.5),
    ("text", "surface-2", 4.5),
    ("muted", "bg", 4.5),
    ("muted", "surface", 4.5),
    ("muted", "surface-2", 4.5),
    ("accent-strong", "accent-soft", 4.5),
    ("accent-strong", "surface", 4.5),
    ("on-accent", "accent", 4.5),
    ("ok", "ok-soft", 4.5),
    ("ok", "surface", 4.5),
    ("warn", "warn-soft", 4.5),
    ("warn", "surface", 4.5),
    ("error", "error-soft", 4.5),
    ("error", "surface", 4.5),
    ("info", "info-soft", 4.5),
    ("info", "surface", 4.5),
    ("off", "off-soft", 4.5),
    ("focus", "surface", 3.0),
    ("focus", "bg", 3.0),
]


def variables(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def block_after(selector: str) -> str:
    start = CSS.index(selector)
    return CSS[CSS.index("{", start) + 1 : CSS.index("}", start)]


LIGHT = variables(block_after(":root {"))
DARK = variables(block_after(':root[data-theme="dark"] {'))
AUTO_DARK = variables(block_after(':root[data-theme="auto"] {'))


def luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    a, b = sorted((luminance(first), luminance(second)), reverse=True)
    return (a + 0.05) / (b + 0.05)


@pytest.mark.parametrize(("theme", "colors"), [("hell", LIGHT), ("dunkel", DARK)])
@pytest.mark.parametrize(("fg", "bg", "minimum"), PAIRS)
def test_colour_pairs_reach_wcag_aa(
    theme: str, colors: dict[str, str], fg: str, bg: str, minimum: float
) -> None:
    ratio = contrast(colors[fg], colors[bg])
    assert ratio >= minimum, f"{theme}: {fg} auf {bg} hat nur {ratio:.2f}:1 (nötig {minimum}:1)"


def test_the_automatic_dark_theme_uses_the_same_colours_as_the_dark_theme() -> None:
    for name, value in DARK.items():
        assert AUTO_DARK.get(name, value) == value, name


def test_the_contrast_function_matches_known_values() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.02)


# ---------- keyboard and landmarks ----------


def test_focus_is_visible_and_the_skip_link_exists() -> None:
    assert ":focus-visible" in CSS
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'class="skip"' in base and 'id="main"' in base
    assert 'aria-label="Hauptnavigation"' in base and 'lang="de"' in base


def test_motion_can_be_reduced() -> None:
    assert "prefers-reduced-motion" in CSS


def test_every_template_input_has_a_label_or_aria_label() -> None:
    """Static check: an input with an id needs a matching label; others need aria-label."""
    problems: list[str] = []
    for path in (WEB / "templates").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<(input|select|textarea)\b([^>]*)>", text):
            attrs = match.group(2)
            if re.search(r'type="(hidden|submit|button)"', attrs):
                continue
            if "aria-label" in attrs or "aria-labelledby" in attrs:
                continue
            ident = re.search(r'\bid="([^"]+)"', attrs)
            if ident is None:
                # wrapped by a label (checkbox/radio in a label) is fine; check the context
                before = text[max(0, match.start() - 400) : match.start()]
                if "<label" in before and before.rfind("<label") > before.rfind("</label>"):
                    continue
                problems.append(f"{path.name}: {match.group(0)[:70]}")
                continue
            raw = ident.group(1)
            before = text[max(0, match.start() - 400) : match.start()]
            if "<label" in before and before.rfind("<label") > before.rfind("</label>"):
                continue  # wrapped by its label
            if "{{" in raw:
                continue  # generated ids are labelled in the same loop; covered by the help test
            if f'for="{raw}"' not in text:
                problems.append(f"{path.name}: id={raw} ohne label")
    assert problems == [], problems
