from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, time
from types import MappingProxyType

import pytest

from octapihub.application.matrix.canvas import BLACK, GREEN, RED, YELLOW, apply_brightness
from octapihub.application.matrix.overlays import Alert, AlertDetector, Overlay
from octapihub.application.matrix.service import (
    FAILURE_FLASH_GAP_SECONDS,
    MatrixDisplayService,
    ambient_factor,
    in_night_window,
)
from octapihub.application.matrix.state import CubeDisplay, DisplayState, Reading
from octapihub.application.matrix.views import VIEWS, ViewContext, ViewUi
from octapihub.application.sense_hat import JoystickAction, JoystickDirection, SenseHatPort
from octapihub.core.config import SenseHatMatrixConfig
from octapihub.core.errors import SenseHatError
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter

DAY = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)
NIGHT = datetime(2026, 11, 25, 23, 30, tzinfo=UTC)
VIEWS_ENABLED = ("brightness", "overview", "cubes", "off")


def matrix_config(**changes: object) -> SenseHatMatrixConfig:
    base = SenseHatMatrixConfig(
        True, 0, False, False, brightness=100, default_view="brightness", views=VIEWS_ENABLED
    )
    return replace(base, **changes)  # type: ignore[arg-type]


class Rig:
    """A display service wired to a fake board and a mutable state."""

    def __init__(
        self,
        config: SenseHatMatrixConfig | None = None,
        *,
        joystick: bool = True,
        state: DisplayState | None = None,
        failures: dict[str, Callable[[], Exception]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.device = FakeSenseHat(failures=failures)
        self.port: SenseHatPort | None = SenseHatAdapter(self.device)
        self.state = state or DisplayState(now=DAY)
        self.provider_error: Exception | None = None
        self.service = MatrixDisplayService(
            config or matrix_config(),
            lambda: self.port,
            self._provide,
            joystick_enabled=joystick,
            tick_seconds=0.005,
            **({"monotonic": clock} if clock else {}),  # type: ignore[arg-type]
        )

    def _provide(self) -> DisplayState:
        if self.provider_error is not None:
            raise self.provider_error
        return self.state

    @property
    def frames(self) -> list[tuple[tuple[int, int, int], ...]]:
        return [
            tuple(tuple(p) for p in call[1][0])  # type: ignore[misc]
            for call in self.device.calls
            if call[0] == "set_pixels"
        ]

    def emit(self, direction: str, action: str = "pressed") -> None:
        self.device.emit_joystick_event(1.0, direction, action)


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def expected(view_id: str, state: DisplayState, ui: ViewUi, percent: int, t: float = 0.0):  # type: ignore[no-untyped-def]
    return apply_brightness(VIEWS[view_id].render(ViewContext(state, ui, t)), percent)


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ---------- drawing ----------


def test_first_frame_is_the_default_view_with_brightness_applied() -> None:
    rig = Rig(matrix_config(brightness=50))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()

    run(scenario())
    assert rig.frames[0] == expected("brightness", rig.state, ViewUi(brightness=50), 50)
    assert "set_rotation" in rig.device.operations


def test_rotation_and_low_light_are_applied_once_to_the_board() -> None:
    rig = Rig(matrix_config(rotation=180, low_light=True))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()

    run(scenario())
    assert ("set_rotation", (180, True), {}) in [(c[0], c[1], c[2]) for c in rig.device.calls]
    assert ("low_light.set", (True,), {}) in [(c[0], c[1], c[2]) for c in rig.device.calls]


def test_unchanged_frames_are_not_rewritten() -> None:
    rig = Rig()  # brightness view is static

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await asyncio.sleep(0.15)  # many ticks
        await rig.service.stop()

    run(scenario())
    assert len(rig.frames) == 1


def test_stop_blanks_the_matrix_and_start_twice_is_rejected() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.service.start()
        with pytest.raises(RuntimeError):
            await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()
        await rig.service.stop()  # stopping twice is harmless
        assert rig.service.status.state == "stopped"

    run(scenario())
    assert "clear" in rig.device.operations


def test_disabled_matrix_never_touches_the_hardware() -> None:
    rig = Rig(matrix_config(enabled=False))

    async def scenario() -> None:
        await rig.service.start()
        await asyncio.sleep(0.05)
        assert rig.service.status.state == "disabled"
        await rig.service.stop()

    run(scenario())
    assert rig.device.operations == ["has_colour_sensor"]


# ---------- joystick ----------


def test_joystick_switches_views_and_the_new_view_is_drawn() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.emit("right")
        await until(lambda: rig.service.status.view_id == "overview")
        await until(lambda: len(rig.frames) >= 2)
        assert rig.service.status.view_id == "overview"
        rig.emit("left")
        await until(lambda: rig.service.status.view_id == "brightness")
        await rig.service.stop()

    run(scenario())
    assert rig.frames[0] != rig.frames[1]


def test_joystick_is_ignored_when_disabled_in_the_settings() -> None:
    rig = Rig(joystick=False)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.emit("right")
        await asyncio.sleep(0.1)
        assert rig.service.status.view_id == "brightness"
        await rig.service.stop()

    run(scenario())
    assert "stick.get_events" not in rig.device.operations


def test_up_down_change_the_brightness_in_the_brightness_view() -> None:
    rig = Rig(matrix_config(brightness=50))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.emit("up")
        await until(lambda: rig.service.status.brightness == 60)
        await until(lambda: len(rig.frames) >= 2)
        await rig.service.stop()

    run(scenario())
    assert rig.frames[-1] == expected("brightness", rig.state, ViewUi(brightness=60), 60)


def test_long_press_turns_the_display_off_and_on_and_blanks_the_preview() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.emit("middle", "pressed")
        rig.emit("middle", "held")
        await until(lambda: not rig.service.status.display_on)
        await until(lambda: rig.frames[-1] == (BLACK,) * 64)
        assert rig.service.status.frame == (BLACK,) * 64
        rig.emit("middle", "released")
        rig.emit("middle", "pressed")
        rig.emit("middle", "held")
        await until(lambda: rig.service.status.display_on)
        await rig.service.stop()

    run(scenario())


def test_short_press_scrolls_an_info_text_that_ends_by_itself() -> None:
    clock = {"t": 100.0}
    rig = Rig(clock=lambda: clock["t"])

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.emit("middle", "pressed")
        rig.emit("middle", "released")
        await until(lambda: rig.service._overlay is not None)
        clock["t"] += 1.0
        await until(lambda: len(rig.frames) >= 2)
        clock["t"] += 60.0  # far past the end of the text
        await until(lambda: rig.service._overlay is None)
        await rig.service.stop()

    run(scenario())


# ---------- night mode and ambient brightness ----------


@pytest.mark.parametrize(
    ("now", "start", "end", "expected_result"),
    [
        (time(23, 0), "22:00", "07:00", True),
        (time(3, 0), "22:00", "07:00", True),
        (time(7, 0), "22:00", "07:00", False),
        (time(6, 59), "22:00", "07:00", True),
        (time(22, 0), "22:00", "07:00", True),
        (time(12, 0), "22:00", "07:00", False),
        (time(12, 0), "09:00", "17:00", True),
        (time(18, 0), "09:00", "17:00", False),
        (time(12, 0), "08:00", "08:00", False),
    ],
)
def test_night_window(now: time, start: str, end: str, expected_result: bool) -> None:
    assert in_night_window(now, start, end) is expected_result


def test_ambient_factor_dims_in_dark_rooms_and_never_blanks() -> None:
    assert ambient_factor(None) == 1.0
    assert ambient_factor((0, 0, 0, 0)) == pytest.approx(0.25)
    assert ambient_factor((1, 1, 1, 200)) == pytest.approx(1.0)
    assert ambient_factor((1, 1, 1, 10_000)) == pytest.approx(1.0)
    assert ambient_factor((1, 1, 1, -50)) == pytest.approx(0.25)


def test_night_mode_off_blanks_until_a_touch_wakes_it_dimmed() -> None:
    rig = Rig(
        matrix_config(night_enabled=True, night_mode="off", brightness=100),
        state=DisplayState(now=NIGHT),
    )

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        assert rig.frames[-1] == (BLACK,) * 64
        assert rig.service.status.night
        rig.emit("right")
        await until(lambda: rig.service.status.view_id == "overview")
        await until(lambda: rig.frames[-1] != (BLACK,) * 64)
        assert rig.service.status.effective_brightness <= 20  # woken, but dimmed
        await rig.service.stop()

    run(scenario())


def test_night_mode_dim_keeps_the_display_at_five_percent() -> None:
    rig = Rig(
        matrix_config(night_enabled=True, night_mode="dim", brightness=100),
        state=DisplayState(now=NIGHT),
    )

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()

    run(scenario())
    assert rig.service.status.effective_brightness == 5
    assert max(max(p) for p in rig.frames[0]) <= 13


def test_daytime_is_unaffected_by_the_night_setting() -> None:
    rig = Rig(matrix_config(night_enabled=True, night_mode="off"), state=DisplayState(now=DAY))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()

    run(scenario())
    assert not rig.service.status.night
    assert rig.frames[0] != (BLACK,) * 64


def test_ambient_brightness_uses_the_colour_sensor() -> None:
    dark = DisplayState(now=DAY, readings=MappingProxyType({"color": Reading(rgba=(0, 0, 0, 0))}))
    rig = Rig(matrix_config(ambient_brightness=True, brightness=100), state=dark)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        await rig.service.stop()

    run(scenario())
    assert rig.service.status.effective_brightness == 25


# ---------- fault isolation ----------


def test_failing_matrix_is_reported_and_recovers_without_stopping_the_hub() -> None:
    rig = Rig(failures={"set_pixels": lambda: OSError("i2c error")})

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: rig.service.status.state == "error")
        status = rig.service.status
        assert status.consecutive_failures >= 1
        assert status.last_error_code is not None
        assert status.last_error and "i2c" not in status.last_error
        rig.device._failures.clear()
        await until(lambda: rig.service.status.state == "running", timeout=3)
        assert rig.service.status.consecutive_failures == 0
        assert rig.service.status.last_error is None
        await rig.service.stop()

    run(scenario())
    assert rig.frames  # drawing resumed after the outage


def test_state_provider_errors_are_isolated_and_hidden_from_the_user() -> None:
    rig = Rig()
    rig.provider_error = RuntimeError("secret internals")

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: rig.service.status.state == "error")
        assert "secret" not in (rig.service.status.last_error or "")
        assert rig.service.status.last_error_code == "CH-GENERIC-000"
        rig.provider_error = None
        await until(lambda: rig.service.status.state == "running")
        await rig.service.stop()

    run(scenario())


