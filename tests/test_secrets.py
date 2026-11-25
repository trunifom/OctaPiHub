from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from octapihub.core.errors import SecretsError
from octapihub.infrastructure.secrets import HUE_APPLICATION_KEY, SecretsFile

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
SECRET = "abc123SECRETvalue"


def _write(path: Path, text: str, mode: int = 0o600) -> None:
    path.write_text(text, encoding="utf-8")
    if os.name == "posix":
        path.chmod(mode)


def test_missing_file_means_no_secrets(tmp_path: Path) -> None:
    store = SecretsFile(tmp_path / "secrets.env")
    assert store.read() == {}
    assert store.get(HUE_APPLICATION_KEY) is None
    assert store.delete(HUE_APPLICATION_KEY) is False


def test_set_creates_file_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "secrets.env"
    store = SecretsFile(path)
    store.set(HUE_APPLICATION_KEY, SECRET)
    assert store.get(HUE_APPLICATION_KEY) == SECRET
    assert path.read_text(encoding="utf-8") == f"HUE_APPLICATION_KEY={SECRET}\n"
    assert list(path.parent.glob(".*.tmp")) == []


@posix_only
def test_new_file_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    SecretsFile(path).set(HUE_APPLICATION_KEY, SECRET)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_update_preserves_comments_and_other_secrets(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    _write(path, "# Zugangsdaten\nMQTT_PASSWORD=pw1\nHUE_APPLICATION_KEY=old\n")
    store = SecretsFile(path)
    store.set(HUE_APPLICATION_KEY, "new")
    assert (
        path.read_text(encoding="utf-8")
        == "# Zugangsdaten\nMQTT_PASSWORD=pw1\nHUE_APPLICATION_KEY=new\n"
    )
    assert store.delete(HUE_APPLICATION_KEY) is True
    assert store.read() == {"MQTT_PASSWORD": "pw1"}
    assert "# Zugangsdaten" in path.read_text(encoding="utf-8")


@posix_only
def test_file_readable_by_others_is_refused_without_leaking_values(tmp_path: Path) -> None:
    path = tmp_path / "secrets.env"
    _write(path, f"HUE_APPLICATION_KEY={SECRET}\n", mode=0o644)
    with pytest.raises(SecretsError) as error:
        SecretsFile(path).read()
    assert "chmod 600" in error.value.user_message
    assert SECRET not in str(error.value)
    assert SECRET not in repr(error.value.details)


@pytest.mark.parametrize(
    "text",
    [
        "NOEQUALS\n",
        "lower=case\n",
        "KEY=\n",
        "=value\n",
        "A=1\nA=2\n",
        "KEY with space=1\n",
    ],
)
def test_malformed_files_are_rejected_with_line_number_not_content(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "secrets.env"
    _write(path, text)
    store = SecretsFile(path)
    with pytest.raises(SecretsError) as error:
        store.read()
    assert "line" in error.value.details
    with pytest.raises(SecretsError):
        store.set("OTHER_KEY", "value")  # never rewrite a file we cannot parse
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("key", ["", "lower", "HAS SPACE", "1START", "A" * 65, "BAD-DASH"])
def test_invalid_names_are_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(SecretsError):
        SecretsFile(tmp_path / "secrets.env").set(key, "value")


@pytest.mark.parametrize("value", ["", "has space", "new\nline", "ümlaut", "x" * 513])
def test_invalid_values_are_rejected_and_not_echoed(tmp_path: Path, value: str) -> None:
    path = tmp_path / "secrets.env"
    with pytest.raises(SecretsError) as error:
        SecretsFile(path).set(HUE_APPLICATION_KEY, value)
    assert value == "" or value not in str(error.value)
    assert not path.exists()


def test_undecodable_and_oversized_files_are_reported(tmp_path: Path) -> None:
    binary = tmp_path / "binary.env"
    binary.write_bytes(b"\xff\xfe\x00bad")
    if os.name == "posix":
        binary.chmod(0o600)
    with pytest.raises(SecretsError):
        SecretsFile(binary).read()
    big = tmp_path / "big.env"
    _write(big, "A=" + "x" * 70000 + "\n")
    with pytest.raises(SecretsError, match="large"):
        SecretsFile(big).read()


def test_write_failure_keeps_existing_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secrets.env"
    _write(path, "HUE_APPLICATION_KEY=keep\n")

    def broken_replace(_source: object, _target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(SecretsError, match="write"):
        SecretsFile(path).set(HUE_APPLICATION_KEY, "new")
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "HUE_APPLICATION_KEY=keep\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_unwritable_directory_is_reported(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    with pytest.raises(SecretsError):
        SecretsFile(blocker / "secrets.env").set(HUE_APPLICATION_KEY, "value")
