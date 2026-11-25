"""Hue lifecycle: pairing, key storage, scene cache, and the ``hue`` action plugin."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import NoReturn, Protocol

from octapihub.application.hue_lights import (
    COLOR_CYCLE,
    LIGHT_ACTION_NAMES,
    HueGroup,
    cycle_index,
    default_group,
    simple_body,
)
from octapihub.core.config import HueConfig
from octapihub.core.errors import (
    HueBridgeError,
    HueNotConfiguredError,
    OctaPiHubError,
    SecretsError,
    ValidationError,
)

logger = logging.getLogger(__name__)

HUE_KEY_NAME = "HUE_APPLICATION_KEY"
_UNEXPECTED = "Unerwarteter Fehler bei der Hue-Anbindung. Details stehen im Log."


class SceneInfo(Protocol):
    """A Hue scene as the application needs it."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def group_id(self) -> str: ...


class HueGateway(Protocol):
    """Operations the application needs from a paired bridge."""

    async def list_scenes(self) -> Sequence[SceneInfo]: ...

    async def recall_scene(self, scene_id: str) -> None: ...

    async def list_groups(self) -> Sequence[HueGroup]: ...

    async def set_group(self, group_id: str, body: dict[str, object]) -> None: ...

    async def group_is_on(self, group_id: str) -> bool | None: ...


class SecretStore(Protocol):
    """Where the application key is kept."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> bool: ...


type GatewayFactory = Callable[[HueConfig, str], HueGateway]
type Pairer = Callable[[HueConfig], Awaitable[str]]
type Clock = Callable[[], datetime]


class HueState(StrEnum):
    """Where the Hue connection stands from the user's point of view."""

    DISABLED = "disabled"
    NOT_PAIRED = "not_paired"
    PAIRED = "paired"


@dataclass(frozen=True, slots=True)
class HueScene:
    """Scene entry kept for the interface."""

    id: str
    name: str
    group_id: str = ""


@dataclass(frozen=True, slots=True)
class HueStatus:
    """Snapshot for the interface; contains no secrets."""

    state: HueState
    host: str
    last_error: str | None
    last_error_code: str | None
    scenes_checked_at: datetime | None


def _raise_project_error(exc: Exception) -> NoReturn:
    """Re-raise project errors unchanged; wrap anything else in a generic Hue error."""
    if isinstance(exc, OctaPiHubError):
        raise exc
    raise HueBridgeError("Unexpected Hue failure", user_message=_UNEXPECTED) from exc


def _describe(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, OctaPiHubError):
        return exc.user_message, exc.error_code
    return _UNEXPECTED, OctaPiHubError.code


