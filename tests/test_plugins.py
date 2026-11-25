from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

import octapihub.application.plugins as plugins
from octapihub.application.plugins import (
    PLUGIN_ENTRY_POINT_GROUP,
    ActionPlugin,
    PluginRegistry,
    load_installed_plugins,
)
from octapihub.core.errors import PluginError


class FakePlugin:
    def __init__(self, kind: str = "test.audio") -> None:
        self.kind = kind
        self.executed: list[tuple[str, Mapping[str, object]]] = []

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        self.executed.append((action, parameters))


class SynchronousPlugin:
    kind = "test.sync"

    def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        return None


class FakeEntryPoint:
    group = PLUGIN_ENTRY_POINT_GROUP

    def __init__(self, name: str, factory: object) -> None:
        self.name = name
        self._factory = factory

    def load(self) -> object:
        if isinstance(self._factory, Exception):
            raise self._factory
        return self._factory


def test_registry_resolves_plugins_and_lists_kinds_stably() -> None:
    registry = PluginRegistry()
    music = FakePlugin("audio.cast")
    light = FakePlugin("hue")

    registry.register(music)
    registry.register(light)

    assert registry.kinds == ("audio.cast", "hue")
    assert registry.require("hue") is light


def test_registry_rejects_duplicate_and_invalid_kinds() -> None:
    registry = PluginRegistry()
    registry.register(FakePlugin())

    with pytest.raises(PluginError, match="Duplicate plugin kind"):
        registry.register(FakePlugin())
    with pytest.raises(PluginError, match="lowercase namespaced identifier"):
        registry.register(FakePlugin("Hue Adapter"))
    with pytest.raises(PluginError, match="No plugin is registered"):
        registry.require("missing")


def test_register_many_is_atomic_on_validation_failure() -> None:
    registry = PluginRegistry()

    with pytest.raises(PluginError, match="Duplicate plugin kind"):
        registry.register_many([FakePlugin("hue"), FakePlugin("hue")])

    assert registry.kinds == ()


def test_registry_rejects_objects_outside_plugin_contract() -> None:
    registry = PluginRegistry()

    with pytest.raises(PluginError, match="does not implement"):
        registry.register(cast(ActionPlugin, object()))


def test_registry_rejects_synchronous_plugin_execution() -> None:
    registry = PluginRegistry()

    with pytest.raises(PluginError, match="must be asynchronous"):
        registry.register(cast(ActionPlugin, SynchronousPlugin()))


def test_register_many_is_atomic_against_existing_plugins() -> None:
    registry = PluginRegistry()
    existing = FakePlugin("existing")
    registry.register(existing)

    with pytest.raises(PluginError, match="Duplicate plugin kind"):
        registry.register_many([FakePlugin("new"), FakePlugin("existing")])

    assert registry.kinds == ("existing",)


def test_entry_point_factories_load_in_deterministic_order() -> None:
    registry = PluginRegistry()
    calls: list[str] = []

    def factory(name: str, kind: str) -> object:
        calls.append(name)
        return FakePlugin(kind)

    candidates = [
        FakeEntryPoint("zeta", lambda: factory("zeta", "audio.local")),
        FakeEntryPoint("alpha", lambda: factory("alpha", "hue")),
    ]

    load_installed_plugins(registry, candidates=candidates)

    assert registry.kinds == ("audio.local", "hue")
    assert calls == ["alpha", "zeta"]


def test_installed_entry_point_discovery_uses_public_group(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = PluginRegistry()
    discovered: dict[str, str] = {}

    def discover(*, group: str) -> list[FakeEntryPoint]:
        discovered["group"] = group
        return [FakeEntryPoint("local", lambda: FakePlugin("audio.local"))]

    monkeypatch.setattr(plugins, "entry_points", discover)
    load_installed_plugins(registry)

    assert discovered["group"] == PLUGIN_ENTRY_POINT_GROUP
    assert registry.kinds == ("audio.local",)


def test_entry_point_failures_are_reported_with_the_original_cause() -> None:
    registry = PluginRegistry()
    existing = FakePlugin("existing")
    registry.register(existing)
    cause = ImportError("plugin dependency unavailable")

    with pytest.raises(PluginError, match="Could not import plugin factory") as error:
        load_installed_plugins(
            registry,
            candidates=[
                FakeEntryPoint("valid", lambda: FakePlugin("valid")),
                FakeEntryPoint("broken", cause),
            ],
        )

    assert error.value.__cause__ is cause
    assert registry.kinds == ("existing",)


def test_factory_failure_and_discovery_failure_are_translated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = PluginRegistry()
    factory_error = RuntimeError("factory failed")

    def fail_factory() -> object:
        raise factory_error

    with pytest.raises(PluginError, match="Could not create plugin instance") as factory_failure:
        load_installed_plugins(
            registry,
            candidates=[FakeEntryPoint("broken", fail_factory)],
        )
    assert factory_failure.value.__cause__ is factory_error

    discovery_error = OSError("metadata unavailable")

    def fail_discovery(*, group: str) -> list[FakeEntryPoint]:
        raise discovery_error

    monkeypatch.setattr(plugins, "entry_points", fail_discovery)
    with pytest.raises(
        PluginError, match="Could not discover installed plugins"
    ) as discovery_failure:
        load_installed_plugins(registry)
    assert discovery_failure.value.__cause__ is discovery_error


def test_entry_point_must_return_a_valid_plugin() -> None:
    registry = PluginRegistry()

    with pytest.raises(PluginError, match="does not implement"):
        load_installed_plugins(
            registry,
            candidates=[FakeEntryPoint("invalid", lambda: object())],
        )


def test_entry_point_must_resolve_to_factory() -> None:
    registry = PluginRegistry()

    with pytest.raises(PluginError, match="must reference a factory"):
        load_installed_plugins(
            registry,
            candidates=[FakeEntryPoint("not-callable", object())],
        )
