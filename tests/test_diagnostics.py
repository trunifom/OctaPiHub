from __future__ import annotations

import io
import json
import logging
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.application.hue_service import HUE_KEY_NAME, HueService
from octapihub.application.sense_hat_service import SenseHatService
from octapihub.application.settings import SettingsManager
from octapihub.core.config import load_config, parse_config, save_config
from octapihub.core.errors import OctaPiHubError
from octapihub.core.logging_support import LogRing, Redactor
from octapihub.infrastructure.fake_sense_hat import FakeSenseHat
from octapihub.infrastructure.sense_hat import SenseHatAdapter
from octapihub.infrastructure.sqlite_sense_hat_history import SQLiteSenseHatHistory
from octapihub.infrastructure.system_info import SystemInfoReader
from octapihub.web.app import WebBackend, create_app
from octapihub.web.diagnostics import build_zip, collect_diagnostics

NOW = datetime(2026, 11, 25, 12, 0, 5, tzinfo=UTC)
TOKEN = "web-token-0123456789"
HUE_KEY = "hue-key-ABCDEFGHIJKLMNOP"
BRIDGE = "192.168.77.31"
MAC_LIGHT = "AA:BB:CC:DD:EE:01"
MAC_MUSIC = "AA:BB:CC:DD:EE:02"


class _Secrets:
    def __init__(self) -> None:
        self.values = {HUE_KEY_NAME: HUE_KEY}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class _Rig:
    def __init__(self, tmp_path: Path, *, with_logs: bool = True) -> None:
        self.config = parse_config(
            {
                "schema_version": 4,
                "bluetooth": {"adapter": "hci0"},
                "cubes": [
                    {"name": "Licht", "address": MAC_LIGHT, "role": "light"},
                    {"name": "Musik", "address": MAC_MUSIC, "role": "music"},
                ],
                "hue": {
                    "enabled": True,
                    "host": BRIDGE,
                    "ca_file": "/etc/octapihub/private-ca.pem",
                    "tls_server_name": "ecb5fafffe123456",
                },
                "sense_hat": {"history": {"database_path": str(tmp_path / "h.sqlite3")}},
            }
        )
        path = tmp_path / "octapihub.toml"
        save_config(path, self.config)
        sense = SenseHatService(
            self.config.sense_hat,
            lambda _c: SenseHatAdapter(FakeSenseHat()),
            lambda c: SQLiteSenseHatHistory(c.history.database_path),
        )

        async def pair(_c: object) -> str:
            return HUE_KEY

        self.hue = HueService(self.config.hue, _Secrets(), lambda _c, _k: _NullGateway(), pair)
        self.redactor = Redactor([TOKEN, HUE_KEY])
        self.ring = LogRing(self.redactor, 50)
        self.logger = logging.getLogger(f"octapihub.diag-{id(self)}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.logger.addHandler(self.ring)
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "uptime").write_text("100 1\n", encoding="utf-8")
        reader = SystemInfoReader(
            tmp_path,
            proc=proc,
            thermal_file=tmp_path / "none",
            runner=lambda _c: "throttled=0x0\n",
            clock=lambda: NOW,
        )
        self.backend = WebBackend(
            SettingsManager(path, self.config, sense, self.hue),
            sense,
            TOKEN,
            hue=self.hue,
            system=reader,
            logs=self.ring if with_logs else None,
            redactor=self.redactor,
            clock=lambda: NOW,
        )


class _NullGateway:
    async def list_scenes(self) -> tuple[()]:
        return ()

    async def recall_scene(self, scene_id: str) -> None:
        return None


