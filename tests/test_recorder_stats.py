"""Tests for Recorder statistics consumption estimator."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.deye_g12_opt.recorder_stats import RecorderConsumptionEstimator

WARSAW_TZ = ZoneInfo("Europe/Warsaw")


@pytest.mark.asyncio
async def test_recorder_statistics_averaging_and_outliers(hass):
    """Test multi-day aggregation, outlier filtering, and averaging."""
    estimator = RecorderConsumptionEstimator(
        hass=hass,
        load_statistic_id="sensor.home_total_energy",
        fallback_hourly_load=0.65,
        days_history=14,
    )

    # Window: 06:00 to 13:00 (7 hours)
    # Day 1: 7 hours at 1.0 kWh/h = 7.0 kWh
    # Day 2: 7 hours at 1.0 kWh/h + 1 outlier of 50.0 kWh (should be ignored) = 7.0 kWh
    records = []
    # Day 1 (2026-05-01)
    for h in range(6, 13):
        dt = datetime(2026, 5, 1, h, 0, tzinfo=WARSAW_TZ)
        records.append({"start": dt.timestamp(), "change": 1.0})

    # Day 2 (2026-05-02): normal hours + 1 outlier at 10:00
    for h in range(6, 13):
        dt = datetime(2026, 5, 2, h, 0, tzinfo=WARSAW_TZ)
        val = 50.0 if h == 10 else 1.0
        records.append({"start": dt.timestamp(), "change": val})

    # Extra hour outside window: 14:00 (should not be included)
    records.append(
        {
            "start": datetime(2026, 5, 1, 14, 0, tzinfo=WARSAW_TZ).timestamp(),
            "change": 5.0,
        }
    )

    mock_stats = {"sensor.home_total_energy": records}

    # Test compute_window_average
    avg = estimator.compute_window_average(mock_stats, 6, 13)
    # Day 1 sum = 7.0 kWh. Day 2 sum (outlier skipped, 6 valid hours scaled to 7h): (6/6)*7 = 7.0 kWh.
    assert round(avg, 1) == 7.0


@pytest.mark.asyncio
async def test_recorder_fallback_when_empty_or_error(hass):
    """Test fallback when recorder has no data or throws an error."""
    estimator = RecorderConsumptionEstimator(
        hass=hass,
        load_statistic_id="sensor.home_total_energy",
        fallback_hourly_load=0.65,
    )

    # Empty stats -> 7h * 0.65 = 4.55 kWh
    avg_empty = estimator.compute_window_average({}, 6, 13)
    assert round(avg_empty, 2) == 4.55

    # Mock async query exception
    with patch("custom_components.deye_g12_opt.recorder_stats.get_instance") as mock_get_inst:
        mock_inst = AsyncMock()
        mock_inst.async_add_executor_job.side_effect = RuntimeError("DB locked")
        mock_get_inst.return_value = mock_inst

        val = await estimator.async_get_window_consumption(6, 13)
        assert round(val, 2) == 4.55


def test_midnight_wrapping_window(hass):
    """Test night window wrapping across midnight (22:00 to 06:00, 8 hours)."""
    estimator = RecorderConsumptionEstimator(
        hass=hass,
        load_statistic_id="sensor.home_total_energy",
        fallback_hourly_load=0.5,
    )

    records = [
        # Night 1: 2026-05-01 22:00 and 23:00 (1.0 kWh each)
        {"start": datetime(2026, 5, 1, 22, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 1, 23, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        # Morning of 2026-05-02: 00:00 to 05:00 (6 hours at 1.0 kWh)
        {"start": datetime(2026, 5, 2, 0, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 2, 1, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 2, 2, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 2, 3, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 2, 4, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
        {"start": datetime(2026, 5, 2, 5, 0, tzinfo=WARSAW_TZ).timestamp(), "change": 1.0},
    ]

    mock_stats = {"sensor.home_total_energy": records}
    avg = estimator.compute_window_average(mock_stats, 22, 6)
    assert round(avg, 1) == 8.0
