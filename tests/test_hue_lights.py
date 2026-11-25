from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from octapihub.application import hue_lights as hl
from octapihub.application.hue_lights import HueGroup
from octapihub.application.hue_service import HueService, HueState
from octapihub.core.config import HueConfig
from octapihub.core.errors import HueBridgeError, ValidationError

KEY = "super-secret-app-key"
GROUPS = (
    HueGroup("g-all", "Alle Lampen", "home", "home-1"),
    HueGroup("g-wz", "Wohnzimmer", "room", "room-wz"),
    HueGroup("g-ku", "Kueche", "room", "room-ku"),
)


@dataclass(frozen=True)
class Scene:
    id: str
    name: str
    group_id: str = ""


class Gateway:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, object]]] = []
        self.recalled: list[str] = []
        self.on_state: bool | None = True
        self.fail: Exception | None = None
        self.groups: tuple[HueGroup, ...] = GROUPS
        self.scenes = [
            Scene("s-b", "Hell", "room-wz"),
            Scene("s-a", "Abend", "room-wz"),
            Scene("s-c", "Kochen", "room-ku"),
        ]

    async def list_scenes(self):  # type: ignore[no-untyped-def]
        return list(self.scenes)

    async def recall_scene(self, scene_id: str) -> None:
        self._maybe_fail()
        self.recalled.append(scene_id)

    async def list_groups(self):  # type: ignore[no-untyped-def]
        return list(self.groups)

    async def set_group(self, group_id: str, body: dict[str, object]) -> None:
        self._maybe_fail()
        self.commands.append((group_id, body))

    async def group_is_on(self, group_id: str) -> bool | None:
        return self.on_state

    def _maybe_fail(self) -> None:
        if self.fail:
            raise self.fail


class Secrets:
    def get(self, key: str) -> str | None:
        return KEY

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> bool:
        return True


def make(enabled: bool = True) -> tuple[HueService, Gateway]:
    gateway = Gateway()

    async def pair(_c: HueConfig) -> str:
        return KEY

    config = HueConfig(enabled=True, host="192.168.1.20") if enabled else HueConfig()
    return HueService(config, Secrets(), lambda _c, _k: gateway, pair), gateway


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


async def started() -> tuple[HueService, Gateway]:
    service, gateway = make()
    await service.start()
    assert service.state is HueState.PAIRED
    return service, gateway


# ---------- pure rules ----------


def test_simple_commands() -> None:
    assert hl.simple_body("light.on", {}, None) == {"on": {"on": True}}
    assert hl.simple_body("light.off", {}, None) == {"on": {"on": False}}
    assert hl.simple_body("light.toggle", {}, True) == {"on": {"on": False}}
    assert hl.simple_body("light.toggle", {}, False) == {"on": {"on": True}}
    assert hl.simple_body("light.toggle", {}, None) == {"on": {"on": True}}
    up = hl.simple_body("light.brighter", {"step": 25}, None)
    assert up["dimming_delta"] == {"action": "up", "brightness_delta": 25}
    assert up["on"] == {"on": True}
    down = hl.simple_body("light.darker", {}, None)
    assert down == {"dimming_delta": {"action": "down", "brightness_delta": 15}}
    assert hl.simple_body("light.brightness", {"level": 0.5}, None)["dimming"] == {
        "brightness": 50.0
    }
    assert hl.simple_body("light.brightness", {"level": 0}, None)["dimming"] == {"brightness": 1.0}


def test_colours() -> None:
    warm = hl.simple_body("light.color", {"color": "warm"}, None)
    assert warm["color_temperature"] == {"mirek": 454}
    blue = hl.simple_body("light.color", {"color": "blue"}, None)
    assert blue["color"] == {"xy": {"x": 0.153, "y": 0.048}}
    assert len(hl.COLOR_CYCLE) == len(hl.COLORS) == 11


@pytest.mark.parametrize(
    ("action", "parameters"),
    [
        ("light.brighter", {"step": 0}),
        ("light.brighter", {"step": 101}),
        ("light.brighter", {"step": True}),
        ("light.brighter", {"step": "5"}),
        ("light.brightness", {}),
        ("light.brightness", {"level": 1.5}),
        ("light.brightness", {"level": float("nan")}),
        ("light.brightness", {"level": True}),
        ("light.color", {"color": "magenta"}),
        ("light.color", {}),
        ("light.explode", {}),
    ],
)
def test_invalid_parameters_are_rejected_in_german(
    action: str, parameters: Mapping[str, object]
) -> None:
    with pytest.raises(ValidationError) as error:
        hl.simple_body(action, parameters, None)
    assert error.value.user_message


