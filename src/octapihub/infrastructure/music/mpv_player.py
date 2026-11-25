"""Music on this device's audio output or a Bluetooth speaker, played by ``mpv``.

One long-lived ``mpv`` process per target keeps its queue and answers JSON commands on a
local socket. Local audio and Bluetooth speakers differ only in the audio device name (and a
Bluetooth connect step), so one player serves both. Linux only; install with ``apt install mpv``.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import random
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from octapihub.application.music.models import PlayerState, PlayerStatus, PlayRequest
from octapihub.application.music.queue import play_order
from octapihub.core.config import MusicTargetConfig, RepeatMode
from octapihub.core.errors import MusicNotConfiguredError, MusicTargetError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 5.0
START_TIMEOUT_SECONDS = 10.0
BLUETOOTH_TIMEOUT_SECONDS = 20.0
_DEVICE_PATTERN = re.compile(r"^[A-Za-z0-9_./:@=,+\-]{1,200}$")
_MPV_MAX_VOLUME = 100
_BT_MESSAGE = (
    "Die Bluetooth-Box lässt sich nicht verbinden. Ist sie eingeschaltet und in Reichweite?"
)


class MpvChannel(Protocol):
    """A connection to one running mpv (JSON commands)."""

    @property
    def alive(self) -> bool:
        """Whether the connection and the process are still usable."""

    async def command(self, *arguments: object) -> object:
        """Run one mpv command and return its ``data``.

        Raises:
            MusicTargetError: mpv reported an error or the connection failed.
        """

    async def close(self) -> None:
        """Stop mpv and release the connection."""


class MpvBackend(Protocol):
    """Starts mpv for a target and returns a channel to it."""

    async def open(self, target: MusicTargetConfig, volume: int) -> MpvChannel:
        """Start (or connect to) mpv."""


class BluetoothConnector(Protocol):
    """Connects a Bluetooth speaker (best effort, before audio is routed to it)."""

    async def connect(self, address: str) -> None:
        """Connect or raise :class:`MusicTargetError`."""


def validate_device(device: str) -> str:
    """Accept only audio device names that cannot be mistaken for options or shell syntax."""
    if device and not _DEVICE_PATTERN.fullmatch(device):
        raise MusicNotConfiguredError(
            "Invalid audio device name",
            user_message="Der Name des Audiogeräts enthält ungültige Zeichen.",
        )
    return device


def build_mpv_command(
    target: MusicTargetConfig, socket_path: str, volume: int, executable: str = "mpv"
) -> list[str]:
    """The mpv command line for one target (an argument list, never a shell string)."""
    device = validate_device(target.device)
    command = [
        executable,
        "--no-video",
        "--no-terminal",
        "--idle=yes",
        "--force-window=no",
        "--really-quiet",
        "--ytdl-format=bestaudio/best",
        f"--input-ipc-server={socket_path}",
        f"--volume={max(0, min(_MPV_MAX_VOLUME, volume))}",
    ]
    if device:
        command.append(f"--audio-device={device}")
    return command


class MpvIpc:
    """JSON line protocol over a stream pair (mpv's IPC); testable without a process."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter | Any,
        *,
        alive: Callable[[], bool] = lambda: True,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._alive_check = alive
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._closed = False
        self._task: asyncio.Task[None] | None = asyncio.get_running_loop().create_task(self._read())

    @property
    def alive(self) -> bool:
        return not self._closed and self._alive_check()

    async def _read(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                request_id = message.get("request_id") if isinstance(message, dict) else None
                future = (
                    self._pending.pop(request_id, None) if isinstance(request_id, int) else None
                )
                if future is not None and not future.done():
                    future.set_result(message)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            self._closed = True
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MusicTargetError("The audio player stopped"))
            self._pending.clear()

    async def command(self, *arguments: object) -> object:
        if not self.alive:
            raise MusicTargetError("The audio player is not running")
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = json.dumps({"command": list(arguments), "request_id": request_id})
        try:
            self._writer.write(payload.encode("utf-8") + b"\n")
            drain = getattr(self._writer, "drain", None)
            if drain is not None:
                await drain()
            reply = await asyncio.wait_for(future, timeout=REQUEST_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MusicTargetError("The audio player did not answer") from exc
        except OSError as exc:
            self._pending.pop(request_id, None)
            raise MusicTargetError("The audio player connection failed") from exc
        error = reply.get("error", "success")
        if error != "success":
            raise MusicTargetError(
                f"mpv rejected the command: {error}", details={"mpv_error": str(error)}
            )
        return reply.get("data")

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        with contextlib.suppress(Exception):
            self._writer.close()


class LocalMpvBackend:
    """Starts a real mpv process and connects to its Unix socket (Linux only)."""

    def __init__(self, executable: str = "mpv") -> None:
        self._executable = executable

    async def open(self, target: MusicTargetConfig, volume: int) -> MpvChannel:
        if os.name != "posix":
            raise MusicTargetError(
                "mpv playback is only supported on Linux",
                user_message="Die lokale Wiedergabe funktioniert nur auf dem Raspberry Pi (Linux).",
            )
        executable = shutil.which(self._executable)
        if executable is None:
            raise MusicTargetError(
                "mpv is not installed",
                user_message="Der Audioplayer „mpv“ ist nicht installiert (sudo apt install mpv).",
            )
        directory = tempfile.mkdtemp(prefix="octapihub-mpv-")
        socket_path = str(Path(directory) / "ipc.sock")
        command = build_mpv_command(target, socket_path, volume, executable)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            shutil.rmtree(directory, ignore_errors=True)
            raise MusicTargetError("Could not start mpv") from exc
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_TIMEOUT_SECONDS
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(socket_path)  # type: ignore[attr-defined]
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if process.returncode is not None or loop.time() > deadline:
                    process.kill()
                    shutil.rmtree(directory, ignore_errors=True)
                    raise MusicTargetError("mpv did not start") from None
                await asyncio.sleep(0.1)
        return _ProcessChannel(
            MpvIpc(reader, writer, alive=lambda: process.returncode is None), process, directory
        )


class _ProcessChannel:
    """An :class:`MpvIpc` that also owns the mpv process and its socket directory."""

    def __init__(self, ipc: MpvIpc, process: asyncio.subprocess.Process, directory: str) -> None:
        self._ipc = ipc
        self._process = process
        self._directory = directory

    @property
    def alive(self) -> bool:
        return self._ipc.alive

    async def command(self, *arguments: object) -> object:
        return await self._ipc.command(*arguments)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._ipc.command("quit"), timeout=2)
        await self._ipc.close()
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self._process.kill()
        shutil.rmtree(self._directory, ignore_errors=True)


class BluetoothctlConnector:
    """Connects a speaker with ``bluetoothctl connect`` (BlueZ)."""

    async def connect(self, address: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "connect",
                address,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await asyncio.wait_for(process.communicate(), BLUETOOTH_TIMEOUT_SECONDS)
        except (OSError, TimeoutError) as exc:
            raise MusicTargetError(
                "Could not connect the Bluetooth speaker",
                user_message=_BT_MESSAGE,
            ) from exc
        text = output.decode("utf-8", errors="replace")
        if "Connection successful" not in text and "already connected" not in text.lower():
            raise MusicTargetError(
                "Bluetooth speaker refused the connection",
                user_message=_BT_MESSAGE,
            )


class MpvPlayer:
    """The music player for local and Bluetooth targets."""

    def __init__(
        self,
        target: MusicTargetConfig,
        backend: MpvBackend,
        bluetooth: BluetoothConnector | None = None,
        *,
        seed: Callable[[], int] = lambda: random.SystemRandom().randrange(1 << 30),
    ) -> None:
        self._target = target
        self._backend = backend
        self._bluetooth = bluetooth
        self._seed = seed
        self._channel: MpvChannel | None = None
        self._volume = target.volume

    async def _ready(self) -> MpvChannel:
        channel = self._channel
        if channel is not None and channel.alive:
            return channel
        if channel is not None:
            await channel.close()
            self._channel = None
        if self._bluetooth is not None and self._target.bluetooth_address:
            await self._bluetooth.connect(self._target.bluetooth_address)
        self._channel = await self._backend.open(self._target, self._volume)
        return self._channel

    async def play(self, request: PlayRequest) -> None:
        if not request.tracks:
            raise MusicTargetError("Nothing to play")
        channel = await self._ready()
        if request.volume is not None:
            self._volume = request.volume
            await channel.command("set_property", "volume", self._volume)
        order = play_order(len(request.tracks), shuffle=request.shuffle, seed=self._seed())
        await channel.command(
            "set_property", "loop-playlist", "inf" if request.repeat is RepeatMode.ALL else "no"
        )
        await channel.command(
            "set_property", "loop-file", "inf" if request.repeat is RepeatMode.ONE else "no"
        )
        await channel.command("set_property", "pause", False)
        for count, index in enumerate(order):
            await channel.command(
                "loadfile", request.tracks[index].location, "replace" if count == 0 else "append"
            )

    async def pause(self) -> None:
        await (await self._ready()).command("set_property", "pause", True)

    async def resume(self) -> None:
        await (await self._ready()).command("set_property", "pause", False)

    async def stop(self) -> None:
        channel = self._channel
        if channel is not None and channel.alive:
            await channel.command("stop")

    async def next(self) -> None:
        await (await self._ready()).command("playlist-next", "force")

    async def previous(self) -> None:
        await (await self._ready()).command("playlist-prev", "force")

    async def set_volume(self, percent: int) -> None:
        self._volume = max(0, min(100, percent))
        channel = await self._ready()
        await channel.command("set_property", "volume", self._volume)

    async def status(self) -> PlayerStatus:
        channel = self._channel
        if channel is None or not channel.alive:
            return PlayerStatus(PlayerState.IDLE, "", self._volume)
        idle = await self._property(channel, "idle-active", True)
        if idle:
            return PlayerStatus(PlayerState.IDLE, "", self._volume)
        paused = await self._property(channel, "pause", False)
        cache = await self._property(channel, "paused-for-cache", False)
        title = await self._property(channel, "media-title", "")
        volume = await self._property(channel, "volume", self._volume)
        state = (
            PlayerState.PAUSED
            if paused
            else PlayerState.BUFFERING
            if cache
            else PlayerState.PLAYING
        )
        return PlayerStatus(
            state,
            str(title or ""),
            int(volume) if isinstance(volume, (int, float)) else self._volume,
        )

    async def _property(self, channel: MpvChannel, name: str, default: Any) -> Any:
        try:
            value = await channel.command("get_property", name)
        except MusicTargetError:
            return default  # e.g. "property unavailable" while nothing is loaded
        return default if value is None else value

    async def close(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            await channel.close()


_AUDIO_DEVICE_LINE = re.compile(r"^\s*'([^']+)'\s*\((.*)\)\s*$")


def parse_audio_devices(output: str) -> list[tuple[str, str]]:
    """Parse ``mpv --audio-device=help`` into ``(name, description)`` pairs."""
    devices: list[tuple[str, str]] = []
    for line in output.splitlines():
        match = _AUDIO_DEVICE_LINE.match(line)
        if match and _DEVICE_PATTERN.fullmatch(match.group(1)):
            devices.append((match.group(1), match.group(2)))
    return devices


async def list_audio_devices(executable: str = "mpv") -> list[tuple[str, str]]:
    """Audio outputs mpv knows (empty if mpv is missing); used to pick a device in the web UI."""
    path = shutil.which(executable)
    if path is None:
        return []
    try:
        process = await asyncio.create_subprocess_exec(
            path,
            "--audio-device=help",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(process.communicate(), 10)
    except (OSError, TimeoutError):
        return []
    return parse_audio_devices(output.decode("utf-8", errors="replace"))
