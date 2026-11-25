from __future__ import annotations

import configparser
import importlib
import re
import shlex
import tomllib
from pathlib import Path

import pytest

from octapihub.core.config import CubeRole, load_config
from octapihub.web.__main__ import MIN_TOKEN_LENGTH, build_parser, token_problem

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


def _unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(DEPLOY / "octapihub.service", encoding="utf-8")
    return parser


def _environment(unit: configparser.ConfigParser) -> dict[str, str]:
    """Environment= lines (configparser keeps only the last duplicate, so read the file)."""
    values: dict[str, str] = {}
    for line in (DEPLOY / "octapihub.service").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"Environment=(\w+)=(.*)", line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    assert unit.has_section("Service")
    return values


# ---------- unit file ----------


def test_unit_has_the_required_sections_and_restart_policy() -> None:
    unit = _unit()
    assert {"Unit", "Service", "Install"} <= set(unit.sections())
    assert unit["Install"]["WantedBy"] == "multi-user.target"
    assert unit["Service"]["Restart"] == "on-failure"
    assert "network-online.target" in unit["Unit"]["After"]
    assert "bluetooth.target" in unit["Unit"]["After"]


def test_unit_runs_unprivileged_and_hardened() -> None:
    service = _unit()["Service"]
    assert service["User"] not in {"root", ""}
    assert service["NoNewPrivileges"] == "true"
    assert service["ProtectSystem"] == "strict"
    assert service["ProtectHome"] == "true"
    assert service["PrivateTmp"] == "true"
    assert service["UMask"] == "0077"
    assert {"bluetooth", "i2c", "input"} <= set(service["SupplementaryGroups"].split())
    assert "MemoryMax" in service


def test_exec_start_matches_the_real_command_line() -> None:
    unit = _unit()
    environment = _environment(unit)
    command = unit["Service"]["ExecStart"]
    expanded = re.sub(r"\$\{(\w+)\}", lambda m: environment[m.group(1)], command)
    tokens = shlex.split(expanded)
    assert tokens[1:3] == ["-m", "octapihub.web"]
    arguments = build_parser({}).parse_args(tokens[3:])
    assert arguments.config == "/etc/octapihub/octapihub.toml"
    assert arguments.secrets == "/etc/octapihub/secrets.env"
    assert arguments.data_dir == "/var/lib/octapihub"
    assert arguments.host == "127.0.0.1"  # LAN exposure must be a deliberate choice
    assert arguments.port == 8080
    assert not arguments.demo


def test_every_variable_used_in_exec_start_has_a_default() -> None:
    unit = _unit()
    used = set(re.findall(r"\$\{(\w+)\}", unit["Service"]["ExecStart"]))
    assert used <= set(_environment(unit))


def test_paths_the_service_writes_are_allowed_by_the_sandbox() -> None:
    unit = _unit()
    service = unit["Service"]
    environment = _environment(unit)
    command = re.sub(r"\$\{(\w+)\}", lambda m: environment[m.group(1)], service["ExecStart"])
    arguments = build_parser({}).parse_args(shlex.split(command.split("octapihub.web", 1)[1]))
    writable = service["ReadWritePaths"].split()
    for path in (arguments.config, arguments.secrets, arguments.data_dir):
        assert any(path == w or path.startswith(w + "/") for w in writable), path


# ---------- example files ----------


def test_example_config_is_valid_and_safe_by_default() -> None:
    config = load_config(DEPLOY / "octapihub.toml.example")
    assert config.schema_version == 5
    assert not config.hue.enabled
    assert {cube.role for cube in config.cubes} == {CubeRole.LIGHT, CubeRole.MUSIC}
    assert config.sense_hat.enabled
    text = (DEPLOY / "octapihub.toml.example").read_text(encoding="utf-8")
    assert "application_key" not in text.lower().replace("zugangsdaten", "")


def test_example_env_token_is_rejected_by_the_startup_check() -> None:
    text = (DEPLOY / "octapihub.env.example").read_text(encoding="utf-8")
    match = re.search(r"^OCTAPIHUB_WEB_TOKEN=(.+)$", text, re.MULTILINE)
    assert match is not None
    assert token_problem(match.group(1)) is not None  # copying the example unchanged fails


def test_install_guide_only_references_existing_files() -> None:
    guide = (DEPLOY / "INSTALL.md").read_text(encoding="utf-8")
    for name in re.findall(r"deploy/([\w.\-]+)", guide):
        assert (DEPLOY / name).exists(), name
    flat = re.sub(r"[\s>*]+", " ", guide.lower())
    assert "noch nicht auf einem echten raspberry pi" in flat  # honest about verification


# ---------- token and parser ----------


@pytest.mark.parametrize(
    ("token", "problem"),
    [
        (None, False),
        ("x" * MIN_TOKEN_LENGTH, False),
        ("a-long-random-looking-token-1234", False),
        ("short", True),
        ("x" * (MIN_TOKEN_LENGTH - 1), True),
        ("", True),
        ("HIER-EIN-LANGES-ZUFAELLIGES-TOKEN", True),
    ],
)
def test_token_check(token: str | None, problem: bool) -> None:
    assert (token_problem(token) is not None) is problem


def test_parser_reads_host_and_port_from_the_environment() -> None:
    parsed = build_parser({"OCTAPIHUB_HOST": "192.168.1.50", "OCTAPIHUB_PORT": "9000"}).parse_args(
        []
    )
    assert (parsed.host, parsed.port) == ("192.168.1.50", 9000)
    explicit = build_parser({"OCTAPIHUB_PORT": "9000"}).parse_args(
        ["--port", "9100", "--host", "::1"]
    )
    assert (explicit.host, explicit.port) == ("::1", 9100)


@pytest.mark.parametrize("port", ["0", "70000", "-1", "abc", "80.5"])
def test_parser_rejects_invalid_ports(port: str, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_parser({}).parse_args(["--port", port])
    assert exit_info.value.code == 2
    assert "Port" in capsys.readouterr().err


def test_invalid_port_in_environment_fails_clearly() -> None:
    with pytest.raises(Exception, match="Port"):
        build_parser({"OCTAPIHUB_PORT": "99999"})


# ---------- packaging ----------


def test_console_script_points_to_a_callable() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    target = project["project"]["scripts"]["octapihub"]
    module_name, _, function = target.partition(":")
    assert callable(getattr(importlib.import_module(module_name), function))
    package_data = project["tool"]["setuptools"]["package-data"]["octapihub.web"]
    assert "templates/*.html" in package_data
    assert "static/*" in package_data


# ---------- start-up refusals ----------


@pytest.fixture
def clean_root_logger():  # type: ignore[no-untyped-def]
    import logging

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def test_main_refuses_a_weak_token_before_serving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    clean_root_logger: None,
) -> None:
    from octapihub.web.__main__ import main

    config = tmp_path / "octapihub.toml"
    config.write_text(
        (DEPLOY / "octapihub.toml.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("OCTAPIHUB_WEB_TOKEN", "short")
    code = main(
        ["--config", str(config), "--data-dir", str(tmp_path), "--secrets", str(tmp_path / "s.env")]
    )
    assert code == 2
    assert "CH-WEB-008" in capsys.readouterr().err


def test_main_reports_a_missing_config_with_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], clean_root_logger: None
) -> None:
    from octapihub.web.__main__ import main

    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "CH-CFG-001" in capsys.readouterr().err
