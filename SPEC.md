---
name: build-deye-g12-plugin
description: Builds, configures, lints, and thoroughly tests the Home Assistant custom component 'deye_g12_opt' for adaptive battery charging on Deye Hybrid inverters (including SUN-15K-SG05LP3-EU-SM2) using ha-solarman, PGE G12/G12w tariffs, PV forecasts, and Recorder statistics.
---

# Specification: Build & Test `deye_g12_opt` Home Assistant Custom Component

## 1. Mission & Engineering Principles
You are tasked with designing, implementing, linting, and verifying a complete, production-grade Home Assistant custom integration named `deye_g12_opt` (domain: `deye_g12_opt`) inside `custom_components/deye_g12_opt/`.

Follow these strict architectural principles:
1. **Four-Layer Separation ("One System Decides, One System Writes"):**
   - **Data Layer:** PV forecast sensors, HA `recorder` hourly consumption statistics, PGE G12/G12w tariff clock, and Deye inverter/BMS telemetry via `davidrapan/ha-solarman`.
   - **Decision Layer (`optimizer.py`):** Pure, side-effect-free mathematical calculation of `SOC_target`, PV surplus headroom, grid-charge enable flag, and optimal charge current `I_chg`.
   - **Execution & Supervision Layer (`executor.py`):** Single guarded gateway performing all writes with deadband filtering, BMS alarm checks, simulation-only mode (`logbook.log`), and read-back verification.
   - **Hardware Layer (external):** `ha-solarman` integration (local Solarman V5 TCP port 8899 or RS485 Modbus TCP bridge).
2. **Code Quality Standards (Home Assistant 2026.x):**
   - Target Python `>= 3.14.2` and Home Assistant Core `>= 2026.10.0`.
   - Full type annotations (`from __future__ import annotations`, strict `pyright` / `mypy` compatibility).
   - Async-first implementation (`DataUpdateCoordinator`, non-blocking `get_instance(hass).async_add_executor_job` for Recorder DB queries).
   - Clean Polish (`translations/pl.json`) and English (`translations/en.json` + `strings.json`) UI translations.

---

## 2. Repository Layout to Create

Create the following workspace structure:

.
├── pyproject.toml
├── hacs.json
├── README.md
├── custom_components/
│   └── deye_g12_opt/
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py
│       ├── pge_tariff.py
│       ├── pv_forecast.py
│       ├── recorder_stats.py
│       ├── optimizer.py
│       ├── executor.py
│       ├── coordinator.py
│       ├── sensor.py
│       ├── switch.py
│       ├── number.py
│       ├── strings.json
│       └── translations/
│           ├── en.json
│           └── pl.json
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_pge_tariff.py
    ├── test_pv_forecast.py
    ├── test_recorder_stats.py
    ├── test_optimizer.py
    ├── test_executor.py
    ├── test_config_flow.py
    └── test_coordinator.py

---

## 3. Domain Requirements & Exact Inverter Constraints

### 3.1. PGE Dystrybucja G12 & G12w Tariff Rules (`pge_tariff.py`)
- **Night off-peak (all year):** `22:00` to `06:00` (8 hours).
- **Midday off-peak (Monday–Friday):**
  - **Winter season (`Oct 1` – `Mar 31`):** `13:00` to `15:00` (morning peak `06:00–13:00`, evening peak `15:00–22:00`).
  - **Summer season (`Apr 1` – `Sep 30`):** `15:00` to `17:00` (morning peak `06:00–15:00`, evening peak `17:00–22:00`).
  - Configurable boolean `pge_seasonal_shift` (default `True`; if `False`, always uses `13:00–15:00`).
