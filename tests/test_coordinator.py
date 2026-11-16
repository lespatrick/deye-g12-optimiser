"""Tests for Deye G12 coordinator and integration lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from custom_components.deye_g12_opt import async_update_options
from custom_components.deye_g12_opt.const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_V,
    CONF_BMS_ALARM_ENTITY,
    CONF_BMS_CCL_ENTITY,
    CONF_CONTROL_MODE,
    CONF_INVERTER_PROFILE,
    CONF_PGE_TARIFF,
    CONF_PV_FORECAST_ENTITY,
    CONF_RATED_POWER_W,
    CONF_TOTAL_LOAD_ENTITY,
    DOMAIN,
    MODE_TOU_SCHEDULE,
    PROFILE_3PH_LV,
    TARIFF_G12W,
)
from custom_components.deye_g12_opt.coordinator import DeyeG12Coordinator


async def test_coordinator_setup_and_entities_lifecycle(hass: HomeAssistant):
    """Test full integration setup, entity creation, and clean unload."""
    # Set up mock sensor states
    hass.states.async_set("sensor.battery_soc", "45.0")
    hass.states.async_set("sensor.total_load", "1200.5")
    hass.states.async_set("sensor.pv_forecast", "12.5")
    hass.states.async_set("sensor.bms_alarm", "alarm")
    hass.states.async_set("sensor.bms_ccl", "30.0")

    # Register mock solarman service so non-simulation calls succeed
    written_registers: dict[int, int] = {}

    async def mock_solarman(call):
        reg = call.data["register"]
        val = call.data["values"][0]
        written_registers[reg] = val

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Deye Inverter",
        data={
            CONF_INVERTER_PROFILE: PROFILE_3PH_LV,
            CONF_CONTROL_MODE: MODE_TOU_SCHEDULE,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_TOTAL_LOAD_ENTITY: "sensor.total_load",
            CONF_PV_FORECAST_ENTITY: "sensor.pv_forecast",
            CONF_BMS_ALARM_ENTITY: "sensor.bms_alarm",
            CONF_BMS_CCL_ENTITY: "sensor.bms_ccl",
            CONF_BATTERY_CAPACITY_KWH: 15.0,
            CONF_BATTERY_VOLTAGE_V: 51.2,
            CONF_RATED_POWER_W: 15000,
            CONF_PGE_TARIFF: TARIFF_G12W,
            "readback_delay_sec": 0,
        },
        entry_id="test_entry_lifecycle",
    )
    entry.add_to_hass(hass)

    # Setup entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator is not None
    assert coordinator.data["current_soc"] == 45.0
    assert coordinator.simulation_mode is True
    assert coordinator._is_bms_alarm_active() is True
    assert coordinator._get_bms_max_charge_current() == 30

    # Test switches
    for sw_name in (
        "switch.deye_g12_optimizer_simulation_mode",
        "switch.deye_g12_optimizer_automation_active",
        "switch.deye_g12_optimizer_emergency_reserve_storm_watch_100",
    ):
        await hass.services.async_call("switch", "turn_off", {"entity_id": sw_name}, blocking=True)
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw_name}, blocking=True)

    # Test numbers
    num_tests = [
        ("number.deye_g12_optimizer_soc_minimum_reserve", 20.0),
        ("number.deye_g12_optimizer_soc_maximum_grid_target", 90.0),
        ("number.deye_g12_optimizer_max_grid_charge_current", 70.0),
        ("number.deye_g12_optimizer_cell_balancing_interval", 10.0),
    ]
    for entity_id, val in num_tests:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": val},
            blocking=True,
        )

    assert coordinator.soc_min == 20.0
    assert coordinator.soc_max == 90.0
    assert coordinator.max_user_charge_current_a == 70
    assert coordinator.balancing_interval_days == 10

    # Clear alarm for optimization run
    hass.states.async_set("sensor.bms_alarm", "off")

    # Test manual evaluation trigger with emergency reserve on -> should yield target 100%
    with patch.object(
        coordinator.recorder_estimator, "async_get_window_consumption", AsyncMock(return_value=6.5)
    ):
        res = await coordinator.async_evaluate_and_apply("night")
        assert res is True
        assert coordinator.current_plan is not None
        assert coordinator.current_plan.soc_target == 100.0

    # Test midday evaluation
    with patch.object(
        coordinator.recorder_estimator, "async_get_window_consumption", AsyncMock(return_value=4.0)
    ):
        res_mid = await coordinator.async_evaluate_and_apply("midday")
        assert res_mid is True

    # Test async_update_options callback
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await async_update_options(hass, entry)
        mock_reload.assert_called_once_with(entry.entry_id)

    # Test unload
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_coordinator_inactive_skips_optimization(hass: HomeAssistant):
    """Test that when Automation Active is turned OFF, optimization is skipped."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Deye Inverter",
        data={
            CONF_INVERTER_PROFILE: PROFILE_3PH_LV,
            CONF_CONTROL_MODE: MODE_TOU_SCHEDULE,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_TOTAL_LOAD_ENTITY: "sensor.total_load",
            CONF_BATTERY_CAPACITY_KWH: 15.0,
            CONF_BATTERY_VOLTAGE_V: 51.2,
            CONF_RATED_POWER_W: 15000,
            CONF_PGE_TARIFF: TARIFF_G12W,
            "readback_delay_sec": 0,
        },
        entry_id="test_entry_inactive",
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.automation_active = False

    res = await coordinator.async_evaluate_and_apply("night")
    assert res is False
    assert "Automation Inactive" in coordinator.executor.last_execution_status

    await hass.config_entries.async_unload(entry.entry_id)


