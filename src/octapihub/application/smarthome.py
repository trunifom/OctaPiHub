"""Smart-home actions for other systems: web requests, MQTT messages and Home Assistant calls.

The service is the action plugin ``smarthome`` with one action, ``trigger``, which takes the
id of an action defined on the Smart-Home page. Home Assistant is also the way to Matter
devices: whatever is connected there (Matter, Zigbee, Google Home integrations, ...) can be
switched by a cube.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

from octapihub.core.config import (
    SmartActionConfig,
    SmartActionKind,
    SmartConnectionConfig,
    SmartHomeConfig,
)
from octapihub.core.errors import (
    OctaPiHubError,
    SmartHomeError,
    SmartHomeNotConfiguredError,
)
from octapihub.infrastructure.smarthome import http_client, mqtt_client

logger = logging.getLogger(__name__)

SECRET_PREFIX = "SMARTHOME_"  # noqa: S105 - a key name, not a secret
type HttpSender = Callable[..., Awaitable[int]]
type MqttPublisher = Callable[..., Awaitable[None]]
type Clock = Callable[[], datetime]


class SecretReader(Protocol):
    """What the service needs from the secrets file."""

    def get(self, key: str) -> str | None: ...


def secret_key(connection_id: str) -> str:
    """The ``secrets.env`` key for a connection's password or token."""
    return SECRET_PREFIX + connection_id.upper().replace("-", "_")


@dataclass(frozen=True, slots=True)
class ActionResult:
    """What happened the last time an action ran (shown on the Smart-Home page)."""

    ok: bool
    at: datetime
    message: str = ""
    code: str = ""


class SmartHomeService:
    """Runs smart-home actions; failures are typed and recorded per action."""

    kind = "smarthome"

    def __init__(
        self,
        config: SmartHomeConfig,
        secrets: SecretReader,
        *,
        http: HttpSender = http_client.send_request,
        mqtt: MqttPublisher = mqtt_client.publish,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._secrets = secrets
        self._http = http
        self._mqtt = mqtt
        self._clock = clock
        self._results: dict[str, ActionResult] = {}

    @property
    def config(self) -> SmartHomeConfig:
        """The active settings."""
        return self._config

    @property
    def results(self) -> Mapping[str, ActionResult]:
        """The outcome of the most recent run of each action."""
        return dict(self._results)

    def reconfigure(self, config: SmartHomeConfig) -> None:
        """Apply new settings; results of actions that no longer exist are forgotten."""
        self._config = config
        known = {a.id for a in config.actions}
        self._results = {k: v for k, v in self._results.items() if k in known}

    def has_secret(self, connection_id: str) -> bool:
        """Whether a password or token is stored for the connection (never its value)."""
        try:
            return bool(self._secrets.get(secret_key(connection_id)))
        except OctaPiHubError:
            return False

    async def execute(self, action: str, parameters: Mapping[str, object]) -> None:
        """Plugin entry point: ``trigger`` with the id of a configured action."""
        if action != "trigger":
            raise SmartHomeNotConfiguredError(
                f"Unsupported smart-home action {action!r}",
                user_message="Diese Smart-Home-Aktion gibt es nicht.",
            )
        action_id = parameters.get("id")
        if not isinstance(action_id, str) or not action_id:
            raise SmartHomeNotConfiguredError(
                "No action id", user_message="Es ist keine Smart-Home-Aktion ausgewählt."
            )
        await self.run(action_id)

    async def run(self, action_id: str) -> None:
        """Run one configured action and record the outcome."""
        found = next((a for a in self._config.actions if a.id == action_id), None)
        if found is None:
            raise SmartHomeNotConfiguredError(
                f"Unknown smart-home action {action_id!r}",
                user_message="Diese Smart-Home-Aktion gibt es nicht (mehr).",
            )
        try:
            await self._dispatch(found)
        except SmartHomeError as exc:
            self._results[found.id] = ActionResult(
                False, self._clock(), exc.user_message, exc.error_code
            )
            logger.warning("Smart-home action %s failed (%s)", found.id, exc.error_code)
            raise
        except Exception as exc:
            self._results[found.id] = ActionResult(
                False,
                self._clock(),
                "Unerwarteter Fehler. Details stehen im Log.",
                SmartHomeError.code,
            )
            logger.exception("Smart-home action %s failed unexpectedly", found.id)
            raise SmartHomeError("Unexpected smart-home failure") from exc
        self._results[found.id] = ActionResult(True, self._clock(), "Gesendet")

    # ----- implementations -----

    def _connection(self, connection_id: str) -> SmartConnectionConfig:
        for connection in self._config.connections:
            if connection.id == connection_id:
                return connection
        raise SmartHomeNotConfiguredError(
            f"Unknown connection {connection_id!r}",
            user_message="Die Verbindung dieser Aktion gibt es nicht (mehr).",
        )

    def _secret(self, connection: SmartConnectionConfig) -> str:
        try:
            return self._secrets.get(secret_key(connection.id)) or ""
        except OctaPiHubError as exc:
            raise SmartHomeNotConfiguredError(
                "Secret unreadable", user_message=exc.user_message
            ) from exc

    async def _dispatch(self, action: SmartActionConfig) -> None:
        if action.kind is SmartActionKind.HTTP:
            await self._http(action.method, action.url, action.body, action.content_type)
        elif action.kind is SmartActionKind.MQTT:
            connection = self._connection(action.connection)
            host, _, port = connection.address.rpartition(":")
            await self._mqtt(
                host,
                int(port),
                action.topic,
                action.payload,
                tls=connection.tls,
                username=connection.username,
                password=self._secret(connection),
                qos=action.qos,
                retain=action.retain,
            )
        else:
            connection = self._connection(action.connection)
            token = self._secret(connection)
            if not token:
                raise SmartHomeNotConfiguredError(
                    "Home Assistant token missing",
                    user_message=(
                        "Für Home Assistant fehlt das Zugriffstoken. Tragen Sie es auf der Seite "
                        "Smart Home ein."
                    ),
                )
            domain, _, service = action.service.partition(".")
            body: dict[str, object] = json.loads(action.data) if action.data else {}
            if action.entity_id:
                body["entity_id"] = action.entity_id
            await self._http(
                "POST",
                f"{connection.address}/api/services/{quote(domain)}/{quote(service)}",
                json.dumps(body, separators=(",", ":")),
                "application/json",
                {"Authorization": f"Bearer {token}"},
            )
