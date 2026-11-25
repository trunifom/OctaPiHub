from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from octapihub.application.matrix.canvas import (
    BLACK,
    CYAN,
    FONT_CHARACTERS,
    GLYPH_HEIGHT,
    GLYPH_WIDTH,
    GRAY,
    GREEN,
    ICONS,
    RED,
    WHITE,
    YELLOW,
    Canvas,
    apply_brightness,
    glyph,
    gradient,
    hsv,
    mix,
    normalize_text,
    scale,
    text_columns,
    text_width,
)
from octapihub.application.matrix.controller import (
    AUTO_CYCLE_PAUSE_SECONDS,
    DisplayController,
    Effect,
    logical_direction,
)
from octapihub.application.matrix.state import (
    CubeDisplay,
    DisplayState,
    Reading,
    battery_level,
    overall_level,
    status_messages,
    worst_level,
)
from octapihub.application.matrix.views import (
    BRIGHTNESS_STEPS,
    LAMP_PRESETS,
    VIEWS,
    ViewContext,
    ViewUi,
)
from octapihub.application.sense_hat import JoystickAction, JoystickDirection, SenseHatInputEvent
from octapihub.core.config import MATRIX_VIEW_IDS, SenseHatMatrixConfig

NOW = datetime(2026, 11, 25, 14, 35, 42, tzinfo=UTC)
UP, DOWN = JoystickDirection.UP, JoystickDirection.DOWN
LEFT, RIGHT = JoystickDirection.LEFT, JoystickDirection.RIGHT
MIDDLE = JoystickDirection.MIDDLE


def cube(name: str = "Licht", level: str = "ok", **kw: object) -> CubeDisplay:
    base = {"role": "light", "connected": True, "last_face": None, "battery_percent": None}
    base.update(kw)
    return CubeDisplay(name=name, level=level, **base)  # type: ignore[arg-type]


def state(**changes: object) -> DisplayState:
    base: dict[str, object] = {"now": NOW}
    base.update(changes)
    return DisplayState(**base)  # type: ignore[arg-type]


def full_state() -> DisplayState:
    return state(
        cubes=(
            cube("Licht", "ok", last_face=3, battery_percent=90),
            cube("Musik", "warn", last_face=0, battery_percent=15, connected=False),
            cube("Extra", "error", battery_percent=4),
        ),
        hue_level="ok",
        sense_level="warn",
        host_level="ok",
        readings=MappingProxyType(
            {
                "temperature": Reading(value=21.7),
                "humidity": Reading(value=45.2),
                "pressure": Reading(value=1013.4),
                "compass": Reading(value=123.0),
                "orientation": Reading(vector=(2.0, -3.0, 90.0)),
                "color": Reading(rgba=(100, 50, 25, 200)),
            }
        ),
        history=MappingProxyType(
            {
                "temperature": (20.0, 20.5, 21.0, 21.5, 21.7),
                "humidity": (40.0, 42.0, 41.0, 45.0, 45.2),
                "pressure": (1010.0, 1011.0, 1012.0, 1013.0, 1013.4),
            }
        ),
    )


def render(view_id: str, st: DisplayState | None = None, t: float = 0.0, **ui: object):  # type: ignore[no-untyped-def]
    return VIEWS[view_id].render(ViewContext(st or full_state(), ViewUi(**ui), t))  # type: ignore[arg-type]


def at(frame, x: int, y: int):  # type: ignore[no-untyped-def]
    return frame[y * 8 + x]