- **Tariff variant `G12w`:** Weekends (Saturday, Sunday) and Polish statutory public holidays are 24h off-peak (`00:00–24:00`). Implement Polish public holiday calculation (fixed dates + Easter/Corpus Christi via Computus algorithm—zero external heavy dependency required, or optional `holidays` if clean).
- **OSD Meter Winter-Time Quirk (`meter_fixed_winter_time`):** Default `False`. When `True` and local timezone (`Europe/Warsaw`) is currently in DST (`dst() > 0`), shift all tariff boundary hours by `+1 hour` in HA local time (e.g., night becomes `23:00–07:00`, summer midday becomes `16:00–18:00`).
- **Tariff Cost Defaults:** Expose configurable peak/off-peak prices (defaults: `1.19` PLN/kWh peak, `0.55` PLN/kWh off-peak) for diagnostic savings estimation sensors.

### 3.2. Deye Hybrid Control Modes & Register Safety (`const.py` & `executor.py`)
Support two selectable control backends in `ConfigFlow`:

#### Mode A: `TOU_SCHEDULE` (EEPROM / Standard Registers via `ha-solarman`)
Support four hardware profiles (`INVERTER_PROFILE`):
1. `1PH_LV` (`SG01LP1` / `SG03LP1` / `deye_hybrid.yaml`):
   - TOU Enable: `248` (`0x00F8`), Slot Times: `250–255`, Slot Powers: `256–261`, Slot SOC: `268–273`, Slot Charge Mode: `274–279`, Grid Charge Current: `230`, Battery Max Charge Current: `210`.
2. `3PH_LV` (`SG04LP3` / `SUN-15K-SG05LP3-EU-SM2` / `deye_p3.yaml`):
   - TOU Enable + Days bitfield: `146` (bit 0 = timer enable, bits 1–7 = Mon–Sun; **NEVER overwrite bits 1–7 when toggling bit 0**).
   - Slot Times: `148–153`, Slot Powers: `154–159`, Slot SOC: `166–171`, Slot Charge Mode: `172–177`.
   - Grid Charge Current: `128`, Grid Charge Master Enable: `130`, Battery Max Charge Current: `108`, Battery Max Discharge Current: `109`.
   - **CRITICAL WARNING FOR `3PH_LV` / `SG05LP3`:** Never confuse 1PH addresses (`248`, `250–279`) with 3PH (`146`, `148–177`). On `SUN-15K-SG05LP3-EU-SM2`, register **`259`** holds packed **EV Charging** configuration bits!
   - **Extended `Sell` Bit Preservation in `172–177`:** Values `0–3` (`Disabled`, `Grid`, `Gen`, `Both`) can have bit 5 (`+32`) set (`32=Disabled+Sell`, `33=Grid+Sell`, `34=Gen+Sell`, `35=Both+Sell`). Always preserve bit 5 (`Sell`) when toggling Grid Charge!
3. `3PH_HV` (`SG01HP3`):
   - Same TOU register map as `3PH_LV`, except Power registers (`154–159`) use `10 W` scale (`× 10`), and battery voltage is HV (`160–700 V`, default `307.2 V`).
4. `ALL_IN_ONE_BK` (`SUN-BK` / `deye_bk250.yaml`):
   - Work Mode `334`, TOU Enable `335`, Times `336–341`, Powers `342–347`, SOC `354–359`, Charge `360–365`.

**6-Slot 24h Mapping (Winter / Summer before DST offset):**
- `Program 1`: `00:00` – `05:00` (`Grid Charge = ON`, `SOC_night_target`)
- `Program 2`: `05:00` – `06:00` (`Grid Charge = ON`, `SOC_night_target` or `100%` on BMS balancing day)
- `Program 3`: `06:00` – `13:00` (Winter) / `15:00` (Summer) (`Grid Charge = OFF`, `SOC_min`)
- `Program 4`: `13:00–15:00` (Winter) / `15:00–17:00` (Summer) (`Grid Charge = ON` if charging needed else `OFF`, `SOC_midday_target` or `max(SOC_now, SOC_midday_target)` with `Grid Charge = OFF` to preserve battery charge during cheap midday window!)
- `Program 5`: `15:00` (Winter) / `17:00` (Summer) – `22:00` (`Grid Charge = OFF`, `SOC_min`)
- `Program 6`: `22:00` – `00:00` (`Grid Charge = ON`, `SOC_night_target`)
- Only write slot start times (`Prog 1–6 Time`) on integration startup, on season transitions (`Apr 1`, `Oct 1`), or on DST transitions when `meter_fixed_winter_time=True`.
- Apply **Deadband Write-Throttling**: skip writing `SOC` if `abs(current - target) < 2.0`, skip writing `Current` if `abs(current - target) < 2.0`.
- Support entity-based writes (`number.set_value`, `select.select_option`, `switch.turn_on/off`, `time.set_value`) AND optional direct `solarman.write_multiple_registers` (`FC16` / `0x10`) using `device_id`, `address`, `values: [int]` when raw register mode is enabled.
- Perform **Read-Back Verification** after 10 seconds (configurable delay, mockable in tests) and retry once if discrepancy exceeds `2%`.