async def test_coordinator_watchdog_auto_migration_and_rearm(hass: HomeAssistant):
    """Test auto-migration of watchdog timeout to 1800s and re-arming after connection drop."""
    from custom_components.deye_g12_opt.const import (
        CONF_WATCHDOG_TIMEOUT_SEC,
        MODE_REMOTE_RAM,
    )
    from custom_components.deye_g12_opt.optimizer import PlanResult

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Deye Inverter",
        data={
            CONF_INVERTER_PROFILE: PROFILE_3PH_LV,
            CONF_CONTROL_MODE: MODE_REMOTE_RAM,
            CONF_BATTERY_SOC_ENTITY: "sensor.battery_soc",
            CONF_TOTAL_LOAD_ENTITY: "sensor.total_load",
            CONF_BATTERY_CAPACITY_KWH: 15.0,
            CONF_BATTERY_VOLTAGE_V: 51.2,
            CONF_RATED_POWER_W: 15000,
            CONF_PGE_TARIFF: TARIFF_G12W,
            CONF_WATCHDOG_TIMEOUT_SEC: 180,  # Old value 180s
            "readback_delay_sec": 0,
        },
        options={
            CONF_WATCHDOG_TIMEOUT_SEC: 180,
        },
        entry_id="test_entry_watchdog_migration",
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator: DeyeG12Coordinator = hass.data[DOMAIN][entry.entry_id]

    # 1. Verify watchdog was migrated to 1800s (30 minutes)
    assert coordinator.watchdog_timeout_sec == 1800
    assert coordinator.executor.watchdog_timeout_sec == 1800
    assert entry.options.get(CONF_WATCHDOG_TIMEOUT_SEC) == 1800

    # 2. Test re-arm mechanism:
    # Setup active Remote Mode with a grid charge plan
    coordinator.simulation_mode = False
    coordinator.executor.remote_mode_active = True
    coordinator.current_plan = PlanResult(
        plan_type="night",
        soc_target=80.0,
        charge_current_a=25,
        grid_charge_enable=True,
        headroom_max_soc=95.0,
        expected_load_kwh=6.0,
        expected_pv_kwh=1.0,
        reason="Test",
    )

    # Simulate watchdog refresh failure (Modbus connection drop)
    with patch.object(coordinator.executor, "async_refresh_watchdog", AsyncMock(return_value=False)):
        await coordinator._async_update_data()
        assert coordinator._remote_mode_needs_rearm is True

    # Next update data: simulate connection restored, verify async_apply_plan (re-arm) was invoked
    with (
        patch.object(coordinator.executor, "async_refresh_watchdog", AsyncMock(return_value=True)),
        patch.object(coordinator.executor, "async_apply_plan", AsyncMock(return_value=True)) as mock_apply,
    ):
        await coordinator._async_update_data()
        assert mock_apply.called
        assert coordinator._remote_mode_needs_rearm is False

    await hass.config_entries.async_unload(entry.entry_id)

