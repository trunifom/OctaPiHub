"""An 8x8 RGB pixel canvas with colour helpers, a 3x5 font, and small icons."""

from __future__ import annotations

import colorsys
import math
from collections.abc import Sequence

type Rgb = tuple[int, int, int]
type Frame = tuple[Rgb, ...]

SIZE = 8
PIXELS = SIZE * SIZE

BLACK: Rgb = (0, 0, 0)
WHITE: Rgb = (255, 255, 255)
RED: Rgb = (255, 0, 0)
GREEN: Rgb = (0, 200, 40)
BLUE: Rgb = (30, 80, 255)
YELLOW: Rgb = (255, 200, 0)
ORANGE: Rgb = (255, 100, 0)
CYAN: Rgb = (0, 200, 220)
MAGENTA: Rgb = (220, 0, 180)
PURPLE: Rgb = (140, 40, 220)
GRAY: Rgb = (60, 60, 60)
DIM_GRAY: Rgb = (22, 22, 22)
WARM_WHITE: Rgb = (255, 170, 80)

LEVEL_COLORS: dict[str, Rgb] = {
    "ok": GREEN,
    "info": BLUE,
    "warn": YELLOW,
    "error": RED,
    "off": DIM_GRAY,
}


def clamp_channel(value: float) -> int:
    """Round and clamp one colour channel to 0..255."""
    if not math.isfinite(value):
        return 0
    return max(0, min(255, int(round(value))))


def scale(color: Rgb, factor: float) -> Rgb:
    """Multiply every channel by ``factor`` (negative factors are treated as 0)."""
    factor = max(0.0, factor) if math.isfinite(factor) else 0.0
    return (
        clamp_channel(color[0] * factor),
        clamp_channel(color[1] * factor),
        clamp_channel(color[2] * factor),
    )


def mix(first: Rgb, second: Rgb, amount: float) -> Rgb:
    """Linear blend: ``amount`` 0 gives ``first``, 1 gives ``second``."""
    amount = max(0.0, min(1.0, amount)) if math.isfinite(amount) else 0.0
    return (
        clamp_channel(first[0] + (second[0] - first[0]) * amount),
        clamp_channel(first[1] + (second[1] - first[1]) * amount),
        clamp_channel(first[2] + (second[2] - first[2]) * amount),
    )


def hsv(hue_degrees: float, saturation: float = 1.0, value: float = 1.0) -> Rgb:
    """HSV (hue in degrees, saturation and value 0..1) to RGB."""
    hue = (hue_degrees % 360.0) / 360.0 if math.isfinite(hue_degrees) else 0.0
    red, green, blue = colorsys.hsv_to_rgb(
        hue, max(0.0, min(1.0, saturation)), max(0.0, min(1.0, value))
    )
    return (clamp_channel(red * 255), clamp_channel(green * 255), clamp_channel(blue * 255))


def gradient(value: float, stops: Sequence[tuple[float, Rgb]]) -> Rgb:
    """Colour at ``value`` along ascending ``(position, colour)`` stops (clamped at the ends)."""
    if not stops:
        return BLACK
    if not math.isfinite(value) or value <= stops[0][0]:
        return stops[0][1]
    for (low, low_color), (high, high_color) in zip(stops, stops[1:], strict=False):
        if value <= high:
            span = high - low
            return mix(low_color, high_color, (value - low) / span if span else 1.0)
    return stops[-1][1]


def apply_brightness(frame: Frame, percent: int) -> Frame:
    """Scale a whole frame; 0 turns every pixel off and 100 leaves it unchanged."""
    factor = max(0, min(100, percent)) / 100
    return tuple(scale(pixel, factor) for pixel in frame)


