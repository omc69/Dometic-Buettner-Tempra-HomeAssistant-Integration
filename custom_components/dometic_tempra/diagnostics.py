"""Diagnostics for the Dometic Büttner Tempra integration.

The dump deliberately includes the raw payloads of every command that is not
decoded yet (section 4.2 of the protocol document): a live dump taken while
toggling shore power or changing load is exactly what is needed to finish
mapping 0x34/0x35/0x36 and the 0x60 status bitfield.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import TempraConfigEntry
from .tempra_ble import UNDECODED_COMMANDS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TempraConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    state = coordinator.data

    return {
        "device": {
            "address": state.address,
            "name": state.name,
            "serial": state.serial,
            "available": coordinator.device.available,
        },
        "measurements": state.as_dict(),
        "undecoded_frames": {
            f"0x{cmd:02X}": {
                "payload": payload,
                "note": UNDECODED_COMMANDS.get(cmd, "unknown command"),
            }
            for cmd, payload in sorted(state.undecoded.items())
        },
    }
