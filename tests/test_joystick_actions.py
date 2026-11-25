from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application.ble import CharacteristicInfo, DeviceProfile
from octapihub.application.ble_runtime import BleRuntime, BleRuntimeTuning
from octapihub.application.cube_service import CubeActivity, CubeService
from octapihub.application.hue_service import HueScene, HueState, HueStatus
from octapihub.application.matrix.canvas import CYAN, GRAY, ICONS, rotate_mask
from octapihub.application.matrix.controller import DisplayController, Effect
from octapihub.application.matrix.service import MatrixDisplayService
from octapihub.application.matrix.state import DisplayState
from octapihub.application.matrix.views import VIEWS, ViewContext, ViewUi
from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import RuleEngine
from octapihub.application.sense_hat import JoystickAction, JoystickDirection, SenseHatInputEvent
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.composition import build_matrix_service
from octapihub.core.config import (
    JOYSTICK_FACE_LABELS,
    JOYSTICK_SOURCE,
    MATRIX_VIEW_IDS,
    AppConfig,
    SenseHatMatrixConfig,
    load_config,
    parse_config,
    save_config,
)
from octapihub.core.errors import ValidationError
from octapihub.infrastructure.ble.fake import FakeTransportFactory
from octapihub.infrastructure.ble.protocol import (
    ORIENTATION_CHARACTERISTIC_UUID,
    decode_orientation,
    supports_orientation_notifications,
)
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.web import forms
from octapihub.web.app import WebBackend, create_app

TOKEN = "joystick-token-12345"
UP, RIGHT, DOWN = JoystickDirection.UP, JoystickDirection.RIGHT, JoystickDirection.DOWN
LEFT, MIDDLE = JoystickDirection.LEFT, JoystickDirection.MIDDLE
PRESSED, HELD, RELEASED = JoystickAction.PRESSED, JoystickAction.HELD, JoystickAction.RELEASED


def scene(scene_id: str) -> dict[str, object]:
    return {"kind": "hue", "action": "scene.activate", "parameters": {"scene_id": scene_id}}


def config(
    tmp_path: Path,
    mappings: list[dict[str, object]] | None = None,
    matrix: dict[str, object] | None = None,
) -> AppConfig:
    return parse_config(
        {
            "schema_version": 5,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": mappings or [],
            "sense_hat": {
                "history": {"database_path": str(tmp_path / "h.sqlite3")},
                "matrix": {
                    "views": ["overview", "control", "clock"],
                    "default_view": "control",
                    **(matrix or {}),
                },
            },
        }
    )


def mapping(source: str, face: int, *actions: dict[str, object]) -> dict[str, object]:
    return {"cube": source, "face": face, "actions": list(actions)}


def ev(direction: JoystickDirection, action: JoystickAction) -> SenseHatInputEvent:
    return SenseHatInputEvent(1.0, direction, action)


# ---------- configuration ----------


def test_joystick_mappings_are_accepted_for_five_inputs(tmp_path: Path) -> None:
    cfg = config(tmp_path, [mapping("joystick", face, scene(f"s{face}")) for face in range(1, 6)])
    assert [(m.cube, m.face) for m in cfg.mappings] == [(JOYSTICK_SOURCE, f) for f in range(1, 6)]
    path = tmp_path / "c.toml"
    save_config(path, cfg)
    assert load_config(path) == cfg


@pytest.mark.parametrize("face", [0, 6, 8, -1])
def test_joystick_has_only_five_inputs(tmp_path: Path, face: int) -> None:
    with pytest.raises(ValidationError, match="from 1 to 5"):
        config(tmp_path, [mapping("Joystick", face, scene("x"))])


def test_cubes_still_have_eight_faces_and_the_name_joystick_is_reserved(tmp_path: Path) -> None:
    assert config(tmp_path, [mapping("Licht", 8, scene("x"))]).mappings[0].face == 8
    with pytest.raises(ValidationError, match="from 1 to 8"):
        config(tmp_path, [mapping("Licht", 9, scene("x"))])
    with pytest.raises(ValidationError, match="reserved"):
        parse_config(
            {
                "schema_version": 5,
                "bluetooth": {"adapter": "hci0"},
                "cubes": [{"name": "JOYSTICK", "address": "AA:BB:CC:DD:EE:01", "role": "light"}],
            }
        )


