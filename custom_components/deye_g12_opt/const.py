"""Constants and register definitions for Deye G12 Optimizer."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "deye_g12_opt"

# Control Backends
MODE_TOU_SCHEDULE: Final = "tou_schedule"
MODE_REMOTE_RAM: Final = "remote_mode_ram"
CONTROL_MODES: Final = [MODE_TOU_SCHEDULE, MODE_REMOTE_RAM]

# Optimization Strategies
STRATEGY_MAX_CHARGE_CHEAP: Final = "max_charge_cheap"
STRATEGY_LEAN_PEAK_COVER: Final = "lean_peak_cover"
OPTIMIZATION_STRATEGIES: Final = [STRATEGY_MAX_CHARGE_CHEAP, STRATEGY_LEAN_PEAK_COVER]

# Hardware Profiles
PROFILE_1PH_LV: Final = "1ph_lv"
PROFILE_3PH_LV: Final = "3ph_lv"
PROFILE_3PH_HV: Final = "3ph_hv"
PROFILE_ALL_IN_ONE_BK: Final = "all_in_one_bk"
INVERTER_PROFILES: Final = [
    PROFILE_3PH_LV,
    PROFILE_1PH_LV,
    PROFILE_3PH_HV,
    PROFILE_ALL_IN_ONE_BK,
]

# PGE Tariffs
TARIFF_G12: Final = "g12"
TARIFF_G12W: Final = "g12w"
TARIFF_VARIANTS: Final = [TARIFF_G12, TARIFF_G12W]

# Tariff Zones
ZONE_OFF_PEAK_NIGHT: Final = "off_peak_night"
ZONE_OFF_PEAK_MIDDAY: Final = "off_peak_midday"
ZONE_OFF_PEAK_WEEKEND: Final = "off_peak_weekend"
ZONE_PEAK_MORNING: Final = "peak_morning"
ZONE_PEAK_EVENING: Final = "peak_evening"

# TOU Charge Modes (bits 0-1)
TOU_CHARGE_DISABLED: Final = 0
TOU_CHARGE_GRID: Final = 1
TOU_CHARGE_GEN: Final = 2
TOU_CHARGE_BOTH: Final = 3
SELL_BIT_MASK: Final = 0x20  # Bit 5 (+32) preserves Sell mode

# Remote Mode RAM Register Map (1100+)
REG_REMOTE_ENABLE: Final = 1100  # 0=Off, 1=On
REG_REMOTE_WATCHDOG: Final = 1101  # Timeout in seconds (10-600)
REG_REMOTE_CONTROL_SIDE: Final = 1104  # 0=AC, 1=Battery/DC, 2=Grid
REG_REMOTE_BATTERY_STRATEGY: Final = 1105  # 2=Power, 5=Power + Target SOC
REG_REMOTE_POWER: Final = 1109  # 0.1% rated power, signed 16-bit (+ dischg, - chg)
REG_REMOTE_TARGET_SOC: Final = 1110  # Target SOC in 0.1% (800 = 80%)
REG_REMOTE_MAX_PV: Final = 1115  # Max PV limit (CRITICAL: NEVER >= 1000)

# Register Maps for TOU Hardware Profiles
TOU_REGISTERS: Final[dict[str, dict[str, int | list[int]]]] = {
    PROFILE_1PH_LV: {
        "enable": 248,
        "times": [250, 251, 252, 253, 254, 255],
        "powers": [256, 257, 258, 259, 260, 261],
        "socs": [268, 269, 270, 271, 272, 273],
        "modes": [274, 275, 276, 277, 278, 279],
        "grid_charge_current": 230,
        "battery_max_charge_current": 210,
        "power_scale": 1,
    },
    PROFILE_3PH_LV: {
        # bit 0 = timer enable, bits 1-7 = Mon-Sun days bitfield
        "enable": 146,
        "times": [148, 149, 150, 151, 152, 153],
        "powers": [154, 155, 156, 157, 158, 159],
        "socs": [166, 167, 168, 169, 170, 171],
        "modes": [172, 173, 174, 175, 176, 177],
        "grid_charge_current": 128,
        "grid_charge_enable": 130,
        "battery_max_charge_current": 108,
        "battery_max_discharge_current": 109,
        "power_scale": 1,
    },
    PROFILE_3PH_HV: {
        "enable": 146,
        "times": [148, 149, 150, 151, 152, 153],
        "powers": [154, 155, 156, 157, 158, 159],
        "socs": [166, 167, 168, 169, 170, 171],
        "modes": [172, 173, 174, 175, 176, 177],
        "grid_charge_current": 128,
        "grid_charge_enable": 130,
        "battery_max_charge_current": 108,
        "battery_max_discharge_current": 109,
        "power_scale": 10,  # 10 W scale on SG01HP3
    },
    PROFILE_ALL_IN_ONE_BK: {
        "work_mode": 334,
        "enable": 335,
        "times": [336, 337, 338, 339, 340, 341],
        "powers": [342, 343, 344, 345, 346, 347],
        "socs": [354, 355, 356, 357, 358, 359],
        "modes": [360, 361, 362, 363, 364, 365],
        "power_scale": 1,
    },
}

# Configuration Keys
CONF_INVERTER_PROFILE: Final = "inverter_profile"
CONF_CONTROL_MODE: Final = "control_mode"
CONF_OPTIMIZATION_STRATEGY: Final = "optimization_strategy"
CONF_SOLARMAN_DEVICE_ID: Final = "solarman_device_id"

CONF_BATTERY_SOC_ENTITY: Final = "battery_soc_entity"
CONF_TOTAL_LOAD_ENTITY: Final = "total_load_entity"
CONF_BMS_ALARM_ENTITY: Final = "bms_alarm_entity"
CONF_BMS_CCL_ENTITY: Final = "bms_ccl_entity"
CONF_PV_FORECAST_ENTITY: Final = "pv_forecast_entity"
CONF_PV_FORECAST_TOMORROW_ENTITY: Final = "pv_forecast_tomorrow_entity"
CONF_TOU_PREFIX: Final = "tou_prefix"

CONF_BATTERY_CAPACITY_KWH: Final = "battery_capacity_kwh"
CONF_BATTERY_VOLTAGE_V: Final = "battery_voltage_v"
CONF_RATED_POWER_W: Final = "rated_power_w"
CONF_PGE_TARIFF: Final = "pge_tariff"
CONF_PGE_SEASONAL_SHIFT: Final = "pge_seasonal_shift"
CONF_METER_FIXED_WINTER_TIME: Final = "meter_fixed_winter_time"
CONF_USE_PESSIMISTIC_BLEND: Final = "use_pessimistic_blend"

CONF_PEAK_PRICE: Final = "peak_price"
CONF_OFF_PEAK_PRICE: Final = "off_peak_price"

CONF_SOC_MIN: Final = "soc_min"
CONF_SOC_MAX: Final = "soc_max"
CONF_ETA_CHG: Final = "eta_chg"
CONF_ETA_DIS: Final = "eta_dis"
CONF_ETA_PV: Final = "eta_pv"
CONF_SAFETY_PV: Final = "safety_pv"
CONF_SAFETY_LOAD: Final = "safety_load"
CONF_MIN_CHARGE_CURRENT_A: Final = "min_charge_current_a"
CONF_MAX_USER_CHARGE_CURRENT_A: Final = "max_user_charge_current_a"
CONF_BALANCING_INTERVAL_DAYS: Final = "balancing_interval_days"
CONF_READBACK_DELAY_SEC: Final = "readback_delay_sec"
CONF_WATCHDOG_TIMEOUT_SEC: Final = "watchdog_timeout_sec"

# Default Values
DEFAULT_BATTERY_CAPACITY_KWH: Final = 15.0
DEFAULT_BATTERY_VOLTAGE_V_LV: Final = 51.2
DEFAULT_BATTERY_VOLTAGE_V_HV: Final = 307.2
DEFAULT_RATED_POWER_W: Final = 15000
DEFAULT_SOC_MIN: Final = 15.0
DEFAULT_SOC_MAX: Final = 100.0
DEFAULT_ETA_CHG: Final = 0.94
DEFAULT_ETA_DIS: Final = 0.94
DEFAULT_ETA_PV: Final = 0.97
DEFAULT_SAFETY_PV: Final = 0.85
DEFAULT_SAFETY_LOAD: Final = 1.10
DEFAULT_MIN_CHARGE_CURRENT_A_LV: Final = 5
DEFAULT_MIN_CHARGE_CURRENT_A_HV: Final = 2
DEFAULT_MAX_USER_CHARGE_CURRENT_A: Final = 80
DEFAULT_BALANCING_INTERVAL_DAYS: Final = 14
DEFAULT_PEAK_PRICE: Final = 1.19
DEFAULT_OFF_PEAK_PRICE: Final = 0.55
DEFAULT_READBACK_DELAY_SEC: Final = 10
DEFAULT_WATCHDOG_TIMEOUT_SEC: Final = 180
DEFAULT_FALLBACK_HOURLY_LOAD_KWH: Final = 0.65

# Entity Unique IDs and Icons
SWITCH_AUTOMATION_ACTIVE: Final = "automation_active"
SWITCH_SIMULATION_MODE: Final = "simulation_mode"
SWITCH_EMERGENCY_RESERVE: Final = "emergency_reserve"

NUMBER_SOC_MIN: Final = "soc_min"
NUMBER_SOC_MAX: Final = "soc_max"
NUMBER_MAX_CHARGE_CURRENT: Final = "max_charge_current"
NUMBER_BALANCING_INTERVAL: Final = "balancing_interval"

SENSOR_PLANNED_SOC_TARGET: Final = "planned_soc_target"
SENSOR_PLANNED_CHARGE_CURRENT: Final = "planned_charge_current"
SENSOR_CURRENT_TARIFF_ZONE: Final = "current_tariff_zone"
SENSOR_EXPECTED_MORNING_LOAD: Final = "expected_morning_load"
SENSOR_EXPECTED_MORNING_PV: Final = "expected_morning_pv"
SENSOR_HEADROOM_MAX_SOC: Final = "headroom_max_soc"
SENSOR_DAILY_ARBITRAGE_SAVINGS: Final = "daily_arbitrage_savings"
SENSOR_LAST_EXECUTION_STATUS: Final = "last_execution_status"