#### Mode B: `REMOTE_MODE_RAM` (Volatile RAM Registers `1100+` for `SUN-15K-SG05LP3-EU-SM2`)
For inverters connected via RS485 (`Modbus` RJ45 port, `9600 8N1`, `Slave`, `Modbus SN = 1`) with firmware supporting Remote Mode (`1100+`):
- All writes MUST use `solarman.write_multiple_registers` (`FC16` / `0x10`, even for a single register) targeting the configured `solarman` device ID.
- Register semantics:
  - `1100`: Remote Mode switch (`0` = Off, `1` = On).
  - `1101`: Watchdog timer in seconds (`10–600`, e.g., `180` or `300`). Coordinator MUST periodically refresh **register `1101` itself** (e.g., every `60 s`) while Remote Mode is active—writing `1109` alone does NOT reset the watchdog! If HA stops or disconnects, watchdog expires, resets power to `0`, sets `1100 = 0`, and inverter safely reverts to EEPROM TOU schedule.
  - `1104`: Control Side (`0` = AC, `1` = Battery/DC, `2` = Grid). Default `1` (Battery side: fixed battery power while PV operates freely).
  - `1105`: Battery Strategy (`2` = Power via `1109`, `5` = Power + Target SOC via `1110`).
  - `1109`: Signed 16-bit (`S_WORD`, convert negative integers to unsigned 16-bit `val & 0xFFFF` for Modbus `values`) in `0.1%` of `rated_power_w` (`+` = Discharge, `-` = Charge). Clamp safely to configured limits (e.g., never exceed `rated_power_w` or BMS CCL/DCL).
  - `1110`: Target SOC in `0.1%` (e.g., `80%` -> `800`).
  - `1115`: Max PV limit — **CRITICAL SAFETY GUARD: NEVER write `>= 1000` to `1115` (that throttles PV to `0 W`); keep at `999` or omit unless explicitly configured.**
- **CRITICAL BMS/SOC SAFETY GUARD IN REMOTE MODE:** In `SUN-15K-SG05LP3-EU-SM2` Remote Mode, internal inverter SOC protection thresholds may be bypassed. Therefore, `deye_g12_opt` MUST continuously monitor `battery_soc` and:
  - Immediately disable Remote Mode (`1100 = 0`, `1109 = 0`) if `battery_soc <= soc_min` during discharge or `battery_soc >= soc_max` during grid charge (except balancing `100%`).
  - Never leave the inverter idling in Remote Mode standby if `battery_soc <= soc_min + 2%` to prevent standby self-consumption from tripping BMS undervoltage shutdown.
  - Perform an `FC03` read check (`solarman.read_holding_registers` on `1100`) before first activation to verify Remote Mode firmware availability.

### 3.3. PV Forecast Parsers (`pv_forecast.py`)
Support three HA PV forecast integrations automatically by inspecting attributes of configured `today` / `tomorrow` / `remaining` entities:
1. **Solcast (`BJReplay/ha-solcast-solar`):** Parses `detailedForecast` list (`period_start`, `pv_estimate`, `pv_estimate10`, `pv_estimate90`). Each 30-min interval contributes `power_kw * 0.5`. Support configurable `use_pessimistic_blend` (`0.7 * pv_estimate + 0.3 * pv_estimate10`).
2. **Open-Meteo Solar Forecast:** Parses `wh_period` dict (`{iso_timestamp: wh_value}`), converting Wh to kWh inside `[start_dt, end_dt)`.
3. **Forecast.Solar / Generic Fallback:** Uses total state value (`kWh` or `Wh` normalized by `unit_of_measurement`), distributing energy proportionally across daylight hours (`sun.sun` rising/setting or default 10h daylight window).
- Fail-safe: If forecast entity is `None`, `unknown`, or `unavailable`, log warning, mark diagnostic `forecast_available = False`, and safely return `0.0 kWh` (conservative fallback so battery covers peak load).

