"""Shared entity base for the Dometic Büttner Tempra integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import TempraCoordinator


class TempraEntity(CoordinatorEntity[TempraCoordinator]):
    """Base entity tied to one battery on the bus."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TempraCoordinator, node: int) -> None:
        """Initialise the entity for one battery node."""
        super().__init__(coordinator)
        self._node = node
        address = coordinator.device.address
        index = coordinator.device.battery_index(node)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{address}_{node:02x}")},
            via_device=(DOMAIN, "bank"),
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"Tempra Batterie {index}" if index else coordinator.device.name,
        )

    @property
    def _state(self):
        """Current state of this battery node, if seen."""
        return self.coordinator.device.state(self._node)

    @property
    def available(self) -> bool:
        """Only report values while this battery's stream is alive."""
        return super().available and self.coordinator.device.node_available(self._node)
