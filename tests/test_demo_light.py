from __future__ import annotations

from fastapi.testclient import TestClient

from octapihub.core.config import load_config
from octapihub.web import forms
from octapihub.web.app import create_app
from octapihub.web.demo import DEMO_TOKEN, build_demo_backend


def paired_client() -> tuple[TestClient, object]:
    backend = build_demo_backend()
    client = TestClient(create_app(backend, manage_service=True))
    client.__enter__()
    client.post(
        "/light",
        data={
            "action": "pair",
            "token": DEMO_TOKEN,
            "hue.enabled": "on",
            "hue.host": "192.168.1.20",
            "hue.port": "443",
        },
    )
    second = client.post(
        "/light",
        data={
            "action": "pair",
            "token": DEMO_TOKEN,
            "hue.enabled": "on",
            "hue.host": "192.168.1.20",
            "hue.port": "443",
        },
    )
    assert second.status_code == 200, second.text[:300]
    return client, backend


def post_map(client: TestClient, backend: object, fields: dict[str, str]):  # type: ignore[no-untyped-def]
    body = {**forms.mapping_config_to_form(backend.settings.config), **fields, "token": DEMO_TOKEN}  # type: ignore[attr-defined]
    return client.post("/cubes/mapping", data=body)


def test_the_light_page_lists_rooms_and_runs_light_actions() -> None:
    client, backend = paired_client()
    try:
        page = client.get("/light").text
        assert "Lampen und" in page
        loaded = client.post("/light", data={"action": "refresh", "token": DEMO_TOKEN})
        assert "Szenen geladen" in loaded.text and "Wohnzimmer" in loaded.text
        for op in ("on", "off", "brighter", "darker", "color.next", "scene.next"):
            ok = client.post(
                "/light", data={"action": f"light:{op}:g-wohnzimmer", "token": DEMO_TOKEN}
            )
            assert ok.status_code == 200 and "Erledigt" in ok.text, op
        bad = client.post("/light", data={"action": "light:on:gibtsnicht", "token": DEMO_TOKEN})
        assert bad.status_code == 422
        worse = client.post(
            "/light", data={"action": "light:explode:g-wohnzimmer", "token": DEMO_TOKEN}
        )
        assert worse.status_code == 422
        denied = client.post("/light", data={"action": "light:on:g-wohnzimmer"})
        assert denied.status_code == 403
    finally:
        client.__exit__(None, None, None)


def test_light_actions_can_be_assigned_to_faces_with_a_room() -> None:
    client, backend = paired_client()
    try:
        client.post("/light", data={"action": "refresh", "token": DEMO_TOKEN})
        page = client.get("/cubes").text
        assert (
            "Licht: Steuern" in page and "Licht: Farbe setzen" in page and "Nächste Szene" in page
        )
        fields = {
            "map.1.0": "hue:off",
            "map.1.4": "hue:brighter",
            "map.1.4.group": "g-kueche",
            "map.1.5": "hue:color.blue",
            "map.1.6": "hue:scene.next",
            "map.1.6.group": "g-wohnzimmer",
        }
        assert post_map(client, backend, fields).status_code == 200
        saved = {
            (m.cube, m.face): (m.actions[0].action, dict(m.actions[0].parameters))
            for m in load_config(backend.settings._path).mappings  # type: ignore[attr-defined]
        }
        assert saved[("Musik", 0)] == ("light.off", {})
        assert saved[("Musik", 4)] == ("light.brighter", {"group": "g-kueche"})
        assert saved[("Musik", 5)] == ("light.color", {"color": "blue"})
        assert saved[("Musik", 6)] == ("scene.next", {"group": "g-wohnzimmer"})
        again = client.get("/cubes").text
        assert 'value="hue:color.blue" selected' in again and 'value="g-kueche" selected' in again
        # unknown room and unknown catalog value are refused
        assert (
            post_map(client, backend, {"map.1.2": "hue:off", "map.1.2.group": "weg"}).status_code
            == 422
        )
        assert post_map(client, backend, {"map.1.2": "hue:explode"}).status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_catalog_helpers_are_consistent() -> None:
    assert forms.hue_choice_action("hue:toggle", "") == {
        "kind": "hue",
        "action": "light.toggle",
        "parameters": {},
    }
    assert forms.hue_choice_action("hue:nope", "") is None
    labels = {value for _, items in forms.hue_choice_options() for value, _ in items}
    assert "hue:toggle" in labels and "hue:color.warm" in labels and "hue:color.next" in labels
    assert all(forms.hue_choice_action(value, "") is not None for value in labels)
