"""Chooses the right player for each configured target."""

from __future__ import annotations

from octapihub.application.music.models import MusicPlayer
from octapihub.core.config import MusicTargetConfig, MusicTargetKind
from octapihub.infrastructure.music.cast_player import CastBackend, CastPlayer, PyChromecastBackend
from octapihub.infrastructure.music.media import MediaUrls
from octapihub.infrastructure.music.mpv_player import (
    BluetoothctlConnector,
    LocalMpvBackend,
    MpvBackend,
    MpvPlayer,
)


class DefaultPlayerFactory:
    """Cast targets use Google Cast; local and Bluetooth targets use mpv."""

    def __init__(
        self,
        media: MediaUrls,
        *,
        cast_backend: CastBackend | None = None,
        mpv_backend: MpvBackend | None = None,
    ) -> None:
        self._media = media
        self._cast_backend = cast_backend or PyChromecastBackend()
        self._mpv_backend = mpv_backend or LocalMpvBackend()

    def create(self, target: MusicTargetConfig) -> MusicPlayer:
        if target.kind is MusicTargetKind.CAST:
            return CastPlayer(target, self._cast_backend, self._media.url_for)
        connector = BluetoothctlConnector() if target.kind is MusicTargetKind.BLUETOOTH else None
        return MpvPlayer(target, self._mpv_backend, connector)