### 3.4. Recorder Consumption Estimator (`recorder_stats.py`)
- Query `homeassistant.components.recorder.statistics.statistics_during_period` via `get_instance(hass).async_add_executor_job(...)` for the last `14` days (`period="hour"`, `types={"change"}`) on the configured `total_load_entity_id` (`state_class: total_increasing`).
- Group hourly `change` values into daily buckets filtered to `[start_hour, end_hour)` (ignoring anomalous outliers `< 0.0` or `> 25.0 kWh/h`), compute average window consumption.
- Support wrapping across midnight if needed and fallback to user-configured default hourly load (`0.65 kWh/h * window_hours`) when Recorder has no data yet.

### 3.5. Mathematical Optimization Model (`optimizer.py`)
Implement `DeyeG12Optimizer` with configurable parameters:
- `battery_capacity_kwh` (e.g., `15.0`), `battery_voltage_v` (`51.2` LV / `307.2` HV), `rated_power_w` (e.g., `15000`).
- `soc_min_pct` (`15.0`), `soc_max_pct` (`95.0`).
- `eta_chg` (`0.94`), `eta_dis` (`0.94`), `eta_pv` (`0.97`).
- `safety_pv` (`0.85`), `safety_load` (`1.10`).
- `min_charge_current_a` (`5` LV / `2` HV), `max_user_charge_current_a` (`80`).
- `balancing_interval_days` (default `14`): track timestamp of last `battery_soc >= 99%`; if exceeded, set `force_balancing_100 = True` for the next `NIGHT` cycle (`Program 2` / full night).

**Night Plan (evaluated at `21:55` local / adjusted for meter offset, effective charge time `T_eff = 7.5 h`):**
- `pv_eff = e_pv_am * safety_pv`, `load_eff = e_load_am * safety_load`
- `e_def_ac = max(0.0, load_eff - pv_eff)`
- `e_req_dc = e_def_ac / eta_dis`
- **PV Headroom Reservation:**
  - `e_surplus_dc = max(0.0, (pv_eff - load_eff) * eta_pv)`
  - `soc_headroom_max = clip(soc_max - (e_surplus_dc / cap_kwh) * 100.0, soc_min, soc_max)`
- `soc_raw = soc_min + (e_req_dc / cap_kwh) * 100.0`
- `soc_target = round(clip(min(soc_raw, soc_headroom_max), soc_min, soc_max))`
- If `soc_target - soc_now < 2.0`: `grid_charge_enable = False`, `charge_current_a = min_charge_current_a`.
- Else:
  - `e_chg_dc = ((soc_target - soc_now) / 100.0) * cap_kwh`
  - `p_chg_dc_w = (e_chg_dc * 1000.0) / 7.5`
  - `i_chg = clip(ceil(p_chg_dc_w / battery_voltage_v), min_charge_current_a, min(max_user_charge_current_a, bms_max_current_a))`

**Midday Plan (evaluated 5 minutes before midday off-peak, effective charge time `T_eff_mid = 1.75 h`):**
- Evaluate remaining PV vs expected evening peak load (`e_load_pm`).
- Compute `soc_target_mid` and required current over `1.75 h` (capped by `min(max_user_charge_current_a, bms_max_current_a)`).

### 3.6. Entities Exposed by `deye_g12_opt`
- **Switches (`switch.py`):**
  - `Automation Active` (Master Kill Switch, persisted via `RestoreEntity`).
  - `Simulation Mode` (logs planned writes to `logbook.log` without touching Modbus, default `True` on fresh install for safety!).
  - `Emergency Reserve (Storm Watch 100%)` (overrides target SOC to `100%`).
