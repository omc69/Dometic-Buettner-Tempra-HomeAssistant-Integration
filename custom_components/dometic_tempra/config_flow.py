"""Config flow for the Dometic Büttner Tempra integration.

A battery can be added in three ways, because relying on discovery alone is
not enough in practice: a battery that is currently held by the Dometic app,
out of range, or simply not advertising at that moment would otherwise be
unaddable. So the flow always offers manual entry of the Bluetooth address,
and an existing entry can be re-pointed at a different address (e.g. after a
battery is swapped) without losing its entities.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback

from .const import CONF_AUTH_TOKEN, DOMAIN
from .coordinator import TempraConfigEntry
from .tempra_ble import DEFAULT_AUTH_TOKEN, LOCAL_NAME_PATTERN, normalize_address


def _is_tempra(name: str | None) -> bool:
    """Whether an advertised local name belongs to a Tempra battery."""
    return bool(name) and fnmatch(name, LOCAL_NAME_PATTERN)


class TempraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery, manual setup, and re-addressing of a battery."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._discovered: dict[str, str] = {}

    # -- discovery ----------------------------------------------------------

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

    # -- manual setup -------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the batteries in range, or go straight to manual entry."""
        self._discovered = self._async_unconfigured_in_range()
        if not self._discovered:
            return await self.async_step_manual()
        return self.async_show_menu(step_id="user", menu_options=["pick", "manual"])

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one of the batteries currently advertising."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered.get(address, address),
                data={CONF_ADDRESS: address},
            )

        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {vol.Required(CONF_ADDRESS): vol.In(self._discovered)}
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a battery by typing its Bluetooth address."""
        errors: dict[str, str] = {}

        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            if address is None:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                name = (user_input.get(CONF_NAME) or "").strip()
                return self.async_create_entry(
                    title=name or self._discovered.get(address) or address,
                    data={CONF_ADDRESS: address},
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS, default=(user_input or {}).get(CONF_ADDRESS, "")
                    ): str,
                    vol.Optional(
                        CONF_NAME, default=(user_input or {}).get(CONF_NAME, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    # -- re-addressing ------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at a different Bluetooth address.

        Keeps the entry -- and therefore every entity id, history, and
        dashboard reference -- when a battery is replaced or its address was
        entered wrongly.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            address = normalize_address(user_input[CONF_ADDRESS])
            if address is None:
                errors[CONF_ADDRESS] = "invalid_address"
            elif any(
                other.entry_id != entry.entry_id
                and other.data.get(CONF_ADDRESS) == address
                for other in self._async_current_entries()
            ):
                errors[CONF_ADDRESS] = "already_configured"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=address,
                    data_updates={CONF_ADDRESS: address},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ADDRESS, default=entry.data.get(CONF_ADDRESS, "")
                    ): str
                }
            ),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    # -- helpers ------------------------------------------------------------

    @callback
    def _async_unconfigured_in_range(self) -> dict[str, str]:
        """Tempra batteries advertising right now that are not set up yet."""
        configured = self._async_current_ids()
        return {
            info.address: f"{info.name} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if _is_tempra(info.name) and info.address not in configured
        }

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
