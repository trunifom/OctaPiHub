from __future__ import annotations

import argparse
import dataclasses
from collections.abc import Sequence
from pathlib import Path

import pytest

from octapihub.core.config import parse_config, save_config
from octapihub.core.selftest import (
    CHECKS,
    Context,
    Probe,
    Result,
    Status,
    exit_code,
    format_report,
    run_checks,
)
from octapihub.web.__main__ import build_parser, run_selftest


class FakeProbe(Probe):
    """A Raspberry Pi that is ready; tests break one thing at a time."""

    def __init__(self, **overrides: object) -> None:
        self.files = {"/dev/i2c-1", "/sys/class/bluetooth/hci0", "/srv/music"}
        self.programs = {"mpv": "/usr/bin/mpv", "yt-dlp": "/usr/bin/yt-dlp"}
        self.modules = {"sense_hat", "pychromecast"}
        self.reachable = True
        self.posix = True
        self.free = 5 * 1024**3
        self.can_write = True
        self.port_ok = True
        self.version = (3, 12)
        self.commands: dict[str, str | None] = {
            "id": "octapihub bluetooth i2c input audio\n",
            "bluetoothctl": "Controller AA:BB Powered: yes\n",
            "aplay": "card 0: Headphones [bcm2835 Headphones]\n",
            "timedatectl": "yes\n",
        }
        self.texts = {"/proc/bus/input/devices": 'N: Name="Raspberry Pi Sense HAT Joystick"\n'}
        self.__dict__.update(overrides)

    def run(self, command: Sequence[str]) -> str | None:
        return self.commands.get(command[0])

    def which(self, program: str) -> str | None:
        return self.programs.get(program)

    def exists(self, path: str) -> bool:
        return path in self.files

    def read_text(self, path: str) -> str | None:
        return self.texts.get(path)

    def has_module(self, name: str) -> bool:
        return name in self.modules

    def tcp(self, host: str, port: int, timeout: float = 3.0) -> bool:
        return self.reachable

    def port_free(self, host: str, port: int) -> bool:
        return self.port_ok

    def is_posix(self) -> bool:
        return self.posix

    def free_bytes(self, path: str) -> int | None:
        return self.free

    def writable(self, path: str) -> bool:
        return self.can_write

    def python_version(self) -> tuple[int, int]:
        return self.version


def make_context(
    tmp_path: Path,
    probe: FakeProbe | None = None,
    token: str | None = "ein-langes-token-12345",
    **data: object,
) -> Context:
    body: dict[str, object] = {
        "schema_version": 7,
        "bluetooth": {"adapter": "hci0"},
        "cubes": [{"name": "A", "address": "AA:BB:CC:DD:EE:01", "role": "general"}],
        "hue": {"enabled": True, "host": "192.168.1.20"},
        "music": {
            "enabled": True,
            "library_dir": "/srv/music",
            "targets": [
                {"id": "pi", "name": "Pi", "kind": "local"},
                {"id": "wz", "name": "WZ", "kind": "cast", "device": "WZ"},
            ],
        },
        "smarthome": {
            "connections": [
                {"id": "mq", "name": "Broker", "kind": "mqtt", "address": "192.168.1.20:1883"},
                {
                    "id": "ha",
                    "name": "HA",
                    "kind": "homeassistant",
                    "address": "http://192.168.1.20:8123",
                },
            ]
        },
    }
    body.update(data)
    chosen = probe or FakeProbe()
    chosen.files.add(str(tmp_path))
    return Context(
        config=parse_config(body),
        config_note="",
        secrets_path=str(tmp_path / "secrets.env"),
        data_dir=str(tmp_path),
        host="127.0.0.1",
        port=8080,
        token=token,
        probe=chosen,
    )


def by_name(results: list[Result]) -> dict[str, Result]:
    return {f"{r.area}/{r.name}": r for r in results}


def test_a_ready_device_passes_everything(tmp_path: Path) -> None:
    results = run_checks(make_context(tmp_path))
    bad = [r for r in results if r.status in (Status.WARN, Status.FAIL)]
    assert bad == [] and exit_code(results) == 0
    names = {r.area for r in results}
    assert {
        "System",
        "Einstellungen",
        "Speicher",
        "Netzwerk",
        "Bluetooth",
        "Sense HAT",
        "Musik",
        "Geräte",
        "Zeit",
    } <= names