def test_joystick_and_cube_faces_do_not_clash_and_duplicates_are_rejected(tmp_path: Path) -> None:
    cfg = config(tmp_path, [mapping("Licht", 1, scene("a")), mapping("Joystick", 1, scene("b"))])
    assert len(cfg.mappings) == 2
    with pytest.raises(ValidationError, match="unique"):
        config(tmp_path, [mapping("Joystick", 1, scene("a")), mapping("joystick", 1, scene("b"))])


def test_rule_engine_resolves_joystick_inputs(tmp_path: Path) -> None:
    engine = RuleEngine(config(tmp_path, [mapping("Joystick", 3, scene("x"))]))
    assert engine.actions_for("joystick", 3)[0].parameters == {"scene_id": "x"}
    assert engine.actions_for("Joystick", 4) == ()
    assert engine.required_plugin_kinds == ("hue",)


# ---------- icons ----------


def test_arrow_icons_are_rotations_of_one_arrow() -> None:
    assert ICONS["arrow_right"] == rotate_mask(ICONS["arrow_up"], 1)
    assert rotate_mask(ICONS["arrow_up"], 4) == ICONS["arrow_up"]
    assert rotate_mask(ICONS["arrow_up"], 0) == ICONS["arrow_up"]
    assert rotate_mask(ICONS["arrow_up"], 5) == rotate_mask(ICONS["arrow_up"], 1)
    assert ICONS["arrow_left"] != ICONS["arrow_right"]
    assert {len(ICONS[n]) for n in ("arrow_right", "arrow_down", "arrow_left", "dot")} == {8}


# ---------- controller: control view ----------


def matrix_cfg(**changes: object) -> SenseHatMatrixConfig:
    base = SenseHatMatrixConfig(
        True, 0, False, False, views=("overview", "control", "clock"), default_view="control"
    )
    return replace(base, **changes)  # type: ignore[arg-type]


STATE = DisplayState(now=datetime(2026, 11, 25, 12, 0, tzinfo=UTC))


def tap(controller: DisplayController, direction: JoystickDirection, now: float = 10.0) -> Effect:
    controller.handle(ev(direction, PRESSED), STATE, now)
    return controller.handle(ev(direction, RELEASED), STATE, now + 0.1)


@pytest.mark.parametrize(
    ("direction", "face"),
    [(UP, 1), (RIGHT, 2), (DOWN, 3), (LEFT, 4), (MIDDLE, 5)],
)
def test_a_short_press_in_the_control_view_runs_the_matching_action(
    direction: JoystickDirection, face: int
) -> None:
    controller = DisplayController(matrix_cfg())
    assert tap(controller, direction) is Effect.TRIGGER
    assert controller.take_trigger() == face
    assert controller.take_trigger() is None  # consumed once
    assert controller.view_id == "control"  # arrows did not navigate


def test_nothing_fires_on_press_only_on_release() -> None:
    controller = DisplayController(matrix_cfg())
    assert controller.handle(ev(UP, PRESSED), STATE, 10.0) is Effect.NONE
    assert controller.take_trigger() is None
    assert controller.handle(ev(UP, RELEASED), STATE, 10.1) is Effect.TRIGGER


@pytest.mark.parametrize(("direction", "delta"), [(RIGHT, 1), (LEFT, -1)])
def test_holding_left_or_right_changes_the_view_without_running_an_action(
    direction: JoystickDirection, delta: int
) -> None:
    controller = DisplayController(matrix_cfg())
    controller.handle(ev(direction, PRESSED), STATE, 10.0)
    assert controller.handle(ev(direction, HELD), STATE, 11.0) is Effect.VIEW_CHANGED
    expected = ("overview", "control", "clock")[(1 + delta) % 3]
    assert controller.view_id == expected
    assert controller.handle(ev(direction, HELD), STATE, 11.3) is Effect.NONE  # one step per hold
    assert controller.handle(ev(direction, RELEASED), STATE, 11.6) is Effect.NONE
    assert controller.take_trigger() is None


def test_holding_up_or_down_does_nothing_and_does_not_fire_on_release() -> None:
    controller = DisplayController(matrix_cfg())
    controller.handle(ev(UP, PRESSED), STATE, 10.0)
    assert controller.handle(ev(UP, HELD), STATE, 11.0) is Effect.NONE
    assert controller.handle(ev(UP, RELEASED), STATE, 11.5) is Effect.NONE
    assert controller.take_trigger() is None


