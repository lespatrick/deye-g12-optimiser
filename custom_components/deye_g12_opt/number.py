"""Number platform for Deye G12 Optimizer."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    NUMBER_BALANCING_INTERVAL,
    NUMBER_MAX_CHARGE_CURRENT,
    NUMBER_SOC_MAX,
    NUMBER_SOC_MIN,
)
from .coordinator import DeyeG12Coordinator

NUMBER_DESCRIPTIONS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(
        key=NUMBER_SOC_MIN,
        translation_key="soc_min",
        icon="mdi:battery-arrow-down",
        native_min_value=10.0,
        native_max_value=50.0,
        native_step=1.0,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.BATTERY,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key=NUMBER_SOC_MAX,
        translation_key="soc_max",
        icon="mdi:battery-arrow-up",
        native_min_value=50.0,
        native_max_value=100.0,
        native_step=1.0,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.BATTERY,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key=NUMBER_MAX_CHARGE_CURRENT,
        translation_key="max_charge_current",
        icon="mdi:current-ac",
        native_min_value=5.0,
        native_max_value=140.0,
        native_step=1.0,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        mode=NumberMode.BOX,
    ),
    NumberEntityDescription(
        key=NUMBER_BALANCING_INTERVAL,
        translation_key="balancing_interval",
        icon="mdi:calendar-sync",
        native_min_value=1.0,
        native_max_value=60.0,
        native_step=1.0,
        native_unit_of_measurement=UnitOfTime.DAYS,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye G12 numbers."""
    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[DeyeG12Number] = [
        DeyeG12Number(coordinator, description) for description in NUMBER_DESCRIPTIONS
    ]
    async_add_entities(entities)


class DeyeG12Number(CoordinatorEntity[DeyeG12Coordinator], NumberEntity, RestoreEntity):
    """Representation of a Deye G12 Optimizer configurable number."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DeyeG12Coordinator,
        description: NumberEntityDescription,
    ) -> None:
        """Initialize number."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "Deye G12 Optimizer",
            "manufacturer": "Deye",
            "model": coordinator.inverter_profile.upper(),
        }

    async def async_added_to_hass(self) -> None:
        """Restore value on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in (None, "", "unknown", "unavailable"):
            try:
                val = float(last_state.state)
                await self.async_set_native_value(val)
            except ValueError, TypeError:
                pass

    @property
    def native_value(self) -> float:
        """Return the current value."""
        if self.entity_description.key == NUMBER_SOC_MIN:
            return self.coordinator.soc_min
        if self.entity_description.key == NUMBER_SOC_MAX:
            return self.coordinator.soc_max
        if self.entity_description.key == NUMBER_MAX_CHARGE_CURRENT:
            return float(self.coordinator.max_user_charge_current_a)
        if self.entity_description.key == NUMBER_BALANCING_INTERVAL:
            return float(self.coordinator.balancing_interval_days)
        return 0.0

    async def async_set_native_value(self, value: float) -> None:
        """Update the value."""
        if self.entity_description.key == NUMBER_SOC_MIN:
            self.coordinator.soc_min = value
            self.coordinator.optimizer.soc_min_pct = value
            self.coordinator.executor.soc_min = value
        elif self.entity_description.key == NUMBER_SOC_MAX:
            self.coordinator.soc_max = value
            self.coordinator.optimizer.soc_max_pct = value
            self.coordinator.executor.soc_max = value
        elif self.entity_description.key == NUMBER_MAX_CHARGE_CURRENT:
            self.coordinator.max_user_charge_current_a = int(value)
            self.coordinator.optimizer.max_user_charge_current_a = int(value)
        elif self.entity_description.key == NUMBER_BALANCING_INTERVAL:
            self.coordinator.balancing_interval_days = int(value)
        self.async_write_ha_state()
