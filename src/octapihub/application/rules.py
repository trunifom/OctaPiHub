"""Pure routing from stable cube events to configured action sequences."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from types import MappingProxyType

from octapihub.application.ble_runtime import CubeEvent
from octapihub.core.config import (
    JOYSTICK_SOURCE,
    AppConfig,
    RuleActionConfig,
    RuleConditionConfig,
)
from octapihub.core.errors import ValidationError
from octapihub.domain.cube import OrientationKind

TIMER_KIND = "timer"


def _minutes(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:])


def condition_met(condition: RuleConditionConfig | None, now: datetime) -> bool:
    """Whether ``now`` (local time) satisfies a condition; no condition is always met.

    A time window may cross midnight (22:00 to 06:00). Weekdays are those of ``now``.
    """
    if condition is None:
        return True
    if condition.days and now.weekday() not in condition.days:
        return False
    if condition.start:
        current = now.hour * 60 + now.minute
        start, end = _minutes(condition.start), _minutes(condition.end)
        inside = start <= current < end if start < end else current >= start or current < end
        if not inside:
            return False
    return True


def with_delays(actions: tuple[RuleActionConfig, ...]) -> tuple[RuleActionConfig, ...]:
    """Insert a ``timer wait`` step before every action that has a delay."""
    expanded: list[RuleActionConfig] = []
    for action in actions:
        if action.delay_seconds > 0:
            expanded.append(
                RuleActionConfig(
                    TIMER_KIND, "wait", MappingProxyType({"seconds": action.delay_seconds})
                )
            )
        expanded.append(action)
    return tuple(expanded)


class RuleEngine:
    """Resolve stable face changes to their configured actions."""

    def __init__(self, config: AppConfig, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._cube_names = {cube.name.casefold(): cube.name for cube in config.cubes}
        self._cube_names[JOYSTICK_SOURCE.casefold()] = JOYSTICK_SOURCE
        self._mappings = {
            (mapping.cube.casefold(), mapping.face): mapping for mapping in config.mappings
        }
        self._required_plugin_kinds = tuple(
            sorted(
                {action.kind for mapping in config.mappings for action in mapping.actions}
                | (
                    {TIMER_KIND}
                    if any(a.delay_seconds > 0 for m in config.mappings for a in m.actions)
                    else set()
                )
            )
        )

    @property
    def required_plugin_kinds(self) -> tuple[str, ...]:
        """Return plugin kinds needed by the configured rules in stable order."""
        return self._required_plugin_kinds

    def actions_for(
        self, cube_name: str, face: int, *, ignore_conditions: bool = False
    ) -> tuple[RuleActionConfig, ...]:
        """Resolve a configured cube and face (0 = holder) to actions; unmapped means none.

        A mapping whose time condition is not met yields none (unless ``ignore_conditions``,
        used for manual tests). Delays become ``timer`` steps in front of their action.
        """
        if isinstance(face, bool) or not isinstance(face, int) or not 0 <= face <= 8:
            raise ValidationError("Rule face must be an integer from 0 to 8")
        canonical_cube_name = self._cube_names.get(cube_name.casefold())
        if canonical_cube_name is None:
            raise ValidationError("Rule event references an unknown cube")
        mapping = self._mappings.get((canonical_cube_name.casefold(), face))
        if mapping is None:
            return ()
        if not ignore_conditions and not condition_met(mapping.when, self._clock()):
            return ()
        return with_delays(mapping.actions)

    def actions_for_event(self, event: CubeEvent) -> tuple[RuleActionConfig, ...]:
        """Resolve a stable face change; baselines and neutral sides do nothing."""
        if event.orientation.kind not in (OrientationKind.FACE_CHANGE, OrientationKind.NEUTRAL):
            return ()
        # a cube resting in its holder reports the neutral value 0, which maps like a face
        return self.actions_for(event.cube_name, event.orientation.value)
