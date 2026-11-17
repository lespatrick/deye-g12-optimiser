"""Recorder statistics query and consumption estimator."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.core import HomeAssistant

from .const import DEFAULT_FALLBACK_HOURLY_LOAD_KWH

_LOGGER = logging.getLogger(__name__)
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def _is_hour_in_window(hour: int, start_hour: int, end_hour: int) -> bool:
    """Check if given hour is within [start_hour, end_hour), supporting midnight wrap."""
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    if start_hour > end_hour:
        return hour >= start_hour or hour < end_hour
    return False


def _get_window_hours(start_hour: int, end_hour: int) -> float:
    """Calculate the duration of the window in hours."""
    if end_hour > start_hour:
        return float(end_hour - start_hour)
    if end_hour < start_hour:
        return float(24 - start_hour + end_hour)
    return 24.0


class RecorderConsumptionEstimator:
    """Estimates load consumption in specific windows using HA Recorder statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        load_statistic_id: str,
        fallback_hourly_load: float = DEFAULT_FALLBACK_HOURLY_LOAD_KWH,
        days_history: int = 14,
    ) -> None:
        """Initialize the consumption estimator."""
        self.hass = hass
        self.load_statistic_id = load_statistic_id
        self.fallback_hourly_load = fallback_hourly_load
        self.days_history = days_history

    async def async_get_window_consumption(
        self,
        start_hour: int,
        end_hour: int,
        ref_dt: datetime | None = None,
    ) -> float:
        """Asynchronously compute average consumption in [start_hour, end_hour)."""
        now = ref_dt or datetime.now(WARSAW_TZ)
        start_time = now - timedelta(days=self.days_history)

        try:
            recorder_instance = get_instance(self.hass)
            stats = await recorder_instance.async_add_executor_job(
                self._query_statistics,
                start_time,
                now,
            )
        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning(
                "Failed to query recorder statistics for %s: %s", self.load_statistic_id, exc
            )
            window_hours = _get_window_hours(start_hour, end_hour)
            return round(self.fallback_hourly_load * window_hours, 2)

        return self.compute_window_average(stats, start_hour, end_hour)

    def _query_statistics(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        """Synchronously query recorder statistics (runs in executor thread)."""
        return statistics_during_period(
            self.hass,
            start_time=start_time,
            end_time=end_time,
            statistic_ids={self.load_statistic_id},
            period="hour",
            types={"change"},
            units=None,
        )

    def compute_window_average(
        self,
        stats: dict[str, list[dict[str, Any]]],
        start_hour: int,
        end_hour: int,
    ) -> float:
        """Group records into daily buckets and compute window average."""
        window_hours = _get_window_hours(start_hour, end_hour)
        records = stats.get(self.load_statistic_id, [])

        if not records:
            _LOGGER.debug(
                "No recorder records found for %s, using fallback", self.load_statistic_id
            )
            return round(self.fallback_hourly_load * window_hours, 2)

        # Map date -> list of valid hourly changes in window
        daily_changes: dict[Any, list[float]] = defaultdict(list)

        for rec in records:
            change = rec.get("change")
            if change is None:
                continue

            # Outlier filter: ignore negative changes or spikes > 25 kWh/h
            if change < 0.0 or change > 25.0:
                continue

            start_val = rec.get("start")
            if isinstance(start_val, (int, float)):
                dt = datetime.fromtimestamp(start_val, tz=WARSAW_TZ)
            elif isinstance(start_val, datetime):
                dt = dt_aware = (
                    start_val.astimezone(WARSAW_TZ)
                    if start_val.tzinfo
                    else start_val.replace(tzinfo=WARSAW_TZ)
                )
                dt = dt_aware
            else:
                continue

            if _is_hour_in_window(dt.hour, start_hour, end_hour):
                # For midnight-wrapping windows (e.g. 22:00 to 06:00), group hours < 06:00 with previous day's evening
                bucket_date = dt.date()
                if start_hour > end_hour and dt.hour < end_hour:
                    bucket_date = bucket_date - timedelta(days=1)

                daily_changes[bucket_date].append(float(change))

        if not daily_changes:
            return round(self.fallback_hourly_load * window_hours, 2)

        # Compute average across days that had at least partial data
        daily_sums: list[float] = []
        expected_count = int(window_hours)

        for changes in daily_changes.values():
            if len(changes) == 0:
                continue
            # If some hours are missing for a day, scale proportionally
            day_sum = sum(changes)
            if 0 < len(changes) < expected_count:
                day_sum = (day_sum / len(changes)) * window_hours
            daily_sums.append(day_sum)

        if not daily_sums:
            return round(self.fallback_hourly_load * window_hours, 2)

        avg_consumption = sum(daily_sums) / len(daily_sums)
        return round(max(0.0, avg_consumption), 2)
