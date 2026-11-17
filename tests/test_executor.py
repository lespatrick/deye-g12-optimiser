"""Tests for Deye executor and safety supervision layer."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from custom_components.deye_g12_opt.const import (
    MODE_REMOTE_RAM,
    MODE_TOU_SCHEDULE,
    PROFILE_3PH_LV,
    PROFILE_SOFAR_HYD_3PH,
    REG_REMOTE_ENABLE,
    REG_REMOTE_MAX_PV,
    REG_REMOTE_POWER,
    REG_REMOTE_WATCHDOG,
    REG_SOFAR_PASSIVE_POWER,
    REG_SOFAR_WORK_MODE,
    SOFAR_MODE_PASSIVE,
    SOFAR_MODE_SELF_USE,
)
from custom_components.deye_g12_opt.executor import DeyeExecutionError, DeyeExecutor, to_signed_16
from custom_components.deye_g12_opt.optimizer import PlanResult
from custom_components.deye_g12_opt.pge_tariff import PGETariffCalculator


def test_signed_16_conversion():
    """Verify signed to 16-bit unsigned conversion."""
    assert to_signed_16(0) == 0
    assert to_signed_16(100) == 100
    assert to_signed_16(-1) == 0xFFFF
    # -133 in two's complement 16-bit
    assert to_signed_16(-133) == 65403


@pytest.mark.asyncio
async def test_simulation_mode_makes_no_modbus_calls(hass):
    """Verify simulation mode logs to logbook and makes zero service calls to solarman."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
        readback_delay_sec=0,
        allow_battery_sell=True,
    )
    tariff = PGETariffCalculator()

    plan = PlanResult(
        plan_type="night",
        soc_target=80.0,
        charge_current_a=25,
        grid_charge_enable=True,
        headroom_max_soc=95.0,
        expected_load_kwh=8.0,
        expected_pv_kwh=1.0,
        reason="Test Plan",
    )

    solarman_calls = []
    logbook_calls = []

    async def mock_solarman(call):
        solarman_calls.append(call.data)

    async def mock_logbook(call):
        logbook_calls.append(call.data)

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)
    hass.services.async_register("logbook", "log", mock_logbook)

    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=True,
        current_soc=20.0,
    )

    assert res is True
    assert len(solarman_calls) == 0
    assert len(logbook_calls) == 1
    assert "Simulation: Planned 80% @ 25A" in executor.last_execution_status


@pytest.mark.asyncio
async def test_bms_alarm_aborts_writes(hass):
    """Verify active BMS alarm halts execution."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
    )
    tariff = PGETariffCalculator()
    plan = PlanResult("night", 80.0, 25, True, 95.0, 8.0, 1.0, "Test")

    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=30.0,
        bms_alarm_active=True,
    )
    assert res is False
    assert "Active BMS alarm detected" in executor.last_execution_status


@pytest.mark.asyncio
async def test_unavailable_soc_aborts_writes(hass):
    """Verify unavailable/negative SOC halts execution."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
    )
    tariff = PGETariffCalculator()
    plan = PlanResult("night", 80.0, 25, True, 95.0, 8.0, 1.0, "Test")

    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=None,
    )
    assert res is False
    assert "Battery SOC unavailable" in executor.last_execution_status


@pytest.mark.asyncio
async def test_tou_sell_bit_preservation_and_deadband(hass):
    """Verify preservation of bit 5 (Sell) in registers 172-177 and deadband throttling."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
        readback_delay_sec=0,
        allow_battery_sell=True,
    )
    tariff = PGETariffCalculator()
    plan = PlanResult("night", 80.0, 20, True, 95.0, 8.0, 1.0, "Test")

    written_registers: dict[int, int] = {}

    async def mock_solarman(call):
        reg = call.data.get("address", call.data.get("register"))
        val = call.data["values"][0]
        written_registers[reg] = val

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    # Simulate existing registers with Sell bit (32) enabled on slot 1 (reg 172 = 32)
    # and all days enabled in 146 (0xFE)
    current_slots = {
        172: 32,  # 32 = Disabled + Sell
        146: 0xFE,
    }

    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=30.0,
        current_slot_values=current_slots,
    )
    assert res is True
    # Reg 172 should now be Grid (1) + Sell (32) = 33
    assert written_registers.get(172) == 33
    # Reg 146 should have bit 0 set (0xFE | 0x01 = 0xFF), preserving bits 1-7
    assert written_registers.get(146) == 0xFF
    # Register 128 (grid charge current) should be 20A
    assert written_registers.get(128) == 20

    # Test default allow_battery_sell=False: bit 5 (32) is stripped, only Grid charge (1) written
    executor_no_sell = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
        readback_delay_sec=0,
        allow_battery_sell=False,
    )
    written_registers.clear()
    res_no_sell = await executor_no_sell.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=30.0,
        current_slot_values=current_slots,
    )
    assert res_no_sell is True
    assert written_registers.get(172) == 1  # 32 is stripped!

    # Now test deadband: if target is 81.0% (diff < 2) and current is 21A (diff < 2)
    written_registers.clear()
    plan_minor = PlanResult("night", 81.0, 21, True, 95.0, 8.0, 1.0, "Minor change")
    res_deadband = await executor.async_apply_plan(
        plan=plan_minor,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=30.0,
    )
    assert res_deadband is True
    assert len(written_registers) == 0  # No writes performed!


@pytest.mark.asyncio
async def test_3ph_register_259_protection(hass):
    """Verify that writing register 259 in 3PH mode triggers a critical safety violation."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
    )

    with pytest.raises(DeyeExecutionError, match="CRITICAL SAFETY VIOLATION.*259"):
        await executor._write_register(259, 100)


