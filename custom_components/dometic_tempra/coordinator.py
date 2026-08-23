"""Push coordinator bridging the BLE stream into Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .tempra_ble import TempraState
from .tempra_ble.device import TempraBleDevice

_LOGGER = logging.getLogger(__name__)

type TempraConfigEntry = ConfigEntry[TempraCoordinator]


class TempraCoordinator(DataUpdateCoordinator[TempraState]):
    """Owns one battery connection and pushes its state to entities.

    There is no polling: after the handshake the battery streams continuously,
    so ``update_interval`` stays ``None`` and updates are pushed in from the
    notify callback.
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
        self.data = device.state
        self._unsubscribers: list[CALLBACK_TYPE] = []

    @property
    def available(self) -> bool:
        """Whether the most recent reading is recent enough to trust."""
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
    def _handle_state(self, state: TempraState) -> None:
        """Publish a new state pushed from the BLE layer."""
        self.async_set_updated_data(state)

    @callback
    def _handle_advertisement(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Adopt the freshest BLEDevice, and poll sooner if we have no data.

        The battery advertises continuously, so an advertisement is the
        earliest reliable sign that a poll can succeed -- better than waiting
        out the backoff after e.g. the Dometic app has released the battery's
        single connection slot.
        """
        self.device.set_ble_device(service_info.device)
        if not self.device.available:
            self.device.request_poll()
