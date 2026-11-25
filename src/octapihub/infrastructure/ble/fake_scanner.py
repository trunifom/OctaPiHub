"""A scanner that returns a fixed list (tests and demo)."""

from __future__ import annotations

from octapihub.application.ble_scan import FoundDevice
from octapihub.core.errors import BleConnectionError


class FakeScanner:
    """Returns ``devices``, or raises ``error`` (to test the failure path)."""

    def __init__(
        self, devices: list[FoundDevice] | None = None, error: Exception | None = None
    ) -> None:
        self.devices = devices or []
        self.error = error
        self.calls: list[tuple[str, float]] = []

    async def scan(self, adapter: str, seconds: float) -> list[FoundDevice]:
        self.calls.append((adapter, seconds))
        if self.error is not None:
            raise self.error
        return list(self.devices)


__all__ = ["BleConnectionError", "FakeScanner"]
