"""PV forecast parser for Solcast, Open-Meteo, Forecast.Solar, and generic fallbacks."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, State

_LOGGER = logging.getLogger(__name__)
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def _to_aware_datetime(val: Any) -> datetime | None:
    """Convert string or datetime to timezone-aware datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=WARSAW_TZ)
        return val
    if isinstance(val, str):
        try:
            # Handle trailing Z
            normalized = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=WARSAW_TZ)
            return dt
        except ValueError, TypeError:
            return None
    return None


class PVForecastParser:
    """Parser for various PV forecast entities in Home Assistant."""

    def __init__(self, use_pessimistic_blend: bool = False) -> None:
        """Initialize PV Forecast Parser."""
        self.use_pessimistic_blend = use_pessimistic_blend

    def get_forecast_energy_kwh(
        self,
        hass: HomeAssistant | None,
        entity_id: str | None,
        start_dt: datetime,
        end_dt: datetime,
        state_override: State | None = None,
    ) -> tuple[float, bool]:
        """Extract expected PV generation (kWh) in [start_dt, end_dt).

        Returns:
            tuple[float, bool]: (energy_kwh, forecast_available)
        """
        if not entity_id and state_override is None:
            return (0.0, False)

        actual_entity_id = entity_id
        if hass is not None and entity_id is not None and state_override is None:
            now_date = datetime.now(WARSAW_TZ).date()
            if start_dt.date() > now_date and "today" in entity_id.lower():
                candidate = entity_id.lower().replace("today", "tomorrow")
                if hass.states.get(candidate):
                    actual_entity_id = candidate
            elif start_dt.date() <= now_date and "tomorrow" in entity_id.lower():
                candidate = entity_id.lower().replace("tomorrow", "today")
                if hass.states.get(candidate):
                    actual_entity_id = candidate

        state: State | None = state_override
        if state is None and hass is not None and actual_entity_id is not None:
            state = hass.states.get(actual_entity_id)

        if state is None or state.state in (None, "", "unknown", "unavailable"):
            _LOGGER.warning("PV forecast entity %s unavailable or unknown", actual_entity_id or entity_id)
            return (0.0, False)

        attrs = state.attributes or {}

        # 1. Try Solcast (detailedForecast attribute and common variants)
        detailed_forecast = (
            attrs.get("detailedForecast")
            or attrs.get("detailed_forecast")
            or attrs.get("forecasts")
            or attrs.get("detailedHourly")
            or attrs.get("detailed_hourly")
        )
        if isinstance(detailed_forecast, list) and detailed_forecast:
            energy, matched = self._parse_solcast(detailed_forecast, start_dt, end_dt)
            if matched:
                return (max(0.0, round(energy, 3)), True)

        # Sibling entity resolution (fallback if candidate wasn't picked or didn't match window)
        if hass is not None and entity_id is not None:
            now_date = datetime.now(WARSAW_TZ).date()
            sibling_id: str | None = None
            if start_dt.date() > now_date and "today" in entity_id.lower():
                sibling_id = entity_id.lower().replace("today", "tomorrow")
            elif start_dt.date() <= now_date and "tomorrow" in entity_id.lower():
                sibling_id = entity_id.lower().replace("tomorrow", "today")

            if sibling_id and sibling_id != actual_entity_id:
                sibling_state = hass.states.get(sibling_id)
                if sibling_state and sibling_state.state not in (
                    None,
                    "",
                    "unknown",
                    "unavailable",
                ):
                    # Check detailedForecast on sibling
                    sib_attrs = sibling_state.attributes or {}
                    sib_detailed = (
                        sib_attrs.get("detailedForecast")
                        or sib_attrs.get("detailed_forecast")
                        or sib_attrs.get("forecasts")
                        or sib_attrs.get("detailedHourly")
                        or sib_attrs.get("detailed_hourly")
                    )
                    if isinstance(sib_detailed, list) and sib_detailed:
                        sib_energy, sib_matched = self._parse_solcast(sib_detailed, start_dt, end_dt)
                        if sib_matched:
                            return (max(0.0, round(sib_energy, 3)), True)

                    # Otherwise try generic fallback on sibling state
                    sib_fallback = self._parse_generic_fallback(sibling_state, start_dt, end_dt)
                    if sib_fallback > 0.0:
                        return (max(0.0, round(sib_fallback, 3)), True)

        # 2. Try Open-Meteo (wh_period attribute)
        wh_period = attrs.get("wh_period")
        if isinstance(wh_period, dict) and wh_period:
            energy = self._parse_open_meteo(wh_period, start_dt, end_dt)
            return (max(0.0, round(energy, 3)), True)

        # 3. Fallback: Forecast.Solar or generic total state
        energy = self._parse_generic_fallback(state, start_dt, end_dt)
        return (max(0.0, round(energy, 3)), True)

    def _parse_solcast(
        self, forecast_list: list[dict[str, Any]], start_dt: datetime, end_dt: datetime
    ) -> tuple[float, bool]:
        """Parse Solcast detailedForecast / detailedHourly list with automatic step duration."""
        total_kwh = 0.0
        matched = False

        step_hours = 0.5
        if len(forecast_list) >= 2:
            try:
                t0 = _to_aware_datetime(
                    forecast_list[0].get("period_start")
                    or forecast_list[0].get("periodStart")
                    or forecast_list[0].get("start_time")
                )
                t1 = _to_aware_datetime(
                    forecast_list[1].get("period_start")
                    or forecast_list[1].get("periodStart")
                    or forecast_list[1].get("start_time")
                )
                if t0 and t1:
                    diff = abs((t1 - t0).total_seconds()) / 3600.0
                    if 0.1 <= diff <= 24.0:
                        step_hours = diff
            except Exception:
                step_hours = 0.5

        for interval in forecast_list:
            period_start = _to_aware_datetime(
                interval.get("period_start")
                or interval.get("periodStart")
                or interval.get("start_time")
            )
            if not period_start:
                continue

            period_end = period_start.astimezone(start_dt.tzinfo or UTC)
            # Check overlap with [start_dt, end_dt)
            if start_dt <= period_end < end_dt:
                matched = True
                est = float(
                    interval.get("pv_estimate")
                    or interval.get("pv_estimate_p50")
                    or interval.get("power")
                    or 0.0
                )
                est10 = float(
                    interval.get("pv_estimate10") or interval.get("pv_estimate_p10") or est
                )

                if self.use_pessimistic_blend:
                    power_kw = 0.7 * est + 0.3 * est10
                else:
                    power_kw = est

                total_kwh += power_kw * step_hours

        return total_kwh, matched

    def _parse_open_meteo(
        self, wh_period: dict[str, Any], start_dt: datetime, end_dt: datetime
    ) -> float:
        """Parse Open-Meteo wh_period dict {iso_timestamp: wh_value}."""
        total_kwh = 0.0

        for ts_str, wh_val in wh_period.items():
            dt = _to_aware_datetime(ts_str)
            if not dt:
                continue

            aware_dt = dt.astimezone(start_dt.tzinfo or UTC)
            if start_dt <= aware_dt < end_dt:
                try:
                    total_kwh += float(wh_val) / 1000.0
                except ValueError, TypeError:
                    continue

        return total_kwh

    def _parse_generic_fallback(self, state: State, start_dt: datetime, end_dt: datetime) -> float:
        """Distribute total daily state value across daylight hours."""
        try:
            total_val = float(state.state)
        except ValueError, TypeError:
            return 0.0

        unit = state.attributes.get("unit_of_measurement", "kWh")
        total_kwh = total_val / 1000.0 if unit == "Wh" else total_val

        # Assume 10 daylight hours: 07:00 to 17:00 local time
        day_date = start_dt.date()
        daylight_start = datetime.combine(day_date, time(7, 0), tzinfo=start_dt.tzinfo)
        daylight_end = datetime.combine(day_date, time(17, 0), tzinfo=start_dt.tzinfo)

        overlap_start = max(start_dt, daylight_start)
        overlap_end = min(end_dt, daylight_end)

        if overlap_end <= overlap_start:
            return 0.0

        overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600.0
        total_daylight_hours = (daylight_end - daylight_start).total_seconds() / 3600.0

        fraction = min(1.0, max(0.0, overlap_hours / total_daylight_hours))
        return total_kwh * fraction