def test_cycling_wraps_in_both_directions() -> None:
    assert hl.cycle_index(-1, 3, 1) == 0
    assert hl.cycle_index(2, 3, 1) == 0
    assert hl.cycle_index(0, 3, -1) == 2
    assert hl.cycle_index(-1, 3, -1) == 2
    with pytest.raises(ValidationError):
        hl.cycle_index(0, 0, 1)


def test_default_group() -> None:
    chosen = hl.default_group(GROUPS)
    assert chosen is not None and chosen.kind == "home"
    assert hl.default_group(GROUPS[1:2]) == GROUPS[1]
    assert hl.default_group(GROUPS[1:]) is None
    assert hl.default_group(()) is None


# ---------- service ----------


def test_on_off_toggle_and_brightness_go_to_the_chosen_group() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        await service.execute("light.on", {"group": "g-wz"})
        await service.execute("light.toggle", {"group": "g-ku"})  # lamps are on -> off
        gateway.on_state = False
        await service.execute("light.toggle", {"group": "g-ku"})
        await service.execute("light.brighter", {"group": "g-wz", "step": 20})
        await service.execute("light.darker", {})  # no group: the whole home
        await service.execute("light.brightness", {"level": 0.3})
        sent = gateway.commands
        assert sent[0] == ("g-wz", {"on": {"on": True}})
        assert sent[1] == ("g-ku", {"on": {"on": False}})
        assert sent[2] == ("g-ku", {"on": {"on": True}})
        assert sent[3][1]["dimming_delta"] == {"action": "up", "brightness_delta": 20}
        assert sent[4][0] == "g-all" and sent[5][0] == "g-all"
        assert service.status.last_error is None

    run(scenario())


def test_the_colour_cycle_advances_per_group_and_wraps() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        for _ in range(len(hl.COLOR_CYCLE) + 1):
            await service.execute("light.color.next", {"group": "g-wz"})
        await service.execute("light.color.next", {"group": "g-ku"})
        colours = [b for g, b in gateway.commands if g == "g-wz"]
        assert colours[0]["color_temperature"] == {"mirek": 454}  # warm first
        assert colours[-1] == colours[0]  # wrapped around
        assert gateway.commands[-1][0] == "g-ku"
        assert gateway.commands[-1][1] == colours[0]  # another group starts over

    run(scenario())


def test_scene_stepping_stays_in_the_room_in_name_order() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        await service.refresh_scenes()
        for action in ("scene.next", "scene.next", "scene.next", "scene.previous"):
            await service.execute(action, {"group": "g-wz"})
        assert gateway.recalled == ["s-a", "s-b", "s-a", "s-b"]  # Abend, Hell, wrap, back
        await service.execute("scene.next", {"group": "g-ku"})
        assert gateway.recalled[-1] == "s-c"

    run(scenario())


def test_scene_stepping_without_scenes_or_group_is_explained() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        gateway.scenes = []
        with pytest.raises(ValidationError) as none:
            await service.execute("scene.next", {"group": "g-wz"})
        assert "Szenen" in none.value.user_message
        gateway.groups = GROUPS[1:]  # no whole-home group and two rooms: ambiguous
        await service.refresh_groups()
        with pytest.raises(ValidationError) as ambiguous:
            await service.execute("light.on", {})
        assert "Raum" in ambiguous.value.user_message

    run(scenario())


def test_unknown_or_malformed_groups_are_rejected_without_a_command() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        for group in ("g-gone", 5):
            with pytest.raises(ValidationError):
                await service.execute("light.on", {"group": group})
        assert gateway.commands == []

    run(scenario())


def test_bridge_failures_are_recorded_and_typed() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        gateway.fail = HueBridgeError("offline")
        with pytest.raises(HueBridgeError):
            await service.execute("light.off", {"group": "g-wz"})
        assert service.status.last_error is not None
        gateway.fail = None
        await service.execute("light.off", {"group": "g-wz"})
        assert service.status.last_error is None

    run(scenario())


def test_light_actions_need_a_paired_bridge() -> None:
    async def scenario() -> None:
        service, _ = make(enabled=False)
        await service.start()
        with pytest.raises(Exception) as error:
            await service.execute("light.on", {})
        assert error.value.__class__.__name__.startswith("Hue")

    run(scenario())


def test_groups_can_be_refreshed_and_failures_keep_the_old_list() -> None:
    async def scenario() -> None:
        service, gateway = await started()
        assert await service.refresh_groups() == GROUPS

        async def boom():  # type: ignore[no-untyped-def]
            raise HueBridgeError("x")

        gateway.list_groups = boom  # type: ignore[method-assign]
        with pytest.raises(HueBridgeError):
            await service.refresh_groups()
        assert service.groups == GROUPS

    run(scenario())
