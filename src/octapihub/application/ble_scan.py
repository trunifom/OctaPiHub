"""Finding Bluetooth devices nearby, so a new cube can be added without typing an address."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

_CUBE_HINTS = ("timeular", "zei", "tracker")


@dataclass(frozen=True, slots=True)
class FoundDevice:
    """One Bluetooth device seen during a scan."""

    name: str
    address: str
    rssi: int | None = None

    @property
    def looks_like_cube(self) -> bool:
        """Whether the advertised name suggests a Timeular/ZEI cube (a hint, not a proof)."""
        lowered = self.name.casefold()
        return any(hint in lowered for hint in _CUBE_HINTS)


class DeviceScanner(Protocol):
    """Looks for nearby Bluetooth LE devices."""

    async def scan(self, adapter: str, seconds: float) -> list[FoundDevice]:
        """Scan for ``seconds`` and return each device once (strongest signal first).

        Raises:
            BleError: Bluetooth is off, missing, or not allowed for this user.
        """
