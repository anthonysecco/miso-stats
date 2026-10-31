# MISO Stats for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/anthonysecco/miso-stats)](https://github.com/anthonysecco/miso-stats/releases)
[![Validate](https://github.com/anthonysecco/miso-stats/actions/workflows/validate.yml/badge.svg)](https://github.com/anthonysecco/miso-stats/actions/workflows/validate.yml)
[![Tests](https://github.com/anthonysecco/miso-stats/actions/workflows/tests.yml/badge.svg)](https://github.com/anthonysecco/miso-stats/actions/workflows/tests.yml)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-donate-yellow.svg)](https://www.buymeacoffee.com/anthonysecco)

The Midcontinent grid (MISO) in Home Assistant: how clean the electricity is right now, the renewable and carbon-free shares, a **Carbon Intensity Index** that compares now with the last two weeks, demand and the load forecast, and the fuel mix. Use them on dashboards and in automations, for example to run the EV charger or water heater when the grid is clean.

It works in two ways:

| | **Out of the box** | **With an optional, free Emissions API key** |
|---|---|---|
| Carbon numbers for | All of MISO (15 states and Manitoba) | **Your utility** (local balancing area), zone or state |
| CO2 intensity | **Estimated** by this integration from MISO's fuel mix | **Measured** consumed (location-based) emissions, including imports |
| Updated | Every 5 minutes | Hourly, about 90 minutes behind |
| Carbon Intensity Index | Builds up one day per night after install | Works right away (history back-filled) |
| Account needed | None | Free account at [miso.singularity.energy](https://miso.singularity.energy) |

The key is **optional**: without it everything works on MISO's free public feeds. Adding it makes the carbon numbers more accurate and specific to where you live. For example, on Oct 31 2026 around 11 am Eastern, Michigan's measured intensity was **405 g/kWh** while the MISO-wide estimate was **446 g/kWh**. See [Utility-level CO2 with an API key](#utility-level-co2-with-an-api-key-optional).

> **Unofficial.** This project is not affiliated with or endorsed by MISO (Midcontinent Independent System Operator) or Singularity Energy. It reads MISO's public [real-time data APIs](https://www.misoenergy.org/markets-and-operations/RTDataAPIs/) and, with a key, MISO's [Emissions API](https://miso.singularity.energy/api-docs). Both can change without notice; a daily CI job checks MISO's live feeds so breakage shows up quickly.

## Contents
- [Installation](#installation)
- [Configuration](#configuration)
- [Utility-level CO2 with an API key (optional)](#utility-level-co2-with-an-api-key-optional)
- [Entities](#entities)
- [How the carbon numbers are worked out](#how-the-carbon-numbers-are-worked-out)
- [Carbon Intensity Index](#carbon-intensity-index)
- [Regional detail and marginal emissions](#regional-detail-and-marginal-emissions)
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

**Settings → Devices & services → Add integration → MISO Stats.**

- **Without a key:** leave the *Emissions API key* field empty and submit. Setup checks that MISO's feed answers and creates one **MISO** device. That's all.
- **With a key:** paste it and submit; a second screen asks for your local area. See [the next section](#utility-level-co2-with-an-api-key-optional).

You can add, change or remove the key later under **Configure**.

### Options
**Settings → Devices & services → MISO Stats → Configure.** Saving reloads the integration.

| Option | Default | What it does |
|---|---|---|
| Update interval | 15 min | How often MISO is polled (5–60 min). MISO refreshes its feeds every 5 minutes. |
| Coal emission factor | 1000 g/kWh | CO2 per kWh from coal, used for the [estimate](#without-a-key-estimated-from-misos-fuel-mix). Not used with a key. |
| Natural gas emission factor | 400 g/kWh | CO2 per kWh from natural gas. Not used with a key. |
| Low carbon index threshold | 25 % | **Low Carbon Energy Now** turns on when the [Carbon Intensity Index](#carbon-intensity-index) is at or below this. |
| Carbon Intensity Index window | 14 days | Days of history (7–30) whose cleanest and dirtiest hour set the index's 0 and 100. |
| Emissions API key | empty | Optional. Switches the carbon numbers to measured figures for your local area; a second screen picks the area. Clear it to go back to MISO-wide estimates. |

## Utility-level CO2 with an API key (optional)

MISO's [Emissions Dashboard](https://miso.singularity.energy) is built and run for MISO by **Singularity Energy**, an independent company. Its API gives **consumed (location-based) emissions** for each part of MISO: what the electricity used in your area emitted, traced along actual power flows and including imports. That's more accurate than this integration's MISO-wide estimate and specific to your utility.

### Get a key (free)
1. Go to [miso.singularity.energy](https://miso.singularity.energy) and click **Login** (top right) to create an account.
2. Confirm your email.
3. Copy your key from [miso.singularity.energy/account](https://miso.singularity.energy/account).
4. Paste it into the MISO Stats setup form, or under **Configure** if it's already installed.

The account and key are between you and Singularity; this integration never creates accounts. Their published limits are 5 requests a second and 100,000 a month; this integration uses under 1,000 a month.

### Choose your area
After the key is checked, a second screen shows where your home is: county, state, **local balancing area** (your utility, e.g. *Detroit Edison Company (DECO)*), local resource zone and subregion. It looks these up from Home Assistant's home location (**Settings → System → General**), rounded to about 1 km before it's sent.

Pick the **Local area** for the carbon numbers:

| Local area | Example | Notes |
|---|---|---|
| **Local balancing area (utility)** *(default)* | Detroit Edison Company (DECO) | The most local level; your utility's service area. |
| Local resource zone | Zone 7 (Michigan) | MISO's planning zones. |
| State | Michigan | Always current. |
| MISO subregion | Central | North, Central or South. |
| All of MISO | | The whole footprint, measured. |

If an area has no recent data, setup says so and you pick another. County isn't offered: MISO publishes county figures only quarterly.

### What changes with a key
The same 20 entities exist either way, with the same entity IDs. With a key, five of them switch to measured figures for your area and gain a `source: singularity` attribute plus `area_level`, `area` and `hour`:

| Entity | Without a key | With a key |
|---|---|---|
| `sensor.miso_co2_intensity` | MISO-wide **estimate**, every 5 min | **Measured** for your area, hourly, about 90 min behind. `fossil_rate` attribute (the fossil share alone) and `miso_wide` (the MISO-wide estimate, for comparison) |
| `sensor.miso_carbon_free` | MISO-wide (nuclear, wind, solar) | Your area, all carbon-free sources. `fuel_mix` (not recorded) and `miso_wide` attributes |
| `sensor.miso_renewables` | MISO-wide (wind, solar) | Your area: wind, solar, hydro and biomass. `miso_wide` attribute |
| `sensor.miso_carbon_intensity_index` | From the MISO-wide estimate; history builds up one day per night | From your area's hourly history, **back-filled at setup**, so it works right away |
| `binary_sensor.miso_low_carbon_energy_now` | Follows the MISO-based index | Follows the local index |

Demand, the load forecast and the MW per fuel always come from MISO's own feeds.

**Good to know:**
- The local figures are measured and specific to your area, but **hourly and about 90 minutes behind**. For "is right now a good time" automations they describe the last hour or two rather than this moment; use `for:` on triggers and pick thresholds from a few days of history.
- Your key is stored in Home Assistant, never logged, and redacted from diagnostics.
- If the API stops accepting the key, a **Repairs** notice explains how to replace it. The carbon sensors go unavailable meanwhile; demand and the fuel mix keep working.
- To go back to MISO-wide estimates, clear the key under **Configure**.

## Entities

Entity IDs below assume the default device name **MISO**. A fresh install enables **5** entities; everything marked ❌ is created disabled. To turn one on, open **Settings → Devices & services → MISO Stats → entities**, select it and choose **Enable**. Disabled entities record no history, which keeps the database small.

### Emissions and shares
| Entity | Friendly name | Unit | Without key | With key | On by default |
|---|---|---|---|---|---|
| `sensor.miso_co2_intensity` | MISO CO2 intensity | g/kWh | MISO-wide estimate | Measured, your area | ✅ |
| `sensor.miso_carbon_intensity_index` | MISO Carbon Intensity Index | % | 0–100 vs. last 14 days (MISO-wide) | 0–100 vs. last 14 days (your area) | ✅ |
| `sensor.miso_carbon_free` | MISO Carbon-free | % | MISO-wide | Your area | ✅ |
| `sensor.miso_renewables` | MISO Renewables | % | MISO-wide | Your area | ✅ |
| `binary_sensor.miso_low_carbon_energy_now` | MISO Low Carbon Energy Now | on/off | Index ≤ threshold | Index ≤ threshold (local) | ❌ |
| `sensor.miso_co2_emissions` | MISO CO2 emissions | t/h | MISO-wide estimate from coal and gas | Same | ❌ |

### Demand
Always from MISO, for the whole footprint.
| Entity | Friendly name | Unit | Notes | On by default |
|---|---|---|---|---|
| `sensor.miso_demand` | MISO Demand | MW | Latest 5-minute total load (`as_of` attribute). | ❌ |
| `sensor.miso_today_s_peak_demand` | MISO Today's peak demand | MW | Highest 5-minute load so far today, EST day (`peak_time` attribute). | ❌ |
| `sensor.miso_forecast_peak_demand_today` | MISO Forecast peak demand today | MW | Highest hour of MISO's load forecast for today (`peak_hour_start` attribute). | ❌ |
| `sensor.miso_load_forecast` | MISO Load forecast | MW | MISO's forecast for the current hour. The `forecast` attribute holds today's 24 hours with `forecast_mw`, `day_ahead_mw` and `actual_mw` for charts (not recorded). | ❌ |

MISO's 5-minute total load runs a few percent above its hourly forecast and day-ahead figures; they're published as-is.

### Supply mix
Always from MISO, for the whole footprint. All off by default.
| Entity | Friendly name | Unit | Notes |
|---|---|---|---|
| `sensor.miso_coal`, `_natural_gas`, `_nuclear`, `_wind`, `_solar` | MISO Coal, Natural gas, … | MW | Current output by fuel. |
| `sensor.miso_battery_storage` | MISO Battery storage | MW | **Negative = charging**, positive = discharging (`mode` attribute). |
| `sensor.miso_other_generation` | MISO Other generation | MW | MISO's "Other": hydro, oil, biomass and the rest. |
| `sensor.miso_imports` | MISO Imports | MW | Net imports into MISO. |
| `sensor.miso_generation` | MISO Generation | MW | Every fuel except imports. `total_with_imports_mw` is MISO's own total. |

### Diagnostic
| Entity | Friendly name | Notes | On by default |
|---|---|---|---|
| `sensor.miso_data_updated` | MISO Data updated | Time of MISO's latest fuel-mix interval (every 5 minutes). | ✅ |

## How the carbon numbers are worked out

### Without a key: estimated from MISO's fuel mix
MISO publishes no CO2 figure, so this integration estimates one from its 5-minute fuel mix:

> **intensity = (coal MW × coal factor + gas MW × gas factor) ÷ (coal + gas + nuclear + wind + solar MW)**

- **Factors** default to 1000 g/kWh for coal and 400 for natural gas, round figures for the US fleets. Change them in Options to match your own source (EPA eGRID, for example). Changing them also re-scores the Carbon Intensity Index's history.
- **Left out on both sides:** *Other* (a mix of hydro, oil, biomass and more that MISO doesn't break down), *Imports* (unknown origin) and *Battery Storage* (re-dispatched energy). Together they're usually a few percent of the total.
- **Carbon-free** = (nuclear + wind + solar) ÷ the same total; **Renewables** = (wind + solar) ÷ the same total.
- Example, Oct 31 2026 10:30 EST: coal 19.7 GW, gas 25.4, nuclear 8.3, wind 8.0, solar 5.5 → (19,727 × 1000 + 25,417 × 400) ÷ 67,019 ≈ **446 g/kWh**, 20 % renewable, 33 % carbon-free.

### With a key: measured for your area
- **CO2 intensity** is Singularity's consumed CO2e rate for your area, converted from lb/MWh to g/kWh. Singularity traces where your area's power actually came from, including imports, using MISO's state-estimator data and network models ([MISO's description](https://help.misoenergy.org/knowledgebase/article/KA-01501/en-us)).
- **Carbon-free** = 100 % minus the area's non-carbon-free share; **Renewables** = wind + solar + hydro + biomass.
- Example, Oct 31 2026, 11 am–noon Eastern, Michigan: **405 g/kWh**, 35 % carbon-free, against the MISO-wide estimate of 446 g/kWh.

Both are **average** (attributional) intensities: how clean the electricity mix is. Neither is *marginal* emissions; see [below](#regional-detail-and-marginal-emissions).

## Carbon Intensity Index

`sensor.miso_carbon_intensity_index` (**MISO Carbon Intensity Index**) puts the current CO2 intensity on a 0–100 % scale relative to recent history:

> **index = (current intensity − cleanest hour) ÷ (dirtiest hour − cleanest hour) × 100**, clamped to 0–100

- **Cleanest and dirtiest hour** are hourly averages over the last **14 complete days** (adjustable 7–30). Hourly averages keep a single odd reading from setting the scale.
- **0** means as clean as any hour in the window, **100** as dirty. New record lows or highs show as 0 or 100.
- Attributes: `source`, `window_min` / `window_max` (g/kWh), `window_min_hour` / `window_max_hour`, `window_days`.
- It's relative, so it turns "cleanest quarter of the last two weeks" into a simple threshold, which works in every season.

**How the history builds up**

*With a key:* the last 15 days of your area's hourly figures are downloaded at setup (3 requests), then each new hour is added. The index works right away. Changing the area starts a new history.

*Without a key:* MISO's keyless API only serves *yesterday's* 5-minute fuel mix, so the window fills one day at a time:
- On install, yesterday is downloaded right away.
- Each night just after **00:20 EST** (01:20 Eastern daylight time), the day that just ended is added.
- The index needs **2 days**, so on the day you install it the index is *unavailable* and Low Carbon Energy Now is *unknown*; both start working that night. The window reaches 14 days two weeks later.
- Only each day's hourly fuel mix is kept, in Home Assistant's storage, so restarts download nothing again.

MISO also posts a daily fuel-mix report that goes back years, but it classifies dual-fuel plants differently (about 7 % cleaner than the live feed), so mixing the two would skew the index; it isn't used.

**Low Carbon Energy Now** (`binary_sensor.miso_low_carbon_energy_now`, off by default) is on when the index is at or below the *Low carbon index threshold* (25 by default): "the grid is in its cleanest quarter for recent days". It's unknown until the index is available.

## Regional detail and marginal emissions

- **Local:** MISO's keyless feeds cover the whole footprint. For your own utility, zone or state, add an [Emissions API key](#utility-level-co2-with-an-api-key-optional).
- **Marginal:** marginal emissions estimate which plant answers *extra* load near you, a different question from how clean the average mix is. MISO's Emissions API documents locational marginal emissions too, but as of October 2026 it returns no data for standard keys, so they aren't included. WattTime's regions (such as `MISO_DETROIT`) are a separate, modelled source of marginal emissions.

## Examples

Ready-to-paste automations (running loads when the grid is clean, easing off near the forecast peak) and dashboard cards are in **[docs/automations.md](docs/automations.md)**. They work the same with or without a key.

Quick start: a reminder when the grid is in its cleanest quarter.
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
            The grid is clean right now ({{ states('sensor.miso_co2_intensity') | int(0) }} g/kWh).
            Good time for the dishwasher or laundry.
```

Tip: values update every poll (15 min by default) and can bounce around a threshold. Use `for:` on triggers (10–15 min) to avoid flapping.

## Data and update frequency

| Feed | URL | Used for | Fetched |
|---|---|---|---|
| Fuel mix | `https://public-api.misoenergy.org/api/FuelMix` | Fuel MW; CO2 estimate and shares without a key | Every poll |
| Load | `https://public-api.misoenergy.org/api/RealTimeTotalLoad` | Demand, peak, load forecast | Every poll |
| Yesterday's fuel mix | `https://public-api.misoenergy.org/api/FuelMix/Yesterday` | Index history without a key | Once a day (about 250 KB) |
| Local emissions *(key)* | `https://api.miso.singularity.energy/v1/events/consumed` | CO2 intensity, shares and index with a key | Once an hour, plus a one-off back-fill |
| Location lookup *(key)* | `https://api.miso.singularity.energy/v1/data-availability/lookup/coordinates` | Finding your area | At setup only |

- MISO's two live feeds are small (about 10 KB together). The default 15-minute interval is frequent enough for home automations and gentle on a free public service; lower it to 5 minutes in Options if you want every interval. Requests time out after 30 s.
- **Time zone:** MISO works in Eastern Standard Time all year, with no daylight saving; the Emissions API uses UTC. Attributes carry the offset, and Home Assistant shows them in your local time.
- If MISO's fuel mix feed fails, all entities go unavailable until the next successful poll. If the load feed fails, the demand sensors keep their last value for up to 30 minutes. If the Emissions API fails, the carbon sensors keep their last value until that hour is more than 4 hours old; failed downloads are retried on the next poll, and a failed back-fill within the hour.

## Troubleshooting

- **Setup says it can't connect:** check that Home Assistant can reach `public-api.misoenergy.org` (DNS, firewall, Pi-hole) and try again in a few minutes.
- **"The Emissions API rejected this key":** copy the key again from [miso.singularity.energy/account](https://miso.singularity.energy/account), without spaces.
- **"No recent data for this area":** pick another level; **State** is always current.
- **"Your home location isn't in a MISO area":** check **Settings → System → General**; the location must be inside MISO's footprint.
- **Carbon sensors unavailable with a key:** look in **Settings → Repairs** for a key notice, and in the diagnostics under `emissions_api`.
- **Carbon Intensity Index unavailable without a key:** it needs 2 days of history, which is normal until just after 00:20 EST on the night after setup. If it stays unavailable, check the `history` section of the diagnostics.
- **Diagnostics:** **Settings → Devices & services → MISO Stats → ⋮ → Download diagnostics** includes the raw payloads and stored history (your key is redacted). Attach it to [issues](https://github.com/anthonysecco/miso-stats/issues).
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

CI runs HACS validation, hassfest, ruff and pytest on every push, plus a daily check against MISO's live feeds. The Emissions API is tested against a captured response; no key is used in CI.

## 💖 Support

If you found this project useful, consider buying me a coffee. ☕

<a href="https://www.buymeacoffee.com/anthonysecco"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## AI Disclosure

This project was built largely with AI coding tools (Claude). I directed the design and reviewed the changes, but most of the code and documentation was written by AI. Please review the code before relying on it, and open an issue if you find a problem.

## License

[MIT](LICENSE). Grid data © MISO; emissions data © MISO and Singularity Energy, used under their public terms.
