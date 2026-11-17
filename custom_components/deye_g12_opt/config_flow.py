"""Config flow and Options flow for Deye G12 Optimizer integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_BATTERY_SELL,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_V,
    CONF_BMS_ALARM_ENTITY,
    CONF_BMS_CCL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_ETA_CHG,
    CONF_ETA_DIS,
    CONF_ETA_PV,
    CONF_INVERTER_PROFILE,
    CONF_MAX_USER_CHARGE_CURRENT_A,
    CONF_METER_FIXED_WINTER_TIME,
    CONF_OFF_PEAK_PRICE,
    CONF_OPTIMIZATION_STRATEGY,
    CONF_PEAK_PRICE,
    CONF_PGE_SEASONAL_SHIFT,
    CONF_PGE_TARIFF,
    CONF_PV_FORECAST_ENTITY,
    CONF_PV_FORECAST_TODAY_ENTITY,
    CONF_PV_FORECAST_TOMORROW_ENTITY,
    CONF_RATED_POWER_W,
    CONF_READBACK_DELAY_SEC,
    CONF_SAFETY_LOAD,
    CONF_SAFETY_PV,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    CONF_SOLARMAN_DEVICE_ID,
    CONF_TOTAL_LOAD_ENTITY,
    CONF_USE_PESSIMISTIC_BLEND,
    CONF_WATCHDOG_TIMEOUT_SEC,
    CONTROL_MODES,
    DEFAULT_ALLOW_BATTERY_SELL,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_VOLTAGE_V_HV,
    DEFAULT_BATTERY_VOLTAGE_V_LV,
    DEFAULT_ETA_CHG,
    DEFAULT_ETA_DIS,
    DEFAULT_ETA_PV,
    DEFAULT_MAX_USER_CHARGE_CURRENT_A,
    DEFAULT_OFF_PEAK_PRICE,
    DEFAULT_PEAK_PRICE,
    DEFAULT_RATED_POWER_W,
    DEFAULT_READBACK_DELAY_SEC,
    DEFAULT_SAFETY_LOAD,
    DEFAULT_SAFETY_PV,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
    DEFAULT_WATCHDOG_TIMEOUT_SEC,
    DOMAIN,
    INVERTER_PROFILES,
    MODE_TOU_SCHEDULE,
    OPTIMIZATION_STRATEGIES,
    PROFILE_3PH_HV,
    PROFILE_3PH_LV,
    STRATEGY_MAX_CHARGE_CHEAP,
    TARIFF_G12W,
    TARIFF_VARIANTS,
)


class DeyeG12ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # pyright: ignore[reportCallIssue,reportGeneralTypeIssues]
    """Handle a config flow for Deye G12 Optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._user_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 1: System profile, mode, and solarman device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_data.update(user_input)
            return await self.async_step_entities()

        data_schema = vol.Schema(
            {
                vol.Required("title", default="Deye G12 Optimizer"): selector.TextSelector(),
                vol.Required(
                    CONF_INVERTER_PROFILE, default=PROFILE_3PH_LV
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=INVERTER_PROFILES,
                        translation_key="inverter_profile",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_CONTROL_MODE, default=MODE_TOU_SCHEDULE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CONTROL_MODES,
                        translation_key="control_mode",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_SOLARMAN_DEVICE_ID): selector.TextSelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 2: Entity selectors for sensors and forecast."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_data.update(user_input)
            return await self.async_step_system_and_tariff()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_BATTERY_SOC_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_TOTAL_LOAD_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PV_FORECAST_TODAY_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PV_FORECAST_TOMORROW_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PV_FORECAST_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_BMS_ALARM_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["binary_sensor", "sensor"])
                ),
                vol.Optional(CONF_BMS_CCL_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        return self.async_show_form(
            step_id="entities",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_system_and_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Step 3: Capacities, voltages, and PGE tariff configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._user_data.update(user_input)
            title = self._user_data.pop("title", "Deye G12 Optimizer")
            return self.async_create_entry(title=title, data=self._user_data)

        profile = self._user_data.get(CONF_INVERTER_PROFILE, PROFILE_3PH_LV)
        default_voltage = (
            DEFAULT_BATTERY_VOLTAGE_V_HV
            if profile == PROFILE_3PH_HV
            else DEFAULT_BATTERY_VOLTAGE_V_LV
        )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BATTERY_CAPACITY_KWH,
                    CONF_ALLOW_BATTERY_SELL,
                    default=DEFAULT_BATTERY_CAPACITY_KWH,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1.0, max=100.0, step=0.5, unit_of_measurement="kWh"
                    )
                ),
                vol.Required(
                    CONF_BATTERY_VOLTAGE_V, default=default_voltage
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20.0, max=800.0, step=0.1, unit_of_measurement="V"
                    )
                ),
                vol.Required(
                    CONF_RATED_POWER_W, default=DEFAULT_RATED_POWER_W
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=50000, step=500, unit_of_measurement="W"
                    )
                ),
                vol.Required(CONF_PGE_TARIFF, default=TARIFF_G12W): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=TARIFF_VARIANTS,
                        translation_key="pge_tariff",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_PGE_SEASONAL_SHIFT, default=True): selector.BooleanSelector(),
                vol.Required(
                    CONF_METER_FIXED_WINTER_TIME, default=False
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="system_and_tariff",
            data_schema=data_schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return DeyeG12OptionsFlowHandler()


class DeyeG12OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Deye G12 Optimizer."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        config = {**self.config_entry.data, **self.config_entry.options}
        pv_today = config.get(CONF_PV_FORECAST_TODAY_ENTITY) or config.get(CONF_PV_FORECAST_ENTITY)
        pv_tomorrow = config.get(CONF_PV_FORECAST_TOMORROW_ENTITY)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_OPTIMIZATION_STRATEGY,
                    default=config.get(CONF_OPTIMIZATION_STRATEGY, STRATEGY_MAX_CHARGE_CHEAP),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=OPTIMIZATION_STRATEGIES,
                        translation_key="optimization_strategy",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_CONTROL_MODE,
                    default=config.get(CONF_CONTROL_MODE, MODE_TOU_SCHEDULE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CONTROL_MODES,
                        translation_key="control_mode",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_SOLARMAN_DEVICE_ID,
                    default=config.get(CONF_SOLARMAN_DEVICE_ID) or "",
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_PV_FORECAST_TODAY_ENTITY,
                    description={"suggested_value": pv_today} if pv_today else {},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(
                    CONF_PV_FORECAST_TOMORROW_ENTITY,
                    description={"suggested_value": pv_tomorrow} if pv_tomorrow else {},
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    CONF_SOC_MIN,
                    default=float(config.get(CONF_SOC_MIN, DEFAULT_SOC_MIN)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10.0, max=50.0, step=1.0, unit_of_measurement="%"
                    )
                ),
                vol.Required(
                    CONF_SOC_MAX,
                    default=float(config.get(CONF_SOC_MAX, DEFAULT_SOC_MAX)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50.0, max=100.0, step=1.0, unit_of_measurement="%"
                    )
                ),
                vol.Required(
                    CONF_MAX_USER_CHARGE_CURRENT_A,
                    default=int(
                        config.get(
                            CONF_MAX_USER_CHARGE_CURRENT_A, DEFAULT_MAX_USER_CHARGE_CURRENT_A
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=140, step=1, unit_of_measurement="A")
                ),
                vol.Required(
                    CONF_ETA_CHG,
                    default=float(config.get(CONF_ETA_CHG, DEFAULT_ETA_CHG)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=1.0, step=0.01)
                ),
                vol.Required(
                    CONF_ETA_DIS,
                    default=float(config.get(CONF_ETA_DIS, DEFAULT_ETA_DIS)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=1.0, step=0.01)
                ),
                vol.Required(
                    CONF_ETA_PV,
                    default=float(config.get(CONF_ETA_PV, DEFAULT_ETA_PV)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=1.0, step=0.01)
                ),
                vol.Required(
                    CONF_SAFETY_PV,
                    default=float(config.get(CONF_SAFETY_PV, DEFAULT_SAFETY_PV)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=1.0, step=0.05)
                ),
                vol.Required(
                    CONF_SAFETY_LOAD,
                    default=float(config.get(CONF_SAFETY_LOAD, DEFAULT_SAFETY_LOAD)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1.0, max=1.5, step=0.05)
                ),
                vol.Required(
                    CONF_PEAK_PRICE,
                    default=float(config.get(CONF_PEAK_PRICE, DEFAULT_PEAK_PRICE)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=5.0, step=0.01, unit_of_measurement="PLN/kWh"
                    )
                ),
                vol.Required(
                    CONF_OFF_PEAK_PRICE,
                    default=float(config.get(CONF_OFF_PEAK_PRICE, DEFAULT_OFF_PEAK_PRICE)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=5.0, step=0.01, unit_of_measurement="PLN/kWh"
                    )
                ),
                vol.Required(
                    CONF_USE_PESSIMISTIC_BLEND,
                    default=config.get(CONF_USE_PESSIMISTIC_BLEND, False),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_METER_FIXED_WINTER_TIME,
                    default=config.get(CONF_METER_FIXED_WINTER_TIME, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_ALLOW_BATTERY_SELL,
                    default=config.get(CONF_ALLOW_BATTERY_SELL, DEFAULT_ALLOW_BATTERY_SELL),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_READBACK_DELAY_SEC,
                    default=int(config.get(CONF_READBACK_DELAY_SEC, DEFAULT_READBACK_DELAY_SEC)),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="s")
                ),
                vol.Required(
                    CONF_WATCHDOG_TIMEOUT_SEC,
                    default=int(
                        config.get(CONF_WATCHDOG_TIMEOUT_SEC, DEFAULT_WATCHDOG_TIMEOUT_SEC)
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=60, max=18000, step=60, unit_of_measurement="s"
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=data_schema)
