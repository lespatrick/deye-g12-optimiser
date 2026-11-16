"""Tests for Deye G12 mathematical optimization model."""

from __future__ import annotations

from custom_components.deye_g12_opt.optimizer import DeyeG12Optimizer


def test_winter_cloudy_scenario():
    """Verify winter cloudy scenario: 15 kWh, 51.2V, SOC=18%, load=8.5 kWh, pv=0.8 kWh."""
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
    )

    plan = optimizer.calculate_night_plan(
        soc_now=18.0,
        e_load_am=8.5,
        e_pv_am=0.8,
    )

    # Expected: target ~76-77%, current ~23A, grid_charge_enable=True
    assert plan.soc_target in (76.0, 77.0)
    assert plan.charge_current_a in (22, 23)
    assert plan.grid_charge_enable is True


def test_spring_mixed_scenario():
    """Verify spring mixed scenario: SOC=25%, load=6.0 kWh, pv=4.5 kWh."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=95.0,
        min_charge_current_a=5,
    )

    plan = optimizer.calculate_night_plan(
        soc_now=25.0,
        e_load_am=6.0,
        e_pv_am=4.5,
    )

    # Moderate target ~35%, current clamped to min 5A
    assert plan.soc_target in (34.0, 35.0)
    assert plan.charge_current_a == 5
    assert plan.grid_charge_enable is True


def test_summer_sunny_scenario_headroom():
    """Verify summer sunny scenario: headroom caps target and grid charge is disabled."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=95.0,
    )

    plan = optimizer.calculate_night_plan(
        soc_now=35.0,
        e_load_am=4.5,
        e_pv_am=14.0,
    )

    # High surplus should reserve headroom and avoid grid charging
    assert plan.grid_charge_enable is False
    assert plan.headroom_max_soc < 60.0
    assert plan.soc_target == 15.0  # clipped to soc_min since surplus covers load


def test_bms_current_clamping():
    """Verify BMS max charge current clamps the calculated charge current."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        max_user_charge_current_a=80,
    )

    # In winter cloudy scenario, calculated current would be ~23A
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


def test_midday_plan():
    """Verify midday plan calculation."""
    optimizer = DeyeG12Optimizer(
        battery_capacity_kwh=15.0,
        battery_voltage_v=51.2,
        soc_min_pct=15.0,
        soc_max_pct=95.0,
    )

    # Midday scenario: low remaining PV (0.5 kWh), high evening load (5.0 kWh), current SOC=20%
    plan = optimizer.calculate_midday_plan(
        soc_now=20.0,
        e_load_pm=5.0,
        e_pv_pm=0.5,
    )
    assert plan.plan_type == "midday"
    assert plan.grid_charge_enable is True
    assert plan.soc_target > 20.0

    # Midday scenario: SOC already high (70%), low load (2.0 kWh), high PV (6.0 kWh)
    plan_high = optimizer.calculate_midday_plan(
        soc_now=70.0,
        e_load_pm=2.0,
        e_pv_pm=6.0,
    )
    assert plan_high.grid_charge_enable is False
    assert plan_high.soc_target == 70.0

