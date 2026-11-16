"""Tests for PGE G12 and G12w tariff calculations."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from custom_components.deye_g12_opt.const import (
    TARIFF_G12,
    TARIFF_G12W,
    ZONE_OFF_PEAK_MIDDAY,
    ZONE_OFF_PEAK_NIGHT,
    ZONE_OFF_PEAK_WEEKEND,
    ZONE_PEAK_EVENING,
    ZONE_PEAK_MORNING,
)
from custom_components.deye_g12_opt.pge_tariff import (
    PGETariffCalculator,
    get_easter_date,
    get_polish_public_holidays,
)

WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def test_polish_public_holidays_2026():
    """Verify Polish statutory holidays for 2026."""
    easter_2026 = get_easter_date(2026)
    assert easter_2026 == date(2026, 4, 5)

    holidays = get_polish_public_holidays(2026)
    assert date(2026, 1, 1) in holidays  # Nowy Rok
    assert date(2026, 1, 6) in holidays  # Trzech Króli
    assert date(2026, 4, 6) in holidays  # Easter Monday
    assert date(2026, 5, 1) in holidays  # Święto Pracy
    assert date(2026, 5, 3) in holidays  # Święto Konstytucji
    assert date(2026, 5, 24) in holidays  # Zielone Świątki
    assert date(2026, 6, 4) in holidays  # Boże Ciało (Corpus Christi)
    assert date(2026, 8, 15) in holidays  # Wniebowzięcie NMP
    assert date(2026, 11, 1) in holidays  # Wszystkich Świętych
    assert date(2026, 11, 11) in holidays  # Niepodległości
    assert date(2026, 12, 25) in holidays
    assert date(2026, 12, 26) in holidays


def test_winter_tariff_schedule():
    """Verify winter midday 13:00-15:00 and night 22:00-06:00 on weekdays."""
    calc = PGETariffCalculator(tariff_variant=TARIFF_G12, pge_seasonal_shift=True)

    # 2026-01-15 is Thursday (weekday)
    dt_night_early = datetime(2026, 1, 15, 3, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_night_early) == ZONE_OFF_PEAK_NIGHT
    assert calc.is_off_peak(dt_night_early) is True

    dt_morning_peak = datetime(2026, 1, 15, 8, 0, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_morning_peak) == ZONE_PEAK_MORNING
    assert calc.is_off_peak(dt_morning_peak) is False

    dt_midday_offpeak = datetime(2026, 1, 15, 13, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_midday_offpeak) == ZONE_OFF_PEAK_MIDDAY
    assert calc.is_off_peak(dt_midday_offpeak) is True

    dt_evening_peak = datetime(2026, 1, 15, 17, 0, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_evening_peak) == ZONE_PEAK_EVENING
    assert calc.is_off_peak(dt_evening_peak) is False

    dt_night_late = datetime(2026, 1, 15, 22, 15, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_night_late) == ZONE_OFF_PEAK_NIGHT

    # Last day of winter: 2026-03-31 (Tuesday)
    dt_mar31 = datetime(2026, 3, 31, 14, 0, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_mar31) == ZONE_OFF_PEAK_MIDDAY


def test_summer_tariff_schedule():
    """Verify summer midday 15:00-17:00 on weekdays."""
    calc = PGETariffCalculator(tariff_variant=TARIFF_G12, pge_seasonal_shift=True)

    # 2026-04-01 is Wednesday (first day of summer)
    dt_14h = datetime(2026, 4, 1, 14, 0, tzinfo=WARSAW_TZ)
    # At 14:00, in summer it is still morning peak
    assert calc.get_tariff_zone(dt_14h) == ZONE_PEAK_MORNING

    dt_midday_summer = datetime(2026, 4, 1, 15, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_midday_summer) == ZONE_OFF_PEAK_MIDDAY

    # Mid summer: 2026-07-15 (Wednesday)
    dt_july = datetime(2026, 7, 15, 16, 0, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_july) == ZONE_OFF_PEAK_MIDDAY

    # Last day of summer: 2026-09-30 (Wednesday)
    dt_sep30 = datetime(2026, 9, 30, 16, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_sep30) == ZONE_OFF_PEAK_MIDDAY


def test_pge_seasonal_shift_disabled():
    """Verify that when seasonal shift is disabled, midday is always 13:00-15:00."""
    calc = PGETariffCalculator(tariff_variant=TARIFF_G12, pge_seasonal_shift=False)

    dt_summer_13h = datetime(2026, 7, 15, 13, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_summer_13h) == ZONE_OFF_PEAK_MIDDAY

    dt_summer_15h = datetime(2026, 7, 15, 15, 30, tzinfo=WARSAW_TZ)
    assert calc.get_tariff_zone(dt_summer_15h) == ZONE_PEAK_EVENING


def test_g12w_weekends_and_holidays():
    """Verify G12w marks weekends and Polish holidays as 24h off-peak."""
    calc_g12w = PGETariffCalculator(tariff_variant=TARIFF_G12W)
    calc_g12 = PGETariffCalculator(tariff_variant=TARIFF_G12)

    # Saturday: 2026-01-17
    dt_sat = datetime(2026, 1, 17, 10, 0, tzinfo=WARSAW_TZ)
    assert calc_g12w.get_tariff_zone(dt_sat) == ZONE_OFF_PEAK_WEEKEND
    assert calc_g12w.is_off_peak(dt_sat) is True
    # G12 standard treats Saturday 10:00 as peak
    assert calc_g12.get_tariff_zone(dt_sat) == ZONE_PEAK_MORNING

    # Easter Monday holiday: 2026-04-06
    dt_easter_mon = datetime(2026, 4, 6, 11, 0, tzinfo=WARSAW_TZ)
    assert calc_g12w.get_tariff_zone(dt_easter_mon) == ZONE_OFF_PEAK_WEEKEND

    # Corpus Christi holiday: 2026-06-04 (Thursday)
    dt_corpus = datetime(2026, 6, 4, 18, 0, tzinfo=WARSAW_TZ)
    assert calc_g12w.get_tariff_zone(dt_corpus) == ZONE_OFF_PEAK_WEEKEND

    # Polish Independence Day: 2026-11-11 (Wednesday)
    dt_nov11 = datetime(2026, 11, 11, 8, 30, tzinfo=WARSAW_TZ)
    assert calc_g12w.get_tariff_zone(dt_nov11) == ZONE_OFF_PEAK_WEEKEND


def test_meter_fixed_winter_time_quirk():
    """Verify +1h boundary shift in summer DST when meter_fixed_winter_time=True."""
    calc_quirk = PGETariffCalculator(
        tariff_variant=TARIFF_G12,
        pge_seasonal_shift=True,
        meter_fixed_winter_time=True,
    )

    # During summer DST (e.g. July):
    # Standard summer midday: 15:00-17:00
    # With quirk (+1h): 16:00-18:00
    dt_15_30 = datetime(2026, 7, 15, 15, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_15_30) == ZONE_PEAK_MORNING

    dt_16_30 = datetime(2026, 7, 15, 16, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_16_30) == ZONE_OFF_PEAK_MIDDAY

    # Night with quirk (+1h): 23:00 to 07:00
    dt_22_30 = datetime(2026, 7, 15, 22, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_22_30) == ZONE_PEAK_EVENING

    dt_23_30 = datetime(2026, 7, 15, 23, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_23_30) == ZONE_OFF_PEAK_NIGHT

    dt_06_30 = datetime(2026, 7, 15, 6, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_06_30) == ZONE_OFF_PEAK_NIGHT

    # During winter (no DST): no shift occurs
    dt_winter_midday = datetime(2026, 1, 15, 13, 30, tzinfo=WARSAW_TZ)
    assert calc_quirk.get_tariff_zone(dt_winter_midday) == ZONE_OFF_PEAK_MIDDAY


def test_windows_and_slots():
    """Verify peak windows and slot times calculation."""
    calc = PGETariffCalculator(pge_seasonal_shift=True)
    d = date(2026, 1, 15)

    start_m, end_m = calc.get_morning_peak_window(d)
    assert start_m.time() == time(6, 0)
    assert end_m.time() == time(13, 0)

    start_e, end_e = calc.get_evening_peak_window(d)
    assert start_e.time() == time(15, 0)
    assert end_e.time() == time(22, 0)

    start_mid, end_mid = calc.get_midday_off_peak_window(d)
    assert start_mid.time() == time(13, 0)
    assert end_mid.time() == time(15, 0)

    slots = calc.get_slot_times(d)
    assert slots == [(0, 0), (5, 0), (6, 0), (13, 0), (15, 0), (22, 0)]
