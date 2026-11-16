# Deye G12 Optimizer (`deye_g12_opt`)

Adaptive battery charging Home Assistant custom component for **Deye Hybrid Inverters** (including `SUN-15K-SG05LP3-EU-SM2`) using **PGE G12 / G12w** tariffs, PV forecasts, and HA Recorder consumption statistics.

## Key Features

- **Four-Layer Architecture:** Strict separation between Data Layer, Decision Layer (`optimizer.py`), Execution/Supervision Layer (`executor.py`), and external Hardware Layer (`ha-solarman`).
- **PGE G12 / G12w Tariff Engine:** Computes exact off-peak hours (night `22:00–06:00`, winter midday `13:00–15:00`, summer midday `15:00–17:00`), weekend 24h off-peak, and all Polish statutory holidays (Easter, Corpus Christi, etc. via internal Computus algorithm). Includes support for OSD meters running permanently on winter time (`meter_fixed_winter_time`).
- **Inverter Profile Support:**
  - `3PH_LV`: Three-phase low-voltage inverters (`SG04LP3`, `SUN-15K-SG05LP3-EU-SM2`). Register `146` bitfield preservation, protection against accidental writes to EV Charging register `259`, and bit 5 (`Sell`) mode preservation in registers `172–177`.
  - `1PH_LV`: Single-phase low-voltage inverters (`SG01LP1`, `SG03LP1`).
  - `3PH_HV`: Three-phase high-voltage inverters (`SG01HP3`, 10W scaling).
  - `ALL_IN_ONE_BK`: SUN-BK series.
- **Dual Control Modes:**
  - **Mode A (`TOU_SCHEDULE`):** Standard 6-slot Time of Use schedule with deadband write-throttling ($<2\%$ SOC, $<2\text{ A}$ current) to protect inverter EEPROM.
  - **Mode B (`REMOTE_MODE_RAM`):** High-speed volatile RAM registers (`1100+`) via Modbus RTU/TCP (`FC16`), periodic watchdog refreshes on register `1101`, signed 16-bit power conversions, and PV throttling guards (never write $\ge 1000$ to register `1115`).
- **PV Forecast Adapters:** Auto-detects Solcast (with optional 70/30 pessimistic blend), Open-Meteo Solar Forecast (`wh_period`), and Forecast.Solar / generic fallbacks.
- **Recorder Statistics:** Queries historical 14-day hourly consumption data with outlier filtering ($<0$ or $>25\text{ kWh/h}$) to anticipate morning and evening peak loads accurately.
- **Safety First:** Starts in **Simulation Mode** by default (`logbook.log` only), contains BMS alarm interrupts, undervoltage standby locks, and read-back verification with automatic retries.

## Hardware Setup Tip for `SUN-15K-SG05LP3-EU-SM2`

When connecting via RS485 (BMS/Modbus port, 9600 8N1, Slave, Modbus SN = 1) with `davidrapan/ha-solarman`, ensure `battery_packs = -1` is configured in `ha-solarman` if custom battery packs are used without external CAN pack addressing. Start with `Simulation Mode` enabled in Home Assistant to inspect planned target SOC calculations before allowing direct inverter writes.

