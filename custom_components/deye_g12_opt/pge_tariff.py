"""PGE Dystrybucja G12 and G12w tariff engine."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .const import (
    TARIFF_G12W,
    ZONE_OFF_PEAK_MIDDAY,
    ZONE_OFF_PEAK_NIGHT,
    ZONE_OFF_PEAK_WEEKEND,
    ZONE_PEAK_EVENING,
    ZONE_PEAK_MORNING,
)

WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def get_easter_date(year: int) -> date:
    """Calculate Easter Sunday for a given year using the Computus algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_val = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_val) // 451
    month = (h + l_val - 7 * m + 114) // 31
    day = ((h + l_val - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def get_polish_public_holidays(year: int) -> set[date]:
    """Get all 13 official Polish statutory public holidays for a given year."""
    easter = get_easter_date(year)
    easter_monday = easter + timedelta(days=1)
    pentecost = easter + timedelta(days=49)
    corpus_christi = easter + timedelta(days=60)

    return {
        date(year, 1, 1),  # Nowy Rok
        date(year, 1, 6),  # Święto Trzech Króli
        easter,  # Wielkanoc
        easter_monday,  # Poniedziałek Wielkanocny
        date(year, 5, 1),  # Święto Pracy
        date(year, 5, 3),  # Święto Konstytucji 3 Maja
        pentecost,  # Zesłanie Ducha Świętego (Zielone Świątki)
        corpus_christi,  # Boże Ciało
        date(year, 8, 15),  # Wniebowzięcie NMP / Święto Wojska Polskiego
        date(year, 11, 1),  # Wszystkich Świętych
        date(year, 11, 11),  # Narodowe Święto Niepodległości
        date(year, 12, 25),  # Boże Narodzenie (pierwszy dzień)
        date(year, 12, 26),  # Boże Narodzenie (drugi dzień)
    }


class PGETariffCalculator:
    """PGE G12/G12w tariff calculator and scheduler."""

    def __init__(
        self,
        tariff_variant: str = TARIFF_G12W,
        pge_seasonal_shift: bool = True,
        meter_fixed_winter_time: bool = False,
    ) -> None:
        """Initialize the tariff calculator."""
        self.tariff_variant = tariff_variant
        self.pge_seasonal_shift = pge_seasonal_shift
        self.meter_fixed_winter_time = meter_fixed_winter_time
        self._holiday_cache: dict[int, set[date]] = {}

    def is_polish_holiday(self, d: date) -> bool:
        """Check if date is a Polish public holiday."""
        if d.year not in self._holiday_cache:
            self._holiday_cache[d.year] = get_polish_public_holidays(d.year)
        return d in self._holiday_cache[d.year]

    def _get_hour_offset(self, dt: datetime) -> int:
        """Get boundary shift offset in hours based on meter quirk."""
        if not self.meter_fixed_winter_time:
            return 0

        # Check DST in Europe/Warsaw
        if dt.tzinfo is None:
            local_dt = dt.replace(tzinfo=WARSAW_TZ)
        else:
            local_dt = dt.astimezone(WARSAW_TZ)

        dst = local_dt.dst()
        return 1 if dst and dst > timedelta(0) else 0

    def is_summer_season(self, d: date) -> bool:
        """Check whether date falls into summer season (Apr 1 to Sep 30)."""
        if not self.pge_seasonal_shift:
            return False
        return (d.month, d.day) >= (4, 1) and (d.month, d.day) <= (9, 30)

    def get_midday_off_peak_hours(self, d: date, dt: datetime) -> tuple[int, int]:
        """Return (start_hour, end_hour) for midday off-peak window."""
        offset = self._get_hour_offset(dt)
        if self.is_summer_season(d):
            return (15 + offset, 17 + offset)
        return (13 + offset, 15 + offset)

    def get_night_off_peak_hours(self, dt: datetime) -> tuple[int, int]:
        """Return (start_hour, end_hour) for night off-peak window."""
        offset = self._get_hour_offset(dt)
        return (22 + offset, 6 + offset)

    def get_tariff_zone(self, dt: datetime) -> str:
        """Determine active tariff zone for given datetime."""
        if dt.tzinfo is not None:
            local_dt = dt.astimezone(WARSAW_TZ)
        else:
            local_dt = dt

        d = local_dt.date()
        t = local_dt.time()
        cur_min = t.hour * 60 + t.minute

        # G12w weekend & holiday check
        if self.tariff_variant == TARIFF_G12W:
            is_weekend = local_dt.weekday() in (5, 6)
            if is_weekend or self.is_polish_holiday(d):
                return ZONE_OFF_PEAK_WEEKEND

        offset = self._get_hour_offset(local_dt)
        night_start_min = (22 + offset) * 60
        night_end_min = (6 + offset) * 60

        # Night window check (22+offset to 24:00, or 00:00 to 06+offset)
        if cur_min >= night_start_min or cur_min < night_end_min:
            return ZONE_OFF_PEAK_NIGHT

        # Midday window check
        mid_start_h, mid_end_h = self.get_midday_off_peak_hours(d, local_dt)
        mid_start_min = mid_start_h * 60
        mid_end_min = mid_end_h * 60

        if mid_start_min <= cur_min < mid_end_min:
            return ZONE_OFF_PEAK_MIDDAY

        if cur_min < mid_start_min:
            return ZONE_PEAK_MORNING

        return ZONE_PEAK_EVENING

    def is_off_peak(self, dt: datetime) -> bool:
        """Return True if given datetime is in any off-peak zone."""
        zone = self.get_tariff_zone(dt)
        return zone in (ZONE_OFF_PEAK_NIGHT, ZONE_OFF_PEAK_MIDDAY, ZONE_OFF_PEAK_WEEKEND)

    def get_morning_peak_window(
        self, target_date: date, dt_context: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return (start_dt, end_dt) for morning peak window."""
        ref_dt = dt_context or datetime.combine(target_date, time(12, 0), tzinfo=WARSAW_TZ)
        offset = self._get_hour_offset(ref_dt)
        start_h = 6 + offset
        mid_start_h, _ = self.get_midday_off_peak_hours(target_date, ref_dt)
        start_dt = datetime.combine(target_date, time(start_h, 0), tzinfo=WARSAW_TZ)
        end_dt = datetime.combine(target_date, time(mid_start_h, 0), tzinfo=WARSAW_TZ)
        return (start_dt, end_dt)

    def get_evening_peak_window(
        self, target_date: date, dt_context: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return (start_dt, end_dt) for evening peak window."""
        ref_dt = dt_context or datetime.combine(target_date, time(12, 0), tzinfo=WARSAW_TZ)
        offset = self._get_hour_offset(ref_dt)
        _, mid_end_h = self.get_midday_off_peak_hours(target_date, ref_dt)
        night_start_h = 22 + offset
        start_dt = datetime.combine(target_date, time(mid_end_h, 0), tzinfo=WARSAW_TZ)
        end_dt = datetime.combine(target_date, time(night_start_h, 0), tzinfo=WARSAW_TZ)
        return (start_dt, end_dt)

    def get_midday_off_peak_window(
        self, target_date: date, dt_context: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return (start_dt, end_dt) for midday off-peak window."""
        ref_dt = dt_context or datetime.combine(target_date, time(12, 0), tzinfo=WARSAW_TZ)
        mid_start_h, mid_end_h = self.get_midday_off_peak_hours(target_date, ref_dt)
        start_dt = datetime.combine(target_date, time(mid_start_h, 0), tzinfo=WARSAW_TZ)
        end_dt = datetime.combine(target_date, time(mid_end_h, 0), tzinfo=WARSAW_TZ)
        return (start_dt, end_dt)

    def get_current_off_peak_end_time(self, dt: datetime) -> datetime | None:
        """Return the exact datetime when the current off-peak window ends, or None if not off-peak."""
        if not self.is_off_peak(dt):
            return None

        local_dt = dt.astimezone(WARSAW_TZ) if dt.tzinfo is not None else dt.replace(tzinfo=WARSAW_TZ)
        zone = self.get_tariff_zone(local_dt)
        offset = self._get_hour_offset(local_dt)
        today = local_dt.date()

        if zone == ZONE_OFF_PEAK_MIDDAY:
            _, mid_end_h = self.get_midday_off_peak_hours(today, local_dt)
            return datetime.combine(today, time(mid_end_h, 0), tzinfo=WARSAW_TZ)

        if zone in (ZONE_OFF_PEAK_NIGHT, ZONE_OFF_PEAK_WEEKEND):
            # Night or weekend/holiday off-peak:
            # Advance day-by-day until reaching a regular workday morning (6 + offset):00
            cur_min = local_dt.hour * 60 + local_dt.minute
            night_end_min = (6 + offset) * 60

            # If currently in the early morning before 06:00 on today:
            candidate_date = today if cur_min < night_end_min else today + timedelta(days=1)

            for _ in range(14):  # Look up to 14 days ahead
                is_weekend = self.tariff_variant == TARIFF_G12W and candidate_date.weekday() in (5, 6)
                is_holiday = self.tariff_variant == TARIFF_G12W and self.is_polish_holiday(candidate_date)
                if not is_weekend and not is_holiday:
                    cand_dt = datetime.combine(candidate_date, time(6, 0), tzinfo=WARSAW_TZ)
                    cand_offset = self._get_hour_offset(cand_dt)
                    return datetime.combine(candidate_date, time(6 + cand_offset, 0), tzinfo=WARSAW_TZ)
                candidate_date += timedelta(days=1)

        return None

    def get_remaining_off_peak_seconds(self, dt: datetime) -> int | None:
        """Return seconds remaining until the end of the current off-peak window, or None."""
        end_dt = self.get_current_off_peak_end_time(dt)
        if end_dt is None:
            return None
        local_dt = dt.astimezone(WARSAW_TZ) if dt.tzinfo is not None else dt.replace(tzinfo=WARSAW_TZ)
        remaining = int((end_dt - local_dt).total_seconds())
        return max(0, remaining)

    def get_slot_times(self, d: date, dt_context: datetime | None = None) -> list[tuple[int, int]]:
        """Return 6 slot start times (hour, minute) for TOU 24h schedule."""
        ref_dt = dt_context or datetime.combine(d, time(12, 0), tzinfo=WARSAW_TZ)
        offset = self._get_hour_offset(ref_dt)
        mid_start_h, mid_end_h = self.get_midday_off_peak_hours(d, ref_dt)
        night_start_h = 22 + offset

        # Prog 1: 00:00 (or 00:00 + offset if needed, but prog 1 is 00:00 start of day)
        # Prog 2: 05:00 (+ offset)
        # Prog 3: 06:00 (+ offset)
        # Prog 4: mid_start_h:00
        # Prog 5: mid_end_h:00
        # Prog 6: night_start_h:00
        return [
            (0, 0),
            (5 + offset, 0),
            (6 + offset, 0),
            (mid_start_h, 0),
            (mid_end_h, 0),
            (night_start_h, 0),
        ]
