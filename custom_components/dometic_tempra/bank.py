"""Aggregate view over every configured Tempra battery.

Each battery is its own config entry, but what a user actually reads off a
dashboard is the bank: how much current is flowing in or out in total, how
much capacity is left, and whether one battery is drifting away from the
others. Those numbers are awkward to assemble from helper groups -- a plain
mean of the state of charge is wrong as soon as the batteries differ in
capacity, and the cell spread has to reach across devices -- so the
integration computes them itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback

from .const import DOMAIN
from .coordinator import TempraCoordinator
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


class TempraBank:
    """Tracks every battery coordinator and derives the bank totals."""

    def __init__(self) -> None:
        """Initialise an empty bank."""
        self._coordinators: dict[str, TempraCoordinator] = {}
        self._unsubscribers: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._owner: str | None = None

    # -- membership ---------------------------------------------------------

    @callback
    def register(self, entry_id: str, coordinator: TempraCoordinator) -> CALLBACK_TYPE:
        """Add a battery to the bank. Returns a callback that removes it."""
        self._coordinators[entry_id] = coordinator
        self._unsubscribers[entry_id] = coordinator.async_add_listener(
            self._async_updated
        )

        @callback
        def _remove() -> None:
            self._coordinators.pop(entry_id, None)
            if unsubscribe := self._unsubscribers.pop(entry_id, None):
                unsubscribe()
            if self._owner == entry_id:
                self._owner = None
            self._async_updated()

        return _remove

    @callback
    def claim(self, entry_id: str) -> bool:
        """Whether this entry should own the bank entities.

        The bank belongs to no single battery, so the first entry to load
        hosts its entities. If that entry is later removed the entities go
        with it and the next reload re-creates them.
        """
        if self._owner is None:
            self._owner = entry_id
        return self._owner == entry_id

    # -- updates ------------------------------------------------------------

    @callback
    def add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        """Subscribe to bank changes. Returns a callback that unsubscribes."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @callback
    def _async_updated(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- aggregation --------------------------------------------------------

    @property
    def state(self) -> BankState:
        """Current bank totals, over the batteries that are reporting."""
        reporting = [
            coordinator
            for coordinator in self._coordinators.values()
            if coordinator.available and coordinator.data.voltage is not None
        ]
        total = len(self._coordinators)
        if not reporting:
            return BankState(online=0, total=total)

        states = [coordinator.data for coordinator in reporting]

        voltages = [s.voltage for s in states if s.voltage is not None]
        currents = [s.current for s in states if s.current is not None]
        powers = [s.power for s in states if s.power is not None]

        # Batteries in parallel sit at one common voltage, so the mean is the
        # bank voltage; currents and powers add up.
        voltage = round(sum(voltages) / len(voltages), 2) if voltages else None
        current = round(sum(currents), 2) if currents else None
        power = round(sum(powers), 1) if powers else None

        # Weight the state of charge by capacity: an even mean would misreport
        # a bank whose batteries differ in size.
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

        # The spread across every cell in the bank is the number worth
        # watching on LiFePO4: it widens long before anything else does.
        cells = [
            millivolts
            for s in states
            for key in cell_keys()
            if (millivolts := getattr(s, key)) is not None
        ]
        delta = max(cells) - min(cells) if len(cells) > 1 else None

        return BankState(
            online=len(reporting),
            total=total,
            voltage=voltage,
            current=current,
            power=power,
            soc=soc,
            capacity_ah=capacity,
            remaining_ah=remaining,
            cell_delta_mv=delta,
        )


@callback
def async_get_bank(hass: HomeAssistant) -> TempraBank:
    """Return the single bank shared by every Tempra config entry."""
    domain_data: dict = hass.data.setdefault(DOMAIN, {})
    bank: TempraBank | None = domain_data.get("bank")
    if bank is None:
        bank = domain_data["bank"] = TempraBank()
    return bank
