"""Pure mathematical optimization model for Deye battery charging."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .const import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_VOLTAGE_V_LV,
    DEFAULT_ETA_CHG,
    DEFAULT_ETA_DIS,
    DEFAULT_ETA_PV,
    DEFAULT_MAX_USER_CHARGE_CURRENT_A,
    DEFAULT_MIN_CHARGE_CURRENT_A_LV,
    DEFAULT_RATED_POWER_W,
    DEFAULT_SAFETY_LOAD,
    DEFAULT_SAFETY_PV,
    DEFAULT_SOC_MAX,
    DEFAULT_SOC_MIN,
)


def _clip(val: float, min_val: float, max_val: float) -> float:
    """Clip val between min_val and max_val."""
    return max(min_val, min(max_val, val))


@dataclass(frozen=True)
class PlanResult:
    """Result of optimization plan calculation."""

    plan_type: str  # "night" or "midday"
    soc_target: float  # Percentage (e.g. 77.0)
    charge_current_a: int  # Amperes (e.g. 23)
    grid_charge_enable: bool  # True if grid charging is needed
    headroom_max_soc: float  # Max SOC to reserve room for PV surplus
    expected_load_kwh: float
    expected_pv_kwh: float
    reason: str  # Human-readable explanation


class DeyeG12Optimizer:
    """Pure mathematical optimization engine for Deye hybrid inverters."""

    def __init__(
        self,
        battery_capacity_kwh: float = DEFAULT_BATTERY_CAPACITY_KWH,
        battery_voltage_v: float = DEFAULT_BATTERY_VOLTAGE_V_LV,
        rated_power_w: int = DEFAULT_RATED_POWER_W,
        soc_min_pct: float = DEFAULT_SOC_MIN,
        soc_max_pct: float = DEFAULT_SOC_MAX,
        eta_chg: float = DEFAULT_ETA_CHG,
        eta_dis: float = DEFAULT_ETA_DIS,
        eta_pv: float = DEFAULT_ETA_PV,
        safety_pv: float = DEFAULT_SAFETY_PV,
        safety_load: float = DEFAULT_SAFETY_LOAD,
        min_charge_current_a: int = DEFAULT_MIN_CHARGE_CURRENT_A_LV,
        max_user_charge_current_a: int = DEFAULT_MAX_USER_CHARGE_CURRENT_A,
    ) -> None:
        """Initialize the optimization model."""
        self.battery_capacity_kwh = battery_capacity_kwh
        self.battery_voltage_v = battery_voltage_v
        self.rated_power_w = rated_power_w
        self.soc_min_pct = soc_min_pct
        self.soc_max_pct = soc_max_pct
        self.eta_chg = eta_chg
        self.eta_dis = eta_dis
        self.eta_pv = eta_pv
        self.safety_pv = safety_pv
        self.safety_load = safety_load
        self.min_charge_current_a = min_charge_current_a
        self.max_user_charge_current_a = max_user_charge_current_a

    def calculate_night_plan(
        self,
        soc_now: float,
        e_load_am: float,
        e_pv_am: float,
        bms_max_current_a: int | None = None,
        force_balancing_100: bool = False,
        emergency_reserve_100: bool = False,
    ) -> PlanResult:
        """Calculate optimal overnight charging plan.

        Evaluated before night off-peak window with effective charge duration T_eff = 7.5h.
        """
        allowed_max_current = self.max_user_charge_current_a
        if bms_max_current_a is not None and bms_max_current_a > 0:
            allowed_max_current = min(allowed_max_current, bms_max_current_a)

        # 1. Emergency Reserve Override
        if emergency_reserve_100:
            return PlanResult(
                plan_type="night",
                soc_target=100.0,
                charge_current_a=allowed_max_current,
                grid_charge_enable=True,
                headroom_max_soc=100.0,
                expected_load_kwh=e_load_am,
                expected_pv_kwh=e_pv_am,
                reason="Emergency Reserve (Storm Watch 100%) active",
            )

        # 2. Cell Balancing Override
        if force_balancing_100:
            return PlanResult(
                plan_type="night",
                soc_target=100.0,
                charge_current_a=allowed_max_current,
                grid_charge_enable=True,
                headroom_max_soc=100.0,
                expected_load_kwh=e_load_am,
                expected_pv_kwh=e_pv_am,
                reason="BMS cell balancing cycle (100% target)",
            )

        # 3. Standard Night Optimization
        pv_eff = max(0.0, e_pv_am) * self.safety_pv
        load_eff = max(0.0, e_load_am) * self.safety_load

        e_def_ac = max(0.0, load_eff - pv_eff)
        e_req_dc = e_def_ac / max(0.01, self.eta_dis)

        # PV Headroom Reservation
        e_surplus_dc = max(0.0, (pv_eff - load_eff) * self.eta_pv)
        soc_headroom_max = _clip(
            self.soc_max_pct - (e_surplus_dc / self.battery_capacity_kwh) * 100.0,
            self.soc_min_pct,
            self.soc_max_pct,
        )

        soc_raw = self.soc_min_pct + (e_req_dc / self.battery_capacity_kwh) * 100.0
        soc_target = float(
            round(_clip(min(soc_raw, soc_headroom_max), self.soc_min_pct, self.soc_max_pct))
        )

        # Deadband check: if target doesn't require >= 2% additional charge
        if (soc_target - soc_now) < 2.0:
            return PlanResult(
                plan_type="night",
                soc_target=soc_target,
                charge_current_a=self.min_charge_current_a,
                grid_charge_enable=False,
                headroom_max_soc=round(soc_headroom_max, 1),
                expected_load_kwh=e_load_am,
                expected_pv_kwh=e_pv_am,
                reason=f"Current SOC ({soc_now:.1f}%) meets target ({soc_target:.0f}%), grid charge disabled",
            )

        # Calculate required charging power and current over 7.5h
        t_eff_hours = 7.5
        e_chg_dc = ((soc_target - soc_now) / 100.0) * self.battery_capacity_kwh
        p_chg_dc_w = (e_chg_dc * 1000.0) / t_eff_hours
        raw_current_a = math.ceil(p_chg_dc_w / max(1.0, self.battery_voltage_v))
        i_chg = int(
            _clip(
                float(raw_current_a), float(self.min_charge_current_a), float(allowed_max_current)
            )
        )

        return PlanResult(
            plan_type="night",
            soc_target=soc_target,
            charge_current_a=i_chg,
            grid_charge_enable=True,
            headroom_max_soc=round(soc_headroom_max, 1),
            expected_load_kwh=e_load_am,
            expected_pv_kwh=e_pv_am,
            reason=f"Calculated target {soc_target:.0f}% at {i_chg}A for morning peak cover",
        )

    def calculate_midday_plan(
        self,
        soc_now: float,
        e_load_pm: float,
        e_pv_pm: float,
        bms_max_current_a: int | None = None,
        emergency_reserve_100: bool = False,
    ) -> PlanResult:
        """Calculate midday off-peak charging plan.

        Evaluated before midday off-peak window with effective charge duration T_eff = 1.75h.
        """
        allowed_max_current = self.max_user_charge_current_a
        if bms_max_current_a is not None and bms_max_current_a > 0:
            allowed_max_current = min(allowed_max_current, bms_max_current_a)

        if emergency_reserve_100:
            return PlanResult(
                plan_type="midday",
                soc_target=100.0,
                charge_current_a=allowed_max_current,
                grid_charge_enable=True,
                headroom_max_soc=100.0,
                expected_load_kwh=e_load_pm,
                expected_pv_kwh=e_pv_pm,
                reason="Emergency Reserve (Storm Watch 100%) active",
            )

        pv_eff = max(0.0, e_pv_pm) * self.safety_pv
        load_eff = max(0.0, e_load_pm) * self.safety_load

        e_def_ac = max(0.0, load_eff - pv_eff)
        e_req_dc = e_def_ac / max(0.01, self.eta_dis)

        soc_raw = self.soc_min_pct + (e_req_dc / self.battery_capacity_kwh) * 100.0
        soc_target = float(round(_clip(soc_raw, self.soc_min_pct, self.soc_max_pct)))

        # During midday, preserve battery if current SOC is already above target
        effective_slot_soc = max(soc_now, soc_target)

        if (soc_target - soc_now) < 2.0:
            return PlanResult(
                plan_type="midday",
                soc_target=effective_slot_soc,
                charge_current_a=self.min_charge_current_a,
                grid_charge_enable=False,
                headroom_max_soc=self.soc_max_pct,
                expected_load_kwh=e_load_pm,
                expected_pv_kwh=e_pv_pm,
                reason=f"Current SOC ({soc_now:.1f}%) sufficient for evening peak, grid charge disabled",
            )

        t_eff_hours = 1.75
        e_chg_dc = ((soc_target - soc_now) / 100.0) * self.battery_capacity_kwh
        p_chg_dc_w = (e_chg_dc * 1000.0) / t_eff_hours
        raw_current_a = math.ceil(p_chg_dc_w / max(1.0, self.battery_voltage_v))
        i_chg = int(
            _clip(
                float(raw_current_a), float(self.min_charge_current_a), float(allowed_max_current)
            )
        )

        return PlanResult(
            plan_type="midday",
            soc_target=soc_target,
            charge_current_a=i_chg,
            grid_charge_enable=True,
            headroom_max_soc=self.soc_max_pct,
            expected_load_kwh=e_load_pm,
            expected_pv_kwh=e_pv_pm,
            reason=f"Midday charge target {soc_target:.0f}% at {i_chg}A for evening peak cover",
        )