@pytest.mark.parametrize(
    ("override", "key", "status", "hint"),
    [
        ({"version": (3, 11)}, "System/Python", Status.FAIL, "Python 3.12"),
        (
            {
                "commands": {
                    "id": "octapihub audio\n",
                    "bluetoothctl": "Powered: yes",
                    "aplay": "card 0",
                    "timedatectl": "yes",
                }
            },
            "System/Gruppen des Benutzers",
            Status.WARN,
            "usermod",
        ),
        ({"can_write": False}, "Speicher/Datenordner", Status.FAIL, "Rechte"),
        ({"free": 50 * 1024**2}, "Speicher/Datenordner", Status.WARN, "Speicherplatz"),
        ({"port_ok": False}, "Netzwerk/Port 8080", Status.WARN, "OCTAPIHUB_PORT"),
        ({"files": {"/dev/i2c-1", "/srv/music"}}, "Bluetooth/Adapter hci0", Status.FAIL, "rfkill"),
        (
            {
                "commands": {
                    "id": "x",
                    "bluetoothctl": "Powered: no",
                    "aplay": "card 0",
                    "timedatectl": "yes",
                }
            },
            "Bluetooth/Adapter hci0",
            Status.FAIL,
            "power on",
        ),
        (
            {
                "commands": {
                    "id": "x",
                    "bluetoothctl": None,
                    "aplay": "card 0",
                    "timedatectl": "yes",
                }
            },
            "Bluetooth/Adapter hci0",
            Status.WARN,
            "apt install bluez",
        ),
        (
            {"files": {"/sys/class/bluetooth/hci0", "/srv/music"}},
            "Sense HAT/I2C",
            Status.FAIL,
            "do_i2c",
        ),
        ({"modules": {"pychromecast"}}, "Sense HAT/Bibliothek sense_hat", Status.FAIL, "sense-hat"),
        ({"texts": {}}, "Sense HAT/Joystick", Status.WARN, "Aufsatz"),
        ({"programs": {"yt-dlp": "x"}}, "Musik/mpv", Status.FAIL, "apt install mpv"),
        ({"programs": {"mpv": "x"}}, "Musik/yt-dlp (YouTube)", Status.WARN, "yt-dlp"),
        (
            {
                "commands": {
                    "id": "x",
                    "bluetoothctl": "Powered: yes",
                    "aplay": "no sound",
                    "timedatectl": "yes",
                }
            },
            "Musik/Audiogeräte",
            Status.WARN,
            "Soundkarte",
        ),
        (
            {"modules": {"sense_hat"}},
            "Musik/pychromecast (Google-Geräte)",
            Status.FAIL,
            "octapihub[music]",
        ),
        (
            {"files": {"/dev/i2c-1", "/sys/class/bluetooth/hci0"}},
            "Musik/Musikordner",
            Status.FAIL,
            "anlegen",
        ),
        ({"reachable": False}, "Geräte/Hue Bridge", Status.WARN, "Hue-App"),
        ({"reachable": False}, "Geräte/Broker", Status.WARN, "Port"),
        (
            {
                "commands": {
                    "id": "x",
                    "bluetoothctl": "Powered: yes",
                    "aplay": "card 0",
                    "timedatectl": "no",
                }
            },
            "Zeit/Uhr",
            Status.WARN,
            "set-ntp",
        ),
    ],
)
def test_each_problem_is_found_with_a_next_step(
    tmp_path: Path, override: dict[str, object], key: str, status: Status, hint: str
) -> None:
    results = by_name(run_checks(make_context(tmp_path, FakeProbe(**override))))
    assert results[key].status is status, results[key]
    assert hint in results[key].hint + results[key].detail
    assert exit_code(list(results.values())) == (2 if status is Status.FAIL else 1)


def test_the_music_folder_under_home_is_flagged(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        FakeProbe(files={"/dev/i2c-1", "/sys/class/bluetooth/hci0", "/home/pi/Music"}),
        music={"enabled": True, "library_dir": "/home/pi/Music", "targets": []},
    )
    assert by_name(run_checks(ctx))["Musik/Musikordner"].status is Status.WARN


