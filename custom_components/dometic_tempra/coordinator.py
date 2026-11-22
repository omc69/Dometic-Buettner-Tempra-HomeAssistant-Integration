"""Push coordinator bridging the BLE stream into Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .tempra_ble.device import TempraBleDevice

_LOGGER = logging.getLogger(__name__)

type TempraConfigEntry = ConfigEntry[TempraCoordinator]


class TempraCoordinator(DataUpdateCoordinator[None]):
    """Owns one BLE connection to the battery bus and pushes updates.

    A single connection relays every battery on the N-BUS, so this one
    coordinator drives the entities of every battery node plus the bank. There
    is no polling interval: after the handshake the bus streams continuously
    and updates are pushed in from the notify callback.
    """

    config_entry: TempraConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: TempraConfigEntry,
        device: TempraBleDevice,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.name}",
            update_interval=None,
        )
        self.device = device
        self._unsubscribers: list[CALLBACK_TYPE] = []

    @property
    def available(self) -> bool:
        """Whether any battery on the bus has a recent reading."""
        return self.device.available

    async def async_start(self) -> None:
        """Begin connecting and watch for advertisements."""
        self._unsubscribers.append(self.device.add_listener(self._handle_state))
        self._unsubscribers.append(
            bluetooth.async_register_callback(
                self.hass,
                self._handle_advertisement,
                {"address": self.device.address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        await self.device.async_start()

    async def async_stop(self) -> None:
        """Tear everything down."""
        while self._unsubscribers:
            self._unsubscribers.pop()()
        await self.device.async_stop()

    @callback
    def _handle_state(self) -> None:
        """Publish an update pushed from the BLE layer."""
        self.async_set_updated_data(None)

    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Adopt the freshest BLEDevice, and poll sooner if we have no data."""
        self.device.set_ble_device(service_info.device)
        if not self.device.available:
            self.device.request_poll()