def test_missing_port_is_unavailable_until_it_shows_up() -> None:
    rig = Rig()
    rig.port = None

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: rig.service.status.state == "unavailable")
        assert not rig.frames
        rig.port = SenseHatAdapter(rig.device)
        await until(lambda: bool(rig.frames))
        await until(lambda: rig.service.status.state == "running")
        await rig.service.stop()

    run(scenario())


def test_a_board_without_led_matrix_is_an_error_not_a_crash() -> None:
    rig = Rig()

    class NoMatrix(SenseHatAdapter):
        @property
        def capabilities(self):  # type: ignore[no-untyped-def]
            return replace(super().capabilities, led_matrix=False)

    rig.port = NoMatrix(rig.device)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: rig.service.status.state == "error")
        await rig.service.stop()

    run(scenario())
    assert not rig.frames


def test_project_errors_from_the_board_keep_their_code() -> None:
    rig = Rig(failures={"get_pixels": lambda: SenseHatError("x")})
    rig.device._failures["set_pixels"] = lambda: SenseHatError("write failed")

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: rig.service.status.state == "error")
        assert rig.service.status.last_error_code is not None
        assert rig.service.status.last_error_code.startswith("CH-SENSE")
        await rig.service.stop()

    run(scenario())


# ---------- controls and overlays ----------


