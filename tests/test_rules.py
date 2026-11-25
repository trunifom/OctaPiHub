from __future__ import annotations

from octapihub.application.ble_runtime import CubeEvent
from octapihub.application.rules import RuleEngine
from octapihub.core.config import AppConfig, parse_config
from octapihub.domain.cube import OrientationEvent, OrientationKind


def rule_config() -> AppConfig:
    return parse_config(
        {
            "schema_version": 2,
            "bluetooth": {"adapter": "hci0"},
            "cubes": [
                {"name": "Licht", "address": "AA:BB:CC:DD:EE:01", "role": "light"},
                {"name": "Musik", "address": "AA:BB:CC:DD:EE:02", "role": "music"},
            ],
            "mappings": [
                {
                    "cube": "Licht",
                    "face": 2,
                    "actions": [{"kind": "hue", "action": "scene.set"}],
                },
                {
                    "cube": "Musik",
                    "face": 3,
                    "actions": [{"kind": "audio", "action": "play.start"}],
                },
            ],
        }
    )


def cube_event(
    cube_name: str,
    role: str,
    kind: OrientationKind,
    value: int,
) -> CubeEvent:
    return CubeEvent(cube_name, role, OrientationEvent(kind, value, observed_at_ms=100))


def test_rule_engine_matches_only_configured_stable_face_changes() -> None:
    engine = RuleEngine(rule_config())

    assert engine.required_plugin_kinds == ("audio", "hue")
    assert engine.actions_for_event(cube_event("Licht", "light", OrientationKind.BASELINE, 2)) == ()
    assert engine.actions_for_event(cube_event("Licht", "light", OrientationKind.NEUTRAL, 0)) == ()
    assert (
        engine.actions_for_event(cube_event("Licht", "light", OrientationKind.FACE_CHANGE, 5)) == ()
    )
    assert (
        engine.actions_for_event(cube_event("licht", "light", OrientationKind.FACE_CHANGE, 2))[
            0
        ].action
        == "scene.set"
    )
    assert (
        engine.actions_for_event(cube_event("Musik", "music", OrientationKind.FACE_CHANGE, 3))[
            0
        ].kind
        == "audio"
    )
    assert engine.actions_for("Licht", 2)[0].action == "scene.set"