- **Numbers (`number.py`):**
  - `SOC Minimum Reserve (%)`, `SOC Maximum Grid Target (%)`, `Max Grid Charge Current (A)`, `Balancing Interval (days)`.
- **Sensors (`sensor.py`):**
  - `Planned SOC Target (%)`, `Planned Charge Current (A)`, `Current PGE Tariff Zone` (`Off-Peak Night` / `Off-Peak Midday` / `Off-Peak Weekend` / `Peak Morning` / `Peak Evening`), `Expected Morning Load (kWh)`, `Expected Morning PV (kWh)`, `Headroom Max SOC (%)`, `Estimated Daily Arbitrage Savings (PLN)`, `Last Execution Status` (with detailed reason attribute).

### 3.7. Config Flow & Options Flow (`config_flow.py`)
Multi-step UI flow:
- Step 1 (`user`): Integration title, `Inverter Profile` (`3PH_LV`, `1PH_LV`, `3PH_HV`, `ALL_IN_ONE_BK`), `Control Mode` (`TOU_SCHEDULE` or `REMOTE_MODE_RAM`), and `ha-solarman` device selector (or entity pickers using Home Assistant `EntitySelector`).
- Step 2 (`entities`): Battery SOC sensor, Total Load Consumption sensor, BMS Alarm sensor (optional), BMS Charge Current Limit sensor (optional), PV Forecast entity (`today`/`tomorrow`/`remaining`), plus `ha-solarman` TOU entity mapping (auto-suggested by prefix or selectable).
- Step 3 (`system_and_tariff`): Battery capacity (kWh), Battery voltage (V), Rated inverter power (W), PGE tariff (`G12` / `G12w`), `pge_seasonal_shift`, `meter_fixed_winter_time`.
- `OptionsFlow`: Allows tuning all parameters, efficiencies, safety factors, and switching between `TOU_SCHEDULE` and `REMOTE_MODE_RAM` without re-adding the entry.

---

## 4. Testing & Tooling Requirements (2026 Standards)

**CRITICAL DEPENDENCY RULE:**
- Do **NOT** use the deprecated/broken `pytest-homeassistant-custom-component` package in `pyproject.toml`.
- Use the centrally maintained **`pytest-homeassistant`** package (e.g., `pytest-homeassistant>=2026.10.0`), `pytest>=8.0`, `pytest-asyncio`, `pytest-cov`, `ruff`, and `pyright`.
- Remember: in `pytest-homeassistant`, custom integrations in `custom_components/` are enabled automatically (and `hass`, `recorder_mock`, `MockConfigEntry` fixtures are provided cleanly; if `recorder_mock` is used in a test, ensure proper fixture ordering).

### Required Test Suite (`tests/`)
1. `tests/test_pge_tariff.py`:
   - Winter (`2026-01-15`, `2026-03-31`) vs Summer (`2026-04-01`, `2026-07-15`, `2026-09-30`) boundaries.
   - `G12` vs `G12w` on Saturday, Sunday, and Polish holidays (e.g. `2026-05-01`, `2026-11-11`, Easter Monday `2026-04-06`, Corpus Christi `2026-06-04`).
   - `meter_fixed_winter_time=True` during summer DST (UTC+2) vs winter standard time (UTC+1).
2. `tests/test_pv_forecast.py`:
   - Solcast `detailedForecast` window slicing & pessimistic blend.
   - Open-Meteo `wh_period` window slicing.
   - Forecast.Solar fallback & `Wh` unit conversion.
   - Unavailable/unknown entity returning `0.0` safely.
3. `tests/test_recorder_stats.py`:
   - Mock `statistics_during_period` returning multi-day hourly `change` records (including outlier spikes `> 25.0` and missing days) and verify accurate morning/evening averages and fallback behavior.
