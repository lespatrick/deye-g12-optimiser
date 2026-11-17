"""DataUpdateCoordinator and scheduler for Deye G12 Optimizer."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_ALLOW_BATTERY_SELL,
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
    DEFAULT_ALLOW_BATTERY_SELL,
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
    MODE_REMOTE_RAM,
    MODE_TOU_SCHEDULE,
    PROFILE_3PH_HV,
    PROFILE_3PH_LV,
    PROFILE_SOFAR_HYD_3PH,
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
        self.pv_forecast_today_entity = config.get(CONF_PV_FORECAST_TODAY_ENTITY) or config.get(
            CONF_PV_FORECAST_ENTITY
        )
        self.pv_forecast_tomorrow_entity = config.get(CONF_PV_FORECAST_TOMORROW_ENTITY)
        self.pv_forecast_entity = (
            self.pv_forecast_today_entity or self.pv_forecast_tomorrow_entity
        )

        # System parameters
        self.battery_capacity_kwh = float(
            config.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        )
        default_voltage = (
            DEFAULT_BATTERY_VOLTAGE_V_HV
            if self.inverter_profile in (PROFILE_3PH_HV, PROFILE_SOFAR_HYD_3PH)
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
            if self.inverter_profile in (PROFILE_3PH_HV, PROFILE_SOFAR_HYD_3PH)
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

        raw_watchdog = int(
            config.get(CONF_WATCHDOG_TIMEOUT_SEC, DEFAULT_WATCHDOG_TIMEOUT_SEC)
        )
        self.watchdog_timeout_sec = max(1800, raw_watchdog)

        self.executor = DeyeExecutor(
            hass=hass,
            inverter_profile=self.inverter_profile,
            control_mode=self.control_mode,
            solarman_device_id=self.solarman_device_id,
            readback_delay_sec=int(config.get(CONF_READBACK_DELAY_SEC, DEFAULT_READBACK_DELAY_SEC)),
            rated_power_w=self.rated_power_w,
            soc_min=self.soc_min,
            soc_max=self.soc_max,
            watchdog_timeout_sec=self.watchdog_timeout_sec,
            allow_battery_sell=bool(
                config.get(CONF_ALLOW_BATTERY_SELL, DEFAULT_ALLOW_BATTERY_SELL)
            ),
            battery_voltage_v=self.battery_voltage_v,
        )

        # Internal state
        self.last_full_soc_time: datetime = datetime.now(WARSAW_TZ)
        self.current_plan: PlanResult | None = None
        self.last_arbitrage_savings: float = 0.0
        self._unsub_scheduled_triggers: list[Any] = []
        self._remote_mode_needs_rearm: bool = False

    async def async_setup(self) -> None:
        """Set up scheduled triggers for plan evaluation."""
        self._schedule_next_triggers()

        # Auto-migrate existing entries with old watchdog timeout < 1800s to 1800s
        saved_watchdog = self.entry.options.get(
            CONF_WATCHDOG_TIMEOUT_SEC
        ) or self.entry.data.get(CONF_WATCHDOG_TIMEOUT_SEC)
        if saved_watchdog is not None and int(saved_watchdog) < 1800:
            _LOGGER.info(
                "Migrating watchdog_timeout_sec from %s to 1800s to bridge Modbus connection dropouts",
                saved_watchdog,
            )
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.entry.options, CONF_WATCHDOG_TIMEOUT_SEC: 1800},
            )

        # Immediately compute initial plan so sensors show real values on startup
        now = datetime.now(WARSAW_TZ)
        plan_type = "night" if now.hour >= 15 or now.hour < 6 else "midday"
        self.hass.async_create_task(self.async_evaluate_and_apply(plan_type))

    def _schedule_next_triggers(self) -> None:
        """Schedule future point-in-time plan evaluations and window boundaries."""
        for unsub in self._unsub_scheduled_triggers:
            unsub()
        self._unsub_scheduled_triggers.clear()

        now = datetime.now(WARSAW_TZ)
        today = now.date()

        # 1. Night plan evaluation (5 min before night off-peak: 21:55 + offset)
        night_start_h, night_end_h = self.tariff.get_night_off_peak_hours(now)
        night_eval_h = (night_start_h - 1) % 24
        night_eval_time = datetime.combine(today, time(night_eval_h, 55), tzinfo=WARSAW_TZ)
        if night_eval_time <= now:
            night_eval_time += timedelta(days=1)

        # 2. Night off-peak START (22:00 + offset)
        night_start_time = datetime.combine(today, time(night_start_h, 0), tzinfo=WARSAW_TZ)
        if night_start_time <= now:
            night_start_time += timedelta(days=1)

        # 3. Night off-peak END (06:00 + offset)
        night_end_time = datetime.combine(today, time(night_end_h, 0), tzinfo=WARSAW_TZ)
        if night_end_time <= now:
            night_end_time += timedelta(days=1)

        # 4. Midday plan evaluation (5 min before midday off-peak: 12:55 + offset)
        mid_start_h, mid_end_h = self.tariff.get_midday_off_peak_hours(today, now)
        mid_eval_h = (mid_start_h - 1) % 24
        mid_eval_time = datetime.combine(today, time(mid_eval_h, 55), tzinfo=WARSAW_TZ)
        if mid_eval_time <= now:
            mid_eval_time += timedelta(days=1)

        # 5. Midday off-peak START (13:00 + offset / 15:00 in summer)
        mid_start_time = datetime.combine(today, time(mid_start_h, 0), tzinfo=WARSAW_TZ)
        if mid_start_time <= now:
            mid_start_time += timedelta(days=1)

        # 6. Midday off-peak END (15:00 + offset / 17:00 in summer)
        mid_end_time = datetime.combine(today, time(mid_end_h, 0), tzinfo=WARSAW_TZ)
        if mid_end_time <= now:
            mid_end_time += timedelta(days=1)

        @callback
        def _on_night_eval(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("night"))
            self._schedule_next_triggers()

        @callback
        def _on_midday_eval(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("midday"))
            self._schedule_next_triggers()

        @callback
        def _on_night_start(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("night"))
            self._schedule_next_triggers()

        @callback
        def _on_midday_start(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_evaluate_and_apply("midday"))
            self._schedule_next_triggers()

        @callback
        def _on_night_end(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_end_cheap_window())
            self._schedule_next_triggers()

        @callback
        def _on_midday_end(point_in_time: datetime) -> None:
            self.hass.async_create_task(self.async_end_cheap_window())
            self._schedule_next_triggers()

        self._unsub_scheduled_triggers.extend([
            async_track_point_in_time(self.hass, _on_night_eval, night_eval_time),
            async_track_point_in_time(self.hass, _on_night_start, night_start_time),
            async_track_point_in_time(self.hass, _on_night_end, night_end_time),
            async_track_point_in_time(self.hass, _on_midday_eval, mid_eval_time),
            async_track_point_in_time(self.hass, _on_midday_start, mid_start_time),
            async_track_point_in_time(self.hass, _on_midday_end, mid_end_time),
        ])

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest states, refresh watchdog, and update sensor data."""
        try:
            now = datetime.now(WARSAW_TZ)
            current_soc = self._get_current_battery_soc()

            # Track last full SOC (>= 99%) for cell balancing
            if current_soc is not None and current_soc >= 99.0:
                self.last_full_soc_time = now

            is_off_peak = self.tariff.is_off_peak(now)

            # Continuous safety guard: if we are in a PEAK zone (expensive energy),
            # Remote Mode and grid charging MUST be unconditionally stopped!
            if not is_off_peak:
                if self.control_mode == MODE_REMOTE_RAM and self.executor.remote_mode_active:
                    _LOGGER.warning(
                        "Peak tariff zone active (%s) but Remote Mode was still active. "
                        "Enforcing unconditional shutdown to start battery self-consumption.",
                        self.tariff.get_tariff_zone(now),
                    )
                    await self.executor.async_disable_remote_mode()
                    self._remote_mode_needs_rearm = False

                if self.current_plan and self.current_plan.grid_charge_enable:
                    self.current_plan = PlanResult(
                        plan_type="peak",
                        soc_target=self.current_plan.soc_target,
                        charge_current_a=0,
                        grid_charge_enable=False,
                        headroom_max_soc=self.current_plan.headroom_max_soc,
                        expected_load_kwh=self.current_plan.expected_load_kwh,
                        expected_pv_kwh=self.current_plan.expected_pv_kwh,
                        reason="Peak tariff zone active; battery self-consumption enabled",
                    )

            # Remote Mode watchdog maintenance and auto re-arming during off-peak
            should_remote_charge = (
                self.control_mode == MODE_REMOTE_RAM
                and is_off_peak
                and self.current_plan is not None
                and self.current_plan.grid_charge_enable
                and self.automation_active
                and not self.simulation_mode
            )

            if self.control_mode == MODE_REMOTE_RAM and (
                self.executor.remote_mode_active or should_remote_charge
            ):
                if (
                    self._remote_mode_needs_rearm
                    and should_remote_charge
                    and self.current_plan is not None
                ):
                    # Probe connection with a single watchdog refresh (1101) to verify link is actually back
                    # This avoids spamming the full 6-register sequence while Solarman is still offline
                    link_alive = await self.executor.async_refresh_watchdog()
                    if link_alive:
                        _LOGGER.info(
                            "Modbus connection verified: Re-arming full Remote Mode charging registers on Deye inverter"
                        )
                        rearm_ok = await self.executor.async_apply_plan(
                            plan=self.current_plan,
                            tariff_calc=self.tariff,
                            simulation_mode=self.simulation_mode,
                            current_soc=current_soc or self.soc_min,
                        )
                        if rearm_ok:
                            self._remote_mode_needs_rearm = False
                    else:
                        _LOGGER.debug(
                            "Modbus connection offline; inverter autonomous hardware timer active"
                        )
                elif self.executor.remote_mode_active:
                    # Calculate remaining seconds in current off-peak window to clamp watchdog
                    remaining_sec = self.tariff.get_remaining_off_peak_seconds(now)
                    effective_timeout = (
                        max(60, min(self.watchdog_timeout_sec, remaining_sec))
                        if remaining_sec is not None
                        else self.watchdog_timeout_sec
                    )
                    refresh_ok = await self.executor.async_refresh_watchdog(effective_timeout)
                    if not refresh_ok:
                        _LOGGER.info(
                            "Watchdog refresh deferred (Modbus connection drop). Buffered by hardware watchdog. Flagging for full re-arm upon reconnection."
                        )
                        self._remote_mode_needs_rearm = True
            elif self.control_mode == MODE_REMOTE_RAM:
                self._remote_mode_needs_rearm = False

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

            # PV forecast for tomorrow morning peak
            forecast_entity = self.pv_forecast_tomorrow_entity or self.pv_forecast_entity
            e_pv, _ = self.pv_parser.get_forecast_energy_kwh(
                self.hass, forecast_entity, start_m, end_m
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

            # PV forecast for today evening peak
            forecast_entity = self.pv_forecast_today_entity or self.pv_forecast_entity
            e_pv, _ = self.pv_parser.get_forecast_energy_kwh(
                self.hass, forecast_entity, start_e, end_e
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

        # In Remote Mode RAM: only apply grid charge to hardware if we are currently in an off-peak window!
        if (
            self.control_mode == MODE_REMOTE_RAM
            and plan.grid_charge_enable
            and not self.tariff.is_off_peak(now)
            and not self.simulation_mode
        ):
            _LOGGER.info(
                "Calculated %s plan has grid charge enabled, but current time (%s) is in peak tariff zone. "
                "Hardware activation deferred until off-peak window begins.",
                plan_type.upper(),
                now.strftime("%H:%M"),
            )
            if self.executor.remote_mode_active:
                await self.executor.async_disable_remote_mode()
            await self.async_request_refresh()
            return True

        success = await self.executor.async_apply_plan(
            plan=plan,
            tariff_calc=self.tariff,
            simulation_mode=self.simulation_mode,
            current_soc=current_soc,
            bms_alarm_active=bms_alarm,
            target_date=target_date,
        )

        if not success and self.automation_active and not self.simulation_mode:
            if self.control_mode == MODE_REMOTE_RAM:
                self._remote_mode_needs_rearm = True
            _LOGGER.warning(
                "Execution failed (Modbus logger unavailable). Rescheduling retry in 120s"
            )

            @callback
            def _on_retry_trigger(*_: Any) -> None:
                self.hass.async_create_task(self.async_evaluate_and_apply(plan_type))

            self._unsub_scheduled_triggers.append(
                async_call_later(self.hass, 120.0, _on_retry_trigger)
            )

        await self.async_request_refresh()
        return success

    async def async_end_cheap_window(self) -> None:
        """Unconditionally stop grid charging at the end of off-peak window and enable battery self-consumption."""
        _LOGGER.info(
            "Off-peak tariff window ended. Unconditionally disabling Remote Mode / grid charge. "
            "Switching inverter to battery self-consumption."
        )

        if self.control_mode == MODE_REMOTE_RAM:
            # 1. Disable Remote Mode (1100=0, 1109=0, 1101=0)
            await self.executor.async_disable_remote_mode()
            self._remote_mode_needs_rearm = False

        elif self.control_mode == MODE_TOU_SCHEDULE:
            # Ensure grid charge is disabled on TOU slots
            current_soc = self._get_current_battery_soc() or self.soc_min
            peak_plan = PlanResult(
                plan_type="peak",
                soc_target=self.soc_min,
                charge_current_a=0,
                grid_charge_enable=False,
                headroom_max_soc=self.soc_max,
                expected_load_kwh=0.0,
                expected_pv_kwh=0.0,
                reason="Off-peak window ended; battery self-consumption active",
            )
            await self.executor.async_apply_plan(
                plan=peak_plan,
                tariff_calc=self.tariff,
                simulation_mode=self.simulation_mode,
                current_soc=current_soc,
            )

        self.current_plan = PlanResult(
            plan_type="peak",
            soc_target=self.soc_min,
            charge_current_a=0,
            grid_charge_enable=False,
            headroom_max_soc=self.soc_max,
            expected_load_kwh=0.0,
            expected_pv_kwh=0.0,
            reason="Off-peak window ended; battery self-consumption active",
        )
        await self.async_request_refresh()

    async def async_unload(self) -> None:
        """Unload coordinator and clean up background listeners."""
        for unsub in self._unsub_scheduled_triggers:
            unsub()
        self._unsub_scheduled_triggers.clear()
        if self.control_mode == "remote_mode_ram" and self.executor.remote_mode_active:
            await self.executor.async_disable_remote_mode()
