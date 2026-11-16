"""DataUpdateCoordinator and scheduler for Deye G12 Optimizer."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_BALANCING_INTERVAL_DAYS,
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
    CONF_MIN_CHARGE_CURRENT_A,
    CONF_OFF_PEAK_PRICE,
    CONF_OPTIMIZATION_STRATEGY,
    CONF_PEAK_PRICE,
    CONF_PGE_SEASONAL_SHIFT,
    CONF_PGE_TARIFF,
    CONF_PV_FORECAST_ENTITY,
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
    DEFAULT_BALANCING_INTERVAL_DAYS,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_VOLTAGE_V_HV,
    DEFAULT_BATTERY_VOLTAGE_V_LV,
    DEFAULT_ETA_CHG,
    DEFAULT_ETA_DIS,
    DEFAULT_ETA_PV,
    DEFAULT_MAX_USER_CHARGE_CURRENT_A,
    DEFAULT_MIN_CHARGE_CURRENT_A_HV,
    DEFAULT_MIN_CHARGE_CURRENT_A_LV,
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
    MODE_TOU_SCHEDULE,
    PROFILE_3PH_HV,
    PROFILE_3PH_LV,
    STRATEGY_MAX_CHARGE_CHEAP,
    TARIFF_G12W,
)
from .executor import DeyeExecutor
from .optimizer import DeyeG12Optimizer, PlanResult
from .pge_tariff import WARSAW_TZ, PGETariffCalculator
from .pv_forecast import PVForecastParser
from .recorder_stats import RecorderConsumptionEstimator

_LOGGER = logging.getLogger(__name__)


class DeyeG12Coordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator handling state polling, optimization decisions, and scheduling."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize Deye G12 Coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=60),
        )
        self.entry = entry
        config = {**entry.data, **entry.options}

        self.inverter_profile = config.get(CONF_INVERTER_PROFILE, PROFILE_3PH_LV)
        self.control_mode = config.get(CONF_CONTROL_MODE, MODE_TOU_SCHEDULE)
        self.solarman_device_id = config.get(CONF_SOLARMAN_DEVICE_ID) or None

        # Entity IDs
        self.battery_soc_entity = config.get(CONF_BATTERY_SOC_ENTITY)
        self.total_load_entity = config.get(CONF_TOTAL_LOAD_ENTITY)
        self.bms_alarm_entity = config.get(CONF_BMS_ALARM_ENTITY)
        self.bms_ccl_entity = config.get(CONF_BMS_CCL_ENTITY)
        self.pv_forecast_entity = config.get(CONF_PV_FORECAST_ENTITY)

        # System parameters
        self.battery_capacity_kwh = float(
            config.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        )
        default_voltage = (
            DEFAULT_BATTERY_VOLTAGE_V_HV
            if self.inverter_profile == PROFILE_3PH_HV
            else DEFAULT_BATTERY_VOLTAGE_V_LV
        )
        self.battery_voltage_v = float(config.get(CONF_BATTERY_VOLTAGE_V, default_voltage))
        self.rated_power_w = int(config.get(CONF_RATED_POWER_W, DEFAULT_RATED_POWER_W))

        # Runtime switches and settings
        self.automation_active: bool = True
        self.simulation_mode: bool = True  # Default True for safety on fresh install
        self.emergency_reserve: bool = False

        self.soc_min: float = float(config.get(CONF_SOC_MIN, DEFAULT_SOC_MIN))
        self.soc_max: float = float(config.get(CONF_SOC_MAX, DEFAULT_SOC_MAX))
        default_min_curr = (
            DEFAULT_MIN_CHARGE_CURRENT_A_HV
            if self.inverter_profile == PROFILE_3PH_HV
            else DEFAULT_MIN_CHARGE_CURRENT_A_LV
        )
        self.min_charge_current_a = int(config.get(CONF_MIN_CHARGE_CURRENT_A, default_min_curr))
        self.max_user_charge_current_a = int(
            config.get(CONF_MAX_USER_CHARGE_CURRENT_A, DEFAULT_MAX_USER_CHARGE_CURRENT_A)
        )
        self.balancing_interval_days = int(
            config.get(CONF_BALANCING_INTERVAL_DAYS, DEFAULT_BALANCING_INTERVAL_DAYS)
        )

        self.peak_price = float(config.get(CONF_PEAK_PRICE, DEFAULT_PEAK_PRICE))
        self.off_peak_price = float(config.get(CONF_OFF_PEAK_PRICE, DEFAULT_OFF_PEAK_PRICE))

        # Core logic engines
        self.tariff = PGETariffCalculator(
            tariff_variant=config.get(CONF_PGE_TARIFF, TARIFF_G12W),
            pge_seasonal_shift=config.get(CONF_PGE_SEASONAL_SHIFT, True),
            meter_fixed_winter_time=config.get(CONF_METER_FIXED_WINTER_TIME, False),
        )

        self.pv_parser = PVForecastParser(
            use_pessimistic_blend=config.get(CONF_USE_PESSIMISTIC_BLEND, False)
        )

        self.recorder_estimator = (
            RecorderConsumptionEstimator(hass, self.total_load_entity)
            if self.total_load_entity
            else None
        )

        self.optimizer = DeyeG12Optimizer(
            battery_capacity_kwh=self.battery_capacity_kwh,
            battery_voltage_v=self.battery_voltage_v,
            rated_power_w=self.rated_power_w,
            soc_min_pct=self.soc_min,
            soc_max_pct=self.soc_max,
            eta_chg=float(config.get(CONF_ETA_CHG, DEFAULT_ETA_CHG)),
            eta_dis=float(config.get(CONF_ETA_DIS, DEFAULT_ETA_DIS)),
            eta_pv=float(config.get(CONF_ETA_PV, DEFAULT_ETA_PV)),
            safety_pv=float(config.get(CONF_SAFETY_PV, DEFAULT_SAFETY_PV)),
            safety_load=float(config.get(CONF_SAFETY_LOAD, DEFAULT_SAFETY_LOAD)),
            min_charge_current_a=self.min_charge_current_a,
            max_user_charge_current_a=self.max_user_charge_current_a,
            strategy=config.get(CONF_OPTIMIZATION_STRATEGY, STRATEGY_MAX_CHARGE_CHEAP),
        )

        self.executor = DeyeExecutor(
            hass=hass,
            inverter_profile=self.inverter_profile,
            control_mode=self.control_mode,
            solarman_device_id=self.solarman_device_id,
            readback_delay_sec=int(config.get(CONF_READBACK_DELAY_SEC, DEFAULT_READBACK_DELAY_SEC)),
            rated_power_w=self.rated_power_w,
            soc_min=self.soc_min,
            soc_max=self.soc_max,
            watchdog_timeout_sec=int(
                config.get(CONF_WATCHDOG_TIMEOUT_SEC, DEFAULT_WATCHDOG_TIMEOUT_SEC)
            ),
        )

        # Internal state
        self.last_full_soc_time: datetime = datetime.now(WARSAW_TZ)
        self.current_plan: PlanResult | None = None
        self.last_arbitrage_savings: float = 0.0
        self._unsub_scheduled_triggers: list[Any] = []

    async def async_setup(self) -> None:
        """Set up scheduled triggers for plan evaluation."""
        self._schedule_next_triggers()
        # Immediately compute initial plan so sensors show real values on startup
        now = datetime.now(WARSAW_TZ)
        plan_type = "night" if now.hour >= 15 or now.hour < 6 else "midday"
        self.hass.async_create_task(self.async_evaluate_and_apply(plan_type))

    def _schedule_next_triggers(self) -> None:
        """Schedule future point-in-time plan evaluations."""
        for unsub in self._unsub_scheduled_triggers:
            unsub()
        self._unsub_scheduled_triggers.clear()

        now = datetime.now(WARSAW_TZ)
        today = now.date()

        # Schedule evaluations 5 minutes before off-peak windows:
        # 1. Night plan evaluation at 21:55 (or adjusted for meter quirk)
        night_start_h, _ = self.tariff.get_night_off_peak_hours(now)
        night_eval_h = (night_start_h - 1) % 24
        night_time = datetime.combine(today, time(night_eval_h, 55), tzinfo=WARSAW_TZ)
        if night_time <= now:
            night_time += timedelta(days=1)

        # 2. Midday plan evaluation 5 minutes before midday off-peak
        mid_start_h, _ = self.tariff.get_midday_off_peak_hours(today, now)
        mid_eval_h = (mid_start_h - 1) % 24
        mid_time = datetime.combine(today, time(mid_eval_h, 55), tzinfo=WARSAW_TZ)
        if mid_time <= now:
            mid_time += timedelta(days=1)

        @callback
        def _on_night_trigger(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("night"))
            self._schedule_next_triggers()

        @callback
        def _on_midday_trigger(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("midday"))
            self._schedule_next_triggers()

        self._unsub_scheduled_triggers.append(
            async_track_point_in_time(self.hass, _on_night_trigger, night_time)
        )
        self._unsub_scheduled_triggers.append(
            async_track_point_in_time(self.hass, _on_midday_trigger, mid_time)
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest states, refresh watchdog, and update sensor data."""
        try:
            now = datetime.now(WARSAW_TZ)
            current_soc = self._get_current_battery_soc()

            # Track last full SOC (>= 99%) for cell balancing
            if current_soc is not None and current_soc >= 99.0:
                self.last_full_soc_time = now

            # Refresh Remote Mode watchdog if active
            await self.executor.async_refresh_watchdog()

            tariff_zone = self.tariff.get_tariff_zone(now)

            return {
                "current_soc": current_soc,
                "current_tariff_zone": tariff_zone,
                "planned_soc_target": self.current_plan.soc_target
                if self.current_plan
                else self.soc_min,
                "planned_charge_current": self.current_plan.charge_current_a
                if self.current_plan
                else self.min_charge_current_a,
                "grid_charge_enable": self.current_plan.grid_charge_enable
                if self.current_plan
                else False,
                "headroom_max_soc": self.current_plan.headroom_max_soc
                if self.current_plan
                else self.soc_max,
                "expected_morning_load": self.current_plan.expected_load_kwh
                if self.current_plan
                else 0.0,
                "expected_morning_pv": self.current_plan.expected_pv_kwh
                if self.current_plan
                else 0.0,
                "daily_arbitrage_savings": self.last_arbitrage_savings,
                "last_execution_status": self.executor.last_execution_status,
                "automation_active": self.automation_active,
                "simulation_mode": self.simulation_mode,
                "emergency_reserve": self.emergency_reserve,
            }
        except Exception as exc:
            raise UpdateFailed(f"Error updating Deye coordinator: {exc}") from exc

    def _get_current_battery_soc(self) -> float | None:
        """Read current battery SOC from configured entity."""
        if not self.battery_soc_entity:
            return None
        state = self.hass.states.get(self.battery_soc_entity)
        if state is None or state.state in (None, "", "unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError, TypeError:
            return None

    def _is_bms_alarm_active(self) -> bool:
        """Check if BMS alarm sensor reports alarm."""
        if not self.bms_alarm_entity:
            return False
        state = self.hass.states.get(self.bms_alarm_entity)
        if state is None:
            return False
        return state.state.lower() in ("on", "true", "alarm", "warning", "1", "fault")

    def _get_bms_max_charge_current(self) -> int | None:
        """Get BMS charge current limit if available."""
        if not self.bms_ccl_entity:
            return None
        state = self.hass.states.get(self.bms_ccl_entity)
        if state is None:
            return None
        try:
            return int(float(state.state))
        except ValueError, TypeError:
            return None

    async def async_evaluate_and_apply(self, plan_type: str = "night") -> bool:
        """Evaluate optimization model and apply plan via executor."""
        if not self.automation_active:
            _LOGGER.info("Automation Active switch is OFF, skipping optimization cycle")
            self.executor.last_execution_status = "Skipped (Automation Inactive)"
            self.async_set_updated_data(self.data or {})
            return False

        now = datetime.now(WARSAW_TZ)
        current_soc = self._get_current_battery_soc() or self.soc_min
        bms_alarm = self._is_bms_alarm_active()
        bms_ccl = self._get_bms_max_charge_current()

        # Check cell balancing interval
        days_since_full = (now - self.last_full_soc_time).total_seconds() / 86400.0
        force_balancing = days_since_full >= self.balancing_interval_days

        if plan_type == "night":
            target_date = now.date() + timedelta(days=1)
            start_m, end_m = self.tariff.get_morning_peak_window(target_date, now)

            # Load estimate
            e_load = 0.0
            if self.recorder_estimator:
                e_load = await self.recorder_estimator.async_get_window_consumption(
                    start_m.hour, end_m.hour, now
                )

            # PV forecast
            e_pv, _ = self.pv_parser.get_forecast_energy_kwh(
                self.hass, self.pv_forecast_entity, start_m, end_m
            )

            plan = self.optimizer.calculate_night_plan(
                soc_now=current_soc,
                e_load_am=e_load,
                e_pv_am=e_pv,
                bms_max_current_a=bms_ccl,
                force_balancing_100=force_balancing,
                emergency_reserve_100=self.emergency_reserve,
            )
        else:
            target_date = now.date()
            start_e, end_e = self.tariff.get_evening_peak_window(target_date, now)

            e_load = 0.0
            if self.recorder_estimator:
                e_load = await self.recorder_estimator.async_get_window_consumption(
                    start_e.hour, end_e.hour, now
                )

            e_pv, _ = self.pv_parser.get_forecast_energy_kwh(
                self.hass, self.pv_forecast_entity, start_e, end_e
            )

            plan = self.optimizer.calculate_midday_plan(
                soc_now=current_soc,
                e_load_pm=e_load,
                e_pv_pm=e_pv,
                bms_max_current_a=bms_ccl,
                emergency_reserve_100=self.emergency_reserve,
            )

        self.current_plan = plan

        # Calculate estimated daily arbitrage savings
        if plan.grid_charge_enable and plan.soc_target > current_soc:
            charged_kwh = ((plan.soc_target - current_soc) / 100.0) * self.battery_capacity_kwh
            price_diff = max(0.0, self.peak_price - self.off_peak_price)
            self.last_arbitrage_savings = round(charged_kwh * price_diff, 2)
        else:
            self.last_arbitrage_savings = 0.0

        success = await self.executor.async_apply_plan(
            plan=plan,
            tariff_calc=self.tariff,
            simulation_mode=self.simulation_mode,
            current_soc=current_soc,
            bms_alarm_active=bms_alarm,
            target_date=target_date,
        )

        await self.async_request_refresh()
        return success

    async def async_unload(self) -> None:
        """Unload coordinator and clean up background listeners."""
        for unsub in self._unsub_scheduled_triggers:
            unsub()
        self._unsub_scheduled_triggers.clear()
        if self.control_mode == "remote_mode_ram" and self.executor.remote_mode_active:
            await self.executor.async_disable_remote_mode()
