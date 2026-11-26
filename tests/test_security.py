from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from octapihub.core.audit import AuditLog
from octapihub.core.auth_guard import FailureLimiter
from octapihub.web.app import create_app
from octapihub.web.demo import build_demo_backend

TOKEN = "demo"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---------- the limiter ----------


def test_five_mistakes_lock_a_client_out_and_the_lock_doubles() -> None:
    clock = Clock()
    limiter = FailureLimiter(5, 300, 60, 900, clock=clock)
    for _ in range(4):
        limiter.record_failure("a")
        assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 60
    assert limiter.retry_after("b") == 0  # other clients are not affected
    clock.now += 61
    assert limiter.retry_after("a") == 0
    for _ in range(5):
        limiter.record_failure("a")
    assert limiter.retry_after("a") == 120
    assert limiter.lockouts == 2 and limiter.failures == 10


def test_old_mistakes_are_forgotten_and_success_resets() -> None:
    clock = Clock()
    limiter = FailureLimiter(3, 100, 30, 900, clock=clock)
    limiter.record_failure("a")
    limiter.record_failure("a")
    clock.now += 101  # outside the window
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    limiter.record_success("a")
    limiter.record_failure("a")
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 0


def test_the_lock_time_is_capped_and_memory_is_bounded() -> None:
    clock = Clock()
    limiter = FailureLimiter(1, 100, 60, 200, clock=clock, max_keys=10)
    for _ in range(6):
        limiter.record_failure("a")
        clock.now += 1000
    assert limiter.retry_after("a") == 0
    limiter.record_failure("a")
    assert limiter.retry_after("a") == 200
    for index in range(50):
        limiter.record_failure(f"client-{index}")
    assert len(limiter._entries) <= 10


@pytest.mark.parametrize("args", [(0, 1, 1), (1, 0, 1), (1, 1, 0)])
def test_the_limiter_rejects_nonsense_limits(args: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError):
        FailureLimiter(*args)


# ---------- through the web interface ----------


def post(client: TestClient, token: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/cubes/manage",
        content=urlencode({"token": token, "action": "nothing"}),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def test_repeated_wrong_tokens_lock_out_even_the_right_one() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend)) as client:
        for _ in range(5):
            assert post(client, "falsch").status_code == 403
        locked = post(client, TOKEN)
        assert locked.status_code == 429
        assert "CH-WEB-021" in locked.text and "Sekunden" in locked.text
    assert backend.guard.lockouts == 1


def test_a_correct_token_resets_the_count() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend)) as client:
        for _ in range(4):
            assert post(client, "falsch").status_code == 403
        assert post(client, TOKEN).status_code != 403
        for _ in range(4):
            assert post(client, "falsch").status_code == 403
        assert post(client, TOKEN).status_code != 429


# ---------- the change log ----------


def test_changes_are_logged_without_content_and_rejections_are_marked(tmp_path: Path) -> None:
    from octapihub.web import forms

    backend = build_demo_backend()
    backend.audit = AuditLog(tmp_path / "audit.jsonl")
    with TestClient(create_app(backend)) as client:
        post(client, "falsch")
        body = {**forms.mapping_config_to_form(backend.settings.config), "token": TOKEN}
        saved = client.post(
            "/cubes/mapping",
            content=urlencode(body),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert saved.status_code == 200
        page = client.get("/system").text
    lines = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text("utf-8").splitlines()]
    assert [(e["action"], e["result"]) for e in lines] == [
        ("Würfel hinzugefügt oder entfernt", "abgelehnt"),
        ("Zuordnung der Würfelseiten gespeichert", "ok"),
    ]
    text = (tmp_path / "audit.jsonl").read_text("utf-8")
    assert "falsch" not in text and f'"{TOKEN}"' not in text
    assert "Änderungsprotokoll" in page and "abgelehnt" in page


