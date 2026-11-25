"""Form handling for the Smart-Home page: connections, actions, and their secrets.

Connections use fields ``c.<row>.<name>`` and actions ``a.<row>.<name>``; ``<row>`` is the row
number of an existing entry or ``new``. Every row is validated on its own so one submission
reports all mistakes at once; the whole table is validated again before anything is saved.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from octapihub.core.config import (
    MAX_SMART_ACTIONS,
    MAX_SMART_CONNECTIONS,
    AppConfig,
    SmartActionKind,
    SmartConnectionKind,
    SmartHomeConfig,
    smarthome_table,
    with_smarthome_table,
)
from octapihub.core.errors import ValidationError
from octapihub.web.music_forms import row_keys, slugify

FORM_ERROR_KEY = "_form"
CONNECTION_KIND_LABELS = {
    SmartConnectionKind.MQTT.value: "MQTT-Broker (Zigbee2MQTT, Tasmota, ESPHome, Node-RED …)",
    SmartConnectionKind.HOMEASSISTANT.value: "Home Assistant (auch der Weg zu Matter)",
}
ACTION_KIND_LABELS = {
    SmartActionKind.HTTP.value: "Webhook (Web-Adresse aufrufen)",
    SmartActionKind.MQTT.value: "MQTT-Nachricht senden",
    SmartActionKind.HOMEASSISTANT.value: "Home-Assistant-Dienst aufrufen",
}
METHOD_LABELS = {m: m for m in ("POST", "GET", "PUT", "PATCH", "DELETE")}
CONTENT_TYPE_LABELS = {"application/json": "JSON", "text/plain": "Text"}
QOS_LABELS = {"0": "0 – höchstens einmal", "1": "1 – mindestens einmal"}
_CONNECTION_FIELDS = ("name", "kind", "address", "username")
_ACTION_FIELDS = (
    "name", "kind", "connection", "url", "method", "body", "content_type",
    "topic", "payload", "service", "entity_id", "data",
)  # fmt: skip
_FIELD_INDEX = re.compile(r"smarthome\.(connections|actions)\[(\d+)\]\.(\w+)")
_FIELD_MESSAGES = {
    "name": "Bitte einen Namen eingeben (höchstens 60 Zeichen).",
    "kind": "Bitte eine Art wählen.",
    "address": "Format prüfen: MQTT „host:1883“, Home Assistant „http://host:8123“.",
    "connection": "Bitte eine passende Verbindung wählen (zuerst oben anlegen).",
    "url": "Die Adresse muss mit http:// oder https:// beginnen.",
    "method": "Bitte eine Anfrage-Art wählen.",
    "content_type": "Bitte JSON oder Text wählen.",
    "topic": "Das Thema darf nicht leer sein und kein +, # oder $ am Anfang enthalten.",
    "qos": "Bitte 0 oder 1 wählen.",
    "service": "Der Dienst muss so aussehen: light.turn_on",
    "entity_id": "Das Gerät muss so aussehen: light.schreibtisch",
    "data": 'Die Zusatzdaten müssen ein JSON-Objekt sein, z. B. {"brightness": 120}',
    "body": "Der Inhalt ist zu lang (höchstens 500 Zeichen).",
    "payload": "Der Inhalt ist zu lang (höchstens 500 Zeichen).",
    "id": "Die interne Kennung ist ungültig. Bitte den Namen ändern.",
    "username": "Der Benutzername ist zu lang.",
}


@dataclass(frozen=True, slots=True)
class SmartHomeFormResult:
    """Parsed submission: the validated table plus new secrets, or per-field errors."""

    table: dict[str, object] | None
    errors: dict[str, str]
    secrets: dict[str, str]  # connection id -> new password/token ("" = unchanged)


def smarthome_to_form(smarthome: SmartHomeConfig) -> dict[str, str]:
    """Current settings as form values (secrets are never part of it)."""
    values: dict[str, str] = {}
    for i, c in enumerate(smarthome.connections):
        p = f"c.{i}."
        values.update(
            {
                p + "id": c.id,
                p + "name": c.name,
                p + "kind": c.kind.value,
                p + "address": c.address,
                p + "tls": "on" if c.tls else "",
                p + "username": c.username,
            }
        )
    for i, a in enumerate(smarthome.actions):
        p = f"a.{i}."
        values.update(
            {
                p + "id": a.id,
                p + "name": a.name,
                p + "kind": a.kind.value,
                p + "connection": a.connection,
                p + "url": a.url,
                p + "method": a.method,
                p + "body": a.body,
                p + "content_type": a.content_type,
                p + "topic": a.topic,
                p + "payload": a.payload,
                p + "retain": "on" if a.retain else "",
                p + "qos": str(a.qos),
                p + "service": a.service,
                p + "entity_id": a.entity_id,
                p + "data": a.data,
            }
        )
    return values


def with_blank_rows(values: dict[str, str]) -> dict[str, str]:
    """Add defaults for the empty "new" rows."""
    values.setdefault("c.new.kind", SmartConnectionKind.MQTT.value)
    values.setdefault("a.new.kind", SmartActionKind.HTTP.value)
    values.setdefault("a.new.method", "POST")
    values.setdefault("a.new.content_type", "application/json")
    values.setdefault("a.new.qos", "0")
    return values


def _blank(form: Mapping[str, str], group: str, key: str, names: tuple[str, ...]) -> bool:
    skip = {"kind", "method", "content_type", "qos"}
    return not any(form.get(f"{group}.{key}.{n}", "").strip() for n in names if n not in skip)


def _connection_row(form: Mapping[str, str], key: str, taken: set[str]) -> dict[str, object]:
    def get(name: str) -> str:
        return form.get(f"c.{key}.{name}", "").strip()

    return {
        "id": get("id") or slugify(get("name"), taken, "verbindung"),
        "name": get("name"),
        "kind": get("kind"),
        "address": get("address"),
        "tls": bool(form.get(f"c.{key}.tls")),
        "username": get("username"),
    }


def _action_row(form: Mapping[str, str], key: str, taken: set[str]) -> dict[str, object]:
    def get(name: str) -> str:
        return form.get(f"a.{key}.{name}", "").strip()

    qos = get("qos") or "0"
    return {
        "id": get("id") or slugify(get("name"), taken, "aktion"),
        "name": get("name"),
        "kind": get("kind"),
        "connection": get("connection"),
        "url": get("url"),
        "method": get("method") or "POST",
        "body": get("body"),
        "content_type": get("content_type") or "application/json",
        "topic": get("topic"),
        "payload": get("payload"),
        "retain": bool(form.get(f"a.{key}.retain")),
        "qos": int(qos) if qos.isdigit() else -1,
        "service": get("service"),
        "entity_id": get("entity_id"),
        "data": get("data"),
    }


def _map_error(exc: ValidationError, group: str, key: str) -> tuple[str, str]:
    match = _FIELD_INDEX.search(str(exc.details.get("field", "")))
    name = match.group(3) if match else "name"
    return f"{group}.{key}.{name}", _FIELD_MESSAGES.get(name, exc.user_message)


def parse_smarthome_form(form: Mapping[str, str], current: AppConfig) -> SmartHomeFormResult:
    """Validate the whole Smart-Home form."""
    errors: dict[str, str] = {}
    secrets: dict[str, str] = {}
    connections: list[tuple[str, dict[str, object]]] = []
    valid_connections: list[dict[str, object]] = []
    taken: set[str] = set()
    for key in row_keys(form, "c"):
        if form.get(f"c.{key}.remove") or (
            key == "new" and _blank(form, "c", key, _CONNECTION_FIELDS)
        ):
            continue
        row = _connection_row(form, key, taken)
        taken.add(str(row["id"]))
        try:
            with_smarthome_table(current, {"connections": [row], "actions": []})
            valid_connections.append(row)
        except ValidationError as exc:
            field, message = _map_error(exc, "c", key)
            errors[field] = message
        connections.append((key, row))
        new_secret = form.get(f"c.{key}.secret", "")
        if new_secret:
            secrets[str(row["id"])] = new_secret
    plain_connections = [dict(r) for _, r in connections[:MAX_SMART_CONNECTIONS]]
    usable = [dict(r) for r in valid_connections[:MAX_SMART_CONNECTIONS]]

    actions: list[tuple[str, dict[str, object]]] = []
    taken = set()
    for key in row_keys(form, "a"):
        if form.get(f"a.{key}.remove") or (key == "new" and _blank(form, "a", key, _ACTION_FIELDS)):
            continue
        row = _action_row(form, key, taken)
        taken.add(str(row["id"]))
        try:
            with_smarthome_table(current, {"connections": usable, "actions": [row]})
        except ValidationError as exc:
            field, message = _map_error(exc, "a", key)
            errors.setdefault(field, message)
        actions.append((key, row))
    if len(connections) > MAX_SMART_CONNECTIONS:
        errors[FORM_ERROR_KEY] = f"Es sind höchstens {MAX_SMART_CONNECTIONS} Verbindungen möglich."
    if len(actions) > MAX_SMART_ACTIONS:
        errors[FORM_ERROR_KEY] = f"Es sind höchstens {MAX_SMART_ACTIONS} Aktionen möglich."
    if errors:
        return SmartHomeFormResult(None, errors, {})
    table: dict[str, Any] = {
        "connections": plain_connections,
        "actions": [r for _, r in actions],
    }
    try:
        with_smarthome_table(current, table)
    except ValidationError as exc:
        return SmartHomeFormResult(None, {FORM_ERROR_KEY: exc.user_message}, {})
    return SmartHomeFormResult(table, {}, secrets)


def table_of(config: AppConfig) -> dict[str, object]:
    """The current table (for tests and the web layer)."""
    return smarthome_table(config.smarthome)