def test_a_release_without_a_press_is_ignored() -> None:
    controller = DisplayController(matrix_cfg())
    assert controller.handle(ev(RIGHT, RELEASED), STATE, 10.0) is Effect.NONE


def test_long_middle_press_still_switches_the_display_not_an_action() -> None:
    controller = DisplayController(matrix_cfg())
    controller.handle(ev(MIDDLE, PRESSED), STATE, 10.0)
    assert controller.handle(ev(MIDDLE, HELD), STATE, 11.0) is Effect.TOGGLE_DISPLAY
    assert controller.handle(ev(MIDDLE, RELEASED), STATE, 11.5) is Effect.NONE
    assert controller.take_trigger() is None


def test_a_sleeping_display_ignores_action_inputs() -> None:
    controller = DisplayController(matrix_cfg())
    controller.set_display(False)
    assert tap(controller, UP) is Effect.NONE
    assert controller.take_trigger() is None


def test_rotation_maps_physical_inputs_to_the_programmed_ones() -> None:
    controller = DisplayController(matrix_cfg(rotation=90))
    assert tap(controller, UP) is Effect.TRIGGER
    assert controller.take_trigger() == 4  # physical up is "left" in the rotated picture


def test_other_views_keep_their_normal_joystick_behaviour() -> None:
    controller = DisplayController(matrix_cfg(default_view="overview"))
    tap(controller, RIGHT)
    assert controller.view_id == "control"  # plain navigation: pressed -> next view
    assert controller.take_trigger() is None


def test_control_view_shows_which_inputs_are_programmed() -> None:
    def frame(faces: tuple[int, ...]):  # type: ignore[no-untyped-def]
        st = replace(STATE, joystick_faces=faces)
        return VIEWS["control"].render(ViewContext(st, ViewUi(), 0.0))

    none, all_five = frame(()), frame((1, 2, 3, 4, 5))
    assert {p for p in none if p != (0, 0, 0)} == {(30, 30, 30)}
    assert sum(1 for p in all_five if p == CYAN) == 5 * 4
    only_up = frame((1,))
    assert only_up[0 * 8 + 3] == CYAN  # the up pad at x=3, y=0
    assert only_up[3 * 8 + 0] != CYAN  # the left pad is not programmed
    assert "KEINE" in VIEWS["control"].describe(STATE)
    assert "2 AKTIONEN" in VIEWS["control"].describe(replace(STATE, joystick_faces=(1, 2)))
    assert "control" in MATRIX_VIEW_IDS


# ---------- display service ----------


class Rig:
    def __init__(self, trigger: Callable[[int], bool] | None) -> None:
        self.device = FakeSenseHat()
        self.port = SenseHatAdapter(self.device)
        self.service = MatrixDisplayService(
            matrix_cfg(brightness=100),
            lambda: self.port,
            lambda: STATE,
            joystick_trigger=trigger,
            tick_seconds=0.005,
        )

    def frames(self) -> list[list[tuple[int, ...]]]:
        return [
            [tuple(p) for p in call[1][0]] for call in self.device.calls if call[0] == "set_pixels"
        ]  # type: ignore[misc]

    def emit(self, direction: str, action: str) -> None:
        self.device.emit_joystick_event(1.0, direction, action)


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_service_runs_the_action_and_shows_a_cyan_arrow() -> None:
    called: list[int] = []

    def trigger(face: int) -> bool:
        called.append(face)
        return True

    rig = Rig(trigger)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames()))
        rig.emit("right", "pressed")
        rig.emit("right", "released")
        await until(lambda: called == [2])
        await until(lambda: any((0, 200, 220) in f for f in rig.frames()))
        await rig.service.stop()

    asyncio.run(scenario())


def test_service_shows_a_grey_cross_when_nothing_is_programmed() -> None:
    rig = Rig(lambda face: False)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames()))
        rig.emit("up", "pressed")
        rig.emit("up", "released")
        await until(lambda: any(GRAY in f for f in rig.frames()[1:]))
        await rig.service.stop()

    asyncio.run(scenario())