def test_web_controls_change_the_display() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        assert rig.service.show_view("cubes")
        assert not rig.service.show_view("compass")  # not enabled
        await until(lambda: rig.service.status.view_id == "cubes")
        rig.service.set_brightness(33)
        assert rig.service.status.brightness == 30
        rig.service.set_display(False)
        await until(lambda: rig.frames[-1] == (BLACK,) * 64)
        await rig.service.stop()

    run(scenario())


def test_virtual_joystick_uses_the_same_rules_as_the_hardware() -> None:
    rig = Rig()

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.service.inject(JoystickDirection.RIGHT, JoystickAction.PRESSED, rig.state)
        assert rig.service.status.view_id == "overview"
        await rig.service.stop()

    run(scenario())


def test_face_and_action_feedback_overlays() -> None:
    state = DisplayState(
        now=DAY,
        cubes=(CubeDisplay("A", "light", "ok", True), CubeDisplay("B", "music", "ok", True)),
    )
    clock = {"t": 50.0}
    rig = Rig(state=state, clock=lambda: clock["t"])

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.service.notify_face("B", 5)
        await until(lambda: any(RED not in f and f != rig.frames[0] for f in rig.frames[1:]))
        face_frame = rig.frames[-1]
        assert face_frame != rig.frames[0]
        clock["t"] += 5
        rig.service.notify_action("A", ok=True)
        await until(lambda: GREEN in rig.frames[-1])
        clock["t"] += 5
        rig.service.notify_action("A", ok=False)
        await until(lambda: RED in rig.frames[-1])
        await rig.service.stop()

    run(scenario())


