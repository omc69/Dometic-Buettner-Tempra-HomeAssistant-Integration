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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .bank import BankState, compute_bank
from .const import DOMAIN, MANUFACTURER
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


BANK_SENSORS: tuple[TempraSensorDescription, ...] = (
    TempraSensorDescription(
        key="bank_voltage",
        field="voltage",
        translation_key="bank_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
    ),
    TempraSensorDescription(
        key="bank_current",
        field="current",
        translation_key="bank_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    TempraSensorDescription(
        key="bank_power",
        field="power",
        translation_key="bank_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=1,
    ),
    TempraSensorDescription(
        key="bank_soc",
        field="soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
    ),
    TempraSensorDescription(
        key="bank_remaining_ah",
        field="remaining_ah",
        translation_key="bank_remaining_ah",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UNIT_AMPERE_HOUR,
        suggested_display_precision=1,
    ),
    TempraSensorDescription(
        key="bank_capacity_ah",
        field="capacity_ah",
        translation_key="bank_capacity_ah",
        native_unit_of_measurement=UNIT_AMPERE_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TempraSensorDescription(
        key="bank_cell_delta",
        field="cell_delta_mv",
        translation_key="bank_cell_delta",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    TempraSensorDescription(
        key="bank_online",
        field="online",
        translation_key="bank_online",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TempraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the bank sensors now and each battery's sensors as it appears."""
    coordinator = entry.runtime_data

    # The bank exists from the start; it simply reports nothing until a battery
    # is online. Its unique_ids are unchanged so history carries over.
    async_add_entities(
        TempraBankSensor(coordinator, description) for description in BANK_SENSORS
    )

    added: set[int] = set()

    @callback
    def _add_node(node: int) -> None:
        if node in added:
            return
        added.add(node)
        async_add_entities(
            TempraSensor(coordinator, node, description) for description in SENSORS
        )

    # Fires once per battery node, replaying any already discovered.
    coordinator.device.set_new_node_callback(_add_node)
    entry.async_on_unload(lambda: coordinator.device.set_new_node_callback(None))


class TempraSensor(TempraEntity, SensorEntity):
    """A single measurement of one battery on the bus."""

    entity_description: TempraSensorDescription

    def __init__(
        self,
        coordinator: TempraCoordinator,
        node: int,
        description: TempraSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, node)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.device.address}_{node:02x}_{description.key}"
        )

    @property
    def native_value(self) -> float | int | None:
        """Return the current measurement."""
        state = self._state
        if state is None:
            return None
        return getattr(state, self.entity_description.field)

    @property
    def available(self) -> bool:
        """A measurement is available once the battery has sent it."""
        return super().available and self.native_value is not None


class TempraBankSensor(SensorEntity):
    """An aggregate reading across every battery on the bus."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: TempraSensorDescription

    def __init__(
        self, coordinator: TempraCoordinator, description: TempraSensorDescription
    ) -> None:
        """Initialise the sensor."""
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_bank_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "bank")},
            manufacturer=MANUFACTURER,
            name="Tempra battery bank",
            entry_type=None,
        )

    async def async_added_to_hass(self) -> None:
        """Follow the shared coordinator."""
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def _state(self) -> BankState:
        return compute_bank(self._coordinator.device)

    @property
    def available(self) -> bool:
        """Available while at least one battery is reporting."""
        return self._state.online > 0

    @property
    def native_value(self) -> float | int | None:
        """Return the aggregate."""
        return getattr(self._state, self.entity_description.field)