@pytest.mark.parametrize(
    ("token", "status"),
    [
        (None, Status.WARN),
        ("kurz", Status.FAIL),
        ("HIER-EIN-LANGES-ZUFAELLIGES-TOKEN", Status.FAIL),
        ("ein-langes-token-12345", Status.OK),
    ],
)
def test_the_token_is_judged(tmp_path: Path, token: str | None, status: Status) -> None:
    assert (
        by_name(run_checks(make_context(tmp_path, token=token)))[
            "Einstellungen/Zugriffs-Token"
        ].status
        is status
    )


def test_a_fallback_configuration_is_a_warning(tmp_path: Path) -> None:
    ctx = make_context(tmp_path)
    ctx = dataclasses.replace(ctx, config_note="Sicherung verwendet")
    assert by_name(run_checks(ctx))["Einstellungen/Konfiguration"].status is Status.WARN


def test_unavailable_things_are_skipped_not_failed(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        FakeProbe(posix=False),
        sense_hat={"enabled": False},
        hue={"enabled": False},
        smarthome={},
        music={"enabled": False},
    )
    results = by_name(run_checks(ctx))
    assert (
        results["Bluetooth/Adapter"].status is Status.SKIP
        and results["Sense HAT/Aufsatz"].status is Status.SKIP
    )
    assert (
        results["Musik/Wiedergabe"].status is Status.SKIP
        and results["Geräte/Hue und Smart Home"].status is Status.SKIP
    )
    assert results["Zeit/Uhr"].status is Status.SKIP
    assert exit_code(list(results.values())) == 0


def test_a_check_that_breaks_is_reported_not_raised(tmp_path: Path) -> None:
    def broken(_ctx: Context) -> list[Result]:
        raise OSError("boom")

    results = run_checks(make_context(tmp_path), [broken])
    assert results[0].status is Status.FAIL and "OSError" in results[0].detail


def test_the_report_groups_by_area_and_shows_next_steps(tmp_path: Path) -> None:
    results = run_checks(make_context(tmp_path, FakeProbe(reachable=False, port_ok=False)))
    text = format_report(results)
    assert (
        text.startswith("OctaPiHub Selbsttest")
        and "\nBluetooth\n" in text
        and "[ OK ]" in text
        and "[WARN]" in text
    )
    assert "→ " in text and "Ergebnis:" in text and "Hinweise sollten Sie ansehen" in text
    assert "Alles bereit." in format_report(run_checks(make_context(tmp_path)))
    assert "Fehler beheben" in format_report([Result("X", "y", Status.FAIL, "d", "h")])


def test_every_check_is_registered() -> None:
    assert len(CHECKS) == 9 and len({c.__name__ for c in CHECKS}) == 9


def test_the_real_probe_works_on_this_machine(tmp_path: Path) -> None:
    probe = Probe()
    assert (
        probe.python_version() >= (3, 12)
        and probe.exists(str(tmp_path))
        and probe.writable(str(tmp_path))
    )
    assert probe.free_bytes(str(tmp_path)) and probe.which("definitely-not-installed-xyz") is None
    assert (
        probe.run(["definitely-not-installed-xyz"]) is None
        and probe.read_text(str(tmp_path / "nope")) is None
    )
    assert probe.has_module("json") and not probe.has_module("no_such_module_xyz")
    assert probe.tcp("127.0.0.1", 1, timeout=0.5) is False
    assert isinstance(probe.is_posix(), bool)


def test_the_command_runs_and_returns_the_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "octapihub.toml"
    save_config(path, make_context(tmp_path).config)
    args = build_parser({}).parse_args(
        [
            "--selftest",
            "--config",
            str(path),
            "--secrets",
            str(tmp_path / "s.env"),
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert isinstance(args, argparse.Namespace) and args.selftest is True
    ready = FakeProbe()
    ready.files.add(str(tmp_path))
    code = run_selftest(args, ready)
    out = capsys.readouterr().out
    assert code in (0, 1) and "OctaPiHub Selbsttest" in out
    path.write_text("kaputt = [", encoding="utf-8")
    assert run_selftest(args, FakeProbe()) == 2
    assert "CH-CFG" in capsys.readouterr().err