@pytest.mark.asyncio
async def test_remote_mode_register_writes_and_pv_safety(hass):
    """Verify Remote Mode register semantics, signed power conversion, and PV limit guard."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_REMOTE_RAM,
        rated_power_w=15000,
        watchdog_timeout_sec=180,
    )
    tariff = PGETariffCalculator()
    plan = PlanResult("night", 80.0, 25, True, 95.0, 8.0, 1.0, "Test")

    written_registers: dict[int, int] = {}

    async def mock_solarman(call):
        reg = call.data.get("address", call.data.get("register"))
        val = call.data["values"][0]
        written_registers[reg] = val

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=40.0,
    )
    assert res is True
    assert written_registers.get(REG_REMOTE_ENABLE) == 1
    # Watchdog timeout is guaranteed to be at least 1800s (30 min) to bridge Modbus dropouts
    assert written_registers.get(REG_REMOTE_WATCHDOG) == 1800
    # Power should be negative in two's complement
    power_val = written_registers.get(REG_REMOTE_POWER)
    assert power_val is not None and power_val > 32767
    # PV limit 1115 must be 999 (not >= 1000)
    assert written_registers.get(REG_REMOTE_MAX_PV) == 999

    # Test watchdog refresh writes specifically to 1101
    written_registers.clear()
    await executor.async_refresh_watchdog()
    assert written_registers.get(REG_REMOTE_WATCHDOG) == 1800

    # Test PV guard: Attempting to write >= 1000 to 1115 raises DeyeExecutionError
    with pytest.raises(DeyeExecutionError, match="Attempted to write >= 1000 to register 1115"):
        await executor._write_register(REG_REMOTE_MAX_PV, 1000)


@pytest.mark.asyncio
async def test_readback_retry_and_unknown_profile(hass):
    """Verify read-back verification retry and unknown profile handling."""
    executor_unknown = DeyeExecutor(
        hass=hass,
        inverter_profile="unknown_profile",
        control_mode=MODE_TOU_SCHEDULE,
    )
    plan = PlanResult("night", 80.0, 20, True, 95.0, 8.0, 1.0, "Test")
    res = await executor_unknown.async_apply_plan(
        plan=plan,
        tariff_calc=PGETariffCalculator(),
        simulation_mode=False,
        current_soc=40.0,
    )
    assert res is False

    # Test readback verification failure triggering retry
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_TOU_SCHEDULE,
        readback_delay_sec=0,
        allow_battery_sell=True,
    )
    verify_attempts = []

    async def mock_verify(address, expected):
        verify_attempts.append(expected)
        # Fail first attempt, succeed second
        return len(verify_attempts) > 1

    executor._verify_readback = mock_verify
    hass.services.async_register("solarman", "write_multiple_registers", AsyncMock())

    res_retry = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=PGETariffCalculator(),
        simulation_mode=False,
        current_soc=40.0,
    )
    assert res_retry is True
    assert len(verify_attempts) == 2


@pytest.mark.asyncio
async def test_remote_mode_boundary_shutdown(hass):
    """Verify immediate shutdown of Remote Mode if SOC hits boundaries."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_REMOTE_RAM,
        soc_min=15.0,
        soc_max=95.0,
    )
    executor.remote_mode_active = True
    tariff = PGETariffCalculator()

    # Discharge plan when SOC <= soc_min (e.g. 14%)
    plan_dischg = PlanResult("night", 15.0, 0, False, 95.0, 5.0, 0.0, "Test")

    written_registers: dict[int, int] = {}

    async def mock_solarman(call):
        reg = call.data.get("address", call.data.get("register"))
        val = call.data["values"][0]
        written_registers[reg] = val

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    res = await executor.async_apply_plan(
        plan=plan_dischg,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=14.0,  # Below soc_min
    )
    assert res is True
    # Both power and enable should be set to 0
    assert written_registers.get(REG_REMOTE_POWER) == 0
    assert written_registers.get(REG_REMOTE_ENABLE) == 0
    assert executor.remote_mode_active is False


