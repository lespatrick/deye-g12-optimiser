"""Tests for Deye G12 config flow and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.deye_g12_opt.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_V,
    CONF_CONTROL_MODE,
    CONF_INVERTER_PROFILE,
    CONF_METER_FIXED_WINTER_TIME,
    CONF_PGE_SEASONAL_SHIFT,
    CONF_PGE_TARIFF,
    CONF_PV_FORECAST_ENTITY,
    CONF_RATED_POWER_W,
    CONF_SOC_MAX,
    CONF_SOC_MIN,
    CONF_TOTAL_LOAD_ENTITY,
    DOMAIN,
    MODE_TOU_SCHEDULE,
    PROFILE_3PH_LV,
    TARIFF_G12W,
)


async def test_full_config_flow(hass: HomeAssistant):
    """Test full multi-step setup flow."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Step 1: User
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "title": "My Deye Inverter",
            CONF_INVERTER_PROFILE: PROFILE_3PH_LV,
            CONF_CONTROL_MODE: MODE_TOU_SCHEDULE,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "entities"

    # Step 2: Entities
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_TOTAL_LOAD_ENTITY: "sensor.total_load",
            CONF_PV_FORECAST_ENTITY: "sensor.solcast_forecast",
        },
    )
    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "system_and_tariff"

    # Step 3: System and Tariff
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        {
            CONF_BATTERY_CAPACITY_KWH: 15.0,
            CONF_BATTERY_VOLTAGE_V: 51.2,
            CONF_RATED_POWER_W: 15000,
            CONF_PGE_TARIFF: TARIFF_G12W,
            CONF_PGE_SEASONAL_SHIFT: True,
            CONF_METER_FIXED_WINTER_TIME: False,
        },
    )
    assert result4["type"] == FlowResultType.CREATE_ENTRY
    assert result4["title"] == "My Deye Inverter"
    assert result4["data"][CONF_INVERTER_PROFILE] == PROFILE_3PH_LV
    assert result4["data"][CONF_BATTERY_CAPACITY_KWH] == 15.0


async def test_options_flow(hass: HomeAssistant):
    """Test options flow allows changing parameters."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Deye Optimizer",
        data={
            CONF_INVERTER_PROFILE: PROFILE_3PH_LV,
            CONF_CONTROL_MODE: MODE_TOU_SCHEDULE,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_TOTAL_LOAD_ENTITY: "sensor.total_load",
            CONF_BATTERY_CAPACITY_KWH: 15.0,
            CONF_BATTERY_VOLTAGE_V: 51.2,
            CONF_RATED_POWER_W: 15000,
            CONF_PGE_TARIFF: TARIFF_G12W,
        },
        entry_id="test_entry_options",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CONTROL_MODE: MODE_TOU_SCHEDULE,
            "solarman_device_id": "SOLARMAN_SN_9999",
            CONF_SOC_MIN: 20.0,
            CONF_SOC_MAX: 90.0,
            "max_user_charge_current_a": 60,
            "eta_chg": 0.95,
            "eta_dis": 0.95,
            "eta_pv": 0.98,
            "safety_pv": 0.80,
            "safety_load": 1.15,
            "peak_price": 1.25,
            "off_peak_price": 0.60,
            "use_pessimistic_blend": True,
            "meter_fixed_winter_time": False,
            "readback_delay_sec": 5,
            "watchdog_timeout_sec": 120,
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["solarman_device_id"] == "SOLARMAN_SN_9999"
    assert entry.options[CONF_SOC_MIN] == 20.0
    assert entry.options[CONF_SOC_MAX] == 90.0
