"""The Dometic Büttner Tempra integration."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_AUTH_TOKEN, PLATFORMS
from .coordinator import TempraConfigEntry, TempraCoordinator
from .tempra_ble import DEFAULT_AUTH_TOKEN
from .tempra_ble.device import TempraBleDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: TempraConfigEntry) -> bool:
    """Set up a Tempra battery from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Battery {address} not found -- it may be out of range, or the "
            "Dometic app is holding its single BLE connection slot"
        )

    device = TempraBleDevice(
        ble_device,
        auth_token=entry.options.get(CONF_AUTH_TOKEN, DEFAULT_AUTH_TOKEN),
        name=entry.title,
    )
    coordinator = TempraCoordinator(hass, entry, device)
    await coordinator.async_start()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TempraConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: TempraConfigEntry) -> None:
    """Reload when the options change (currently only the auth token)."""
    await hass.config_entries.async_reload(entry.entry_id)
