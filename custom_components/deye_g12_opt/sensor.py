"""Sensor platform for Deye G12 Optimizer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfElectricCurrent, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SENSOR_CURRENT_TARIFF_ZONE,
    SENSOR_DAILY_ARBITRAGE_SAVINGS,
    SENSOR_EXPECTED_MORNING_LOAD,
    SENSOR_EXPECTED_MORNING_PV,
    SENSOR_HEADROOM_MAX_SOC,
    SENSOR_LAST_EXECUTION_STATUS,
    SENSOR_PLANNED_CHARGE_CURRENT,
    SENSOR_PLANNED_SOC_TARGET,
)
from .coordinator import DeyeG12Coordinator

SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key=SENSOR_PLANNED_SOC_TARGET,
        translation_key="planned_soc_target",
        icon="mdi:battery-charging-high",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_PLANNED_CHARGE_CURRENT,
        translation_key="planned_charge_current",
        icon="mdi:current-ac",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_CURRENT_TARIFF_ZONE,
        translation_key="current_tariff_zone",
        icon="mdi:clock-time-four-outline",
    ),
    SensorEntityDescription(
        key=SENSOR_EXPECTED_MORNING_LOAD,
        translation_key="expected_morning_load",
        icon="mdi:home-lightning-bolt",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_EXPECTED_MORNING_PV,
        translation_key="expected_morning_pv",
        icon="mdi:solar-power",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_HEADROOM_MAX_SOC,
        translation_key="headroom_max_soc",
        icon="mdi:battery-lock",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_DAILY_ARBITRAGE_SAVINGS,
        translation_key="daily_arbitrage_savings",
        icon="mdi:cash-multiple",
        native_unit_of_measurement="PLN",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
    ),
    SensorEntityDescription(
        key=SENSOR_LAST_EXECUTION_STATUS,
        translation_key="last_execution_status",
        icon="mdi:information-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye G12 sensors."""
    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[DeyeG12Sensor] = [
        DeyeG12Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class DeyeG12Sensor(CoordinatorEntity[DeyeG12Coordinator], SensorEntity):
    """Representation of a Deye G12 Optimizer sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DeyeG12Coordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.entry.entry_id)},
            "name": "Deye G12 Optimizer",
            "manufacturer": "Deye",
            "model": coordinator.inverter_profile.upper(),
        }

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        data = self.coordinator.data or {}
        return data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for diagnostic sensors."""
        if self.entity_description.key == SENSOR_LAST_EXECUTION_STATUS:
            plan = self.coordinator.current_plan
            return {
                "reason": plan.reason if plan else "None",
                "plan_type": plan.plan_type if plan else "None",
                "grid_charge_enable": plan.grid_charge_enable if plan else False,
            }
        return {}