4. `tests/test_optimizer.py`:
   - **Winter cloudy scenario** (`15 kWh`, `51.2 V`, `SOC=18%`, `load=8.5 kWh`, `pv=0.8 kWh`) -> verify ~`77%` SOC target, ~`23 A` charge current.
   - **Spring mixed scenario** (`SOC=25%`, `load=6.0 kWh`, `pv=4.5 kWh`) -> verify moderate target (`~35%`) and minimum current `5 A`.
   - **Summer sunny scenario** (`SOC=35%`, `load=4.5 kWh`, `pv=14.0 kWh`) -> verify headroom caps target and `grid_charge_enable=False` (`0` grid charge).
   - **BMS limit clamping** (e.g., `bms_max_current_a=15` clamps calculated current).
   - **Force balancing 100%** and **Emergency Reserve 100%**.
5. `tests/test_executor.py`:
   - **TOU Mode (`3PH_LV` / `SUN-15K-SG05LP3-EU-SM2`):**
     - Verify deadband skips redundant writes (`< 2%`, `< 2 A`).
     - Verify `Sell` flag preservation (`"Grid + Sell"` / `"Disabled + Sell"` when currently `"Sell"` is active, and bitmask preservation on raw register `146` / `172–177`).
     - Verify `259` is NEVER written as a TOU slot in `3PH_LV` mode.
     - Verify `Simulation Mode` logs to `logbook.log` and issues zero `number`/`select`/`solarman` writes.
     - Verify BMS alarm or `unavailable` battery SOC aborts writes safely.
     - Verify Read-Back Verification triggers a retry when state does not match after delay.
   - **Remote Mode (`1100+` RAM):**
     - Verify `solarman.write_multiple_registers` calls with unsigned 16-bit two's complement conversion for negative charging power (`1109`).
     - Verify watchdog `1101` keepalive writes specifically to register `1101`.
     - Verify safety guard NEVER writes `>= 1000` to `1115`.
     - Verify immediate shutdown (`1100 = 0`, `1109 = 0`) if `battery_soc <= soc_min` or `battery_soc >= soc_max`.
6. `tests/test_config_flow.py` & `tests/test_coordinator.py`:
   - Full `MockConfigEntry` setup/unload cycle, multi-step `ConfigFlow` + `OptionsFlow`, scheduled trigger simulation (`21:55`, `12:55`, `14:55`), and watchdog timer loop.

---

## 5. Step-by-Step Execution Workflow for Antigravity

Execute these steps in order:
1. **Plan First:** Generate a concise implementation plan outlining any assumptions and confirm module interfaces before creating files.
2. **Environment & Scaffolding:** Create `pyproject.toml` (with `pytest-homeassistant`, `ruff`, `pyright`, `pytest-cov`, `pytest-asyncio`), `hacs.json`, and `manifest.json`. Check Python and dependency availability in a virtual environment (`uv` or `venv`).
3. **Implement Core Modules:** Build `const.py`, `pge_tariff.py`, `pv_forecast.py`, `recorder_stats.py`, `optimizer.py`, `executor.py`.
4. **Implement HA Integration Layer:** Build `config_flow.py`, `coordinator.py`, `__init__.py`, `sensor.py`, `switch.py`, `number.py`, `strings.json`, `translations/en.json`, and `translations/pl.json`.
5. **Write Tests:** Implement all test files under `tests/` with high branch coverage (`>= 90%`).
6. **Automated Verification Loop:** Run the following checks in the terminal and fix every single error or warning until all pass cleanly:
   ```bash
   ruff check .
   ruff format --check .
   pyright
   pytest -v --cov=custom_components/deye_g12_opt --cov-report=term-missing
   ```
   Also validate all JSON files (`manifest.json`, `strings.json`, `translations/*.json`, `hacs.json`) for valid syntax and required Home Assistant manifest keys (`domain`, `name`, `codeowners`, `config_flow`, `dependencies`, `after_dependencies`, `iot_class`, `version`).
7. **Final Deliverable Report:** Present the test results, coverage percentage, list of created files, and a concise user checklist for installing in Home Assistant (including the `ha-solarman` `battery_packs = -1` tip for `SUN-15K-SG05LP3-EU-SM2` over RS485 and starting in `Simulation Mode`).