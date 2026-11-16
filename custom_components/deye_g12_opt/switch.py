"""Switch platform for Deye G12 Optimizer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SWITCH_AUTOMATION_ACTIVE,
    SWITCH_EMERGENCY_RESERVE,
    SWITCH_SIMULATION_MODE,
)
from .coordinator import DeyeG12Coordinator

SWITCH_DESCRIPTIONS: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        key=SWITCH_AUTOMATION_ACTIVE,
        translation_key="automation_active",
        icon="mdi:robot",
    ),
    SwitchEntityDescription(
        key=SWITCH_SIMULATION_MODE,
        translation_key="simulation_mode",
        icon="mdi:test-tube",
    ),
    SwitchEntityDescription(
        key=SWITCH_EMERGENCY_RESERVE,
        translation_key="emergency_reserve",
        icon="mdi:shield-alert",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Deye G12 switches."""
    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[DeyeG12Switch] = [
        DeyeG12Switch(coordinator, description) for description in SWITCH_DESCRIPTIONS
    ]
    async_add_entities(entities)


class DeyeG12Switch(CoordinatorEntity[DeyeG12Coordinator], SwitchEntity, RestoreEntity):
    """Representation of a Deye G12 Optimizer switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DeyeG12Coordinator,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize switch."""
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
        """Restore state on startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            is_on = last_state.state == "on"
            if self.entity_description.key == SWITCH_AUTOMATION_ACTIVE:
                self.coordinator.automation_active = is_on
            elif self.entity_description.key == SWITCH_SIMULATION_MODE:
                self.coordinator.simulation_mode = is_on
            elif self.entity_description.key == SWITCH_EMERGENCY_RESERVE:
                self.coordinator.emergency_reserve = is_on

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        if self.entity_description.key == SWITCH_AUTOMATION_ACTIVE:
            return self.coordinator.automation_active
        if self.entity_description.key == SWITCH_SIMULATION_MODE:
            return self.coordinator.simulation_mode
        if self.entity_description.key == SWITCH_EMERGENCY_RESERVE:
            return self.coordinator.emergency_reserve
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        if self.entity_description.key == SWITCH_AUTOMATION_ACTIVE:
            self.coordinator.automation_active = True
        elif self.entity_description.key == SWITCH_SIMULATION_MODE:
            self.coordinator.simulation_mode = True
        elif self.entity_description.key == SWITCH_EMERGENCY_RESERVE:
            self.coordinator.emergency_reserve = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        if self.entity_description.key == SWITCH_AUTOMATION_ACTIVE:
            self.coordinator.automation_active = False
        elif self.entity_description.key == SWITCH_SIMULATION_MODE:
            self.coordinator.simulation_mode = False
        elif self.entity_description.key == SWITCH_EMERGENCY_RESERVE:
            self.coordinator.emergency_reserve = False
        self.async_write_ha_state()