def lit(frame):  # type: ignore[no-untyped-def]
    return {(i % 8, i // 8) for i, pixel in enumerate(frame) if pixel != BLACK}


# ---------- canvas, colours, font ----------


def test_canvas_ignores_drawing_outside_and_reads_black() -> None:
    canvas = Canvas()
    for x, y in ((-1, 0), (0, -1), (8, 0), (0, 8), (100, 100)):
        canvas.set(x, y, RED)
        assert canvas.get(x, y) == BLACK
    canvas.set(7, 7, RED)
    assert canvas.get(7, 7) == RED
    assert len(canvas.frame()) == 64


def test_rect_is_clipped_and_fill_covers_everything() -> None:
    canvas = Canvas()
    canvas.rect(6, 6, 5, 5, RED)
    assert lit(canvas.frame()) == {(6, 6), (7, 6), (6, 7), (7, 7)}
    canvas.fill(GREEN)
    assert set(canvas.frame()) == {GREEN}


def test_outline_and_line_cover_exact_pixels() -> None:
    canvas = Canvas()
    canvas.outline(RED)
    assert len(lit(canvas.frame())) == 28
    line = Canvas()
    line.line(0, 0, 7, 7, WHITE)
    assert lit(line.frame()) == {(i, i) for i in range(8)}
    horizontal = Canvas()
    horizontal.line(7, 3, 0, 3, WHITE)
    assert lit(horizontal.frame()) == {(i, 3) for i in range(8)}
    single = Canvas()
    single.line(2, 2, 2, 2, WHITE)
    assert lit(single.frame()) == {(2, 2)}


def test_every_glyph_is_three_by_five_and_every_icon_is_eight_by_eight() -> None:
    for character in FONT_CHARACTERS:
        rows = glyph(character)
        assert len(rows) == GLYPH_HEIGHT
        assert all(len(row) == GLYPH_WIDTH and set(row) <= {"#", "."} for row in rows)
    for name, rows in ICONS.items():
        assert len(rows) == 8, name
        assert all(len(row) == 8 and set(row) <= {"#", "."} for row in rows), name


def test_font_covers_digits_letters_and_status_symbols() -> None:
    needed = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ -.:%+!?/=,_*°")
    assert needed <= FONT_CHARACTERS


def test_text_normalization_and_measurement() -> None:
    assert normalize_text("Würfel größe") == "WURFEL GROSSE"
    assert normalize_text("Äpfel Öl Über ß") == "APFEL OL UBER SS"
    assert normalize_text("a~b") == "A?B"
    assert text_width("") == 0
    assert text_width("A") == 3
    assert text_width("AB") == 7
    assert len(text_columns("AB")) == 8  # two glyphs plus one gap column each
    assert glyph("§") == glyph("?")


def test_text_draws_digits_where_expected() -> None:
    canvas = Canvas()
    end = canvas.text("21", 0, 0, WHITE)
    assert end == 8
    frame = canvas.frame()
    expected_two = {
        (x, y) for y, row in enumerate(glyph("2")) for x, c in enumerate(row) if c == "#"
    }
    assert {p for p in lit(frame) if p[0] < 3} == expected_two


def test_colour_helpers_handle_edge_cases() -> None:
    assert scale((100, 200, 250), 2) == (200, 255, 255)
    assert scale((100, 100, 100), -1) == BLACK
    assert scale((100, 100, 100), math.nan) == BLACK
    assert mix(BLACK, WHITE, 0.5) == (128, 128, 128)
    assert mix(BLACK, WHITE, 5) == WHITE
    assert mix(BLACK, WHITE, -5) == BLACK
    assert hsv(0) == RED
    assert hsv(120)[1] == 255
    assert hsv(360) == hsv(0)
    assert hsv(math.nan) == hsv(0)
    stops = ((0.0, BLACK), (10.0, WHITE))
    assert gradient(-5, stops) == BLACK
    assert gradient(5, stops) == (128, 128, 128)
    assert gradient(50, stops) == WHITE
    assert gradient(math.nan, stops) == BLACK
    assert gradient(1, ()) == BLACK


def test_brightness_scaling_is_linear_and_bounded() -> None:
    frame = (WHITE,) * 64
    assert apply_brightness(frame, 100) == frame
    assert apply_brightness(frame, 0) == (BLACK,) * 64
    assert apply_brightness(frame, 50)[0] == (128, 128, 128)
    assert apply_brightness(frame, 500) == frame
    assert apply_brightness(frame, -5) == (BLACK,) * 64


# ---------- state helpers ----------


def test_levels_and_messages() -> None:
    assert worst_level(["ok", "error", "warn"]) == "error"
    assert worst_level(["off", "off"]) == "off"
    assert worst_level([]) == "off"
    assert [battery_level(p) for p in (None, 100, 21, 20, 11, 10, 0)] == [
        "off",
        "ok",
        "ok",
        "warn",
        "warn",
        "error",
        "error",
    ]
    assert overall_level(state()) == "off"
    assert overall_level(state(hue_level="ok")) == "ok"
    assert overall_level(full_state()) == "error"
    healthy = state(cubes=(cube(battery_percent=5),), hue_level="ok")
    assert overall_level(healthy) == "error"  # a critical battery counts
    texts = [text for text, _ in status_messages(full_state())]
    assert any("AKKU 4%" in text for text in texts)
    assert status_messages(full_state())[0][1] == "error"  # most important first


# ---------- views: contract ----------


def test_registry_matches_the_configuration_names() -> None:
    assert tuple(VIEWS) == MATRIX_VIEW_IDS
    for view_id, view in VIEWS.items():
        assert view.id == view_id
        assert view.label
        assert view.description
        assert view.controls


STATES = {
    "empty": state(),
    "full": full_state(),
    "single": state(cubes=(cube(),)),
    "garbage": state(
        cubes=(cube(last_face=99, battery_percent=250),),
        readings=MappingProxyType(
            {
                "temperature": Reading(value=math.nan),
                "humidity": Reading(value=-500.0),
                "pressure": Reading(value=math.inf),
                "compass": Reading(value=math.nan),
                "orientation": Reading(vector=(math.nan, math.inf, 0.0)),
                "color": Reading(rgba=(0, 0, 0, 0)),
            }
        ),
        history=MappingProxyType({"temperature": (math.nan, math.inf, 1.0)}),
    ),
}


@pytest.mark.parametrize("view_id", MATRIX_VIEW_IDS)
@pytest.mark.parametrize("state_name", list(STATES))
@pytest.mark.parametrize("t", [0.0, 0.37, 1.0, 12.9, 1234.5])
def test_every_view_always_renders_a_valid_frame(view_id: str, state_name: str, t: float) -> None:
    frame = render(view_id, STATES[state_name], t)
    assert len(frame) == 64
    for pixel in frame:
        assert len(pixel) == 3
        assert all(isinstance(c, int) and 0 <= c <= 255 for c in pixel)


@pytest.mark.parametrize("view_id", MATRIX_VIEW_IDS)
def test_descriptions_use_only_drawable_characters(view_id: str) -> None:
    for st in STATES.values():
        text = VIEWS[view_id].describe(st)
        assert "?" not in normalize_text(text), text
        assert all(c in FONT_CHARACTERS for c in normalize_text(text))
        assert "NAN" not in text.upper()
        assert "INF" not in text.upper()


@pytest.mark.parametrize("view_id", MATRIX_VIEW_IDS)
def test_views_are_deterministic_for_equal_input(view_id: str) -> None:
    assert render(view_id, t=3.3) == render(view_id, t=3.3)


def test_animated_flag_matches_real_animation() -> None:
    for view_id, view in VIEWS.items():
        frames = {render(view_id, t=t) for t in (0.0, 0.3, 0.6, 1.1, 2.7, 4.2)}
        if view.animated:
            continue  # animated views may legitimately repeat for a given state
        assert len(frames) == 1, f"{view_id} claims to be static but changes over time"
    for view_id in ("rainbow", "breathing", "clock", "pressure", "ticker"):
        assert len({render(view_id, t=t) for t in (0.0, 0.3, 0.6, 1.1, 2.7)}) > 1, view_id


# ---------- views: details ----------


def test_overview_shows_traffic_lights_and_blinks_errors() -> None:
    st = state(
        cubes=(cube(level="ok"), cube("B", level="error"), cube("C", level="warn")),
        hue_level="ok",
        sense_level="error",
        host_level="off",
    )
    on = render("overview", st, t=0.0)
    off = render("overview", st, t=0.6)
    assert at(on, 0, 0) == GREEN
    assert at(on, 3, 0) == RED
    assert at(off, 3, 0) != RED  # error cell is dimmed in the off phase
    assert at(on, 6, 0) == YELLOW
    assert at(on, 6, 3) != BLACK
    assert at(on, 2, 0) == BLACK  # the gap between cells stays dark
    empty = render("overview", state(), t=0.0)
    assert at(empty, 0, 0) == (22, 22, 22)  # absent cubes are dim, not missing


def test_cubes_view_shows_face_connection_and_selected_cube() -> None:
    st = full_state()
    first = render("cubes", st)
    assert at(first, 1, 1) == WHITE  # selector dot of cube 1
    assert at(first, 1, 3) == GRAY
    face = {p for p in lit(first) if 2 <= p[0] <= 4 and 1 <= p[1] <= 5}
    assert face == {
        (x + 2, y + 1) for y, row in enumerate(glyph("3")) for x, c in enumerate(row) if c == "#"
    }
    second = render("cubes", st, sub={"cubes": 1})
    assert at(second, 1, 3) == WHITE
    assert RED in second  # disconnected cube shows a red X during the on phase
    third = render("cubes", st, sub={"cubes": 2})
    assert at(third, 1, 5) == WHITE
    assert VIEWS["cubes"].sub_count(st) == 3
    assert VIEWS["cubes"].sub_count(state()) == 0
    assert RED not in render("cubes", state())  # no cubes: plain icon
    assert render("cubes", st, sub={"cubes": 99}) == render("cubes", st, sub={"cubes": 2})


def test_cube_battery_bar_height_and_colours() -> None:
    def bar(percent: int | None, t: float = 0.0):  # type: ignore[no-untyped-def]
        st = state(cubes=(cube(battery_percent=percent, last_face=1),))
        frame = render("cubes", st, t=t)
        return [(y, at(frame, 6, y)) for y in range(1, 7) if at(frame, 6, y) != BLACK]

    assert len(bar(100)) == 6
    assert len(bar(50)) == 3
    assert len(bar(1)) == 1
    assert bar(0) == []
    assert {c for _, c in bar(80)} == {GREEN}
    assert {c for _, c in bar(15)} == {YELLOW}
    assert {c for _, c in bar(5, t=0.0)} == {RED}
    assert {c for _, c in bar(5, t=0.3)} != {RED}  # critical level blinks
    assert {c for _, c in bar(None)} == {GRAY}


def test_battery_view_draws_one_column_pair_per_cube() -> None:
    frame = render("battery", t=0.0)
    column_heights = [sum(1 for y in range(8) if at(frame, x, y) != BLACK) for x in (0, 3, 6)]
    assert column_heights == [7, 1, 1]  # 90 %, 15 %, 4 % (at least one row while above 0)
    assert at(frame, 0, 7) == GREEN
    assert at(frame, 3, 7) == YELLOW
    assert at(frame, 2, 7) == BLACK  # gap between bars
    assert at(frame, 1, 7) == GREEN  # bars are two pixels wide
    unknown = render("battery", state(cubes=(cube(battery_percent=None),)))
    assert at(unknown, 0, 0) == GRAY
    assert at(unknown, 0, 1) == BLACK  # dotted, not solid
    only_icon = render("battery", state())
    assert lit(only_icon)  # shows an icon rather than a blank screen


@pytest.mark.parametrize(
    ("value", "expected"),
    [(21.4, "21"), (5.2, " 5"), (0.0, " 0"), (99.9, "99"), (150.0, "99"), (-5.0, "-5")],
)
def test_temperature_digits(value: float, expected: str) -> None:
    st = state(readings=MappingProxyType({"temperature": Reading(value=value)}))
    frame = render("temperature", st)
    expected_pixels: set[tuple[int, int]] = set()
    reference = Canvas()
    reference.text(expected, 0, 0, WHITE)
    expected_pixels = lit(reference.frame())
    digits = {p for p in lit(frame) if p[1] <= 4 and p[0] <= 6}
    assert digits == expected_pixels


def test_temperature_colour_follows_the_value_and_missing_value_shows_icon() -> None:
    def colour(value: float):  # type: ignore[no-untyped-def]
        frame = render(
            "temperature", state(readings=MappingProxyType({"temperature": Reading(value=value)}))
        )
        return next(p for p in frame if p != BLACK)

    cold, comfortable, hot = colour(2.0), colour(21.0), colour(35.0)
    assert cold[2] > cold[0]  # bluish
    assert comfortable[1] > comfortable[0]  # greenish
    assert hot[0] > hot[2]  # reddish
    missing = render("temperature", state())
    assert lit(missing)
    assert RED not in missing


def test_scalar_views_show_a_scale_bar_proportional_to_the_value() -> None:
    def bar(group: str, value: float) -> int:
        st = state(readings=MappingProxyType({group: Reading(value=value)}))
        frame = render(group, st)
        return sum(1 for x in range(8) if at(frame, x, 7) != BLACK)

    assert bar("humidity", 0.0) == 0
    assert bar("humidity", 50.0) == 4
    assert bar("humidity", 100.0) == 8
    assert bar("temperature", -10.0) == 0
    assert bar("temperature", 40.0) == 8


def test_pressure_scrolls_and_handles_missing_values() -> None:
    st = state(readings=MappingProxyType({"pressure": Reading(value=1013.0)}))
    assert len({render("pressure", st, t=t) for t in (0.0, 0.4, 0.8, 1.2)}) == 4
    assert render("pressure", state()) != render("pressure", st)


def test_graph_scales_recent_values_and_selects_metrics() -> None:
    rising = state(history=MappingProxyType({"temperature": tuple(float(i) for i in range(8))}))
    frame = render("graph", rising)
    heights = [sum(1 for y in range(8) if at(frame, x, y) != BLACK) for x in range(8)]
    assert heights == sorted(heights)
    assert heights[0] == 1
    assert heights[-1] == 8
    flat = state(history=MappingProxyType({"temperature": (20.0,) * 8}))
    flat_heights = {
        sum(1 for y in range(8) if at(render("graph", flat), x, y) != BLACK) for x in range(8)
    }
    assert len(flat_heights) == 1  # a flat series is not exaggerated
    assert 3 <= flat_heights.pop() <= 5
    waiting = render("graph", state())
    assert lit(waiting)
    assert render("graph", full_state(), sub={"graph": 1}) != render(
        "graph", full_state(), sub={"graph": 0}
    )
    assert VIEWS["graph"].sub_count(state()) == 3
    shortened = state(history=MappingProxyType({"temperature": (1.0, 2.0, 3.0)}))
    columns = {x for x, _ in lit(render("graph", shortened))}
    assert columns == {5, 6, 7}  # newest values are right-aligned


def test_clock_marks_the_second_and_binary_clock_encodes_digits() -> None:
    frame = render("clock", t=0.0)
    assert at(frame, int(42 * 8 / 60), 7) != BLACK
    binary = render("clock_binary")
    # 14:35:42 -> digits 1,4,3,5,4,2 in columns 1..6 (bit 0 at the bottom row 6)
    expected = {1: 1, 2: 4, 3: 3, 4: 5, 5: 4, 6: 2}
    for column, digit in expected.items():
        for bit in range(4):
            on = at(binary, column, 6 - bit)
            assert (on[1] > 60 or on[0] > 60) == bool(digit & (1 << bit)), (column, bit)


def test_ticker_uses_the_worst_level_colour_and_falls_back_to_the_name() -> None:
    frames = [render("ticker", full_state(), t=t) for t in (1.0, 2.0, 3.0, 4.0)]
    assert any(RED in frame for frame in frames)
    quiet = [render("ticker", state(), t=t) for t in (1.0, 2.0, 3.0)]
    assert any(lit(frame) for frame in quiet)  # falls back to scrolling the product name


def test_compass_points_in_the_measured_direction() -> None:
    def tip(heading: float) -> tuple[int, int]:
        st = state(readings=MappingProxyType({"compass": Reading(value=heading)}))
        frame = render("compass", st)
        return next(p for p in lit(frame) if at(frame, *p) == CYAN)

    north, east, south, west = tip(0), tip(90), tip(180), tip(270)
    assert north[1] < 3 and abs(north[0] - 3.5) <= 1
    assert east[0] > 4 and abs(east[1] - 3.5) <= 1
    assert south[1] > 4
    assert west[0] < 3
    assert tip(360) == north
    assert tip(-90) == west
    assert lit(render("compass", state()))  # unavailable -> icon, no crash


def test_level_bubble_moves_and_changes_colour_with_tilt() -> None:
    def bubble(pitch: float, roll: float):  # type: ignore[no-untyped-def]
        st = state(readings=MappingProxyType({"orientation": Reading(vector=(pitch, roll, 0.0))}))
        frame = render("level", st)
        pixel = next(p for p in lit(frame) if at(frame, *p) in {GREEN, YELLOW, RED})
        return pixel, at(frame, *pixel)

    flat, flat_color = bubble(0.0, 0.0)
    assert flat_color == GREEN
    assert 3 <= flat[0] <= 4
    right, _ = bubble(0.0, 25.0)
    assert right[0] > flat[0]
    _, slightly = bubble(10.0, 0.0)
    _, steep = bubble(40.0, 0.0)
    assert slightly == YELLOW
    assert steep == RED
    assert bubble(350.0, 0.0)[0] == bubble(-10.0, 0.0)[0]  # angles wrap around


def test_ambient_color_is_normalised_and_shows_light_level() -> None:
    frame = render("ambient")
    assert at(frame, 0, 0) == (255, 128, 64)
    assert sum(1 for x in range(8) if at(frame, x, 7) == WHITE) == 6  # clear=200 of 255
    assert lit(render("ambient", state()))
    black = render("ambient", STATES["garbage"])
    assert len(black) == 64


def test_lamp_uses_presets_value_and_edit_indicator() -> None:
    assert at(render("lamp", lamp_index=0, lamp_value=100), 3, 3) == LAMP_PRESETS[0][1]
    assert at(render("lamp", lamp_index=2, lamp_value=100), 3, 3) == (255, 0, 0)
    dim = at(render("lamp", lamp_index=2, lamp_value=50), 3, 3)
    assert dim == (128, 0, 0)
    assert at(render("lamp", lamp_index=2 + len(LAMP_PRESETS), lamp_value=100), 3, 3) == (255, 0, 0)
    editing = render("lamp", t=0.0, edit_mode=True)
    assert at(editing, 0, 0) == WHITE
    assert at(render("lamp", t=0.0, edit_mode=False), 0, 0) != WHITE
    assert VIEWS["lamp"].has_edit_mode


def test_decorative_views_differ_by_selection_or_time() -> None:
    assert render("rainbow", t=0.0) != render("rainbow", t=1.0)
    assert {render("breathing", t=t) for t in (0.0, 1.25, 2.5)} and len(
        {render("breathing", t=t)[0] for t in (0.0, 1.25, 2.5)}
    ) > 1
    assert render("breathing", sub={"breathing": 0}) != render("breathing", sub={"breathing": 2})
    modes = {render("palette", sub={"palette": i}) for i in range(3)}
    assert len(modes) == 3
    assert VIEWS["breathing"].sub_count(state()) == 4
    assert VIEWS["palette"].sub_count(state()) == 3


def test_brightness_view_shows_the_level_and_off_is_black() -> None:
    def bar(percent: int) -> int:
        frame = render("brightness", brightness=percent)
        return sum(1 for x in range(8) if at(frame, x, 6) == WHITE)

    assert bar(5) == 1
    assert bar(50) == 4
    assert bar(100) == 8
    assert set(render("off")) == {BLACK}


# ---------- controller ----------


def ev(
    direction: JoystickDirection, action: JoystickAction = JoystickAction.PRESSED, ts: float = 0.0
):  # type: ignore[no-untyped-def]
    return SenseHatInputEvent(ts, direction, action)


def matrix(**changes: object) -> SenseHatMatrixConfig:
    base = SenseHatMatrixConfig(
        True, 0, False, False, views=("overview", "cubes", "lamp", "graph", "off")
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def press(
    controller: DisplayController, direction: JoystickDirection, st: DisplayState, now: float = 10.0
) -> Effect:
    return controller.handle(ev(direction), st, now)


def test_left_and_right_walk_through_enabled_views_and_wrap() -> None:
    c = DisplayController(matrix())
    st = full_state()
    assert c.view_id == "overview"
    assert press(c, RIGHT, st) is Effect.VIEW_CHANGED
    assert c.view_id == "cubes"
    for _ in range(4):
        press(c, RIGHT, st)
    assert c.view_id == "overview"  # wrapped
    press(c, LEFT, st)
    assert c.view_id == "off"


def test_default_view_selects_the_start_view() -> None:
    assert DisplayController(matrix(default_view="graph")).view_id == "graph"


def test_up_down_select_within_views_that_have_a_selection() -> None:
    c = DisplayController(matrix())
    st = full_state()
    c.show("cubes")
    press(c, DOWN, st)
    assert c.ui.sub["cubes"] == 1
    press(c, DOWN, st)
    press(c, DOWN, st)
    assert c.ui.sub["cubes"] == 0  # wraps after three cubes
    press(c, UP, st)
    assert c.ui.sub["cubes"] == 2
    c.show("graph")
    press(c, DOWN, st)
    assert c.ui.sub["graph"] == 1


def test_up_down_change_brightness_where_nothing_can_be_selected() -> None:
    c = DisplayController(matrix(brightness=60))
    st = full_state()
    assert c.ui.brightness == 60
    press(c, UP, st)
    assert c.ui.brightness == 70
    press(c, DOWN, st)
    press(c, DOWN, st)
    assert c.ui.brightness == 50
    for _ in range(20):
        press(c, DOWN, st)
    assert c.ui.brightness == BRIGHTNESS_STEPS[0]
    for _ in range(20):
        press(c, UP, st)
    assert c.ui.brightness == 100


def test_brightness_from_config_snaps_to_a_supported_step() -> None:
    assert DisplayController(matrix(brightness=63)).ui.brightness == 60
    assert DisplayController(matrix(brightness=100)).ui.brightness == 100


def test_held_vertical_input_repeats_but_is_rate_limited() -> None:
    c = DisplayController(matrix(brightness=50))
    st = full_state()
    c.handle(ev(UP, JoystickAction.PRESSED), st, 10.0)
    for step in range(1, 6):
        c.handle(ev(UP, JoystickAction.HELD), st, 10.0 + step * 0.05)  # 20 events/s
    assert c.ui.brightness == 70  # the press plus one repeat; 20 events/s are not all accepted
    c.handle(ev(UP, JoystickAction.HELD), st, 10.6)
    assert c.ui.brightness == 80
    c.handle(ev(UP, JoystickAction.RELEASED), st, 10.7)
    assert c.ui.brightness == 80


def test_holding_left_or_right_does_not_fly_through_all_views() -> None:
    c = DisplayController(matrix())
    st = full_state()
    c.handle(ev(RIGHT, JoystickAction.PRESSED), st, 10.0)
    for step in range(10):
        c.handle(ev(RIGHT, JoystickAction.HELD), st, 10.1 + step * 0.2)
    assert c.view_id == "cubes"


def test_short_middle_press_asks_for_an_info_text() -> None:
    c = DisplayController(matrix())
    st = full_state()
    assert c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 10.0) is Effect.NONE
    assert c.handle(ev(MIDDLE, JoystickAction.RELEASED), st, 10.1) is Effect.SHOW_INFO


def test_long_middle_press_toggles_the_display_once_and_not_the_info() -> None:
    c = DisplayController(matrix())
    st = full_state()
    c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 10.0)
    assert c.handle(ev(MIDDLE, JoystickAction.HELD), st, 11.0) is Effect.TOGGLE_DISPLAY
    assert not c.display_on
    assert c.handle(ev(MIDDLE, JoystickAction.HELD), st, 11.3) is Effect.NONE  # repeat ignored
    assert c.handle(ev(MIDDLE, JoystickAction.RELEASED), st, 11.5) is Effect.NONE
    c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 20.0)
    assert c.handle(ev(MIDDLE, JoystickAction.HELD), st, 21.0) is Effect.TOGGLE_DISPLAY
    assert c.display_on