def _post(client: TestClient, token: str | None = TOKEN):  # type: ignore[no-untyped-def]
    body = {} if token is None else {"token": token}
    return client.post(
        "/system/diagnostics",
        content=urlencode(body),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def _archive(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


@pytest.fixture
def rig(tmp_path: Path) -> _Rig:
    result = _Rig(tmp_path)
    asyncio_run_hue_start(result)
    return result


def asyncio_run_hue_start(rig: _Rig) -> None:
    import asyncio

    asyncio.run(rig.hue.start())


# ---------- download ----------


def test_download_contains_expected_files_and_headers(rig: _Rig) -> None:
    rig.logger.info("Würfel verbunden")
    with TestClient(create_app(rig.backend)) as client:
        response = _post(client)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        'attachment; filename="octapihub-diagnose-20261125-120005.zip"'
    )
    assert response.headers["cache-control"] == "no-store"
    files = _archive(response.content)
    assert set(files) == {"README.txt", "status.json", "config.toml", "system.json", "logs.txt"}
    assert "Würfel verbunden" in files["logs.txt"]
    assert json.loads(files["system.json"])["uptime_seconds"] == 100.0


def test_bundle_never_contains_secrets_or_full_addresses(rig: _Rig) -> None:
    rig.logger.error(
        "token=%s key %s bridge failed for %s at %s", TOKEN, HUE_KEY, MAC_LIGHT, BRIDGE
    )
    with TestClient(create_app(rig.backend)) as client:
        files = _archive(_post(client).content)
    everything = "\n".join(files.values())
    for forbidden in (TOKEN, HUE_KEY, MAC_LIGHT, MAC_MUSIC, BRIDGE, "private-ca.pem"):
        assert forbidden not in everything, forbidden
    assert "AA:**:**:**:**:01" in files["logs.txt"]
    assert "AA:**:**:**:**:01" in files["config.toml"]
    assert "AA:**:**:**:**:02" in files["config.toml"]


def test_status_reports_component_states_without_addresses(rig: _Rig) -> None:
    with TestClient(create_app(rig.backend)) as client:
        status = json.loads(_archive(_post(client).content)["status.json"])
    assert status["hue"] == {
        "state": "paired",
        "paired": True,
        "bridge_configured": True,
        "error_code": None,
        "scenes_known": 0,
    }
    assert status["cubes"] is None  # no cube service in this rig
    assert status["sense_hat"]["enabled"] is True
    assert status["version"]


def test_bundled_config_is_valid_toml_and_the_real_config_is_untouched(
    rig: _Rig, tmp_path: Path
) -> None:
    before = (tmp_path / "octapihub.toml").read_text(encoding="utf-8")
    with TestClient(create_app(rig.backend)) as client:
        files = _archive(_post(client).content)
    parsed = tomllib.loads(files["config.toml"])
    assert parsed["schema_version"] == 4
    assert parsed["hue"]["host"] == "hidden.invalid"
    assert (tmp_path / "octapihub.toml").read_text(encoding="utf-8") == before
    assert load_config(tmp_path / "octapihub.toml").hue.host == BRIDGE
    assert rig.backend.settings.config.hue.host == BRIDGE  # redaction used a copy


def test_optional_parts_are_left_out_when_unavailable(tmp_path: Path) -> None:
    rig = _Rig(tmp_path, with_logs=False)
    rig.backend.system = None
    with TestClient(create_app(rig.backend)) as client:
        files = _archive(_post(client).content)
    assert set(files) == {"README.txt", "status.json", "config.toml"}


# ---------- access and failures ----------


@pytest.mark.parametrize("token", [None, "", "wrong-token-zzz"])
def test_wrong_or_missing_token_returns_page_not_zip(rig: _Rig, token: str | None) -> None:
    with TestClient(create_app(rig.backend)) as client:
        response = _post(client, token)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "CH-WEB-004" in response.text
    assert "wrong-token-zzz" not in response.text
    assert "PK" not in response.text[:4]


def test_export_is_refused_without_a_configured_token(rig: _Rig) -> None:
    rig.backend.token = None
    with TestClient(create_app(rig.backend)) as client:
        response = _post(client, "anything")
        assert response.status_code == 403
        assert "CH-WEB-003" in response.text
        assert "disabled" in client.get("/system").text


def test_failure_while_building_is_reported_with_code(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octapihub.web.app as app_module

    def broken(*_args: object, **_kwargs: object) -> bytes:
        raise OctaPiHubError("zip failed", user_message="Kein Platz.", code="CH-WEB-007")

    monkeypatch.setattr(app_module, "build_zip", broken)
    with TestClient(create_app(rig.backend)) as client:
        response = _post(client)
    assert response.status_code == 500
    assert "Kein Platz." in response.text
    assert "CH-WEB-007" in response.text


def test_unexpected_failure_does_not_leak_details(
    rig: _Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    import octapihub.web.app as app_module

    def broken(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise RuntimeError(f"secret detail {TOKEN}")

    monkeypatch.setattr(app_module, "collect_diagnostics", broken)
    with TestClient(create_app(rig.backend)) as client:
        response = _post(client)
    assert response.status_code == 500
    assert "Traceback" not in response.text
    assert "secret detail" not in response.text
    assert "CH-WEB-007" in response.text


def test_system_page_offers_the_download_form(rig: _Rig) -> None:
    with TestClient(create_app(rig.backend)) as client:
        text = client.get("/system").text
    assert 'action="/system/diagnostics"' in text
    assert "Diagnose-Paket herunterladen" in text
    assert "nicht</strong> enthalten" in text


# ---------- unit level ----------


def test_collect_applies_final_redaction_to_every_file(rig: _Rig) -> None:
    redactor = Redactor(["README"])  # forces masking even inside generated text
    files = collect_diagnostics(rig.backend, redactor, NOW)
    assert all("README" not in text.replace("README.txt", "") for text in files.values())


def test_build_zip_is_deterministic_in_content_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = build_zip({"a.txt": "hello", "b.txt": "wörld"}, NOW)
    assert _archive(data) == {"a.txt": "hello", "b.txt": "wörld"}

    class Broken:
        def __init__(self, *_a: object, **_k: object) -> None:
            raise OSError("no space")

    monkeypatch.setattr(zipfile, "ZipFile", Broken)
    with pytest.raises(OctaPiHubError) as error:
        build_zip({"a": "b"}, NOW)
    assert error.value.error_code == "CH-WEB-007"
