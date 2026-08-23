"""Config flow for the Dometic Büttner Tempra integration."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import CONF_AUTH_TOKEN, DOMAIN
from .coordinator import TempraConfigEntry
from .tempra_ble import DEFAULT_AUTH_TOKEN, LOCAL_NAME_PATTERN


def _is_tempra(name: str | None) -> bool:
    """Whether an advertised local name belongs to a Tempra battery."""
    return bool(name) and fnmatch(name, LOCAL_NAME_PATTERN)


class TempraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery and manual setup of a Tempra battery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a battery discovered by the Bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding a discovered battery."""
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address},
            )

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovery.name},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a battery from the ones currently in range."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered.get(address, address),
                data={CONF_ADDRESS: address},
            )

        configured = self._async_current_ids()
        self._discovered = {
            info.address: info.name
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_tempra(info.name) and info.address not in configured
        }
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TempraConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TempraOptionsFlow()


class TempraOptionsFlow(OptionsFlow):
    """Expose the handshake token, in case a capture ever shows a new one."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_AUTH_TOKEN, DEFAULT_AUTH_TOKEN)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_AUTH_TOKEN, default=current): str}
            ),
        )