class Canvas:
    """Mutable 8x8 drawing surface; drawing outside the surface is silently ignored."""

    def __init__(self, background: Rgb = BLACK) -> None:
        self._pixels: list[Rgb] = [background] * PIXELS

    def set(self, x: int, y: int, color: Rgb) -> None:
        """Set one pixel."""
        if 0 <= x < SIZE and 0 <= y < SIZE:
            self._pixels[y * SIZE + x] = color

    def get(self, x: int, y: int) -> Rgb:
        """Read one pixel (black outside the surface)."""
        if 0 <= x < SIZE and 0 <= y < SIZE:
            return self._pixels[y * SIZE + x]
        return BLACK

    def fill(self, color: Rgb) -> None:
        """Fill the whole surface."""
        self._pixels = [color] * PIXELS

    def rect(self, x: int, y: int, width: int, height: int, color: Rgb) -> None:
        """Fill a rectangle."""
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.set(column, row, color)

    def outline(self, color: Rgb) -> None:
        """Draw a one-pixel border around the surface."""
        for index in range(SIZE):
            self.set(index, 0, color)
            self.set(index, SIZE - 1, color)
            self.set(0, index, color)
            self.set(SIZE - 1, index, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: Rgb) -> None:
        """Bresenham line."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        step_x = 1 if x0 < x1 else -1
        step_y = 1 if y0 < y1 else -1
        error = dx + dy
        while True:
            self.set(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x0 += step_x
            if doubled <= dx:
                error += dx
                y0 += step_y

    def mask(self, rows: Sequence[str], color: Rgb, x: int = 0, y: int = 0) -> None:
        """Draw a bitmap given as strings; ``#`` pixels are painted, everything else is skipped."""
        for row_index, row in enumerate(rows):
            for column_index, symbol in enumerate(row):
                if symbol == "#":
                    self.set(x + column_index, y + row_index, color)

    def text(self, text: str, x: int, y: int, color: Rgb) -> int:
        """Draw 3x5 text starting at ``x``; returns the x position after the last glyph."""
        cursor = x
        for character in normalize_text(text):
            self.mask(glyph(character), color, cursor, y)
            cursor += GLYPH_WIDTH + 1
        return cursor

    def frame(self) -> Frame:
        """Immutable snapshot of the surface."""
        return tuple(self._pixels)


# ---------------------------------------------------------------------------
# 3x5 font
# ---------------------------------------------------------------------------

GLYPH_WIDTH = 3
GLYPH_HEIGHT = 5

_FONT: dict[str, tuple[str, ...]] = {
    " ": ("...", "...", "...", "...", "..."),
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", "###", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "B": ("##.", "#.#", "##.", "#.#", "##."),
    "C": (".##", "#..", "#..", "#..", ".##"),
    "D": ("##.", "#.#", "#.#", "#.#", "##."),
    "E": ("###", "#..", "##.", "#..", "###"),
    "F": ("###", "#..", "##.", "#..", "#.."),
    "G": (".##", "#..", "#.#", "#.#", ".##"),
    "H": ("#.#", "#.#", "###", "#.#", "#.#"),
    "I": ("###", ".#.", ".#.", ".#.", "###"),
    "J": ("..#", "..#", "..#", "#.#", ".#."),
    "K": ("#.#", "#.#", "##.", "#.#", "#.#"),
    "L": ("#..", "#..", "#..", "#..", "###"),
    "M": ("#.#", "###", "###", "#.#", "#.#"),
    "N": ("###", "#.#", "#.#", "#.#", "#.#"),
    "O": (".#.", "#.#", "#.#", "#.#", ".#."),
    "P": ("##.", "#.#", "##.", "#..", "#.."),
    "Q": (".#.", "#.#", "#.#", "###", ".##"),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"),
    "S": (".##", "#..", ".#.", "..#", "##."),
    "T": ("###", ".#.", ".#.", ".#.", ".#."),
    "U": ("#.#", "#.#", "#.#", "#.#", "###"),
    "V": ("#.#", "#.#", "#.#", "#.#", ".#."),
    "W": ("#.#", "#.#", "###", "###", "#.#"),
    "X": ("#.#", "#.#", ".#.", "#.#", "#.#"),
    "Y": ("#.#", "#.#", ".#.", ".#.", ".#."),
    "Z": ("###", "..#", ".#.", "#..", "###"),
    "-": ("...", "...", "###", "...", "..."),
    ".": ("...", "...", "...", "...", ".#."),
    ":": ("...", ".#.", "...", ".#.", "..."),
    "%": ("#.#", "..#", ".#.", "#..", "#.#"),
    "+": ("...", ".#.", "###", ".#.", "..."),
    "!": (".#.", ".#.", ".#.", "...", ".#."),
    "?": ("###", "..#", ".#.", "...", ".#."),
    "/": ("..#", "..#", ".#.", "#..", "#.."),
    "=": ("...", "###", "...", "###", "..."),
    ",": ("...", "...", "...", ".#.", "#.."),
    "°": (".#.", "#.#", ".#.", "...", "..."),
    "_": ("...", "...", "...", "...", "###"),
    "*": ("#.#", ".#.", "###", ".#.", "#.#"),
}
FONT_CHARACTERS = frozenset(_FONT)
_REPLACEMENTS = str.maketrans({"Ä": "A", "Ö": "O", "Ü": "U", "ä": "a", "ö": "o", "ü": "u"})


def normalize_text(text: str) -> str:
    """Upper-case and map characters the font lacks (umlauts, ß) to drawable ones."""
    result = text.replace("ß", "SS").translate(_REPLACEMENTS).upper()
    return "".join(character if character in _FONT else "?" for character in result)


def glyph(character: str) -> tuple[str, ...]:
    """Bitmap rows of one character (``?`` for anything unknown)."""
    return _FONT.get(character.upper(), _FONT["?"])