class HueService:
    """Own the Hue connection and act as the ``hue`` action plugin.

    The service is registered as a plugin even before the bridge is paired: actions then
    fail with ``CH-HUE-005`` and the outbox retries them until they expire, so a cube
    gesture made shortly before pairing still takes effect afterwards. Faults (unreadable
    secrets, a bad CA file, an unreachable bridge) are recorded for the interface and never
    stop the hub.
    """

    kind = "hue"

    def __init__(
        self,
        config: HueConfig,
        secrets: SecretStore,
        gateway_factory: GatewayFactory,
        pairer: Pairer,
        *,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._gateway_factory = gateway_factory
        self._pairer = pairer
        self._clock = clock
        self._lock = asyncio.Lock()
        self._key: str | None = None
        self._gateway: HueGateway | None = None
        self._gateway_error: OctaPiHubError | None = None
        self._scenes: tuple[HueScene, ...] = ()
        self._groups: tuple[HueGroup, ...] = ()
        self._cycle: dict[str, int] = {}
        self._scenes_checked_at: datetime | None = None
        self._last_error: str | None = None
        self._last_error_code: str | None = None

    # ----- state -----

    @property
    def config(self) -> HueConfig:
        """Active Hue settings."""
        return self._config

    @property
    def state(self) -> HueState:
        """Disabled, waiting for pairing, or paired."""
        if not self._config.enabled:
            return HueState.DISABLED
        return HueState.PAIRED if self._key else HueState.NOT_PAIRED

    @property
    def status(self) -> HueStatus:
        """Current status without secrets."""
        return HueStatus(
            self.state,
            self._config.host,
            self._last_error,
            self._last_error_code,
            self._scenes_checked_at,
        )

    @property
    def scenes(self) -> tuple[HueScene, ...]:
        """Last successfully read scenes (possibly stale; see ``scenes_checked_at``)."""
        return self._scenes

    @property
    def groups(self) -> tuple[HueGroup, ...]:
        """Rooms, zones and the whole home (possibly stale or empty before the first refresh)."""
        return self._groups

    # ----- lifecycle -----

    async def start(self) -> None:
        """Load the stored key and build the gateway; failures are recorded, not raised."""
        async with self._lock:
            await self._load()

    async def reconfigure(self, config: HueConfig) -> None:
        """Apply new settings; a stored key is kept (the bridge may only have a new address)."""
        async with self._lock:
            self._config = config
            self._scenes = ()
            self._groups = ()
            self._cycle = {}
            self._scenes_checked_at = None
            await self._load()

    async def pair(self) -> None:
        """Pair with the bridge and store the key.

        Raises:
            HueNotConfiguredError: Hue is disabled.
            HueLinkButtonError: The bridge button was not pressed (try again).
            OctaPiHubError: Any other pairing, network, certificate, or storage failure.
        """
        async with self._lock:
            if not self._config.enabled:
                raise HueNotConfiguredError(
                    "Hue is disabled", user_message="Hue ist ausgeschaltet. Schalten Sie es ein."
                )
            try:
                key = await self._pairer(self._config)
                await asyncio.to_thread(self._secrets.set, HUE_KEY_NAME, key)
            except Exception as exc:  # noqa: BLE001 - always report, never crash the caller
                self._record(exc)
                _raise_project_error(exc)
            self._key = key
            self._clear_error()
            self._build_gateway()
            logger.info("Hue bridge paired")

    async def unpair(self) -> None:
        """Forget the stored key.

        Raises:
            SecretsError: The key could not be removed (it stays active in that case).
        """
        async with self._lock:
            try:
                await asyncio.to_thread(self._secrets.delete, HUE_KEY_NAME)
            except Exception as exc:  # noqa: BLE001 - the key stays active on any failure
                self._record(exc)
                _raise_project_error(exc)
            self._key = None
            self._gateway = None
            self._gateway_error = None
            self._scenes = ()
            self._scenes_checked_at = None
            self._clear_error()

    # ----- operations -----

    async def refresh_scenes(self) -> tuple[HueScene, ...]:
        """Read the scene list; on failure the previous list is kept and the error raised."""
        gateway = self._require_gateway()
        try:
            found = await gateway.list_scenes()
        except Exception as exc:  # noqa: BLE001 - keep the previous scene list on any failure
            self._record(exc)
            _raise_project_error(exc)
        self._scenes = tuple(
            HueScene(scene.id, scene.name, getattr(scene, "group_id", "") or "") for scene in found
        )
        self._scenes_checked_at = self._clock()
        self._clear_error()
        await self._refresh_groups_quietly(gateway)
        return self._scenes

    async def refresh_groups(self) -> tuple[HueGroup, ...]:
        """Read rooms, zones and the whole home; the previous list is kept on failure."""
        gateway = self._require_gateway()
        try:
            self._groups = tuple(await gateway.list_groups())
        except Exception as exc:  # noqa: BLE001 - keep the previous list on any failure
            self._record(exc)
            _raise_project_error(exc)
        return self._groups

    async def _refresh_groups_quietly(self, gateway: HueGateway) -> None:
        """Groups are optional for scenes: a bridge or fake without them must not fail this."""
        try:
            self._groups = tuple(await gateway.list_groups())
        except Exception:  # noqa: BLE001 - scenes were read successfully; groups can be retried
            logger.warning("Hue groups could not be read", exc_info=False)

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        """Run one configured action (plugin contract)."""
        if action in LIGHT_ACTION_NAMES - {"scene.activate"}:
            await self._execute_light(action, parameters)
            return
        if action != "scene.activate":
            raise ValidationError(
                "Unsupported Hue action",
                user_message="Diese Hue-Aktion wird nicht unterstützt.",
            )
        scene_id = parameters.get("scene_id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValidationError(
                "Hue scene action requires a string scene_id",
                user_message="Für die Aktion ist keine Hue-Szene eingestellt.",
            )
        gateway = self._require_gateway()
        try:
            await gateway.recall_scene(scene_id)
        except Exception as exc:
            self._record(exc)
            raise
        self._clear_error()

    # ----- light control -----

    async def _execute_light(self, action: str, parameters: Mapping[str, object]) -> None:
        """Run a group action: on/off, brightness, colour, or the next/previous scene."""
        gateway = self._require_gateway()
        try:
            if action in ("scene.next", "scene.previous"):
                await self._step_scene(gateway, action, parameters)
            else:
                group = await self._resolve_group(gateway, parameters)
                if action == "light.color.next":
                    index = cycle_index(self._cycle.get(group.id, -1), len(COLOR_CYCLE), 1)
                    self._cycle[group.id] = index
                    body = simple_body("light.color", {"color": COLOR_CYCLE[index]}, None)
                else:
                    on = await gateway.group_is_on(group.id) if action == "light.toggle" else None
                    body = simple_body(action, parameters, on)
                await gateway.set_group(group.id, body)
        except Exception as exc:  # noqa: BLE001 - record for the interface, then re-raise typed
            self._record(exc)
            _raise_project_error(exc)
        self._clear_error()

    async def _resolve_group(
        self, gateway: HueGateway, parameters: Mapping[str, object]
    ) -> HueGroup:
        if not self._groups:
            self._groups = tuple(await gateway.list_groups())
        wanted = parameters.get("group")
        if wanted not in (None, ""):
            if not isinstance(wanted, str):
                raise ValidationError(
                    "Hue group must be a string", user_message="Die Lampengruppe ist ungültig."
                )
            for group in self._groups:
                if group.id == wanted:
                    return group
            raise ValidationError(
                "Unknown Hue group",
                user_message="Diese Lampengruppe gibt es nicht (mehr). Laden Sie die Gruppen neu.",
            )
        chosen = default_group(self._groups)
        if chosen is None:
            raise ValidationError(
                "No Hue group chosen",
                user_message="Bitte einen Raum oder „Alle Lampen“ wählen.",
            )
        return chosen

    async def _step_scene(
        self, gateway: HueGateway, action: str, parameters: Mapping[str, object]
    ) -> None:
        group = await self._resolve_group(gateway, parameters)
        if not self._scenes:
            found = await gateway.list_scenes()
            self._scenes = tuple(
                HueScene(s.id, s.name, getattr(s, "group_id", "") or "") for s in found
            )
        scenes = sorted(
            (s for s in self._scenes if s.group_id and s.group_id == group.room_id),
            key=lambda s: s.name.casefold(),
        )
        key = f"scene:{group.id}"
        index = cycle_index(
            self._cycle.get(key, -1), len(scenes), 1 if action == "scene.next" else -1
        )
        self._cycle[key] = index
        await gateway.recall_scene(scenes[index].id)

    # ----- internals -----

    def _require_gateway(self) -> HueGateway:
        if self.state is not HueState.PAIRED:
            raise HueNotConfiguredError("Hue is not configured and paired")
        if self._gateway_error is not None:
            raise self._gateway_error
        if self._gateway is None:
            raise HueNotConfiguredError("Hue gateway is unavailable")
        return self._gateway

    async def _load(self) -> None:
        self._key = None
        self._gateway = None
        self._gateway_error = None
        self._clear_error()
        if not self._config.enabled:
            return
        try:
            self._key = await asyncio.to_thread(self._secrets.get, HUE_KEY_NAME)
        except SecretsError as exc:
            self._record(exc)
            logger.error("Hue key unavailable: %s", exc)
            return
        if self._key:
            self._build_gateway()

    def _build_gateway(self) -> None:
        self._gateway = None
        self._gateway_error = None
        if not self._key:
            return
        try:
            self._gateway = self._gateway_factory(self._config, self._key)
        except OctaPiHubError as exc:
            self._gateway_error = exc
            self._record(exc)
            logger.error("Hue gateway could not be created: %s", exc)

    def _record(self, exc: Exception) -> None:
        self._last_error, self._last_error_code = _describe(exc)
        if not isinstance(exc, OctaPiHubError):
            logger.exception("Unexpected Hue failure")

    def _clear_error(self) -> None:
        self._last_error = None
        self._last_error_code = None
