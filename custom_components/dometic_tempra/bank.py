"""Aggregate view over every battery on the bus.

What a user reads off a dashboard is the bank: how much current is flowing in
or out in total, how much capacity is left, and whether one battery is drifting
from the others. A plain mean of the state of charge is wrong once the
batteries differ in capacity, and the cell spread has to reach across
batteries, so the integration computes these itself from the per-node states.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tempra_ble.device import TempraBleDevice
from .tempra_ble.models import TempraState
from .tempra_ble.parser import cell_keys


@dataclass(frozen=True, slots=True)
class BankState:
    """Aggregated readings across every battery reporting right now."""

    online: int
    total: int
    voltage: float | None = None
    current: float | None = None
    power: float | None = None
    soc: float | None = None
    capacity_ah: int | None = None
    remaining_ah: float | None = None
    cell_delta_mv: int | None = None


def compute_bank(device: TempraBleDevice) -> BankState:
    """Aggregate the bus totals over the batteries that are reporting."""
    total = len(device.battery_nodes)
    states: list[TempraState] = [
        state
        for node in device.battery_nodes
        if device.node_available(node)
        and (state := device.state(node)) is not None
        and state.voltage is not None
    ]
    if not states:
        return BankState(online=0, total=total)

    voltages = [s.voltage for s in states if s.voltage is not None]
    currents = [s.current for s in states if s.current is not None]
    powers = [s.power for s in states if s.power is not None]

    # Batteries in parallel sit at one common voltage, so the mean is the bank
    # voltage; currents and powers add up.
    voltage = round(sum(voltages) / len(voltages), 2) if voltages else None
    current = round(sum(currents), 2) if currents else None
    power = round(sum(powers), 1) if powers else None

    # Weight the state of charge by capacity so a bank of differently sized
    # batteries is reported correctly.
    weighted = [(s.soc, s.capacity_ah) for s in states if s.soc is not None]
    soc = None
    remaining = None
    if weighted:
        if all(capacity for _, capacity in weighted):
            total_ah = sum(capacity for _, capacity in weighted)
            soc = round(
                sum(value * capacity for value, capacity in weighted) / total_ah, 1
            )
            remaining = round(
                sum(value * capacity / 100 for value, capacity in weighted), 1
            )
        else:
            soc = round(sum(value for value, _ in weighted) / len(weighted), 1)

    capacities = [s.capacity_ah for s in states if s.capacity_ah is not None]
    capacity = sum(capacities) if capacities else None

    # The spread across every cell in the bank is the number worth watching on
    # LiFePO4: it widens long before anything else does.
    cells = [
        millivolts
        for s in states
        for key in cell_keys()
        if (millivolts := getattr(s, key)) is not None
    ]
    delta = max(cells) - min(cells) if len(cells) > 1 else None

    return BankState(
        online=len(states),
        total=total,
        voltage=voltage,
        current=current,
        power=power,
        soc=soc,
        capacity_ah=capacity,
        remaining_ah=remaining,
        cell_delta_mv=delta,
    )
