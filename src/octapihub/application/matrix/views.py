"""The LED matrix views: pure functions from a state snapshot to one 8x8 frame."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from octapihub.application.matrix.canvas import (
    BLACK,
    BLUE,
    CYAN,
    DIM_GRAY,
    GRAY,
    GREEN,
    ICONS,
    LEVEL_COLORS,
    ORANGE,
    PURPLE,
    RED,
    SIZE,
    WARM_WHITE,
    WHITE,
    YELLOW,
    Canvas,
    Frame,
    Rgb,
    gradient,
    hsv,
    mix,
    scale,
    text_columns,
)
from octapihub.application.matrix.state import (
    CubeDisplay,
    DisplayState,
    battery_level,
    level_word,
    overall_level,
    status_messages,
    worst_level,
)
from octapihub.core.config import MATRIX_VIEW_IDS

# Lamp colour presets reachable with the joystick (name used in info texts, colour).
LAMP_PRESETS: tuple[tuple[str, Rgb], ...] = (
    ("WARMWEISS", WARM_WHITE),
    ("WEISS", (255, 255, 255)),
    ("ROT", (255, 0, 0)),
    ("ORANGE", (255, 90, 0)),
    ("GELB", (255, 200, 0)),
    ("LIMETTE", (130, 255, 0)),
    ("GRUEN", (0, 255, 40)),
    ("TUERKIS", (0, 255, 150)),
    ("CYAN", (0, 220, 255)),
    ("HIMMEL", (0, 120, 255)),
    ("BLAU", (20, 40, 255)),
    ("LILA", (140, 40, 255)),
    ("MAGENTA", (255, 0, 200)),
    ("PINK", (255, 60, 110)),
)
BREATHING_PRESETS: tuple[tuple[str, Rgb], ...] = (
    ("WARM", WARM_WHITE),
    ("KALT", (120, 170, 255)),
    ("GRUEN", (40, 255, 90)),
    ("LILA", (170, 60, 255)),
)
PALETTE_MODES = ("SPEKTRUM", "PASTELL", "WAERME")
GRAPH_GROUPS: tuple[tuple[str, str, Rgb, float], ...] = (
    ("temperature", "TEMPERATUR", ORANGE, 1.0),
    ("humidity", "FEUCHTE", CYAN, 5.0),
    ("pressure", "DRUCK", PURPLE, 2.0),
)
BRIGHTNESS_STEPS = (5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100)
_TEMPERATURE_STOPS: tuple[tuple[float, Rgb], ...] = (
    (-5.0, (90, 60, 255)),
    (8.0, (0, 140, 255)),
    (16.0, (0, 210, 200)),
    (21.0, (0, 220, 70)),
    (26.0, (255, 200, 0)),
    (32.0, (255, 40, 0)),
)
_PRESSURE_STOPS: tuple[tuple[float, Rgb], ...] = (
    (970.0, (80, 120, 255)),
    (1013.0, (0, 220, 120)),
    (1040.0, (255, 150, 0)),
)


@dataclass(slots=True)
class ViewUi:
    """Joystick-adjustable settings the views read; the controller owns and edits them."""

    brightness: int = 60
    lamp_index: int = 0
    lamp_value: int = 80
    edit_mode: bool = False
    sub: dict[str, int] = field(default_factory=dict)

    def selected(self, view_id: str, count: int) -> int:
        """Current sub-selection of ``view_id`` clamped to ``count`` items."""
        return 0 if count <= 0 else max(0, min(count - 1, self.sub.get(view_id, 0)))


@dataclass(frozen=True, slots=True)
class ViewContext:
    """Everything one render call may use."""

    state: DisplayState
    ui: ViewUi
    t: float  # seconds; drives animation only


def blink(t: float, rate: float = 2.0) -> bool:
    """True during the 'on' half of a blink of ``rate`` Hz."""
    return int(t * rate * 2) % 2 == 0


def marquee(
    canvas: Canvas, text: str, color: Rgb, t: float, speed: float = 6.0, y: int = 1
) -> None:
    """Scroll ``text`` from right to left, looping with a gap."""
    columns = text_columns(text)
    if not columns:
        return
    period = len(columns) + SIZE
    offset = int(t * speed) % period
    for screen_x in range(SIZE):
        index = screen_x + offset - SIZE
        if 0 <= index < len(columns):
            for row, on in enumerate(columns[index]):
                if on:
                    canvas.set(screen_x, y + row, color)
    # progress hint along the bottom edge shows where the loop is
    canvas.set(int(offset * SIZE / period), SIZE - 1, scale(color, 0.35))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(value: float | None, digits: int = 0) -> str:
    """Format a measurement for the 3x5 font; missing or non-finite values become ``-``."""
    if value is None or not math.isfinite(value):
        return "-"
    return f"{value:.{digits}f}"


def _usable(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


class View:
    """Base class: metadata plus ``render``; subclasses override what they need."""

    id = ""
    label = ""
    description = ""
    controls = ""
    animated = False
    has_edit_mode = False

    def sub_count(self, state: DisplayState) -> int:
        """Number of items ↑/↓ can step through; 0 means ↑/↓ change the brightness."""
        return 0

    def render(self, ctx: ViewContext) -> Frame:
        """Draw one frame."""
        raise NotImplementedError

    def describe(self, state: DisplayState) -> str:
        """Short text (3x5 font characters) scrolled when the joystick is pressed."""
        return self.label.upper()


class OverviewView(View):
    id = "overview"
    label = "Übersicht"
    description = "Ein Feld je Komponente in Ampelfarben: Würfel, Hue, Sensoren, System."
    controls = "Mitte: Erklärung"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        state, canvas = ctx.state, Canvas()
        cubes = state.cubes
        battery = worst_level([battery_level(c.battery_percent) for c in cubes])
        bluetooth = "off" if not cubes else "ok" if any(c.connected for c in cubes) else "warn"
        cells: list[str | None] = [
            *(cubes[i].level if i < len(cubes) else None for i in range(3)),
            state.hue_level,
            state.sense_level,
            state.host_level,
            bluetooth,
            battery,
            overall_level(state),
        ]
        for index, level in enumerate(cells):
            column, row = index % 3, index // 3
            color = DIM_GRAY if level is None else LEVEL_COLORS.get(level, DIM_GRAY)
            if level == "error" and not blink(ctx.t, 1.0):
                color = scale(color, 0.2)
            canvas.rect(column * 3, row * 3, 2, 2, color)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        parts = [f"UEBERSICHT {level_word(overall_level(state))}"]
        parts.extend(text for text, _ in status_messages(state)[:3])
        return " ".join(parts)


class CubesView(View):
    id = "cubes"
    label = "Würfel"
    description = "Verbindung, zuletzt erkannte Seite und Akku des gewählten Würfels."
    controls = "↑/↓: Würfel wählen"
    animated = True

    def sub_count(self, state: DisplayState) -> int:
        return len(state.cubes)

    def render(self, ctx: ViewContext) -> Frame:
        state, canvas = ctx.state, Canvas()
        if not state.cubes:
            canvas.mask(ICONS["cube"], GRAY)
            return canvas.frame()
        index = ctx.ui.selected(self.id, len(state.cubes))
        cube = state.cubes[index]
        level_color = LEVEL_COLORS.get(cube.level, DIM_GRAY)
        canvas.outline(scale(level_color, 0.55))
        for dot in range(len(state.cubes)):
            canvas.set(1, 1 + dot * 2, WHITE if dot == index else GRAY)
        if not cube.connected:
            if blink(ctx.t, 1.0):
                canvas.text("X", 2, 1, RED)
        elif cube.last_face is None:
            canvas.text("-", 2, 1, GRAY)
        else:
            canvas.text(str(cube.last_face), 2, 1, WHITE if cube.last_face else GRAY)
        _vertical_battery(canvas, 6, cube.battery_percent, ctx.t)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        if not state.cubes:
            return "KEINE WUERFEL"
        return " ".join(
            f"{c.name} {level_word(c.level)} SEITE "
            f"{c.last_face if c.last_face is not None else '-'}"
            for c in state.cubes
        )


def _battery_color(percent: int | None) -> Rgb:
    level = battery_level(percent)
    return {"ok": GREEN, "warn": YELLOW, "error": RED}.get(level, GRAY)


def _vertical_battery(canvas: Canvas, x: int, percent: int | None, t: float) -> None:
    """A bar on the inner area (rows 1..6) of the cube view."""
    if percent is None:
        for y in range(1, 7, 2):
            canvas.set(x, y, GRAY)
        return
    height = max(1, round(percent * 6 / 100)) if percent > 0 else 0
    color = _battery_color(percent)
    if battery_level(percent) == "error" and not blink(t, 2.0):
        color = scale(color, 0.25)
    for step in range(height):
        canvas.set(x, 6 - step, color)


class BatteryView(View):
    id = "battery"
    label = "Akku"
    description = "Der Akkustand aller Würfel als Balken (grün, gelb, rot)."
    controls = "Mitte: Erklärung"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        for index, cube in enumerate(ctx.state.cubes[:3]):
            x = index * 3
            percent = cube.battery_percent
            if percent is None:
                for y in range(0, SIZE, 2):
                    canvas.set(x, y, GRAY)
                    canvas.set(x + 1, y, GRAY)
                continue
            rows = max(1, round(percent * SIZE / 100)) if percent > 0 else 0
            color = _battery_color(percent)
            if battery_level(percent) == "error" and not blink(ctx.t, 2.0):
                color = scale(color, 0.25)
            canvas.rect(x, SIZE - rows, 2, rows, color)
        if not ctx.state.cubes:
            canvas.mask(ICONS["battery"], GRAY)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        known = [
            f"{c.name} {c.battery_percent}%" for c in state.cubes if c.battery_percent is not None
        ]
        return "AKKU " + (" ".join(known) if known else "UNBEKANNT")


def _two_digits(canvas: Canvas, number: int, color: Rgb) -> None:
    number = int(_clamp(number, -9, 99))
    if number < 0:
        canvas.text("-", 0, 0, color)
        canvas.text(str(-number), 4, 0, color)
        return
    text = f"{number:02d}" if number >= 10 else f" {number}"
    canvas.text(text, 0, 0, color)


class _ScalarView(View):
    """Shared layout for temperature and humidity: two digits, tenths bar, scale bar."""

    group = ""
    icon = ""
    unit_word = ""
    animated = True

    def color_for(self, value: float) -> Rgb:
        raise NotImplementedError

    def scale_fraction(self, value: float) -> float:
        raise NotImplementedError

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        reading = ctx.state.readings.get(self.group)
        if reading is None or not _usable(reading.value):
            canvas.mask(ICONS[self.icon], GRAY)
            if blink(ctx.t, 1.0):
                canvas.text("?", 5, 0, GRAY)
            return canvas.frame()
        value = float(reading.value)  # type: ignore[arg-type]
        color = self.color_for(value)
        rounded = round(value)
        _two_digits(canvas, rounded, color)
        tenth = int(abs(value * 10) % 10)
        for row in range(round(tenth * 5 / 9)):  # right edge: the tenths next to the digits
            canvas.set(7, 4 - row, scale(color, 0.5))
        filled = max(0, min(SIZE, round(self.scale_fraction(value) * SIZE)))
        canvas.rect(0, 7, filled, 1, scale(color, 0.6))
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        reading = state.readings.get(self.group)
        if reading is None or not _usable(reading.value):
            return f"{self.label.upper()} NICHT VERFUEGBAR"
        return f"{self.label.upper()} {_number(reading.value, 1)} {self.unit_word}"


class TemperatureView(_ScalarView):
    id = "temperature"
    label = "Temperatur"
    description = "Zwei Ziffern in Farbe von kalt (blau) bis heiß (rot) mit Skala."
    controls = "↑/↓: Helligkeit"
    group = "temperature"
    icon = "thermometer"
    unit_word = "GRAD"

    def color_for(self, value: float) -> Rgb:
        return gradient(value, _TEMPERATURE_STOPS)

    def scale_fraction(self, value: float) -> float:
        return _clamp((value + 10) / 50, 0.0, 1.0)


class HumidityView(_ScalarView):
    id = "humidity"
    label = "Luftfeuchtigkeit"
    description = "Zwei Ziffern; die Farbe geht von trocken (orange) zu feucht (blau)."
    controls = "↑/↓: Helligkeit"
    group = "humidity"
    icon = "drop"
    unit_word = "PROZENT"

    def color_for(self, value: float) -> Rgb:
        return gradient(value, ((20.0, ORANGE), (45.0, GREEN), (70.0, CYAN), (95.0, BLUE)))

    def scale_fraction(self, value: float) -> float:
        return _clamp(value / 100, 0.0, 1.0)


class PressureView(View):
    id = "pressure"
    label = "Luftdruck"
    description = "Der Luftdruck als Laufschrift, Farbe nach Tief- und Hochdruck."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        reading = ctx.state.readings.get("pressure")
        if reading is None or reading.value is None or not math.isfinite(reading.value):
            canvas.mask(ICONS["question"], GRAY)
            return canvas.frame()
        marquee(
            canvas, f"{reading.value:.0f} HPA", gradient(reading.value, _PRESSURE_STOPS), ctx.t, 5.0
        )
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        reading = state.readings.get("pressure")
        return "LUFTDRUCK " + (f"{_number(reading.value)} HPA" if reading else "-")


class GraphView(View):
    id = "graph"
    label = "Verlauf"
    description = "Die letzten acht Messwerte als Balken; ↑/↓ wählt Temperatur, Feuchte, Druck."
    controls = "↑/↓: Messwert wählen"
    animated = True

    def sub_count(self, state: DisplayState) -> int:
        return len(GRAPH_GROUPS)

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        index = ctx.ui.selected(self.id, len(GRAPH_GROUPS))
        group, _label, color, min_span = GRAPH_GROUPS[index]
        values = [v for v in ctx.state.history.get(group, ()) if math.isfinite(v)][-SIZE:]
        if len(values) < 2:
            dots = int(ctx.t * 3) % 4
            for dot in range(dots):
                canvas.set(2 + dot, 4, scale(color, 0.7))
            canvas.set(0, 7, scale(color, 0.4))
            return canvas.frame()
        low, high = min(values), max(values)
        span = max(high - low, min_span)
        middle = (high + low) / 2
        low = middle - span / 2
        offset = SIZE - len(values)
        for position, value in enumerate(values):
            height = 1 + round((value - low) / span * (SIZE - 1))
            height = int(_clamp(height, 1, SIZE))
            is_last = position == len(values) - 1
            canvas.rect(
                offset + position,
                SIZE - height,
                1,
                height,
                color if is_last else scale(color, 0.45),
            )
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "VERLAUF"


class ClockView(View):
    id = "clock"
    label = "Uhr"
    description = "Die Uhrzeit als Laufschrift mit Sekundenpunkt am unteren Rand."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        now = ctx.state.now
        marquee(canvas, f"{now.hour:02d}:{now.minute:02d}", WARM_WHITE, ctx.t, 5.0)
        canvas.set(int(now.second * SIZE / 60), SIZE - 1, WHITE)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return f"{state.now.hour:02d}:{state.now.minute:02d}"


class BinaryClockView(View):
    id = "clock_binary"
    label = "Binäruhr"
    description = "Stunden, Minuten und Sekunden als Binärziffern (unten = 1)."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        now = ctx.state.now
        digits = (
            (now.hour // 10, 2, CYAN),
            (now.hour % 10, 4, CYAN),
            (now.minute // 10, 3, GREEN),
            (now.minute % 10, 4, GREEN),
            (now.second // 10, 3, YELLOW),
            (now.second % 10, 4, YELLOW),
        )
        for column, (digit, bits, color) in enumerate(digits):
            for bit in range(4):
                if bit >= bits:
                    continue
                y = 6 - bit
                on = bool(digit & (1 << bit))
                canvas.set(1 + column, y, color if on else scale(color, 0.12))
        canvas.set(int(now.second * SIZE / 60), 0, scale(WHITE, 0.5))
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return f"BINAERUHR {state.now.hour:02d}:{state.now.minute:02d}"


class TickerView(View):
    id = "ticker"
    label = "Laufschrift"
    description = "Statusmeldungen als Text, geordnet nach Wichtigkeit."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        messages = status_messages(ctx.state)
        text = "   ".join(text for text, _ in messages) if messages else "OCTAPIHUB"
        level = worst_level([level for _, level in messages]) if messages else "ok"
        marquee(canvas, text, LEVEL_COLORS.get(level, WHITE), ctx.t, 6.0)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "LAUFSCHRIFT"


def _heading(reading_value: float) -> float:
    return reading_value % 360.0 if math.isfinite(reading_value) else 0.0


class CompassView(View):
    id = "compass"
    label = "Kompass"
    description = "Ein Zeiger zeigt die Richtung; der rote Punkt oben markiert Norden."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        reading = ctx.state.readings.get("compass")
        if reading is None or not _usable(reading.value):
            canvas.mask(ICONS["question"], GRAY)
            return canvas.frame()
        angle = math.radians(_heading(float(reading.value)))  # type: ignore[arg-type]
        center = 3.5
        end_x = center + math.sin(angle) * 3.4
        end_y = center - math.cos(angle) * 3.4
        canvas.set(3, 0, scale(RED, 0.5))
        canvas.set(4, 0, scale(RED, 0.5))
        canvas.line(3, 3, round(end_x), round(end_y), scale(WHITE, 0.45))
        canvas.set(round(end_x), round(end_y), CYAN)
        canvas.set(3, 3, WHITE)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        reading = state.readings.get("compass")
        return "KOMPASS " + (
            f"{_heading(reading.value):.0f} GRAD" if reading and reading.value is not None else "-"
        )


def _signed_angle(value: float) -> float:
    """Fold any angle in degrees into -180..180."""
    return ((value + 180.0) % 360.0) - 180.0 if math.isfinite(value) else 0.0


class LevelView(View):
    id = "level"
    label = "Wasserwaage"
    description = "Eine Luftblase zeigt die Neigung; grün heißt waagerecht."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        reading = ctx.state.readings.get("orientation")
        if reading is None or reading.vector is None:
            canvas.mask(ICONS["question"], GRAY)
            return canvas.frame()
        pitch, roll = _signed_angle(reading.vector[0]), _signed_angle(reading.vector[1])
        for x, y in ((3, 3), (4, 3), (3, 4), (4, 4)):
            canvas.set(x, y, scale(GRAY, 0.8))
        bubble_x = round(3.5 + _clamp(roll / 30, -1, 1) * 3.4)
        bubble_y = round(3.5 + _clamp(pitch / 30, -1, 1) * 3.4)
        tilt = max(abs(pitch), abs(roll))
        color = GREEN if tilt <= 3 else YELLOW if tilt <= 15 else RED
        canvas.set(bubble_x, bubble_y, color)
        canvas.set(bubble_x + 1 if bubble_x < 7 else bubble_x - 1, bubble_y, scale(color, 0.4))
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "WASSERWAAGE"


class AmbientView(View):
    id = "ambient"
    label = "Umgebungsfarbe"
    description = "Zeigt die vom Farbsensor (nur Sense HAT v2) gemessene Farbe."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        reading = ctx.state.readings.get("color")
        if reading is None or reading.rgba is None:
            canvas.mask(ICONS["sun"], GRAY)
            return canvas.frame()
        red, green, blue, clear = reading.rgba
        peak = max(red, green, blue, 1)
        color = (round(red * 255 / peak), round(green * 255 / peak), round(blue * 255 / peak))
        canvas.fill(color)
        lit = max(0, min(SIZE, round(clear / 255 * SIZE)))
        canvas.rect(0, 7, SIZE, 1, BLACK)
        canvas.rect(0, 7, lit, 1, WHITE)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "UMGEBUNGSFARBE"


class LampView(View):
    id = "lamp"
    label = "Lampe"
    description = "Eine Farbfläche als Stimmungslicht. Mitte drücken zum Einstellen."
    controls = "Mitte: Einstellen (←/→ Farbe, ↑/↓ Helligkeit)"
    animated = True
    has_edit_mode = True

    def render(self, ctx: ViewContext) -> Frame:
        _name, color = LAMP_PRESETS[ctx.ui.lamp_index % len(LAMP_PRESETS)]
        canvas = Canvas(scale(color, ctx.ui.lamp_value / 100))
        if ctx.ui.edit_mode and blink(ctx.t, 1.5):
            canvas.set(0, 0, WHITE)
            canvas.set(7, 0, WHITE)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "LAMPE"


class RainbowView(View):
    id = "rainbow"
    label = "Regenbogen"
    description = "Ein wandernder Regenbogen."
    controls = "↑/↓: Helligkeit"
    animated = True

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        for y in range(SIZE):
            for x in range(SIZE):
                canvas.set(x, y, hsv(x * 22 + y * 22 + ctx.t * 70, 1.0, 1.0))
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "REGENBOGEN"


class BreathingView(View):
    id = "breathing"
    label = "Atmen"
    description = "Eine Farbe, die ruhig heller und dunkler wird (Nachtlicht)."
    controls = "↑/↓: Farbe wählen"
    animated = True

    def sub_count(self, state: DisplayState) -> int:
        return len(BREATHING_PRESETS)

    def render(self, ctx: ViewContext) -> Frame:
        index = ctx.ui.selected(self.id, len(BREATHING_PRESETS))
        _name, color = BREATHING_PRESETS[index]
        phase = 0.5 + 0.5 * math.sin(2 * math.pi * ctx.t / 5.0)
        return Canvas(scale(color, 0.08 + 0.92 * phase)).frame()

    def describe(self, state: DisplayState) -> str:
        return "ATMEN"


class PaletteView(View):
    id = "palette"
    label = "Farbpalette"
    description = "Farbverläufe zum Ausprobieren; ↑/↓ wechselt zwischen drei Paletten."
    controls = "↑/↓: Palette wählen"
    animated = False

    def sub_count(self, state: DisplayState) -> int:
        return len(PALETTE_MODES)

    def render(self, ctx: ViewContext) -> Frame:
        mode = ctx.ui.selected(self.id, len(PALETTE_MODES))
        canvas = Canvas()
        for y in range(SIZE):
            for x in range(SIZE):
                if mode == 0:
                    color = hsv(x * 360 / SIZE, 1.0, 1.0 - y * 0.11)
                elif mode == 1:
                    color = hsv(x * 360 / SIZE, 0.15 + y * 0.12, 1.0)
                else:
                    color = gradient(
                        x + y * 0.5, ((0, (60, 0, 120)), (4, RED), (8, YELLOW), (11, WHITE))
                    )
                canvas.set(x, y, color)
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "PALETTE"


class BrightnessView(View):
    id = "brightness"
    label = "Helligkeit"
    description = "Stellt die Helligkeit der Anzeige ein."
    controls = "↑/↓: Helligkeit"
    animated = False

    def render(self, ctx: ViewContext) -> Frame:
        canvas = Canvas()
        canvas.mask(ICONS["sun"][:5], mix(YELLOW, ORANGE, 0.3))
        filled = max(1, round(ctx.ui.brightness * SIZE / 100))
        canvas.rect(0, 6, filled, 2, WHITE)
        canvas.rect(filled, 6, SIZE - filled, 2, scale(GRAY, 0.6))
        return canvas.frame()

    def describe(self, state: DisplayState) -> str:
        return "HELLIGKEIT"


class OffView(View):
    id = "off"
    label = "Aus"
    description = "Die Anzeige bleibt dunkel (Joystick-Eingaben wecken sie nicht auf)."
    controls = "←/→: Ansicht wechseln"
    animated = False

    def render(self, ctx: ViewContext) -> Frame:
        return Canvas().frame()

    def describe(self, state: DisplayState) -> str:
        return "AUS"


def _build_registry() -> dict[str, View]:
    views: list[View] = [
        OverviewView(),
        CubesView(),
        BatteryView(),
        TemperatureView(),
        HumidityView(),
        PressureView(),
        GraphView(),
        ClockView(),
        BinaryClockView(),
        TickerView(),
        CompassView(),
        LevelView(),
        AmbientView(),
        LampView(),
        RainbowView(),
        BreathingView(),
        PaletteView(),
        BrightnessView(),
        OffView(),
    ]
    registry = {view.id: view for view in views}
    if tuple(registry) != MATRIX_VIEW_IDS:
        raise RuntimeError("Matrix views and configuration view names are out of sync")
    return registry


VIEWS: dict[str, View] = _build_registry()


def requirements(view_id: str) -> str | None:
    """Sensor group a view needs, or ``None``."""
    return {
        "compass": "compass",
        "level": "orientation",
        "ambient": "color",
        "temperature": "temperature",
        "humidity": "humidity",
        "pressure": "pressure",
    }.get(view_id)


def available_cube_summary(cubes: Sequence[CubeDisplay]) -> str:
    """Plain text with the cube names, used in descriptions and logs."""
    return ", ".join(cube.name for cube in cubes)