def test_a_crashing_trigger_never_stops_the_display() -> None:
    attempts: list[int] = []

    def trigger(face: int) -> bool:
        attempts.append(face)
        raise RuntimeError("queue broken")

    rig = Rig(trigger)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames()))
        rig.emit("down", "pressed")
        rig.emit("down", "released")
        await until(lambda: attempts == [3])
        await until(lambda: rig.service.status.state == "running")
        rig.emit("down", "pressed")
        rig.emit("down", "released")
        await until(lambda: attempts == [3, 3])
        await rig.service.stop()

    asyncio.run(scenario())


def test_without_a_trigger_callback_the_display_still_works() -> None:
    rig = Rig(None)

    async def scenario() -> None:
        await rig.service.start()
        await until(lambda: bool(rig.frames()))
        rig.emit("middle", "pressed")
        rig.emit("middle", "released")
        await asyncio.sleep(0.1)
        assert rig.service.status.state == "running"
        await rig.service.stop()

    asyncio.run(scenario())


# ---------- the whole chain ----------


class RecordingPlugin:
    kind = "hue"

    def __init__(self) -> None:
        self.scenes: list[str] = []
        self.fail = False

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        if self.fail:
            raise RuntimeError("bridge down")
        self.scenes.append(str(parameters["scene_id"]))


class Chain:
    """Fake board + fake cubes + recording plugin, wired like production."""

    def __init__(self, tmp_path: Path, mappings: list[dict[str, object]] | None = None) -> None:
        self.tmp_path = tmp_path
        self.config = config(tmp_path, mappings)
        self.path = tmp_path / "octapihub.toml"
        save_config(self.path, self.config)
        self.plugin = RecordingPlugin()
        self.device = FakeSenseHat()
        self.sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(self.device),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )
        self.factory = FakeTransportFactory(
            DeviceProfile(
                (CharacteristicInfo(ORIENTATION_CHARACTERISTIC_UUID, frozenset({"notify"})),)
            )
        )

        def runtime(settings: AppConfig) -> BleRuntime:
            return BleRuntime(
                settings,
                self.factory,
                orientation_characteristic_uuid=ORIENTATION_CHARACTERISTIC_UUID,
                decode_orientation=decode_orientation,
                supports_orientation=supports_orientation_notifications,
                tuning=BleRuntimeTuning(health_poll_seconds=0.01, debounce_ms=0),
            )

        def dispatcher(settings: AppConfig) -> OutboxDispatcher:
            registry = PluginRegistry()
            registry.register(self.plugin)
            return OutboxDispatcher(
                RuleEngine(settings),
                SQLiteOutbox(tmp_path / "o.db"),
                registry,
                backoff_base_seconds=0.01,
                backoff_cap_seconds=0.02,
            )

        self.cubes = CubeService(self.config, runtime, dispatcher, poll_interval_seconds=0.005)
        self.matrix = build_matrix_service(self.config, self.sense, self.cubes)
        self.settings = SettingsManager(
            self.path, self.config, self.sense, None, self.cubes, self.matrix
        )
        self.backend = WebBackend(self.settings, self.sense, TOKEN, self.cubes, matrix=self.matrix)

    def emit(self, direction: str, action: str) -> None:
        self.device.emit_joystick_event(1.0, direction, action)


def test_joystick_press_runs_the_programmed_scene_end_to_end(tmp_path: Path) -> None:
    chain = Chain(
        tmp_path, [mapping("Joystick", 2, scene("evening")), mapping("Joystick", 5, scene("off"))]
    )
    activity: list[CubeActivity] = []
    chain.cubes.add_listener(activity.append)

    async def scenario() -> None:
        await chain.sense.start()
        await chain.cubes.start()
        await chain.matrix.start()
        await until(lambda: chain.matrix.status.state == "running")
        assert chain.matrix.status.view_id == "control"
        chain.emit("right", "pressed")
        chain.emit("right", "released")
        await until(lambda: chain.plugin.scenes == ["evening"])
        chain.emit("middle", "pressed")
        chain.emit("middle", "released")
        await until(lambda: chain.plugin.scenes == ["evening", "off"])
        chain.emit("up", "pressed")  # nothing programmed on "up"
        chain.emit("up", "released")
        await asyncio.sleep(0.1)
        assert chain.plugin.scenes == ["evening", "off"]
        await chain.matrix.stop()
        await chain.cubes.stop()
        await chain.sense.stop()

    asyncio.run(scenario())
    assert ("action_ok", JOYSTICK_SOURCE) in [(a.kind, a.cube_name) for a in activity]


