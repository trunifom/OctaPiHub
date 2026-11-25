"""Application ports for BLE device transports (notifications and reads only)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

NotificationCallback = Callable[[bytes], None]
DisconnectCallback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class CharacteristicInfo:
    """The small subset of a discovered GATT characteristic used by the hub."""

    uuid: str
    properties: frozenset[str]


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Discovered GATT characteristics, without exposing a backend client object."""

    characteristics: tuple[CharacteristicInfo, ...]


class DeviceTransport(Protocol):
    """One connection to one cube, owned by a single asyncio event loop.

    The contract intentionally exposes no GATT write method (reads are allowed).
    Adapter-specific client objects must not escape this boundary.
    """

    @property
    def is_connected(self) -> bool:
        """Return the transport's current connection state."""

    def set_disconnected_callback(self, callback: DisconnectCallback) -> None:
        """Set the callback notified when the connection is lost."""

    async def connect(self) -> None:
        """Connect to the configured device."""

    async def discover(self) -> DeviceProfile:
        """Discover the device's GATT characteristics."""

    async def start_notify(self, characteristic_uuid: str, callback: NotificationCallback) -> None:
        """Subscribe to notifications or indications for one characteristic."""

    async def read_characteristic(self, characteristic_uuid: str) -> bytes:
        """Read one characteristic that discovery confirmed as readable."""

    async def disconnect(self) -> None:
        """Release the connection and all associated subscriptions."""


class TransportFactory(Protocol):
    """Create a fresh transport for one configured device connection cycle."""

    def create(self, address: str, adapter: str) -> DeviceTransport:
        """Return a new transport without connecting it."""
