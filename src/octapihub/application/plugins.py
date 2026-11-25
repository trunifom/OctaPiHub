"""Typed action-plugin contract and Python entry-point discovery."""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Iterable, Mapping
from importlib.metadata import entry_points
from typing import Protocol, TypeGuard, runtime_checkable

from octapihub.core.errors import PluginError

PLUGIN_ENTRY_POINT_GROUP = "octapihub.plugins"
_PLUGIN_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


@runtime_checkable
class ActionPlugin(Protocol):
    """Contract implemented by an installed action adapter.

    Plugin implementations are trusted Python code and run in the hub process. The
    application owns scheduling and error isolation; a plugin only performs its own
    action and must not start a competing BLE owner.
    """

    @property
    def kind(self) -> str:
        """Return the unique, stable action namespace handled by this plugin."""

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        """Execute one action or raise an explicit error."""


PluginFactory = Callable[[], ActionPlugin]


class PluginEntryPoint(Protocol):
    """Minimal metadata entry-point surface required by the loader."""

    @property
    def name(self) -> str:
        """Return the entry-point name."""

    def load(self) -> object:
        """Load the entry-point target."""


class PluginRegistry:
    """Explicit, process-local registry of action plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, ActionPlugin] = {}

    @property
    def kinds(self) -> tuple[str, ...]:
        """Return registered plugin kinds in stable order."""
        return tuple(sorted(self._plugins))

    def register(self, plugin: ActionPlugin) -> None:
        """Register a plugin after checking its contract and unique kind."""
        self.register_many((plugin,))

    def register_many(self, plugins: Iterable[ActionPlugin]) -> None:
        """Register a group atomically; invalid groups leave the registry unchanged."""
        pending: dict[str, ActionPlugin] = {}
        for plugin in plugins:
            kind = _validated_kind(plugin)
            if kind in self._plugins or kind in pending:
                raise PluginError("Duplicate plugin kind", details={"kind": kind})
            pending[kind] = plugin
        self._plugins.update(pending)

    def require(self, kind: str) -> ActionPlugin:
        """Return a registered plugin or raise instead of silently skipping an action."""
        try:
            return self._plugins[kind]
        except KeyError as exc:
            raise PluginError(
                "No plugin is registered for this action kind", details={"kind": kind}
            ) from exc


def _validated_kind(plugin: ActionPlugin) -> str:
    if not isinstance(plugin, ActionPlugin):
        raise PluginError(
            "Plugin does not implement the action plugin contract",
            details={"plugin_type": type(plugin).__name__},
        )
    kind = plugin.kind
    if not inspect.iscoroutinefunction(plugin.execute):
        raise PluginError(
            "Plugin execute method must be asynchronous",
            details={"kind": kind if isinstance(kind, str) else type(kind).__name__},
        )
    if not isinstance(kind, str) or not _PLUGIN_KIND_PATTERN.fullmatch(kind):
        raise PluginError(
            "Plugin kind must be a lowercase namespaced identifier",
            details={"kind": kind if isinstance(kind, str) else type(kind).__name__},
        )
    return kind


def load_installed_plugins(
    registry: PluginRegistry, *, candidates: Iterable[PluginEntryPoint] | None = None
) -> None:
    """Load installed plugin factories from the ``octapihub.plugins`` entry-point group.

    Each entry point must resolve to a zero-argument factory returning an
    :class:`ActionPlugin`. Discovery order is sorted by entry-point name for predictable
    startup and diagnostics. Any broken plugin fails explicitly; startup cannot appear
    successful while a configured extension is missing.
    """
    discovered: tuple[PluginEntryPoint, ...]
    if candidates is None:
        try:
            discovered = tuple(entry_points(group=PLUGIN_ENTRY_POINT_GROUP))
        except Exception as exc:
            raise PluginError(
                "Could not discover installed plugins",
                details={"entry_point_group": PLUGIN_ENTRY_POINT_GROUP},
            ) from exc
    else:
        discovered = tuple(candidates)
    staged_registry = PluginRegistry()
    loaded_plugins: list[ActionPlugin] = []
    for candidate in sorted(discovered, key=lambda item: item.name):
        plugin_name = candidate.name
        try:
            factory = candidate.load()
        except Exception as exc:
            raise PluginError(
                "Could not import plugin factory",
                details={"entry_point": plugin_name},
            ) from exc
        if not _is_plugin_factory(factory):
            raise PluginError(
                "Plugin entry point must reference a factory",
                details={"entry_point": plugin_name},
            )
        try:
            plugin = factory()
        except Exception as exc:
            raise PluginError(
                "Could not create plugin instance",
                details={"entry_point": plugin_name},
            ) from exc
        try:
            staged_registry.register(plugin)
        except PluginError:
            raise
        except Exception as exc:
            raise PluginError(
                "Could not register plugin instance",
                details={"entry_point": plugin_name},
            ) from exc
        loaded_plugins.append(plugin)
    try:
        registry.register_many(loaded_plugins)
    except PluginError:
        raise
    except Exception as exc:
        raise PluginError("Could not register discovered plugins") from exc


def _is_plugin_factory(candidate: object) -> TypeGuard[PluginFactory]:
    return callable(candidate)