def text_width(text: str) -> int:
    """Pixel width of ``text`` including one blank column between glyphs."""
    count = len(normalize_text(text))
    return count * (GLYPH_WIDTH + 1) - 1 if count else 0


def text_columns(text: str) -> list[tuple[bool, ...]]:
    """The text as pixel columns (top to bottom) including a gap column after each glyph."""
    columns: list[tuple[bool, ...]] = []
    for character in normalize_text(text):
        rows = glyph(character)
        for column in range(GLYPH_WIDTH):
            columns.append(tuple(rows[row][column] == "#" for row in range(GLYPH_HEIGHT)))
        columns.append((False,) * GLYPH_HEIGHT)
    return columns


# ---------------------------------------------------------------------------
# 8x8 icons (monochrome masks; the caller picks the colour)
# ---------------------------------------------------------------------------

ICONS: dict[str, tuple[str, ...]] = {
    "check": (
        "........",
        ".......#",
        "......##",
        "#....##.",
        "##..##..",
        ".####...",
        "..##....",
        "........",
    ),
    "cross": (
        "#......#",
        ".#....#.",
        "..#..#..",
        "...##...",
        "...##...",
        "..#..#..",
        ".#....#.",
        "#......#",
    ),
    "bluetooth": (
        "...#....",
        "...##...",
        "#..#.#..",
        ".####...",
        "..##....",
        ".####...",
        "#..#.#..",
        "...##...",
    ),
    "bulb": (
        "..####..",
        ".######.",
        ".######.",
        ".######.",
        "..####..",
        "...##...",
        "...##...",
        "..####..",
    ),
    "cube": (
        "..####..",
        ".#....#.",
        "#.#..#.#",
        "#..##..#",
        "#..##..#",
        "#.#..#.#",
        ".#....#.",
        "..####..",
    ),
    "battery": (
        "........",
        ".######.",
        ".#....##",
        ".#....##",
        ".#....##",
        ".#....##",
        ".######.",
        "........",
    ),
    "thermometer": (
        "...##...",
        "..#..#..",
        "..#..#..",
        "..#..#..",
        "..#..#..",
        ".#.##.#.",
        ".#.##.#.",
        "..####..",
    ),
    "drop": (
        "...#....",
        "...#....",
        "..###...",
        "..###...",
        ".#####..",
        ".#####..",
        ".#####..",
        "..###...",
    ),
    "warning": (
        "...##...",
        "...##...",
        "..#..#..",
        "..#..#..",
        ".#.##.#.",
        ".#.##.#.",
        "#......#",
        "########",
    ),
    "music": (
        "...#####",
        "...#...#",
        "...#...#",
        "...#...#",
        ".###.###",
        "####.###",
        ".##..##.",
        "........",
    ),
    "sun": (
        "#..#..#.",
        ".#.#.#..",
        "..###...",
        "#######.",
        "..###...",
        ".#.#.#..",
        "#..#..#.",
        "........",
    ),
    "moon": (
        "..###...",
        ".##.....",
        "##......",
        "##......",
        "##......",
        ".##...#.",
        "..#####.",
        "........",
    ),
    "power": (
        "...##...",
        "..#.##..",
        ".#..#.#.",
        "#...#..#",
        "#......#",
        "#......#",
        ".#....#.",
        "..####..",
    ),
    "heart": (
        ".##..##.",
        "########",
        "########",
        "########",
        ".######.",
        "..####..",
        "...##...",
        "........",
    ),
    "question": (
        "..####..",
        ".#....#.",
        "......#.",
        ".....#..",
        "....#...",
        "....#...",
        "........",
        "....#...",
    ),
    "arrow_up": (
        "...##...",
        "..####..",
        ".######.",
        "...##...",
        "...##...",
        "...##...",
        "...##...",
        "........",
    ),
    "dot": (
        "........",
        "........",
        "........",
        "...##...",
        "...##...",
        "........",
        "........",
        "........",
    ),
    "wifi": (
        "........",
        ".######.",
        "#......#",
        "..####..",
        ".#....#.",
        "...##...",
        "...##...",
        "........",
    ),
}


def rotate_mask(rows: Sequence[str], quarter_turns: int) -> tuple[str, ...]:
    """Rotate a square bitmap clockwise by ``quarter_turns`` * 90 degrees."""
    current = tuple(rows)
    for _ in range(quarter_turns % 4):
        size = len(current)
        current = tuple("".join(current[size - 1 - y][x] for y in range(size)) for x in range(size))
    return current


ICONS["arrow_right"] = rotate_mask(ICONS["arrow_up"], 1)
ICONS["arrow_down"] = rotate_mask(ICONS["arrow_up"], 2)
ICONS["arrow_left"] = rotate_mask(ICONS["arrow_up"], 3)
