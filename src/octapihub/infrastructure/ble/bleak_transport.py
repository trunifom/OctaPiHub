"""Production BLE transport implemented with Bleak's BlueZ backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from typing import Protocol, cast

from bleak import BleakClient
from bleak.exc import BleakError

from octapihub.application.ble import (
    CharacteristicInfo,
    DeviceProfile,
    DeviceTransport,
    DisconnectCallback,
    NotificationCallback,
)
from octapihub.core.errors import BleConnectionError, UnsupportedDeviceError
from octapihub.infrastructure.ble.protocol import normalize_uuid

logger = logging.getLogger(__name__)


class GattCharacteristic(Protocol):
    """Public Bleak GATT characteristic data consumed by the adapter."""

    uuid: str
    properties: Iterable[str]


class GattService(Protocol):
    """Public Bleak GATT service data consumed by the adapter."""

    characteristics: Iterable[GattCharacteristic]


class BleakClientLike(Protocol):
    """Narrow Bleak client surface for the adapter and deterministic unit tests."""

    @property
    def is_connected(self) -> bool:
        """Return the client connection state."""

    @property
    def services(self) -> Iterable[GattService]:
        """Return discovered GATT services."""

    async def connect(self) -> None:
        """Connect to the peripheral."""

    async def disconnect(self) -> None:
        """Disconnect from the peripheral."""

    async def start_notify(
        self,
        characteristic: str,
        callback: Callable[[GattCharacteristic, bytearray], None],
    ) -> None:
        """Subscribe to a GATT characteristic."""

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Read one GATT characteristic."""


ClientFactory = Callable[[str, Callable[[object], None], str], BleakClientLike]


def _create_client(
    address: str, disconnected: Callable[[object], None], adapter: str
) -> BleakClientLike:
    """Create Bleak's Linux client with explicit BlueZ adapter selection."""
    client = BleakClient(
        address,
        disconnected_callback=disconnected,
        bluez={"adapter": adapter},
    )
    return cast(BleakClientLike, client)


class BleakDeviceTransport(DeviceTransport):
    """One read-only GATT connection managed by a single asyncio loop."""

    def __init__(
        self,
        address: str,
        adapter: str,
        *,
        timeout_seconds: float = 20.0,
        client_factory: ClientFactory = _create_client,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._address = address
        self._adapter = adapter
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory
        self._client: BleakClientLike | None = None
        self._disconnect_callback: DisconnectCallback | None = None
        self._closing = False
        self._profile: DeviceProfile | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether the current Bleak client is connected."""
        return self._client is not None and self._client.is_connected

    def set_disconnected_callback(self, callback: DisconnectCallback) -> None:
        """Set the callback for an unexpected disconnection."""
        self._disconnect_callback = callback

    async def connect(self) -> None:
        """Connect using the configured BlueZ adapter."""
        if self._client is not None:
            raise BleConnectionError("BLE transport already has a client")
        client = self._client_factory(
            self._address,
            self._on_client_disconnected,
            self._adapter,
        )
        self._client = client
        try:
            await asyncio.wait_for(client.connect(), timeout=self._timeout_seconds)
        except (BleakError, OSError, TimeoutError) as exc:
            self._closing = True
            try:
                if client.is_connected:
                    await asyncio.wait_for(
                        client.disconnect(),
                        timeout=self._timeout_seconds,
                    )
            except (BleakError, OSError, TimeoutError) as cleanup_error:
                logger.error(
                    "BLE cleanup after a failed connection also failed (%s)",
                    type(cleanup_error).__name__,
                )
            finally:
                self._client = None
                self._profile = None
                self._closing = False
            raise BleConnectionError(
                "Could not connect to the configured Bluetooth device"
            ) from exc

    async def discover(self) -> DeviceProfile:
        """Snapshot discovered characteristics; do not read or write any GATT values."""
        client = self._require_connected()
        try:
            profile = DeviceProfile(
                characteristics=tuple(
                    CharacteristicInfo(
                        uuid=normalize_uuid(characteristic.uuid),
                        properties=frozenset(characteristic.properties),
                    )
                    for service in client.services
                    for characteristic in service.characteristics
                )
            )
        except (BleakError, OSError, ValueError) as exc:
            raise BleConnectionError("Could not discover Bluetooth device capabilities") from exc
        self._profile = profile
        return profile

    async def start_notify(self, characteristic_uuid: str, callback: NotificationCallback) -> None:
        """Subscribe only when discovery confirmed notify or indicate capability."""
        client = self._require_connected()
        requested_uuid = normalize_uuid(characteristic_uuid)
        supported = self._profile is not None and any(
            characteristic.uuid == requested_uuid
            and bool({"notify", "indicate"} & characteristic.properties)
            for characteristic in self._profile.characteristics
        )
        if not supported:
            raise UnsupportedDeviceError(
                "The requested characteristic does not support notifications",
                details={"characteristic_uuid": requested_uuid},
            )

        def on_notification(_sender: GattCharacteristic, payload: bytearray) -> None:
            callback(bytes(payload))

        try:
            await asyncio.wait_for(
                client.start_notify(requested_uuid, on_notification),
                timeout=self._timeout_seconds,
            )
        except (BleakError, OSError, TimeoutError) as exc:
            raise BleConnectionError("Could not subscribe to the Bluetooth characteristic") from exc

    async def read_characteristic(self, characteristic_uuid: str) -> bytes:
        """Read a characteristic only when discovery confirmed that it is readable."""
        client = self._require_connected()
        requested_uuid = normalize_uuid(characteristic_uuid)
        readable = self._profile is not None and any(
            characteristic.uuid == requested_uuid and "read" in characteristic.properties
            for characteristic in self._profile.characteristics
        )
        if not readable:
            raise UnsupportedDeviceError(
                "The requested characteristic cannot be read",
                details={"characteristic_uuid": requested_uuid},
            )
        try:
            payload = await asyncio.wait_for(
                client.read_gatt_char(requested_uuid), timeout=self._timeout_seconds
            )
        except (BleakError, OSError, TimeoutError) as exc:
            raise BleConnectionError("Could not read the Bluetooth characteristic") from exc
        return bytes(payload)

    async def disconnect(self) -> None:
        """Disconnect and release the cached GATT profile."""
        client = self._client
        if client is None:
            return
        self._closing = True
        try:
            if client.is_connected:
                await asyncio.wait_for(client.disconnect(), timeout=self._timeout_seconds)
        except (BleakError, OSError, TimeoutError) as exc:
            raise BleConnectionError("Could not disconnect from the Bluetooth device") from exc
        finally:
            self._client = None
            self._profile = None
            self._closing = False

    def _require_connected(self) -> BleakClientLike:
        client = self._client
        if client is None or not client.is_connected:
            raise BleConnectionError("Bluetooth device is not connected")
        return client

    def _on_client_disconnected(self, _client: object) -> None:
        if self._closing:
            return
        callback = self._disconnect_callback
        if callback is not None:
            callback()


class BleakTransportFactory:
    """Create fresh Bleak transports while keeping the platform dependency at the edge."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        client_factory: ClientFactory = _create_client,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory

    def create(self, address: str, adapter: str) -> DeviceTransport:
        """Return a transport; connection is deferred until the owner starts the cycle."""
        return BleakDeviceTransport(
            address,
            adapter,
            timeout_seconds=self._timeout_seconds,
            client_factory=self._client_factory,
        )
