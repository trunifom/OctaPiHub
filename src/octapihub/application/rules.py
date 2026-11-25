"""Pure routing from stable cube events to configured action sequences."""

from __future__ import annotations

from octapihub.application.ble_runtime import CubeEvent
from octapihub.core.config import AppConfig, RuleActionConfig
from octapihub.core.errors import ValidationError
from octapihub.domain.cube import OrientationKind


class RuleEngine:
    """Resolve stable face changes to their configured actions."""

    def __init__(self, config: AppConfig) -> None:
        self._cube_names = {cube.name.casefold(): cube.name for cube in config.cubes}
        self._mappings = {
            (mapping.cube.casefold(), mapping.face): mapping.actions for mapping in config.mappings
        }
        self._required_plugin_kinds = tuple(
            sorted({action.kind for mapping in config.mappings for action in mapping.actions})
        )

    @property
    def required_plugin_kinds(self) -> tuple[str, ...]:
        """Return plugin kinds needed by the configured rules in stable order."""
        return self._required_plugin_kinds

    def actions_for(self, cube_name: str, face: int) -> tuple[RuleActionConfig, ...]:
        """Resolve a configured cube and face to actions; an unmapped face has no actions."""
        if isinstance(face, bool) or not isinstance(face, int) or not 1 <= face <= 8:
            raise ValidationError("Rule face must be an integer from 1 to 8")
        canonical_cube_name = self._cube_names.get(cube_name.casefold())
        if canonical_cube_name is None:
            raise ValidationError("Rule event references an unknown cube")
        return self._mappings.get((canonical_cube_name.casefold(), face), ())

    def actions_for_event(self, event: CubeEvent) -> tuple[RuleActionConfig, ...]:
        """Resolve a stable face change; baselines and neutral sides do nothing."""
        if event.orientation.kind is not OrientationKind.FACE_CHANGE:
            return ()
        return self.actions_for(event.cube_name, event.orientation.value)
