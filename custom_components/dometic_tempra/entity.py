"""Shared entity base for the Dometic Büttner Tempra integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import TempraCoordinator


class TempraEntity(CoordinatorEntity[TempraCoordinator]):
    """Base entity tied to one battery."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TempraCoordinator) -> None:
        """Initialise the entity."""
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address)},
            connections={(CONNECTION_BLUETOOTH, device.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=device.name,
            serial_number=coordinator.data.serial,
        )

    @property
    def available(self) -> bool:
        """Only report values while the telemetry stream is alive."""
        return super().available and self.coordinator.available
