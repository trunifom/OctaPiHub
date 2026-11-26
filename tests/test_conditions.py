from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from octapihub.application.outbox import OutboxDispatcher
from octapihub.application.plugins import PluginRegistry
from octapihub.application.rules import (
    TIMER_KIND,
    RuleEngine,
    condition_met,
    with_delays,
)
from octapihub.application.timer import TimerPlugin
from octapihub.core.config import (
    RuleConditionConfig,
    load_config,
    mappings_table,
    parse_config,
    save_config,
)
from octapihub.core.errors import ValidationError
from octapihub.infrastructure.sqlite_outbox import SQLiteOutbox

ZH = ZoneInfo("Europe/Zurich")
MON_9 = datetime(2026, 11, 23, 9, 0, tzinfo=ZH)  # a Monday
MON_23 = datetime(2026, 11, 23, 23, 0, tzinfo=ZH)
TUE_2 = datetime(2026, 11, 24, 2, 0, tzinfo=ZH)
SAT_12 = datetime(2026, 11, 28, 12, 0, tzinfo=ZH)


def action(delay: object = None, name: str = "stop") -> dict[str, object]:
    entry: dict[str, object] = {"kind": "fake", "action": name, "parameters": {}}
    if delay is not None:
        entry["delay_seconds"] = delay
    return entry