def test_failure_flashes_are_rate_limited_per_cube() -> None:
    clock = {"t": 10.0}
    rig = Rig(clock=lambda: clock["t"])
    rig.service.notify_action("A", ok=False)
    first = rig.service._overlay
    assert first is not None
    rig.service._overlay = None
    clock["t"] += FAILURE_FLASH_GAP_SECONDS - 1
    rig.service.notify_action("A", ok=False)
    assert rig.service._overlay is None
    rig.service.notify_action("B", ok=False)
    assert rig.service._overlay is not None
    rig.service._overlay = None
    clock["t"] += FAILURE_FLASH_GAP_SECONDS
    rig.service.notify_action("A", ok=False)
    assert rig.service._overlay is not None


def test_no_feedback_while_the_display_is_off_or_it_is_night() -> None:
    rig = Rig()
    rig.service.set_display(False)
    rig.service.notify_action("A", ok=True)
    rig.service.notify_face("A", 3)
    assert rig.service._overlay is None
    night = Rig(matrix_config(night_enabled=True, night_mode="off"), state=DisplayState(now=NIGHT))

    async def scenario() -> None:
        await night.service.start()
        await until(lambda: bool(night.frames))
        night.service.notify_action("A", ok=True)
        assert night.service._overlay is None
        await night.service.stop()

    run(scenario())


def test_a_higher_priority_overlay_is_not_replaced_by_a_lower_one() -> None:
    clock = {"t": 1.0}
    rig = Rig(clock=lambda: clock["t"])
    rig.service._offer(Overlay.icon_flash("warning", RED, 1.0, 3.0, priority=3))
    rig.service._offer(Overlay.face_flash(0, 1, 2, 1.0))
    assert rig.service._overlay is not None
    assert rig.service._overlay.kind == "icon"
    clock["t"] = 10.0  # the alert has expired, so a lower priority may take over
    rig.service._offer(Overlay.face_flash(0, 1, 2, 10.0))
    assert rig.service._overlay.kind == "face"


