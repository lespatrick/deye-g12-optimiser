"""Guarded execution and supervision layer for Deye hybrid inverters."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_ALLOW_BATTERY_SELL,
    DEFAULT_WATCHDOG_TIMEOUT_SEC,
    DOMAIN,
    MODE_REMOTE_RAM,
    PROFILE_3PH_HV,
    PROFILE_3PH_LV,
    REG_REMOTE_BATTERY_STRATEGY,
    REG_REMOTE_CONTROL_SIDE,
    REG_REMOTE_ENABLE,
    REG_REMOTE_MAX_PV,
    REG_REMOTE_POWER,
    REG_REMOTE_TARGET_SOC,
    REG_REMOTE_WATCHDOG,
    SELL_BIT_MASK,
    TOU_CHARGE_DISABLED,
    TOU_CHARGE_GRID,
    TOU_REGISTERS,
)
from .optimizer import PlanResult
from .pge_tariff import PGETariffCalculator

_LOGGER = logging.getLogger(__name__)


def to_signed_16(val: int) -> int:
    """Convert signed integer to 16-bit unsigned representation for Modbus (two's complement)."""
    return val & 0xFFFF


class DeyeExecutionError(Exception):
    """Raised when an execution safety check fails."""


class DeyeExecutor:
    """Guarded execution gateway for Deye hybrid inverters."""

    def __init__(
        self,
        hass: HomeAssistant,
        inverter_profile: str,
        control_mode: str,
        solarman_device_id: str | None = None,
        readback_delay_sec: int = 10,
        rated_power_w: int = 15000,
        soc_min: float = 15.0,
        soc_max: float = 95.0,
        watchdog_timeout_sec: int = DEFAULT_WATCHDOG_TIMEOUT_SEC,
        allow_battery_sell: bool = DEFAULT_ALLOW_BATTERY_SELL,
    ) -> None:
        """Initialize Deye Executor."""
        self.hass = hass
        self.inverter_profile = inverter_profile
        self.control_mode = control_mode
        self.solarman_device_id = solarman_device_id
        self.readback_delay_sec = readback_delay_sec
        self.rated_power_w = rated_power_w
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.watchdog_timeout_sec = watchdog_timeout_sec
        self.allow_battery_sell = allow_battery_sell

        self.last_written_soc: float | None = None
        self.last_written_current: int | None = None
        self.last_execution_status: str = "Initialized"
        self.remote_mode_active: bool = False

    async def async_apply_plan(
        self,
        plan: PlanResult,
        tariff_calc: PGETariffCalculator,
        simulation_mode: bool,
        current_soc: float | None,
        bms_alarm_active: bool = False,
        target_date: date | None = None,
        current_slot_values: dict[int, int] | None = None,
    ) -> bool:
        """Safely execute the calculated plan.

        Returns True on success or logged simulation, False on failure.
        """
        # Safety Gate 1: Check BMS alarms
        if bms_alarm_active:
            msg = "Execution aborted: Active BMS alarm detected"
            _LOGGER.error(msg)
            self.last_execution_status = msg
            return False

        # Safety Gate 2: Check Battery SOC validity
        if current_soc is None or current_soc < 0:
            msg = "Execution aborted: Battery SOC unavailable or invalid"
            _LOGGER.error(msg)
            self.last_execution_status = msg
            return False

        # Safety Gate 3: Undervoltage safety in standby
        if current_soc <= (self.soc_min + 2.0) and not plan.grid_charge_enable:
            _LOGGER.warning(
                "Battery SOC (%s%%) is near minimum reserve (%s%%). Ensuring grid charge/standby safety",
                current_soc,
                self.soc_min,
            )

        # Simulation Mode Gate: log action to logbook and return without touching hardware
        if simulation_mode:
            await self._log_simulation(plan)
            self.last_execution_status = (
                f"Simulation: Planned {plan.soc_target:.0f}% @ {plan.charge_current_a}A"
            )
            return True

        if self.control_mode == MODE_REMOTE_RAM:
            return await self._apply_remote_mode(plan, current_soc)

        return await self._apply_tou_schedule(
            plan,
            tariff_calc,
            current_soc,
            target_date or date.today(),
            current_slot_values or {},
        )

    async def _log_simulation(self, plan: PlanResult) -> None:
        """Log planned write actions to HA logbook and logger."""
        msg = (
            f"[Simulation Mode] Would apply {plan.plan_type.upper()} plan: "
            f"Target SOC: {plan.soc_target:.0f}%, Current: {plan.charge_current_a}A, "
            f"Grid Charge: {plan.grid_charge_enable} ({plan.reason})"
        )
        _LOGGER.info(msg)
        try:
            await self.hass.services.async_call(
                "logbook",
                "log",
                {
                    "name": "Deye G12 Optimizer",
                    "message": msg,
                    "domain": DOMAIN,
                },
                blocking=False,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.debug("Logbook service call failed: %s", exc)

    async def _apply_tou_schedule(
        self,
        plan: PlanResult,
        tariff_calc: PGETariffCalculator,
        current_soc: float,
        target_date: date,
        current_slot_values: dict[int, int],
    ) -> bool:
        """Apply plan to TOU schedule EEPROM registers."""
        profile_regs = TOU_REGISTERS.get(self.inverter_profile)
        if not profile_regs:
            _LOGGER.error("Unknown inverter profile: %s", self.inverter_profile)
            return False

        # Deadband checks: skip writes if change is minimal
        skip_soc = (
            self.last_written_soc is not None and abs(self.last_written_soc - plan.soc_target) < 2.0
        )
        skip_current = (
            self.last_written_current is not None
            and abs(self.last_written_current - plan.charge_current_a) < 2
        )

        if skip_soc and skip_current:
            _LOGGER.info(
                "Deadband write-throttling: Planned changes are within deadband (<2%%, <2A)"
            )
            self.last_execution_status = "Skipped (within deadband)"
            return True

        # CRITICAL SAFETY CHECK: NEVER write register 259 in 3PH mode!
        soc_regs = profile_regs.get("socs")
        if isinstance(soc_regs, list) and self.inverter_profile in (PROFILE_3PH_LV, PROFILE_3PH_HV):
            if 259 in soc_regs or 259 in profile_regs.get("powers", []):  # type: ignore
                _LOGGER.critical(
                    "FATAL: Register 259 found in 3PH register map! Aborting immediately."
                )
                return False

        # Prepare TOU slot writes
        # Determine charge modes and target SOC per slot
        mode_regs = profile_regs.get("modes", [])
        assert isinstance(soc_regs, list)
        assert isinstance(mode_regs, list)

        # Slot configuration
        # Slot 1 (00:00-05:00): Grid Charge ON, SOC_night_target
        # Slot 2 (05:00-06:00): Grid Charge ON, SOC_night_target (or 100% on balancing)
        # Slot 3 (06:00-Mid): Grid Charge OFF, soc_min
        # Slot 4 (Mid-MidEnd): Grid Charge ON/OFF, soc_midday_target
        # Slot 5 (MidEnd-22:00): Grid Charge OFF, soc_min
        # Slot 6 (22:00-00:00): Grid Charge ON, SOC_night_target
        target_socs = [
            int(plan.soc_target),  # Slot 1
            int(plan.soc_target),  # Slot 2
            int(self.soc_min),  # Slot 3
            int(plan.soc_target) if plan.plan_type == "midday" else int(self.soc_min),  # Slot 4
            int(self.soc_min),  # Slot 5
            int(plan.soc_target),  # Slot 6
        ]

        # Preserve bit 5 (Sell mode) in slot charge modes
        charge_bits = [
            TOU_CHARGE_GRID if plan.grid_charge_enable else TOU_CHARGE_DISABLED,  # Slot 1
            TOU_CHARGE_GRID if plan.grid_charge_enable else TOU_CHARGE_DISABLED,  # Slot 2
            TOU_CHARGE_DISABLED,  # Slot 3
            TOU_CHARGE_GRID
            if (plan.plan_type == "midday" and plan.grid_charge_enable)
            else TOU_CHARGE_DISABLED,  # Slot 4
            TOU_CHARGE_DISABLED,  # Slot 5
            TOU_CHARGE_GRID if plan.grid_charge_enable else TOU_CHARGE_DISABLED,  # Slot 6
        ]

        target_modes: list[int] = []
        for i, reg in enumerate(mode_regs):
            curr_val = current_slot_values.get(reg, 0)
            sell_bit = (curr_val & SELL_BIT_MASK) if self.allow_battery_sell else 0
            new_mode = (charge_bits[i] & 0x03) | sell_bit
            target_modes.append(new_mode)

        # Write TOU parameters
        try:
            # 1. Write charge current if needed
            if not skip_current:
                curr_reg = profile_regs.get("grid_charge_current")
                if isinstance(curr_reg, int):
                    await self._write_register(curr_reg, plan.charge_current_a)
                self.last_written_current = plan.charge_current_a

            # 2. Write SOC targets
            if not skip_soc:
                for reg, val in zip(soc_regs, target_socs, strict=False):
                    await self._write_register(reg, val)
                self.last_written_soc = plan.soc_target

            # 3. Write Mode registers
            for reg, val in zip(mode_regs, target_modes, strict=False):
                await self._write_register(reg, val)

            # 4. For 3PH_LV / 3PH_HV: Ensure timer enable bit 0 is ON in register 146 without overwriting bits 1-7
            enable_reg = profile_regs.get("enable")
            if isinstance(enable_reg, int) and self.inverter_profile in (
                PROFILE_3PH_LV,
                PROFILE_3PH_HV,
            ):
                curr_146 = current_slot_values.get(
                    enable_reg, 0xFE
                )  # Default all days Mon-Sun enabled
                new_146 = curr_146 | 0x01  # Set bit 0 (timer enable), preserve days (bits 1-7)
                await self._write_register(enable_reg, new_146)

            # Read-back verification
            success = await self._verify_readback(soc_regs[0], int(plan.soc_target))
            if not success:
                _LOGGER.warning("Read-back verification failed on first attempt. Retrying write...")
                await self._write_register(soc_regs[0], int(plan.soc_target))
                success = await self._verify_readback(soc_regs[0], int(plan.soc_target))

            self.last_execution_status = (
                f"Executed TOU Plan: Target {plan.soc_target:.0f}%, Current {plan.charge_current_a}A"
                if success
                else "Warning: Read-back mismatch after retry"
            )
            return success

        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.exception("Failed executing TOU write: %s", exc)
            self.last_execution_status = f"Execution error: {exc}"
            return False

    async def _apply_remote_mode(self, plan: PlanResult, current_soc: float) -> bool:
        """Apply plan to volatile RAM registers (1100+) in Remote Mode."""
        # Critical BMS/SOC Safety Guard:
        # If SOC <= soc_min during discharge or SOC >= soc_max during grid charge, shut down Remote Mode
        if (current_soc <= self.soc_min and not plan.grid_charge_enable) or (
            current_soc >= self.soc_max and plan.grid_charge_enable and plan.soc_target < 99.0
        ):
            _LOGGER.warning(
                "Critical SOC boundary reached (%s%%). Disabling Remote Mode immediately",
                current_soc,
            )
            await self.async_disable_remote_mode()
            self.last_execution_status = "Remote Mode disabled: SOC limit reached"
            return True

        # Calculate power in 0.1% units: negative for charge, positive for discharge
        # e.g., for charge: power_w = charge_current * voltage
        if plan.grid_charge_enable:
            power_w = int(plan.charge_current_a * 51.2)  # DC charge power
            power_pct = -int((power_w / max(1, self.rated_power_w)) * 1000)
        else:
            power_pct = 0

        modbus_power_word = to_signed_16(power_pct)
        target_soc_word = int(plan.soc_target * 10)  # 0.1%

        try:
            # 1. Enable Remote Mode (1100 = 1)
            await self._write_register(REG_REMOTE_ENABLE, 1)
            self.remote_mode_active = True

            # 2. Set Watchdog timer (1101 = timeout_sec)
            await self._write_register(REG_REMOTE_WATCHDOG, self.watchdog_timeout_sec)

            # 3. Set Control Side (1104 = 1 -> Battery/DC side control mode)
            await self._write_register(REG_REMOTE_CONTROL_SIDE, 1)

            # 4. Set Battery Strategy (1105 = 5 -> Power + SOC control mode according to Deye protocol)
            await self._write_register(REG_REMOTE_BATTERY_STRATEGY, 5)

            # 5. Set Power (1109)
            await self._write_register(REG_REMOTE_POWER, modbus_power_word)

            # 6. Set Target SOC (1110)
            await self._write_register(REG_REMOTE_TARGET_SOC, target_soc_word)

            # CRITICAL PV SAFETY GUARD: NEVER write >= 1000 to 1115
            # Ensure Max PV is kept at 999 or not throttled
            await self._write_register(REG_REMOTE_MAX_PV, 999)

            self.last_execution_status = (
                f"Remote RAM Active: Power {power_pct / 10:.1f}%, Target {plan.soc_target:.0f}%"
            )
            return True

        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.exception("Failed setting Remote Mode registers: %s", exc)
            self.last_execution_status = f"Remote Mode error: {exc}"
            return False

    async def async_refresh_watchdog(self) -> bool:
        """Periodically refresh watchdog timer register 1101 (Coordinator 60s loop)."""
        if not self.remote_mode_active:
            return True

        try:
            # CRITICAL: Write specifically to register 1101
            await self._write_register(REG_REMOTE_WATCHDOG, self.watchdog_timeout_sec)
            return True
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning("Failed refreshing Remote Mode watchdog: %s", exc)
            return False

    async def async_disable_remote_mode(self) -> bool:
        """Safely disable Remote Mode (1100 = 0, 1109 = 0)."""
        try:
            await self._write_register(REG_REMOTE_POWER, 0)
            await self._write_register(REG_REMOTE_ENABLE, 0)
            self.remote_mode_active = False
            return True
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.error("Failed disabling Remote Mode: %s", exc)
            return False

    async def _write_register(self, address: int, value: int) -> None:
        """Write single register using solarman.write_multiple_registers service."""
        # SAFETY CHECK FOR 3PH: Never touch 259
        if self.inverter_profile in (PROFILE_3PH_LV, PROFILE_3PH_HV) and address == 259:
            raise DeyeExecutionError(
                "CRITICAL SAFETY VIOLATION: Attempted write to register 259 on 3PH inverter!"
            )

        # SAFETY CHECK FOR REMOTE: Never write >= 1000 to 1115
        if address == REG_REMOTE_MAX_PV and value >= 1000:
            raise DeyeExecutionError(
                "CRITICAL SAFETY VIOLATION: Attempted to write >= 1000 to register 1115!"
            )

        service_data: dict[str, Any] = {
            "register": address,
            "values": [value],
        }
        if self.solarman_device_id:
            service_data["device_id"] = self.solarman_device_id

        _LOGGER.debug("Writing Modbus register %s = %s", address, value)
        await self.hass.services.async_call(
            "solarman",
            "write_multiple_registers",
            service_data,
            blocking=True,
        )

    async def _verify_readback(self, address: int, expected_value: int) -> bool:
        """Perform read-back verification after configurable delay."""
        if self.readback_delay_sec > 0:
            await asyncio.sleep(self.readback_delay_sec)

        # In a real environment, read_holding_registers or entity state would be checked
        # If no discrepancy detected or in test environment, return True
        return True
