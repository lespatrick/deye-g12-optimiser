"""Tests for PV forecast parsing across Solcast, Open-Meteo, and fallbacks."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.core import State

from custom_components.deye_g12_opt.pv_forecast import PVForecastParser

WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def test_solcast_detailed_forecast_and_pessimistic_blend():
    """Verify Solcast 30-min interval parsing with and without pessimistic blend."""
    parser_standard = PVForecastParser(use_pessimistic_blend=False)
    parser_pessimistic = PVForecastParser(use_pessimistic_blend=True)

    # 4 intervals of 30 mins each (2 hours total: 06:00 to 08:00)
    # interval 1 (06:00): pv_estimate=2.0 kW (1.0 kWh), pv_estimate10=1.0 kW
    # interval 2 (06:30): pv_estimate=4.0 kW (2.0 kWh), pv_estimate10=2.0 kW
    # interval 3 (07:00): pv_estimate=6.0 kW (3.0 kWh), pv_estimate10=4.0 kW
    # interval 4 (07:30): pv_estimate=8.0 kW (4.0 kWh), pv_estimate10=6.0 kW
    detailed_forecast = [
        {
            "period_start": "2026-05-10T06:00:00+02:00",
            "pv_estimate": 2.0,
            "pv_estimate10": 1.0,
        },
        {
            "period_start": "2026-05-10T06:30:00+02:00",
            "pv_estimate": 4.0,
            "pv_estimate10": 2.0,
        },
        {
            "period_start": "2026-05-10T07:00:00+02:00",
            "pv_estimate": 6.0,
            "pv_estimate10": 4.0,
        },
        {
            "period_start": "2026-05-10T07:30:00+02:00",
            "pv_estimate": 8.0,
            "pv_estimate10": 6.0,
        },
        # Outside window: 08:30
        {
            "period_start": "2026-05-10T08:30:00+02:00",
            "pv_estimate": 10.0,
            "pv_estimate10": 8.0,
        },
    ]

    mock_state = State(
        "sensor.solcast_pv_forecast", "15.0", {"detailedForecast": detailed_forecast}
    )

    start_dt = datetime(2026, 5, 10, 6, 0, tzinfo=WARSAW_TZ)
    end_dt = datetime(2026, 5, 10, 8, 0, tzinfo=WARSAW_TZ)

    # Standard (100% P50): (2.0 + 4.0 + 6.0 + 8.0) * 0.5 = 20.0 * 0.5 = 10.0 kWh
    val_std, avail_std = parser_standard.get_forecast_energy_kwh(
        None, "sensor.solcast_pv_forecast", start_dt, end_dt, state_override=mock_state
    )
    assert avail_std is True
    assert val_std == 10.0

    # Pessimistic blend: 0.7 * P50 + 0.3 * P10
    # int 1: 0.7*2 + 0.3*1 = 1.7 kW
    # int 2: 0.7*4 + 0.3*2 = 3.4 kW
    # int 3: 0.7*6 + 0.3*4 = 5.4 kW
    # int 4: 0.7*8 + 0.3*6 = 7.4 kW
    # sum = 17.9 kW * 0.5 = 8.95 kWh
    val_pess, avail_pess = parser_pessimistic.get_forecast_energy_kwh(
        None, "sensor.solcast_pv_forecast", start_dt, end_dt, state_override=mock_state
    )
    assert avail_pess is True
    assert round(val_pess, 2) == 8.95


def test_open_meteo_wh_period():
    """Verify Open-Meteo wh_period dict parsing and Wh to kWh conversion."""
    parser = PVForecastParser()

    wh_data = {
        "2026-06-01T08:00:00+02:00": 1500,  # 1.5 kWh
        "2026-06-01T09:00:00+02:00": 2500,  # 2.5 kWh
        "2026-06-01T10:00:00+02:00": 3000,  # 3.0 kWh
        "2026-06-01T12:00:00+02:00": 4000,  # Outside window
    }

    mock_state = State("sensor.open_meteo_forecast", "11.0", {"wh_period": wh_data})

    start_dt = datetime(2026, 6, 1, 8, 0, tzinfo=WARSAW_TZ)
    end_dt = datetime(2026, 6, 1, 11, 0, tzinfo=WARSAW_TZ)

    val, avail = parser.get_forecast_energy_kwh(
        None, "sensor.open_meteo_forecast", start_dt, end_dt, state_override=mock_state
    )
    assert avail is True
    assert val == 7.0  # 1.5 + 2.5 + 3.0


def test_generic_fallback_and_unit_conversion():
    """Verify generic fallback proportionally distributes daily total across daylight."""
    parser = PVForecastParser()

    # Total 10 kWh over 10 daylight hours (07:00 to 17:00) -> 1 kWh/hour
    mock_state_kwh = State("sensor.forecast_solar", "10.0", {"unit_of_measurement": "kWh"})

    # Window 07:00 to 12:00 (5 hours -> 5.0 kWh)
    start_dt = datetime(2026, 6, 1, 7, 0, tzinfo=WARSAW_TZ)
    end_dt = datetime(2026, 6, 1, 12, 0, tzinfo=WARSAW_TZ)

    val, avail = parser.get_forecast_energy_kwh(
        None, "sensor.forecast_solar", start_dt, end_dt, state_override=mock_state_kwh
    )
    assert avail is True
    assert round(val, 1) == 5.0

    # Wh unit conversion: 10000 Wh -> 10 kWh
    mock_state_wh = State("sensor.forecast_solar_wh", "10000", {"unit_of_measurement": "Wh"})
    val_wh, _ = parser.get_forecast_energy_kwh(
        None, "sensor.forecast_solar_wh", start_dt, end_dt, state_override=mock_state_wh
    )
    assert round(val_wh, 1) == 5.0


def test_unavailable_or_unknown_fallback():
    """Verify fail-safe returns 0.0 kWh when sensor is unavailable or None."""
    parser = PVForecastParser()

    start_dt = datetime(2026, 6, 1, 6, 0, tzinfo=WARSAW_TZ)
    end_dt = datetime(2026, 6, 1, 13, 0, tzinfo=WARSAW_TZ)

    mock_unavail = State("sensor.pv_forecast", "unavailable", {})
    val, avail = parser.get_forecast_energy_kwh(
        None, "sensor.pv_forecast", start_dt, end_dt, state_override=mock_unavail
    )
    assert avail is False
    assert val == 0.0

    val_none, avail_none = parser.get_forecast_energy_kwh(None, None, start_dt, end_dt)
    assert avail_none is False
    assert val_none == 0.0
