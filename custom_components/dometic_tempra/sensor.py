"""Sensor platform for the Dometic Büttner Tempra integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TempraConfigEntry, TempraCoordinator
from .entity import TempraEntity

#: Ampere-hours has no device class in Home Assistant.
UNIT_AMPERE_HOUR = "Ah"


@dataclass(frozen=True, kw_only=True)
class TempraSensorDescription(SensorEntityDescription):
    """Describes a Tempra sensor and the state field backing it."""

    field: str


SENSORS: tuple[TempraSensorDescription, ...] = (
    TempraSensorDescription(
        key="voltage",
        field="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
    ),
    TempraSensorDescription(
        key="current",
        field="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    TempraSensorDescription(
        key="power",
        field="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
    ),
    TempraSensorDescription(
        key="soc",
        field="soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    TempraSensorDescription(
        key="soh",
        field="soh",
        translation_key="soh",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TempraSensorDescription(
        key="capacity_ah",
        field="capacity_ah",
        translation_key="capacity_ah",
        native_unit_of_measurement=UNIT_AMPERE_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    *(
        TempraSensorDescription(
            key=f"cell_{cell}_mv",
            field=f"cell_{cell}_mv",
            translation_key="cell_voltage",
            translation_placeholders={"cell": str(cell)},
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
        )
        for cell in range(1, 5)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Tempra sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        TempraSensor(coordinator, description) for description in SENSORS
    )


class TempraSensor(TempraEntity, SensorEntity):
    """A single measurement of a Tempra battery."""

    entity_description: TempraSensorDescription

    def __init__(
        self, coordinator: TempraCoordinator, description: TempraSensorDescription
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device.address}_{description.key}"

    @property
    def native_value(self) -> float | int | None:
        """Return the current measurement."""
        return getattr(self.coordinator.data, self.entity_description.field)

    @property
    def available(self) -> bool:
        """A measurement is available once the battery has sent it at least once."""
        return super().available and self.native_value is not None