@pytest.mark.asyncio
async def test_write_register_coordinator_direct_fallback(hass):
    """Verify fallback to direct coordinator execution if solarman service crashes with AttributeError."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_REMOTE_RAM,
    )

    # Mock service that crashes with 'ConfigEntry' object has no attribute 'runtime_data'
    async def broken_solarman(call):
        raise AttributeError("'ConfigEntry' object has no attribute 'runtime_data'")

    hass.services.async_register("solarman", "write_multiple_registers", broken_solarman)

    direct_writes: list[tuple[int, int, list[int]]] = []

    class MockCoordinator:
        async def execute(self, code, address, data=None):
            direct_writes.append((code, address, data))

    mock_coord = MockCoordinator()
    hass.data["solarman"] = {"test_entry_id": mock_coord}

    # Execute write
    await executor._write_register(REG_REMOTE_ENABLE, 1)

    assert len(direct_writes) == 1
    code, address, data = direct_writes[0]
    assert code == 16
    assert address == REG_REMOTE_ENABLE
    assert data == [1]


@pytest.mark.asyncio
async def test_write_register_offline_solarman_defers_cleanly(hass):
    """Verify that when Solarman entry is offline without runtime_data, writes defer cleanly without reloading."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant.common import MockConfigEntry

    from custom_components.deye_g12_opt.executor import DeyeConnectionError

    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_3PH_LV,
        control_mode=MODE_REMOTE_RAM,
    )

    # Add Solarman entry in SETUP_RETRY (no runtime_data)
    solarman_entry = MockConfigEntry(
        domain="solarman",
        entry_id="solarman_offline_entry",
        state=ConfigEntryState.SETUP_RETRY,
    )
    solarman_entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        with pytest.raises(DeyeConnectionError, match="offline or reconnecting"):
            await executor._write_register(REG_REMOTE_ENABLE, 1)

        # Ensure we NEVER aggressively reload Solarman during network drops
        assert not mock_reload.called


@pytest.mark.asyncio
async def test_sofar_passive_mode_execution_and_lifecycle(hass):
    """Verify Sofar Passive Mode execution, power setpoint in Watts, heartbeat, and self-use shutdown."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_SOFAR_HYD_3PH,
        control_mode=MODE_REMOTE_RAM,
        battery_voltage_v=300.0,
        rated_power_w=15000,
    )
    assert executor.is_sofar is True

    tariff = PGETariffCalculator()
    plan = PlanResult("night", 80.0, 10, True, 95.0, 8.0, 1.0, "Test")

    written_registers: dict[int, int] = {}

    async def mock_solarman(call):
        reg = call.data.get("address", call.data.get("register"))
        val = call.data["values"][0]
        written_registers[reg] = val

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    # 1. Apply plan: sets Work Mode to Passive (3) and Power in Watts (10A * 300V = 3000W)
    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=tariff,
        simulation_mode=False,
        current_soc=40.0,
    )
    assert res is True
    assert executor.remote_mode_active is True
    assert written_registers.get(REG_SOFAR_WORK_MODE) == SOFAR_MODE_PASSIVE  # 3
    assert written_registers.get(REG_SOFAR_PASSIVE_POWER) == 3000  # 3000 W

    # 2. Heartbeat keepalive: refreshes passive work mode (3)
    written_registers.clear()
    assert await executor.async_refresh_watchdog() is True
    assert written_registers.get(REG_SOFAR_WORK_MODE) == SOFAR_MODE_PASSIVE

    # 3. Shutdown: sets Power to 0, returns Work Mode to Self-Use (0)
    written_registers.clear()
    assert await executor.async_disable_remote_mode() is True
    assert executor.remote_mode_active is False
    assert written_registers.get(REG_SOFAR_PASSIVE_POWER) == 0
    assert written_registers.get(REG_SOFAR_WORK_MODE) == SOFAR_MODE_SELF_USE  # 0


@pytest.mark.asyncio
async def test_sofar_simulation_mode_makes_no_writes(hass):
    """Verify that Sofar profile in simulation mode performs zero register writes."""
    executor = DeyeExecutor(
        hass=hass,
        inverter_profile=PROFILE_SOFAR_HYD_3PH,
        control_mode=MODE_REMOTE_RAM,
        battery_voltage_v=300.0,
    )

    written = []

    async def mock_solarman(call):
        written.append(call.data)

    hass.services.async_register("solarman", "write_multiple_registers", mock_solarman)

    plan = PlanResult("night", 90.0, 15, True, 95.0, 8.0, 1.0, "Test")
    res = await executor.async_apply_plan(
        plan=plan,
        tariff_calc=PGETariffCalculator(),
        simulation_mode=True,
        current_soc=45.0,
    )
    assert res is True
    assert len(written) == 0
    assert "Simulation" in executor.last_execution_status


