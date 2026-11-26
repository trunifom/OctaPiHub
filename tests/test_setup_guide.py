from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from test_web_music import Rig

from octapihub.web.app import create_app
from octapihub.web.demo import build_demo_backend
from octapihub.web.setup_guide import build_steps, overview_hint, progress


def keys(steps) -> dict[str, bool]:  # type: ignore[no-untyped-def]
    return {s.key: s.done for s in steps}


def test_a_fresh_installation_has_the_cube_and_face_steps_open(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.backend.token = None
    steps = build_steps(rig.backend)
    state = keys(steps)
    assert state["token"] is False and state["cube"] is False and state["face"] is False
    assert state["music"] is True  # the test rig has music with devices
    done, total = progress(steps)
    assert (done, total) == (1, 4)  # light is optional and not done: not counted
    hint = overview_hint(rig.backend)
    assert hint is not None and hint["next"] == "Zugriffs-Token festlegen"


def test_configured_but_unconnected_cubes_keep_the_cube_step_open() -> None:
    backend = build_demo_backend()  # token, mappings and music exist; no cube is connected
    state = keys(build_steps(backend))
    assert state["token"] and state["face"] and not state["cube"]
    hint = overview_hint(backend)
    assert hint is not None and hint["next"] == "Würfel koppeln"
    with TestClient(create_app(backend)) as client:
        assert "Zur Einrichtung" in client.get("/").text
        page = client.get("/setup").text
    assert "Kippen Sie den Würfel" in page and "Würfel koppeln" in page


def test_the_overview_points_to_the_next_step_and_the_page_renders(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.backend.token = None
    with rig.client() as client:
        overview = client.get("/").text
        assert (
            "Zur Einrichtung" in overview and "Als Nächstes: Zugriffs-Token festlegen" in overview
        )
        page = client.get("/setup").text
    assert "von 4 Schritten erledigt" in page and 'href="/cubes"' in page
    assert page.count('<li class="setup-step') == 5


def test_a_mapping_completes_the_face_step(tmp_path: Path) -> None:
    import asyncio

    rig = Rig(tmp_path)
    assert keys(build_steps(rig.backend))["face"] is False
    asyncio.run(
        rig.settings.apply_mappings(
            [
                {
                    "cube": "Musik",
                    "face": 1,
                    "actions": [{"kind": "music", "action": "stop", "parameters": {}}],
                }
            ]
        )
    )
    assert keys(build_steps(rig.backend))["face"] is True