def test_a_sleeping_display_ignores_everything_but_the_middle_button() -> None:
    c = DisplayController(matrix())
    st = full_state()
    c.set_display(False)
    assert press(c, RIGHT, st) is Effect.NONE
    assert c.view_id == "overview"
    press(c, UP, st)
    assert c.ui.brightness == 60
    c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 10.0)
    assert (
        c.handle(ev(MIDDLE, JoystickAction.RELEASED), st, 10.1) is Effect.NONE
    )  # no info while off


def test_lamp_edit_mode_changes_colour_and_value_instead_of_view() -> None:
    c = DisplayController(matrix())
    st = full_state()
    c.show("lamp")
    c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 10.0)
    assert c.handle(ev(MIDDLE, JoystickAction.RELEASED), st, 10.1) is Effect.NONE
    assert c.ui.edit_mode
    press(c, RIGHT, st)
    press(c, RIGHT, st)
    assert (c.view_id, c.ui.lamp_index) == ("lamp", 2)
    press(c, LEFT, st)
    press(c, LEFT, st)
    press(c, LEFT, st)
    assert c.ui.lamp_index == len(LAMP_PRESETS) - 1  # wraps
    press(c, UP, st)  # value already 80 -> 90
    assert c.ui.lamp_value == 90
    for _ in range(20):
        press(c, DOWN, st)
    assert c.ui.lamp_value == 10
    assert c.ui.brightness == 60  # the global brightness was not touched
    c.handle(ev(MIDDLE, JoystickAction.PRESSED), st, 12.0)
    c.handle(ev(MIDDLE, JoystickAction.RELEASED), st, 12.1)
    assert not c.ui.edit_mode
    assert press(c, RIGHT, st) is Effect.VIEW_CHANGED


