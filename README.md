# MISO Stats for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/anthonysecco/miso-stats)](https://github.com/anthonysecco/miso-stats/releases)
[![Validate](https://github.com/anthonysecco/miso-stats/actions/workflows/validate.yml/badge.svg)](https://github.com/anthonysecco/miso-stats/actions/workflows/validate.yml)
[![Tests](https://github.com/anthonysecco/miso-stats/actions/workflows/tests.yml/badge.svg)](https://github.com/anthonysecco/miso-stats/actions/workflows/tests.yml)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-donate-yellow.svg)](https://www.buymeacoffee.com/anthonysecco)

The Midcontinent grid, live in Home Assistant: demand and the load forecast, the fuel mix, renewable and carbon-free shares, an **estimated CO2 intensity**, and a **Carbon Intensity Index** that compares the grid right now with the last two weeks. Use them on dashboards and in automations, for example to run the EV charger or water heater when the grid is clean.

Data comes from MISO's free public [real-time data APIs](https://www.misoenergy.org/markets-and-operations/RTDataAPIs/), the same feeds behind its [market displays](https://www.misoenergy.org/markets-and-operations/real-time--market-data/markets-displays/). No account, API key or cloud service is needed.

Optionally, add a free key for MISO's [Emissions API](https://miso.singularity.energy/api-docs) (run for MISO by Singularity Energy): every carbon number then comes from measured data for **your** utility, zone or state instead of a MISO-wide estimate. See [Local emissions](#local-emissions-optional).

> **Unofficial.** This project is not affiliated with or endorsed by MISO (Midcontinent Independent System Operator). The feeds are public but lightly documented and can change without notice. A daily CI job checks the live feeds so breakage shows up quickly.

## Contents
- [Installation](#installation)
- [Configuration](#configuration)
- [Entities](#entities)
- [CO2 intensity, estimated](#co2-intensity-estimated)
- [Carbon Intensity Index](#carbon-intensity-index)
- [Local emissions (optional)](#local-emissions-optional)
- [Regional detail](#regional-detail)
- [Examples](#examples)
- [Data and update frequency](#data-and-update-frequency)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=anthonysecco&repository=miso-stats&category=integration)

Or add it by hand:
1. In Home Assistant open **HACS**, then the **⋮** menu (top right), then **Custom repositories**.
2. Repository: `https://github.com/anthonysecco/miso-stats`. Type: **Integration**. Click **Add**.
3. Search HACS for **MISO Stats**, open it and click **Download**.
4. **Restart Home Assistant.**
5. Add the integration (next section).

Requires Home Assistant **2026.3** or newer.

### Manual
Copy `custom_components/miso_stats` from the [latest release](https://github.com/anthonysecco/miso-stats/releases) into your `config/custom_components/` folder, then restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up MISO Stats.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=miso_stats)

**Settings → Devices & services → Add integration → MISO Stats → Submit.** The only field, an Emissions API key, is optional; leave it empty for the free feeds alone (see [Local emissions](#local-emissions-optional)). Setup checks that MISO's feed answers and creates one **MISO** device.

### Options
**Settings → Devices & services → MISO Stats → Configure.** Saving reloads the integration.

| Option | Default | What it does |
|---|---|---|
| Update interval | 15 min | How often MISO is polled (5–60 min). MISO refreshes its feeds every 5 minutes. |
| Coal emission factor | 1000 g/kWh | CO2 per kWh from coal, used for the [estimate](#co2-intensity-estimated). |
| Natural gas emission factor | 400 g/kWh | CO2 per kWh from natural gas. |
| Low carbon index threshold | 25 % | **Low Carbon Energy Now** turns on when the [Carbon Intensity Index](#carbon-intensity-index) is at or below this. |
| Carbon Intensity Index window | 14 days | Days of history (7–30) whose cleanest and dirtiest hour set the index's 0 and 100. |
| Emissions API key | empty | Optional. Switches the carbon numbers to measured figures for your local area; a second screen picks the area. Clear it to go back to MISO-wide estimates. |

## Entities

Entity IDs below assume the default device name **MISO**. Power values are in MW for the **whole MISO footprint** (15 states and Manitoba), not your utility; see [Regional detail](#regional-detail).

A fresh install enables 6 entities: CO2 intensity, the Carbon Intensity Index, the renewable and carbon-free shares, demand and when the data was last updated. Everything marked *Off by default* is created disabled. To turn one on, open **Settings → Devices & services → MISO Stats → entities**, select it and choose **Enable**. Disabled entities record no history, which keeps the database small.

### Emissions and shares
| Entity | Unit | Notes |
|---|---|---|
| `sensor.miso_co2_intensity` | g/kWh | **Estimated** average CO2 per kWh of MISO generation right now. See [CO2 intensity, estimated](#co2-intensity-estimated). Attributes: `coal_factor`, `gas_factor`. |
| `sensor.miso_carbon_intensity_index` | % | **MISO Carbon Intensity Index.** How dirty the grid is right now compared with recent days: 0 % = the cleanest hour, 100 % = the dirtiest. See [Carbon Intensity Index](#carbon-intensity-index). |
| `sensor.miso_renewables` | % | Wind and solar share of coal + gas + nuclear + wind + solar output. |
| `sensor.miso_carbon_free` | % | Nuclear, wind and solar share of the same total. |
| `sensor.miso_co2_emissions` | t/h | Estimated metric tons of CO2 per hour from coal and gas. *Off by default.* |
| `binary_sensor.miso_low_carbon_energy_now` | on/off | **Low Carbon Energy Now.** On when the Carbon Intensity Index is at or below your threshold (25 by default). *Off by default.* |

### Demand
| Entity | Unit | Notes |
|---|---|---|
| `sensor.miso_demand` | MW | Latest 5-minute total load (`as_of` attribute). |
| `sensor.miso_today_s_peak_demand` | MW | Highest 5-minute load so far today, EST day (`peak_time` attribute). *Off by default.* |
| `sensor.miso_forecast_peak_demand_today` | MW | Highest hour of MISO's medium-term load forecast for today (`peak_hour_start` attribute). *Off by default.* |
| `sensor.miso_load_forecast` | MW | MISO's load forecast for the current hour. The `forecast` attribute holds today's 24 hours with `forecast_mw`, `day_ahead_mw` (day-ahead cleared load) and `actual_mw` (hourly average of the 5-minute load) for charts; it isn't saved to the database. *Off by default.* |

MISO's 5-minute total load runs a few percent above its hourly forecast and day-ahead figures; they're published as-is.

### Supply mix
All *off by default*.
| Entity | Unit | Notes |
|---|---|---|
| `sensor.miso_coal`, `_natural_gas`, `_nuclear`, `_wind`, `_solar` | MW | Current output by fuel. |
| `sensor.miso_battery_storage` | MW | **Negative = charging**, positive = discharging (`mode` attribute). |
| `sensor.miso_other_generation` | MW | MISO's "Other": hydro, oil, biomass and the rest. |
| `sensor.miso_imports` | MW | Net imports into MISO. |
| `sensor.miso_generation` | MW | Every fuel except imports. `total_with_imports_mw` is MISO's own total. |

### Diagnostic
| Entity | Notes |
|---|---|
| `sensor.miso_data_updated` | Time of the fuel mix interval shown (every 5 minutes on MISO's side). |

## CO2 intensity, estimated

MISO doesn't publish a CO2 figure, so this integration estimates one from the fuel mix:

> **intensity = (coal MW × coal factor + gas MW × gas factor) ÷ (coal + gas + nuclear + wind + solar MW)**

- **Factors** default to 1000 g/kWh for coal and 400 for natural gas, round figures for the US fleets. Change them in Options to match your own source (EPA eGRID, for example). Changing them also re-scores the Carbon Intensity Index's history.
- **Left out on both sides:** *Other* (a mix of hydro, oil, biomass and more that MISO doesn't break down), *Imports* (unknown origin) and *Battery Storage* (re-dispatched energy). Together they're usually a few percent of the total.
- Example, Oct 31 2026 10:30 EST: coal 19.7 GW, gas 25.4, nuclear 8.3, wind 8.0, solar 5.5 → (19,727 × 1000 + 25,417 × 400) ÷ 67,019 ≈ **446 g/kWh**, 20 % renewable, 33 % carbon-free.

It's an average-mix estimate for the whole footprint. It is **not** marginal emissions like WattTime's MOER, which estimates which plant answers *your* extra load.

## Carbon Intensity Index

`sensor.miso_carbon_intensity_index` (**MISO Carbon Intensity Index**) puts the current estimated intensity on a 0–100 % scale relative to recent history:

> **index = (current intensity − cleanest hour) ÷ (dirtiest hour − cleanest hour) × 100**, clamped to 0–100

- **Cleanest and dirtiest hour** are hourly averages over the last **14 complete days** (adjustable 7–30), computed the same way from MISO's 5-minute fuel mix with your current factors. Hourly averages keep a single odd reading from setting the scale.
- **0** means as clean as any hour in the window, **100** as dirty. New record lows or highs show as 0 or 100.
- Attributes: `window_min` / `window_max` (g/kWh), `window_min_hour` / `window_max_hour`, `window_days`.

**How the history builds up.** MISO's keyless API only serves *yesterday's* 5-minute fuel mix (`FuelMix/Yesterday`), not older days. So the window fills one day at a time:
- On install, yesterday is downloaded right away.
- Each night just after midnight EST, the day that just ended is added.
- The index needs **2 days**, so on the day you install it the Carbon Intensity Index is *unavailable* and Low Carbon Energy Now is *unknown*. Both start working just after **00:20 EST** that night (01:20 Eastern daylight time), when the second day arrives. The window reaches the full 14 days two weeks later.
- Each day's hourly fuel mix (5 numbers × 24 hours) is kept in Home Assistant's storage, so restarts download nothing again. A day missed because Home Assistant was off for more than a day stays missing; the window just uses the days it has.

MISO also posts a daily state-estimator fuel mix report that goes back years, but it classifies dual-fuel plants differently (about 2.5 GW more gas and less coal than the live feed), which would put the window about 7 % cleaner than the live value. Mixing the two would skew the index, so it isn't used.

**Low Carbon Energy Now** (`binary_sensor.miso_low_carbon_energy_now`, off by default) is on when the index is at or below the *Low carbon index threshold* (25 by default): "the grid is in its cleanest quarter for recent days". It's unknown until the index is available.

## Local emissions (optional)

MISO's [Emissions Dashboard](https://miso.singularity.energy) is built and run for MISO by **Singularity Energy**, an independent company. Its [API](https://miso.singularity.energy/api-docs) needs a free account and key, and adds what MISO's keyless feeds lack: emissions for *your* part of MISO.

**Get a key:** sign up with the **Login** button at [miso.singularity.energy](https://miso.singularity.energy), confirm your email, then copy the key from [miso.singularity.energy/account](https://miso.singularity.energy/account). Paste it into the setup form or **Configure**.

**Choose your area:** after the key is checked, a second screen shows the state, local resource zone and balancing area your home is in. It finds these from Home Assistant's home location, rounded to about 1 km before it's sent. Pick:
- **Local area**: the area for the carbon numbers. The local balancing area (your utility) is preselected; local resource zone, state, MISO subregion and all of MISO are the alternatives. State and subregion are always current; if the chosen area has no recent data, setup says so and you pick another. County isn't offered: MISO publishes county figures only quarterly.

**What changes with a key.** Every carbon number switches from a MISO-wide estimate to Singularity's measured figures for your local area; the entity IDs stay the same, and each has a `source` attribute (`miso` or `singularity`).

| Entity | Without a key | With a key |
|---|---|---|
| `sensor.miso_co2_intensity` | MISO-wide estimate, every 5 minutes | **Consumed** (location-based) CO2e for your area, from Singularity's tracing of actual power flows, including imports. Hourly, about 90 minutes late. Extra attributes: `area_level`, `area`, `hour`, `fossil_rate`, and `miso_wide` (the MISO-wide estimate, for comparison). |
| `sensor.miso_carbon_free` | MISO-wide (nuclear, wind, solar) | Your area, all carbon-free sources. `fuel_mix` (not recorded) and `miso_wide` attributes. |
| `sensor.miso_renewables` | MISO-wide (wind, solar) | Your area: wind, solar, hydro and biomass. `miso_wide` attribute. |
| `sensor.miso_carbon_intensity_index`, `binary_sensor.miso_low_carbon_energy_now` | From MISO's estimate; history builds up one day per night | From your area's hourly history, **back-filled from the API at setup**, so the index works right away. |

Demand, the load forecast and the MW per fuel always come from MISO's own feeds.

**Good to know:** the local figures are measured and specific to your area, but hourly and about 90 minutes behind. For "is right now a good time" automations they reflect the last hour or two rather than this moment; use `for:` on triggers and pick thresholds from a few days of history.

**Requests:** local emissions once an hour plus a one-off back-fill of the index window (3 requests): under 1,000 a month, well within the API's 100,000 a month and 5 a second. Your key is stored in Home Assistant, never logged, and redacted from diagnostics. If the API stops accepting it, a **Repairs** notice explains how to replace it; demand and the MISO-wide estimate keep working meanwhile.

## Regional detail

MISO's real-time fuel mix and demand from its keyless feeds cover the whole footprint. For your own utility, zone or state, add an Emissions API key ([above](#local-emissions-optional)). For *marginal* emissions (which plant answers extra load near you), WattTime's regions such as `MISO_DETROIT` are a separate, modelled source; MISO's Emissions API documents locational marginal emissions too, but returns no data for standard keys yet (October 2026).

## Examples

Ready-to-paste automations (running loads when the grid is clean, easing off near the forecast peak) and dashboard cards are in **[docs/automations.md](docs/automations.md)**.

Quick start: run the dishwasher reminder when the grid is in its cleanest quarter.
```yaml
automation:
  - alias: "Nudge: clean grid"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.miso_carbon_intensity_index
        below: 25
        for: "00:15:00"
    actions:
      - action: notify.notify
        data:
          message: >-
            The MISO grid is clean right now ({{ states('sensor.miso_co2_intensity') | int(0) }} g/kWh).
            Good time for the dishwasher or laundry.
```

Tip: values update every poll (15 min by default) and can bounce around a threshold. Use `for:` on triggers (10–15 min) to avoid flapping.

## Data and update frequency

| Feed | URL | Used for | Fetched |
|---|---|---|---|
| Fuel mix | `https://public-api.misoenergy.org/api/FuelMix` | Fuel MW, CO2 estimate, shares | Every poll |
| Load | `https://public-api.misoenergy.org/api/RealTimeTotalLoad` | Demand, peak, load forecast | Every poll |
| Yesterday's fuel mix | `https://public-api.misoenergy.org/api/FuelMix/Yesterday` | Carbon Intensity Index history | Once a day (about 250 KB) |
| Local emissions *(key)* | `https://api.miso.singularity.energy/v1/events/consumed` | CO2 intensity, carbon-free and renewable shares, the index (with a key) | Once an hour, plus a one-off back-fill |

- The two live feeds are small (about 10 KB together). The default 15-minute interval is frequent enough for home automations and gentle on a free public service; lower it to 5 minutes in Options if you want every interval. Requests time out after 30 s.
- **Time zone:** MISO works in Eastern Standard Time all year, with no daylight saving. Attributes carry the offset (`-05:00`), and Home Assistant shows them in your local time.
- If the fuel mix feed fails, all entities go unavailable until the next successful poll. If the load feed fails, the demand sensors keep their last value for up to 30 minutes, then go unavailable; the rest keep working.
- If yesterday's download fails, it's retried an hour later.

## Troubleshooting

- **Setup says it can't connect:** check that Home Assistant can reach `public-api.misoenergy.org` (DNS, firewall, Pi-hole) and try again in a few minutes.
- **Carbon Intensity Index unavailable / Low Carbon Energy Now unknown:** they need 2 days of history, which is normal until just after 00:20 EST on the night after setup. If it stays unavailable, check the `history` section of the diagnostics for the last download error.
- **Diagnostics:** **Settings → Devices & services → MISO Stats → ⋮ → Download diagnostics** includes the raw MISO payloads and which history days are stored. Attach it to [issues](https://github.com/anthonysecco/miso-stats/issues).
- **Debug logging:**
  ```yaml
  logger:
    logs:
      custom_components.miso_stats: debug
  ```

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check .
pytest
python scripts/check_live.py   # parse MISO's live feeds
```

CI runs HACS validation, hassfest, ruff and pytest on every push, plus a daily check against the live feeds.

## 💖 Support

If you found this project useful, consider buying me a coffee. ☕

<a href="https://www.buymeacoffee.com/anthonysecco"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## AI Disclosure

This project was built largely with AI coding tools (Claude). I directed the design and reviewed the changes, but most of the code and documentation was written by AI. Please review the code before relying on it, and open an issue if you find a problem.

## License

[MIT](LICENSE). Grid data © MISO, used under its public website terms.