def test_the_log_rotates_and_survives_garbage(tmp_path: Path) -> None:
    clock_values = iter(range(100))
    log = AuditLog(
        tmp_path / "a.jsonl",
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC).replace(second=next(clock_values) % 60),
        max_bytes=300,
    )
    for index in range(20):
        log.record("10.0.0.1", f"Aktion {index}")
    assert (tmp_path / "a.jsonl.1").exists()
    (tmp_path / "a.jsonl").write_text("kaputt\n{}\n[1]\n", encoding="utf-8")
    recent = log.recent(5)
    assert recent and all(entry.action.startswith("Aktion") for entry in recent)


def test_log_entries_are_cleaned_and_write_errors_are_harmless(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    log.record("1.2.3.4\r\nfake", "Zeile\x00" + "x" * 500)
    entry = log.recent(1)[0]
    assert "\n" not in entry.client and "\x00" not in entry.action and len(entry.action) <= 200
    blocked = AuditLog(tmp_path / "a.jsonl" / "nope" / "x.jsonl")
    blocked.record("a", "b")  # must not raise
    assert blocked.recent() == []


def test_without_a_log_the_page_says_so() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend)) as client:
        assert "nicht verfügbar" in client.get("/system").text


# ---------- metrics ----------


def test_metrics_need_the_token_and_show_numbers() -> None:
    backend = build_demo_backend()
    with TestClient(create_app(backend)) as client:
        assert client.get("/metrics").status_code == 403
        assert client.get("/metrics", headers={"Authorization": "Bearer falsch"}).status_code == 403
        response = client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        text = response.text
        assert "octapihub_up 1" in text and "octapihub_uptime_seconds" in text
        assert "octapihub_login_failures_total" in text
        assert 'octapihub_http_requests_total{class="4xx"}' in text
        assert client.get("/metrics", headers={"X-Octapihub-Token": TOKEN}).status_code == 200


def test_metrics_are_closed_without_a_configured_token() -> None:
    backend = build_demo_backend()
    backend.token = None
    with TestClient(create_app(backend)) as client:
        assert client.get("/metrics", headers={"Authorization": "Bearer "}).status_code == 403


# ---------- optional HTTPS ----------

_FIXTURES = Path(__file__).parent / "fixtures"
_CERT = str(_FIXTURES / "hue-test-ca.pem")
_KEY = str(_FIXTURES / "hue-test-server.key")


def test_https_files_are_checked(tmp_path: Path) -> None:
    from octapihub.web.__main__ import tls_problem

    assert tls_problem(None, None) is None
    assert tls_problem("", "") is None
    assert tls_problem(_CERT, _KEY) is None
    assert "beide" in (tls_problem(_CERT, None) or "")
    assert "beide" in (tls_problem(None, _KEY) or "")
    assert "gibt es nicht" in (tls_problem(str(tmp_path / "x.pem"), _KEY) or "")
    garbage = tmp_path / "g.pem"
    garbage.write_text("kein zertifikat", encoding="utf-8")
    assert "passen nicht" in (tls_problem(str(garbage), _KEY) or "")


def test_the_parser_reads_the_https_files_from_the_environment() -> None:
    from octapihub.web.__main__ import build_parser

    parsed = build_parser(
        {"OCTAPIHUB_SSL_CERT": "/c.pem", "OCTAPIHUB_SSL_KEY": "/k.pem"}
    ).parse_args([])
    assert (parsed.ssl_certfile, parsed.ssl_keyfile) == ("/c.pem", "/k.pem")
    plain = build_parser({}).parse_args([])
    assert plain.ssl_certfile is None and plain.ssl_keyfile is None


def test_main_serves_https_only_with_good_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import logging

    import uvicorn

    from octapihub.web.__main__ import main

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    try:
        assert main(["--demo", "--ssl-certfile", _CERT, "--ssl-keyfile", _KEY]) == 0
        assert calls[0]["ssl_certfile"] == _CERT and calls[0]["ssl_keyfile"] == _KEY
        assert main(["--demo"]) == 0
        assert calls[1]["ssl_certfile"] is None
        code = main(
            ["--demo", "--ssl-certfile", str(tmp_path / "missing.pem"), "--ssl-keyfile", _KEY]
        )
        assert code == 2 and len(calls) == 2
        assert "CH-WEB-022" in capsys.readouterr().err
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
        root.setLevel(level)
