"""Deterministic in-memory BLE transport for hardware-independent tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from octapihub.application.ble import (
    DeviceProfile,
    DeviceTransport,
    DisconnectCallback,
    NotificationCallback,
)
from octapihub.core.errors import BleConnectionError, UnsupportedDeviceError
from octapihub.infrastructure.ble.protocol import normalize_uuid

Failure = Callable[[], Exception]


class FakeDeviceTransport:
    """A controllable transport fake that never accesses Bluetooth hardware."""

    def __init__(
        self,
        profile: DeviceProfile,
        *,
        failures: dict[str, Failure] | None = None,
    ) -> None:
        self._profile = profile
        self._failures = failures or {}
        self._connected = False
        self._disconnect_callback: DisconnectCallback | None = None
        self._subscriptions: dict[str, NotificationCallback] = {}
        self.operations: list[str] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_disconnected_callback(self, callback: DisconnectCallback) -> None:
        self._disconnect_callback = callback

    async def connect(self) -> None:
        self.operations.append("connect")
        self._raise_failure("connect")
        if self._connected:
            raise BleConnectionError("Fake transport is already connected")
        await asyncio.sleep(0)
        self._connected = True

    async def discover(self) -> DeviceProfile:
        self.operations.append("discover")
        self._require_connected()
        self._raise_failure("discover")
        await asyncio.sleep(0)
        return self._profile

    async def start_notify(self, characteristic_uuid: str, callback: NotificationCallback) -> None:
        self.operations.append("start_notify")
        self._require_connected()
        self._raise_failure("start_notify")
        requested_uuid = normalize_uuid(characteristic_uuid)
        characteristic = next(
            (
                item
                for item in self._profile.characteristics
                if normalize_uuid(item.uuid) == requested_uuid
            ),
            None,
        )
        if characteristic is None or not ({"notify", "indicate"} & characteristic.properties):
            raise UnsupportedDeviceError(
                "The requested characteristic does not support notifications",
                details={"characteristic_uuid": requested_uuid},
            )
        self._subscriptions[requested_uuid] = callback
        await asyncio.sleep(0)

    async def disconnect(self) -> None:
        self.operations.append("disconnect")
        self._raise_failure("disconnect")
        await asyncio.sleep(0)
        self._connected = False
        self._subscriptions.clear()

    def emit_notification(self, characteristic_uuid: str, payload: bytes) -> None:
        """Deliver a test notification to an active subscription."""
        requested_uuid = normalize_uuid(characteristic_uuid)
        callback = self._subscriptions.get(requested_uuid)
        if callback is None:
            raise BleConnectionError(
                "No fake subscription exists for the requested characteristic",
                details={"characteristic_uuid": requested_uuid},
            )
        callback(bytes(payload))

    def lose_connection(self) -> None:
        """Simulate a remote disconnect and notify the owner once."""
        if not self._connected:
            return
        self._connected = False
        self._subscriptions.clear()
        callback = self._disconnect_callback
        if callback is not None:
            callback()

    def _require_connected(self) -> None:
        if not self._connected:
            raise BleConnectionError("Fake transport is not connected")

    def _raise_failure(self, operation: str) -> None:
        failure = self._failures.get(operation)
        if failure is not None:
            raise failure()


class FakeTransportFactory:
    """Produce deterministic fake transports and retain them for test assertions."""

    def __init__(
        self,
        profile: DeviceProfile,
        *,
        failures: dict[str, Failure] | None = None,
    ) -> None:
        self._profile = profile
        self._failures = failures
        self.created: list[tuple[str, str, FakeDeviceTransport]] = []

    def create(self, address: str, adapter: str) -> DeviceTransport:
        transport = FakeDeviceTransport(self._profile, failures=self._failures)
        self.created.append((address, adapter, transport))
        return cast(DeviceTransport, transport)
