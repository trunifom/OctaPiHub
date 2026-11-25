"""A small, safe Markdown renderer for the built-in help pages.

Supports what our own documents use: headings, paragraphs, bullet and numbered lists (nested by
indentation), tables, fenced code, block quotes, rules, bold, italic, inline code, and links to
http(s) addresses, other help pages or anchors. All text is HTML-escaped first, so a document can
never inject markup.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_SAFE_LINK = re.compile(r"^(https?://|/|#|[A-Za-z0-9_.\-]+(\.md)?(#[\w\-]*)?$)")


@dataclass(frozen=True, slots=True)
class Heading:
    """One heading for the table of contents."""

    level: int
    text: str
    anchor: str


def slugify(text: str) -> str:
    """An anchor for a heading ("2. Start" becomes "2-start")."""
    text = text.lower()
    for source, target in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "abschnitt"


def _link(match: re.Match[str]) -> str:
    label, target = match.group(1), match.group(2)
    target = html.unescape(target)
    if not _SAFE_LINK.match(target):
        return label
    if target.endswith(".md") or ".md#" in target:
        name, _, anchor = target.partition("#")
        slug = name.removesuffix(".md").rsplit("/", 1)[-1].lower()
        target = f"/help/{slug}" + (f"#{anchor}" if anchor else "")
    external = target.startswith("http")
    attributes = ' rel="noopener noreferrer" target="_blank"' if external else ""
    return f'<a href="{html.escape(target, quote=True)}"{attributes}>{label}</a>'


def inline(text: str) -> str:
    """Escape ``text`` and apply the inline formatting."""
    codes: list[str] = []

    def keep(match: re.Match[str]) -> str:
        codes.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    work = _INLINE_CODE.sub(keep, text)
    work = html.escape(work, quote=False)
    work = _LINK.sub(_link, work)
    work = _BOLD.sub(r"<strong>\1</strong>", work)
    work = _ITALIC.sub(r"<em>\1</em>", work)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], work)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render(text: str) -> tuple[str, list[Heading]]:
    """Render Markdown ``text`` to HTML and return it with the headings found."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    headings: list[Heading] = []
    used: dict[str, int] = {}
    i = 0
    paragraph: list[str] = []
    list_stack: list[tuple[int, str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph.clear()

    def close_lists(to: int = 0) -> None:
        while len(list_stack) > to:
            out.append(f"</li></{list_stack.pop()[1]}>")

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_lists()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            out.append("<pre><code>" + html.escape("\n".join(block)) + "</code></pre>")
            i += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level, title = len(heading.group(1)), heading.group(2)
            anchor = slugify(re.sub(r"[`*]", "", title))
            used[anchor] = used.get(anchor, 0) + 1
            if used[anchor] > 1:
                anchor = f"{anchor}-{used[anchor]}"
            headings.append(Heading(level, re.sub(r"[`*]", "", title), anchor))
            out.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
            i += 1
            continue
        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            close_lists()
            out.append("<hr>")
            i += 1
            continue
        if (
            stripped.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1])
        ):
            flush_paragraph()
            close_lists()
            head = _cells(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            out.append('<div class="table-wrap"><table><thead><tr>')
            out.extend(f"<th>{inline(c)}</th>" for c in head)
            out.append("</tr></thead><tbody>")
            for row in rows:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
            out.append("</tbody></table></div>")
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + inline(" ".join(q for q in quote if q)) + "</blockquote>")
            continue
        item = _BULLET.match(line) or _NUMBERED.match(line)
        if item:
            flush_paragraph()
            indent = len(item.group(1).expandtabs(4))
            kind = "ul" if _BULLET.match(line) else "ol"
            if not list_stack or indent > list_stack[-1][0]:
                out.append(f"<{kind}>")
                list_stack.append((indent, kind))
            else:
                while len(list_stack) > 1 and indent < list_stack[-1][0]:
                    out.append(f"</li></{list_stack.pop()[1]}>")
                out.append("</li>")
            out.append("<li>" + inline(item.group(2)))
            i += 1
            continue
        if not stripped:
            flush_paragraph()
            close_lists()
            i += 1
            continue
        if list_stack and line.startswith((" ", "\t")):
            out.append(" " + inline(stripped))  # continuation of a list item
        else:
            close_lists()
            paragraph.append(stripped)
        i += 1
    flush_paragraph()
    close_lists()
    return "\n".join(out), headings