def test_failed_joystick_actions_are_retried_like_cube_actions(tmp_path: Path) -> None:
    chain = Chain(tmp_path, [mapping("Joystick", 1, scene("x"))])
    chain.plugin.fail = True

    async def scenario() -> None:
        await chain.sense.start()
        await chain.cubes.start()
        await chain.matrix.start()
        await until(lambda: chain.matrix.status.state == "running")
        chain.emit("up", "pressed")
        chain.emit("up", "released")
        await asyncio.sleep(0.15)
        assert chain.plugin.scenes == []
        chain.plugin.fail = False  # the bridge comes back; the outbox retries
        await until(lambda: chain.plugin.scenes == ["x"])
        await chain.matrix.stop()
        await chain.cubes.stop()
        await chain.sense.stop()

    asyncio.run(scenario())


# ---------- CubeService.trigger ----------


def test_trigger_queues_actions_for_any_source_and_reports_problems(tmp_path: Path) -> None:
    chain = Chain(
        tmp_path,
        [mapping("Licht", 3, scene("cube-scene")), mapping("Joystick", 4, scene("joy-scene"))],
    )
    seen: list[CubeActivity] = []
    chain.cubes.add_listener(seen.append)

    async def scenario() -> None:
        assert chain.cubes.trigger("Licht", 3) is None  # pipeline not running yet
        await chain.cubes.start()
        assert chain.cubes.trigger("Licht", 3) is not None
        assert chain.cubes.trigger("Joystick", 4) is not None
        assert chain.cubes.trigger("Joystick", 1) is None  # nothing mapped
        assert chain.cubes.trigger("Nobody", 1) is None  # unknown source is not an exception
        assert chain.cubes.trigger("Licht", 99) is None
        await until(lambda: sorted(chain.plugin.scenes) == ["cube-scene", "joy-scene"])
        await chain.cubes.stop()

    asyncio.run(scenario())
    assert [(a.kind, a.cube_name, a.face) for a in seen if a.kind == "face"] == [
        ("face", "Licht", 3),
        ("face", "Joystick", 4),
    ]


# ---------- web ----------


def post(client: TestClient, path: str, fields: dict[str, str], token: str | None = TOKEN):  # type: ignore[no-untyped-def]
    body = dict(fields)
    if token is not None:
        body["token"] = token
    return client.post(
        path, content=urlencode(body), headers={"content-type": "application/x-www-form-urlencoded"}
    )


def test_editor_has_a_joystick_block_with_five_labelled_inputs(tmp_path: Path) -> None:
    chain = Chain(tmp_path, [mapping("Joystick", 2, scene("evening"))])
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        text = client.get("/cubes").text
    for label in JOYSTICK_FACE_LABELS:
        assert f'<span class="face-tile-title">{label}</span>' in text
    assert text.count("face-tile-head") == 2 * 8 + 5
    assert 'name="map.2.5"' in text
    assert 'name="map.2.6"' not in text
    assert "Steuerung" in text
    assert "links/rechts lang halten" in text
    assert "▶" in text


def test_editor_only_offers_run_now_for_saved_mappings(tmp_path: Path) -> None:
    chain = Chain(
        tmp_path, [mapping("Joystick", 2, scene("evening")), mapping("Licht", 1, scene("x"))]
    )
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        text = client.get("/cubes").text
    assert text.count('formaction="/cubes/trigger"') == 2
    assert 'value="2.2"' in text  # joystick input "Rechts"
    assert 'value="0.1"' in text  # cube face 1


def _hint(chain: Chain) -> str:
    with TestClient(create_app(chain.backend)) as client:
        return client.get("/cubes").text


def test_joystick_readiness_hints(tmp_path: Path) -> None:
    ready = Chain(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path)
    assert "Noch nicht bereit" not in _hint(ready)

    no_view = Chain(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path)
    cfg = no_view.settings.config
    no_view.settings._config = replace(
        cfg,
        sense_hat=replace(
            cfg.sense_hat,
            matrix=replace(cfg.sense_hat.matrix, views=("overview",), default_view="overview"),
        ),
    )
    assert "Ansicht „Steuerung“ unter Einstellungen" in _hint(no_view)

    no_stick = Chain(tmp_path / "c" if (tmp_path / "c").mkdir() is None else tmp_path)
    cfg = no_stick.settings.config
    no_stick.settings._config = replace(
        cfg, sense_hat=replace(cfg.sense_hat, joystick_enabled=False)
    )
    assert "Der Joystick ist unter Einstellungen ausgeschaltet" in _hint(no_stick)

    no_matrix = Chain(tmp_path / "d" if (tmp_path / "d").mkdir() is None else tmp_path)
    no_matrix.backend.matrix = None
    assert "LED-Anzeige ist in diesem Modus nicht aktiv" in _hint(no_matrix)


