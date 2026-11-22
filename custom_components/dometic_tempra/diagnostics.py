"""Diagnostics for the Dometic Büttner Tempra integration.

The dump includes the raw payloads of every command that is not decoded yet
(section 4.2 of the protocol document), per battery node: a live dump taken
while toggling shore power or changing load is exactly what is needed to finish
mapping 0x34/0x35/0x36 and the 0x60 status bitfield.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .bank import compute_bank
from .coordinator import TempraConfigEntry
from .tempra_ble import UNDECODED_COMMANDS


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TempraConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device = entry.runtime_data.device

    batteries: dict[str, Any] = {}
    for node in device.battery_nodes:
        state = device.state(node)
        if state is None:
            continue
        batteries[f"0x{node:02X}"] = {
            "available": device.node_available(node),
            "serial": state.serial,
            "measurements": state.as_dict(),
            "undecoded_frames": {
                f"0x{cmd:02X}": {
                    "payload": payload,
                    "note": UNDECODED_COMMANDS.get(cmd, "unknown command"),
                }
                for cmd, payload in sorted(state.undecoded.items())
            },
        }

    bank = compute_bank(device)
    return {
        "address": device.address,
        "name": device.name,
        "battery_nodes": [f"0x{node:02X}" for node in device.battery_nodes],
        "aux_stream": device.aux_frames,
        "bank": {
            "online": bank.online,
            "total": bank.total,
            "voltage": bank.voltage,
            "current": bank.current,
            "power": bank.power,
            "soc": bank.soc,
            "capacity_ah": bank.capacity_ah,
            "remaining_ah": bank.remaining_ah,
            "cell_delta_mv": bank.cell_delta_mv,
        },
        "batteries": batteries,
    }