def config(mappings: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    return parse_config(
        {
            "schema_version": 7,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
            "mappings": mappings,
        }
    )


def mapping(
    actions: list[dict[str, object]], when: object = None, face: int = 1
) -> dict[str, object]:
    entry: dict[str, object] = {"cube": "A", "face": face, "actions": actions}
    if when is not None:
        entry["when"] = when
    return entry


# ---------- conditions ----------


@pytest.mark.parametrize(
    ("condition", "now", "expected"),
    [
        (None, MON_9, True),
        (RuleConditionConfig("08:00", "18:00"), MON_9, True),
        (RuleConditionConfig("08:00", "18:00"), MON_23, False),
        (RuleConditionConfig("09:00", "10:00"), MON_9, True),  # start is inside
        (RuleConditionConfig("08:00", "09:00"), MON_9, False),  # end is outside
        (RuleConditionConfig("22:00", "06:00"), MON_23, True),  # crosses midnight
        (RuleConditionConfig("22:00", "06:00"), TUE_2, True),
        (RuleConditionConfig("22:00", "06:00"), MON_9, False),
        (RuleConditionConfig(days=(0, 1, 2, 3, 4)), MON_9, True),
        (RuleConditionConfig(days=(0, 1, 2, 3, 4)), SAT_12, False),
        (RuleConditionConfig("08:00", "18:00", (5, 6)), SAT_12, True),
        (RuleConditionConfig("08:00", "18:00", (5, 6)), MON_9, False),  # right time, wrong day
    ],
)
def test_conditions(condition: RuleConditionConfig | None, now: datetime, expected: bool) -> None:
    assert condition_met(condition, now) is expected


@pytest.mark.parametrize(
    "when",
    [
        {"start": "25:00", "end": "26:00"},
        {"start": "8:00", "end": "18:00"},
        {"start": "08:00"},
        {"end": "18:00"},
        {"start": "08:00", "end": "08:00"},
        {"days": [7]},
        {"days": [-1]},
        {"days": [1, 1]},
        {"days": ["Mo"]},
        {"days": [True]},
        {"days": "Mo"},
        {"start": 800, "end": 1800},
        {"hours": 3},
        "immer",
    ],
)
def test_invalid_conditions_are_rejected(when: object) -> None:
    with pytest.raises(ValidationError):
        config([mapping([action()], when)])


def test_an_empty_condition_means_always() -> None:
    assert config([mapping([action()], {})]).mappings[0].when is None


@pytest.mark.parametrize("delay", [-1, 31, float("nan"), float("inf"), True, "2", None.__class__])
def test_invalid_delays_are_rejected(delay: object) -> None:
    with pytest.raises(ValidationError):
        config([mapping([action(delay)])])


def test_conditions_and_delays_round_trip_through_the_file(tmp_path: Path) -> None:
    cfg = config(
        [
            mapping(
                [action(2.5, "a"), action(None, "b"), action(30, "c")],
                {"start": "18:00", "end": "23:30", "days": [4, 0]},
            )
        ]
    )
    assert cfg.mappings[0].when == RuleConditionConfig("18:00", "23:30", (0, 4))
    assert [a.delay_seconds for a in cfg.mappings[0].actions] == [2.5, 0.0, 30.0]
    path = tmp_path / "c.toml"
    save_config(path, cfg)
    text = path.read_text(encoding="utf-8")
    assert "delay_seconds = 2.5" in text and "when = {" in text
    assert load_config(path).mappings == cfg.mappings
    table = mappings_table(cfg)[0]
    assert table["when"] == {"start": "18:00", "end": "23:30", "days": [0, 4]}
    again = config(table_to_list(cfg))
    assert again.mappings == cfg.mappings


def table_to_list(cfg):  # type: ignore[no-untyped-def]
    return mappings_table(cfg)


def test_mappings_without_the_new_fields_look_as_before(tmp_path: Path) -> None:
    cfg = config([mapping([action()])])
    assert (
        "when" not in mappings_table(cfg)[0]
        and "delay_seconds" not in mappings_table(cfg)[0]["actions"][0]
    )  # type: ignore[index]
    path = tmp_path / "c.toml"
    save_config(path, cfg)
    assert "when" not in path.read_text(encoding="utf-8") and "delay" not in path.read_text(
        encoding="utf-8"
    )


# ---------- rules ----------


def test_the_engine_skips_a_mapping_outside_its_condition_but_manual_tests_ignore_it() -> None:
    cfg = config([mapping([action()], {"start": "08:00", "end": "18:00"})])
    morning = RuleEngine(cfg, lambda: MON_9)
    night = RuleEngine(cfg, lambda: MON_23)
    assert len(morning.actions_for("A", 1)) == 1
    assert night.actions_for("A", 1) == ()
    assert len(night.actions_for("A", 1, ignore_conditions=True)) == 1


def test_delays_become_wait_steps_in_front_of_their_action() -> None:
    cfg = config([mapping([action(2, "first"), action(None, "second"), action(0.5, "third")])])
    steps = RuleEngine(cfg, lambda: MON_9).actions_for("A", 1)
    assert [(s.kind, s.action) for s in steps] == [
        ("timer", "wait"),
        ("fake", "first"),
        ("fake", "second"),
        ("timer", "wait"),
        ("fake", "third"),
    ]
    assert steps[0].parameters == {"seconds": 2.0} and steps[3].parameters == {"seconds": 0.5}
    assert with_delays(()) == ()


def test_the_timer_plugin_is_required_only_when_a_delay_exists() -> None:
    assert TIMER_KIND not in RuleEngine(config([mapping([action()])])).required_plugin_kinds
    assert TIMER_KIND in RuleEngine(config([mapping([action(1)])])).required_plugin_kinds


# ---------- timer plugin ----------


def test_the_timer_waits_and_validates() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    plugin = TimerPlugin(fake_sleep)
    asyncio.run(plugin.execute("wait", {"seconds": 2.5}))
    asyncio.run(plugin.execute("wait", {"seconds": 0}))
    assert slept == [2.5, 0.0]
    for action_name, params in (
        ("explode", {"seconds": 1}),
        ("wait", {}),
        ("wait", {"seconds": -1}),
        ("wait", {"seconds": 31}),
        ("wait", {"seconds": True}),
        ("wait", {"seconds": float("nan")}),
        ("wait", {"seconds": "2"}),
    ):
        with pytest.raises(ValidationError):
            asyncio.run(plugin.execute(action_name, params))
    assert slept == [2.5, 0.0]


# ---------- end to end through the queue ----------


class Recorder:
    kind = "fake"

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.log.append(action)


def test_a_sequence_with_a_delay_runs_in_order_through_the_queue(tmp_path: Path) -> None:
    log: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        log.append(f"wait {seconds:g}")

    registry = PluginRegistry()
    registry.register(Recorder(log))
    registry.register(TimerPlugin(fake_sleep))
    cfg = config([mapping([action(None, "stop"), action(2, "start")])])
    dispatcher = OutboxDispatcher(
        RuleEngine(cfg, lambda: MON_9), SQLiteOutbox(tmp_path / "o.sqlite3"), registry
    )

    async def scenario() -> None:
        assert dispatcher.enqueue_face_change("A", 1) is not None
        while await dispatcher.dispatch_next("A"):
            pass

    asyncio.run(scenario())
    assert log == ["stop", "wait 2", "start"]


def test_outside_the_condition_nothing_is_queued(tmp_path: Path) -> None:
    registry = PluginRegistry()
    registry.register(Recorder([]))
    cfg = config([mapping([action()], {"days": [5, 6]})])
    dispatcher = OutboxDispatcher(
        RuleEngine(cfg, lambda: MON_9), SQLiteOutbox(tmp_path / "o.sqlite3"), registry
    )
    assert dispatcher.enqueue_face_change("A", 1) is None
    assert dispatcher.enqueue_face_change("A", 1, ignore_conditions=True) is not None
