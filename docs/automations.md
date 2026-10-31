# Automation and dashboard examples

Replace the `switch.*`, `climate.*` and `notify.*` entities with your own. Each example works on its own; paste it into **Settings → Automations → ⋮ → Edit in YAML**, or into `automations.yaml` without the leading `- `.

- [EV charging when the grid is clean](#ev-charging-when-the-grid-is-clean)
- [Water heater boost when the grid is clean](#water-heater-boost-when-the-grid-is-clean)
- [Ease off near the forecast peak](#ease-off-near-the-forecast-peak)
- [Nudge to start the dishwasher or laundry](#nudge-to-start-the-dishwasher-or-laundry)
- [Dashboard cards](#dashboard-cards)
- [Template ideas](#template-ideas)

## EV charging when the grid is clean

Charge while **Low Carbon Energy Now** is on (enable it first; it's off by default). A safety net still starts charging overnight.

```yaml
alias: "EV: charge on clean power"
triggers:
  - trigger: state
    entity_id: binary_sensor.miso_low_carbon_energy_now
    to: "on"
    for: "00:10:00"
    id: start
  - trigger: state
    entity_id: binary_sensor.miso_low_carbon_energy_now
    to: "off"
    for: "00:10:00"
    id: stop
  - trigger: time
    at: "00:30:00"
    id: start
actions:
  - action: "switch.turn_{{ 'on' if trigger.id == 'start' else 'off' }}"
    target:
      entity_id: switch.ev_charger
mode: single
```

## Water heater boost when the grid is clean

The [Carbon Intensity Index](../README.md#carbon-intensity-index) is 0 at the cleanest hour of recent days and 100 at the dirtiest. This starts the boost in the cleanest quarter and stops it if things get dirty again.

```yaml
alias: "Water heater: boost when the grid is clean"
triggers:
  - trigger: numeric_state
    entity_id: sensor.miso_carbon_intensity_index
    below: 25
    for: "00:10:00"
    id: clean
  - trigger: numeric_state
    entity_id: sensor.miso_carbon_intensity_index
    above: 50
    id: dirty
actions:
  - action: "switch.turn_{{ 'on' if trigger.id == 'clean' else 'off' }}"
    target:
      entity_id: switch.water_heater_boost
mode: restart
```

## Ease off near the forecast peak

Cool the house a little extra two hours before MISO's forecast peak hour, then let it drift through the peak. Enable `sensor.miso_forecast_peak_demand_today` first; it's off by default.

```yaml
alias: "HVAC: coast through the forecast peak"
triggers:
  - trigger: template
    value_template: >-
      {% set start = state_attr('sensor.miso_forecast_peak_demand_today', 'peak_hour_start') %}
      {{ start is not none and now() >= as_datetime(start) - timedelta(hours=2)
         and now() < as_datetime(start) }}
    id: precool
  - trigger: template
    value_template: >-
      {% set start = state_attr('sensor.miso_forecast_peak_demand_today', 'peak_hour_start') %}
      {{ start is not none and now() >= as_datetime(start) and now() < as_datetime(start) + timedelta(hours=2) }}
    id: coast
  - trigger: template
    value_template: >-
      {% set start = state_attr('sensor.miso_forecast_peak_demand_today', 'peak_hour_start') %}
      {{ start is not none and now() >= as_datetime(start) + timedelta(hours=2) }}
    id: normal
conditions:
  - condition: state
    entity_id: climate.living_room
    state: cool
actions:
  - action: climate.set_temperature
    target:
      entity_id: climate.living_room
    data:
      temperature: "{{ {'precool': 72, 'coast': 78, 'normal': 75}[trigger.id] }}"
mode: queued
```

## Nudge to start the dishwasher or laundry

```yaml
alias: "Nudge: good time for big appliances"
triggers:
  - trigger: numeric_state
    entity_id: sensor.miso_carbon_intensity_index
    below: 25
    for: "00:15:00"
conditions:
  - condition: time
    after: "08:00:00"
    before: "21:00:00"
actions:
  - action: notify.notify
    data:
      message: >-
        Clean power on the MISO grid right now ({{ states('sensor.miso_co2_intensity') | int(0) }} g/kWh,
        {{ states('sensor.miso_carbon_free') | int(0) }} % carbon-free). Good time for the dishwasher or laundry.
mode: single
```

## Dashboard cards

Grid summary with built-in cards:
```yaml
type: vertical-stack
cards:
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: sensor.miso_co2_intensity
      - type: tile
        entity: sensor.miso_carbon_free
      - type: tile
        entity: sensor.miso_demand
      - type: tile
        entity: sensor.miso_renewables
  - type: gauge
    entity: sensor.miso_carbon_intensity_index
    name: Grid carbon vs. last 14 days
    min: 0
    max: 100
    severity:
      green: 0
      yellow: 35
      red: 70
  - type: history-graph
    hours_to_show: 24
    entities:
      - entity: sensor.miso_demand
      - entity: sensor.miso_co2_intensity
```

Load forecast vs. actual, with [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (also from HACS). Enable `sensor.miso_load_forecast` first; it's off by default.
```yaml
type: custom:apexcharts-card
header:
  show: true
  title: MISO load today
graph_span: 24h
span:
  start: day
now:
  show: true
series:
  - entity: sensor.miso_load_forecast
    name: Forecast
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.time).getTime(), p.forecast_mw]);
  - entity: sensor.miso_load_forecast
    name: Day-ahead
    data_generator: |
      return entity.attributes.forecast.map(p => [new Date(p.time).getTime(), p.day_ahead_mw]);
  - entity: sensor.miso_load_forecast
    name: Actual
    data_generator: |
      return entity.attributes.forecast.filter(p => p.actual_mw !== null)
        .map(p => [new Date(p.time).getTime(), p.actual_mw]);
```

The MISO day runs on Eastern Standard Time, so in summer "today" starts at 1 AM Eastern daylight time.

## Template ideas

Fossil share (coal + gas) of the generation the estimate uses:
```jinja
{{ 100 - states('sensor.miso_carbon_free') | float(0) }}
```

Is the grid charging its batteries? (enable the Battery storage sensor first)
```jinja
{{ states('sensor.miso_battery_storage') | float(0) < 0 }}
```