def test_changing_the_view_leaves_edit_mode() -> None:
    c = DisplayController(matrix())
    c.show("lamp")
    c.ui.edit_mode = True
    c.step_view(1)
    assert not c.ui.edit_mode


@pytest.mark.parametrize(
    ("rotation", "physical", "logical"),
    [
        (0, UP, UP),
        (90, UP, LEFT),
        (90, RIGHT, UP),
        (180, UP, DOWN),
        (180, LEFT, RIGHT),
        (270, UP, RIGHT),
        (270, LEFT, UP),
        (90, MIDDLE, MIDDLE),
    ],
)
def test_joystick_follows_the_picture_rotation(
    rotation: int, physical: JoystickDirection, logical: JoystickDirection
) -> None:
    assert logical_direction(physical, rotation) is logical


def test_rotated_picture_navigates_with_rotated_directions() -> None:
    c = DisplayController(matrix(rotation=90))
    st = full_state()
    assert press(c, UP, st) is Effect.VIEW_CHANGED  # physical up is "left" in the picture
    assert c.view_id == "off"


def test_show_and_set_helpers_validate() -> None:
    c = DisplayController(matrix())
    assert c.show("lamp")
    assert not c.show("compass")  # not enabled
    assert c.view_id == "lamp"
    c.set_brightness(33)
    assert c.ui.brightness == 30
    c.set_display(False)
    assert not c.snapshot.display_on
    assert c.snapshot.view_id == "lamp"


def test_auto_cycle_waits_pauses_after_input_and_stops_when_editing() -> None:
    c = DisplayController(matrix(auto_cycle_seconds=10), now=0.0)
    assert not c.tick(5.0)
    assert c.tick(10.0)
    assert c.view_id == "cubes"
    assert not c.tick(12.0)
    assert c.tick(20.0)
    press(c, UP, full_state(), now=25.0)  # user touched the joystick
    assert not c.tick(30.0)
    assert not c.tick(25.0 + AUTO_CYCLE_PAUSE_SECONDS - 1)
    assert c.tick(25.0 + AUTO_CYCLE_PAUSE_SECONDS + 1)
    c.show("lamp")
    c.ui.edit_mode = True
    assert not c.tick(500.0)
    c.ui.edit_mode = False
    c.set_display(False)
    assert not c.tick(900.0)


def test_auto_cycle_is_off_by_default_and_needs_two_views() -> None:
    assert not DisplayController(matrix(), now=0.0).tick(10_000.0)
    single = DisplayController(matrix(views=("overview",), auto_cycle_seconds=5), now=0.0)
    assert not single.tick(100.0)