def test_saving_a_joystick_mapping_through_the_editor(tmp_path: Path) -> None:
    chain = Chain(tmp_path)
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        assert chain.backend.cubes is not None
        # a loaded Hue scene list is required to choose a new scene
        from octapihub.application.hue_service import HueService  # noqa: F401

        class FakeHue:
            scenes = (HueScene("s-1", "Abend"),)
            status = HueStatus(HueState.PAIRED, "", None, None, None)

        chain.backend.hue = FakeHue()  # type: ignore[assignment]
        values = forms.mapping_config_to_form(chain.settings.config)
        values["map.2.1"] = "s-1"
        response = post(client, "/cubes/mapping", values)
        assert response.status_code == 200
        assert "Zuordnung gespeichert" in response.text
    saved = load_config(chain.path).mappings
    assert [(m.cube, m.face) for m in saved] == [(JOYSTICK_SOURCE, 1)]


def test_run_now_button_triggers_cube_faces_and_joystick_inputs(tmp_path: Path) -> None:
    chain = Chain(
        tmp_path,
        [mapping("Licht", 4, scene("cube-scene")), mapping("Joystick", 3, scene("joy-scene"))],
    )
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        deadline = time.monotonic() + 3
        while not chain.cubes.running and time.monotonic() < deadline:
            time.sleep(0.01)
        first = post(client, "/cubes/trigger", {"trigger": "0.4"})
        second = post(client, "/cubes/trigger", {"trigger": "2.3"})
        assert first.status_code == second.status_code == 200
        assert "Aktion ausgelöst" in second.text
        assert "runter" in second.text
        deadline = time.monotonic() + 3
        while (
            sorted(chain.plugin.scenes) != ["cube-scene", "joy-scene"]
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
    assert sorted(chain.plugin.scenes) == ["cube-scene", "joy-scene"]


@pytest.mark.parametrize(
    ("fields", "status", "code"),
    [
        ({"trigger": ""}, 422, "CH-WEB-011"),
        ({"trigger": "x.y"}, 422, "CH-WEB-011"),
        ({"trigger": "9.1"}, 422, "CH-WEB-011"),
        ({"trigger": "0.9"}, 422, "CH-WEB-011"),
        ({"trigger": "2.6"}, 422, "CH-WEB-011"),
        ({"trigger": "0.0"}, 422, "CH-WEB-011"),
        ({"trigger": "1.2"}, 409, "CH-WEB-013"),  # valid face, nothing mapped
    ],
)
def test_run_now_validates_input_and_reports_unmapped_faces(
    tmp_path: Path, fields: dict[str, str], status: int, code: str
) -> None:
    chain = Chain(tmp_path)
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        deadline = time.monotonic() + 3
        while not chain.cubes.running and time.monotonic() < deadline:
            time.sleep(0.01)
        response = post(client, "/cubes/trigger", fields)
    assert response.status_code == status
    assert code in response.text
    assert chain.plugin.scenes == []


@pytest.mark.parametrize("token", [None, "", "wrong-zzz"])
def test_run_now_needs_the_token(tmp_path: Path, token: str | None) -> None:
    chain = Chain(tmp_path, [mapping("Licht", 4, scene("x"))])
    with TestClient(create_app(chain.backend, manage_service=True)) as client:
        response = post(client, "/cubes/trigger", {"trigger": "0.4"}, token=token)
    assert response.status_code == 403
    assert chain.plugin.scenes == []
    assert "wrong-zzz" not in response.text


def test_run_now_without_action_pipeline_explains(tmp_path: Path) -> None:
    chain = Chain(tmp_path, [mapping("Licht", 4, scene("x"))])
    chain.backend.cubes = None
    with TestClient(create_app(chain.backend)) as client:
        response = post(client, "/cubes/trigger", {"trigger": "0.4"})
    assert response.status_code == 409
    assert "CH-WEB-012" in response.text