def test_restart_applies_new_settings_and_keeps_the_view_when_possible() -> None:
    rig = Rig(matrix_config(brightness=100))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.service.show_view("cubes")
        await rig.service.restart(
            matrix_config(brightness=40, views=("brightness", "cubes", "lamp")),
            joystick_enabled=True,
        )
        assert rig.service.status.view_id == "cubes"
        assert rig.service.status.brightness == 40
        await rig.service.restart(
            matrix_config(views=("brightness", "overview"), default_view="overview"),
            joystick_enabled=False,
        )
        assert rig.service.status.view_id == "overview"  # cubes no longer enabled
        await rig.service.stop()

    run(scenario())


# ---------- alerts ----------


def cubes_state(*cubes: CubeDisplay, **changes: object) -> DisplayState:
    return DisplayState(now=DAY, cubes=cubes, **changes)  # type: ignore[arg-type]


def cube(name: str = "A", connected: bool = True, battery: int | None = None) -> CubeDisplay:
    return CubeDisplay(name, "light", "ok" if connected else "warn", connected, None, battery)


def test_first_snapshot_only_sets_the_baseline() -> None:
    detector = AlertDetector()
    assert detector.detect(cubes_state(cube(connected=False, battery=3))) == []


def test_connection_loss_and_return_are_reported_once() -> None:
    detector = AlertDetector()
    detector.detect(cubes_state(cube()))
    lost = detector.detect(cubes_state(cube(connected=False)))
    assert [a.key for a in lost] == ["lost:A"]
    assert lost[0].icon == "bluetooth"
    assert detector.detect(cubes_state(cube(connected=False))) == []
    back = detector.detect(cubes_state(cube()))
    assert [a.key for a in back] == ["back:A"]
    assert back[0].color == GREEN


def test_battery_alerts_fire_on_crossing_and_again_only_after_recharging() -> None:
    detector = AlertDetector()
    detector.detect(cubes_state(cube(battery=50)))
    assert detector.detect(cubes_state(cube(battery=30))) == []
    low = detector.detect(cubes_state(cube(battery=19)))
    assert [(a.key, a.color) for a in low] == [("battery:A", YELLOW)]
    assert detector.detect(cubes_state(cube(battery=18))) == []  # still low: no nagging
    critical = detector.detect(cubes_state(cube(battery=9)))
    assert [(a.key, a.color) for a in critical] == [("battery:A", RED)]
    assert detector.detect(cubes_state(cube(battery=8))) == []
    assert detector.detect(cubes_state(cube(battery=100))) == []  # recharged
    again = detector.detect(cubes_state(cube(battery=5)))
    assert [a.color for a in again] == [RED]


def test_component_errors_alert_on_the_transition_only() -> None:
    detector = AlertDetector()
    detector.detect(cubes_state(hue_level="ok", sense_level="ok", host_level="ok"))
    alerts = detector.detect(
        cubes_state(hue_level="error", sense_level="error", host_level="error")
    )
    assert {a.key for a in alerts} == {"hue", "sense", "host"}
    assert (
        detector.detect(cubes_state(hue_level="error", sense_level="error", host_level="error"))
        == []
    )


def test_new_and_unknown_cubes_do_not_alert() -> None:
    detector = AlertDetector()
    detector.detect(cubes_state(cube("A")))
    assert detector.detect(cubes_state(cube("A"), cube("B", connected=False))) == []


def test_service_shows_alerts_as_icons() -> None:
    rig = Rig(state=cubes_state(cube()))

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames))
        rig.state = cubes_state(cube(connected=False))
        await until(lambda: any(RED in frame for frame in rig.frames))
        await rig.service.stop()

    run(scenario())


def test_alert_dataclass_defaults() -> None:
    alert = Alert("k", "bulb", RED)
    assert alert.seconds == 3.0
    assert alert.priority == 3
