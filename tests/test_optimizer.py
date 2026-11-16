"""Tests for Deye G12 mathematical optimization model."""

from __future__ import annotations

from custom_components.deye_g12_opt.const import (
    STRATEGY_LEAN_PEAK_COVER,
    STRATEGY_MAX_CHARGE_CHEAP,
)
from custom_components.deye_g12_opt.optimizer import DeyeG12Optimizer


def test_strategy_max_charge_cheap_cloudy_day():
    """Verify cloudy day charges battery to 100% (or soc_max) overnight."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        rated_power_w=15000,
        soc_min_pct=15.0,
        soc_max_pct=100.0,
        strategy=STRATEGY_MAX_CHARGE_CHEAP,
    )

    # Cloudy day: low PV (0.5 kWh), regular load (8.0 kWh), current SOC 20%
    plan = optimizer.calculate_night_plan(
        soc_now=20.0,
        e_load_am=8.0,
        e_pv_am=0.5,
    )

    # Battery should target 100% full charge from cheap off-peak grid
    assert plan.soc_target == 100.0
    assert plan.grid_charge_enable is True
    assert plan.charge_current_a > 15


def test_strategy_max_charge_cheap_mixed_day():
    """Verify mixed day: charges to ~70-80% overnight, leaving headroom for solar to reach 100%."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=100.0,
        strategy=STRATEGY_MAX_CHARGE_CHEAP,
    )

    # Mixed day: 11.47 kWh PV, 6.0 kWh daytime load, current SOC 25%
    plan = optimizer.calculate_night_plan(
        soc_now=25.0,
        e_load_am=6.0,
        e_pv_am=11.47,
    )

    # Expected solar surplus will top up to 100%, overnight target is high (~75-85%)
    assert 70.0 <= plan.soc_target <= 85.0
    assert plan.grid_charge_enable is True
    assert "Expected solar" in plan.reason


def test_strategy_max_charge_cheap_sunny_day():
    """Verify very sunny day: reserves full headroom for solar, skips grid charge."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=100.0,
        strategy=STRATEGY_MAX_CHARGE_CHEAP,
    )

    # Very sunny summer day: 25 kWh PV, 5 kWh load, current SOC 30%
    plan = optimizer.calculate_night_plan(
        soc_now=30.0,
        e_load_am=5.0,
        e_pv_am=25.0,
    )

    # Solar will completely charge battery, target is at minimum reserve (15%)
    assert plan.soc_target == 15.0
    assert plan.grid_charge_enable is False


def test_strategy_max_charge_cheap_midday():
    """Verify midday off-peak tops up battery to enter evening peak near 100%."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=100.0,
        strategy=STRATEGY_MAX_CHARGE_CHEAP,
    )

    # Midday: SOC down to 40%, only 1.0 kWh afternoon PV left, evening load 6.0 kWh
    plan = optimizer.calculate_midday_plan(
        soc_now=40.0,
        e_load_pm=6.0,
        e_pv_pm=1.0,
    )

    assert plan.grid_charge_enable is True
    assert plan.soc_target >= 90.0


def test_legacy_lean_strategy_scenarios():
    """Verify legacy lean peak cover strategy still works if selected."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        rated_power_w=15000,
        soc_min_pct=15.0,
        soc_max_pct=95.0,
        eta_chg=0.94,
        eta_dis=0.94,
        eta_pv=0.97,
        safety_pv=0.85,
        safety_load=1.10,
        min_charge_current_a=5,
        max_user_charge_current_a=80,
        strategy=STRATEGY_LEAN_PEAK_COVER,
    )

    # Winter cloudy in lean mode
    plan_winter = optimizer.calculate_night_plan(
        soc_now=18.0,
        e_load_am=8.5,
        e_pv_am=0.8,
    )
    assert plan_winter.soc_target in (76.0, 77.0)
    assert plan_winter.charge_current_a in (22, 23)

    # Spring mixed in lean mode
    plan_spring = optimizer.calculate_night_plan(
        soc_now=25.0,
        e_load_am=6.0,
        e_pv_am=4.5,
    )
    assert plan_spring.soc_target in (34.0, 35.0)
    assert plan_spring.charge_current_a == 5


def test_bms_current_clamping():
    """Verify BMS max charge current clamps the calculated charge current."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        max_user_charge_current_a=80,
        strategy=STRATEGY_MAX_CHARGE_CHEAP,
    )

    plan = optimizer.calculate_night_plan(
        soc_now=18.0,
        e_load_am=8.5,
        e_pv_am=0.8,
        bms_max_current_a=15,  # BMS limits to 15A
    )

    assert plan.charge_current_a == 15


def test_force_balancing_and_emergency_reserve():
    """Verify cell balancing and emergency reserve override targets to 100%."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        max_user_charge_current_a=60,
    )

    plan_bal = optimizer.calculate_night_plan(
        soc_now=50.0,
        e_load_am=2.0,
        e_pv_am=10.0,
        force_balancing_100=True,
    )
    assert plan_bal.soc_target == 100.0
    assert plan_bal.charge_current_a == 60
    assert plan_bal.grid_charge_enable is True

    plan_emerg = optimizer.calculate_night_plan(
        soc_now=50.0,
        e_load_am=2.0,
        e_pv_am=10.0,
        emergency_reserve_100=True,
    )
    assert plan_emerg.soc_target == 100.0
    assert plan_emerg.charge_current_a == 60
    assert plan_emerg.grid_charge_enable is True
