"""Setup, entities, history and failure handling."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miso_stats.api import parse_fuel_mix_day
from custom_components.miso_stats.const import (
    DEFAULT_OPTIONS,
    DOMAIN,
    FUEL_MIX_URL,
    FUEL_MIX_YESTERDAY_URL,
    LOAD_URL,
)
from custom_components.miso_stats.history import STORAGE_KEY, STORAGE_VERSION

from .conftest import FIXTURE_NOW_ISO, load_fixture

ENABLED_BY_DEFAULT = {
    "sensor.miso_co2_intensity",
    "sensor.miso_carbon_intensity_index",
    "sensor.miso_renewables",
    "sensor.miso_carbon_free",
    "sensor.miso_data_updated",
}


async def _setup(hass: HomeAssistant, enable: tuple[str, ...] = (), **options: Any) -> MockConfigEntry:
    """Set up the entry, then enable the given disabled-by-default entities and reload."""
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)  # yesterday's fuel mix
    if enable:
        registry = er.async_get(hass)
        for entity_id in enable:
            registry.async_update_entity(entity_id, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _seed_history(hass_storage: dict[str, Any], *days: str) -> None:
    """Store the Oct 30 fixture day's hourly mix under the given dates."""
    hours = parse_fuel_mix_day(load_fixture("fuelmix_yesterday.json")).hours
    hass_storage[STORAGE_KEY] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": STORAGE_KEY,
        "data": {"days": {day: {str(h): mix for h, mix in hours.items()} for day in days}},
    }


def _yesterday_fetches(aioclient_mock) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == FUEL_MIX_YESTERDAY_URL)


async def test_default_enabled_entities(hass: HomeAssistant, mock_miso) -> None:
    """The headline values are enabled on a fresh install; per-fuel detail and the signal aren't."""
    entry = await _setup(hass)
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {e.entity_id for e in entries if e.disabled_by is None} == ENABLED_BY_DEFAULT
    for hidden in (
        "sensor.miso_demand",
        "sensor.miso_coal",
        "sensor.miso_load_forecast",
        "binary_sensor.miso_low_carbon_energy_now",
    ):
        item = registry.async_get(hidden)
        assert item is not None and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_setup_creates_entities(hass: HomeAssistant, mock_miso, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(
        hass,
        enable=(
            "sensor.miso_demand",
            "sensor.miso_coal",
            "sensor.miso_imports",
            "sensor.miso_battery_storage",
            "sensor.miso_generation",
            "sensor.miso_co2_emissions",
            "sensor.miso_today_s_peak_demand",
            "sensor.miso_forecast_peak_demand_today",
            "sensor.miso_load_forecast",
        ),
    )
    assert entry.state is ConfigEntryState.LOADED

    intensity = hass.states.get("sensor.miso_co2_intensity")
    assert float(intensity.state) == 446.0
    assert intensity.attributes["unit_of_measurement"] == "g/kWh"
    assert intensity.attributes["coal_factor"] == 1000
    assert float(hass.states.get("sensor.miso_renewables").state) == 20.2
    assert float(hass.states.get("sensor.miso_carbon_free").state) == 32.6
    assert float(hass.states.get("sensor.miso_co2_emissions").state) == 29894

    demand = hass.states.get("sensor.miso_demand")
    assert float(demand.state) == 72619
    assert demand.attributes["unit_of_measurement"] == "MW"
    assert demand.attributes["device_class"] == "power"
    assert demand.attributes["as_of"] == "2026-10-31T10:30:00-05:00"
    assert float(hass.states.get("sensor.miso_today_s_peak_demand").state) == 72619
    peak = hass.states.get("sensor.miso_forecast_peak_demand_today")
    assert float(peak.state) == 72302
    assert peak.attributes["peak_hour_start"] == "2026-10-31T15:00:00-05:00"
    forecast = hass.states.get("sensor.miso_load_forecast")
    assert float(forecast.state) == 69547  # hour ending 11
    assert len(forecast.attributes["forecast"]) == 24
    assert forecast.attributes["forecast"][0]["day_ahead_mw"] == 59391

    assert float(hass.states.get("sensor.miso_coal").state) == 19727
    assert float(hass.states.get("sensor.miso_imports").state) == 2840
    assert hass.states.get("sensor.miso_battery_storage").attributes["mode"] == "discharging"
    generation = hass.states.get("sensor.miso_generation")
    assert float(generation.state) == 67509
    assert generation.attributes["total_with_imports_mw"] == 70349
    assert hass.states.get("sensor.miso_data_updated").state == "2026-10-31T15:30:00+00:00"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_load_feed_down(hass: HomeAssistant, aioclient_mock, fuel_mix_text, yesterday_text) -> None:
    """The fuel mix still works when the demand feed fails; the demand sensors go unavailable."""
    aioclient_mock.get(FUEL_MIX_URL, text=fuel_mix_text)
    aioclient_mock.get(LOAD_URL, text="<html>oops</html>")
    aioclient_mock.get(FUEL_MIX_YESTERDAY_URL, text=yesterday_text)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert float(hass.states.get("sensor.miso_co2_intensity").state) == 446.0
    assert entry.runtime_data.data.load is None  # the demand sensors read this and go unavailable
    assert set(entry.runtime_data.data.errors) == {"load"}


async def test_failed_feed_keeps_recent_values(
    hass: HomeAssistant, mock_miso, fuel_mix_text, yesterday_text, freezer
) -> None:
    """A demand feed failure keeps the last reading for up to 30 minutes, then goes unavailable."""
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass)
    coordinator = entry.runtime_data

    mock_miso.clear_requests()
    mock_miso.get(FUEL_MIX_URL, text=fuel_mix_text)
    mock_miso.get(LOAD_URL, status=500)
    mock_miso.get(FUEL_MIX_YESTERDAY_URL, text=yesterday_text)

    freezer.move_to("2026-10-31 10:50:00-05:00")  # 20 minutes after the reading
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data.load is not None and coordinator.data.load.demand[1] == 72619

    freezer.move_to("2026-10-31 11:05:00-05:00")  # 35 minutes after
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data.load is None


async def test_fuel_mix_down_retries(hass: HomeAssistant, aioclient_mock) -> None:
    for url in (FUEL_MIX_URL, LOAD_URL, FUEL_MIX_YESTERDAY_URL):
        aioclient_mock.get(url, status=500)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics(hass: HomeAssistant, mock_miso) -> None:
    from custom_components.miso_stats.diagnostics import async_get_config_entry_diagnostics

    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["derived"]["co2_intensity"] == 446.0
    assert diag["derived"]["fuel_mix_mw"]["coal"] == 19727
    assert diag["raw"]["fuel_mix"]["TotalMW"] == "70349"
    assert diag["history"]["days"] == ["2026-10-30"]
    assert diag["history"]["hours_per_day"] == {"2026-10-30": 24}


async def test_index_waits_for_two_days(hass: HomeAssistant, mock_miso, freezer) -> None:
    """A fresh install stores yesterday right away; one day isn't enough for the index yet."""
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass)
    assert _yesterday_fetches(mock_miso) == 1
    assert list(entry.runtime_data.history.days) == [date(2026, 10, 30)]
    assert hass.states.get("sensor.miso_carbon_intensity_index").state == STATE_UNAVAILABLE


async def test_co2_intensity_index(hass: HomeAssistant, hass_storage, mock_miso, freezer) -> None:
    """With Oct 29 stored and Oct 30 downloaded, 446 g/kWh sits at 51 % of the 376.9-511.8 range."""
    _seed_history(hass_storage, "2026-10-29")
    freezer.move_to(FIXTURE_NOW_ISO)
    await _setup(hass, enable=("binary_sensor.miso_low_carbon_energy_now",))

    state = hass.states.get("sensor.miso_carbon_intensity_index")
    assert int(state.state) == 51
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["friendly_name"] == "MISO Carbon Intensity Index"
    assert state.attributes["window_days"] == 2
    assert abs(state.attributes["window_min"] - 376.9) <= 0.1
    assert abs(state.attributes["window_max"] - 511.8) <= 0.1
    assert state.attributes["window_min_hour"] == "2026-10-29T10:00:00-05:00"  # earliest of two equal days
    assert state.attributes["co2_intensity"] == 446.0

    clean = hass.states.get("binary_sensor.miso_low_carbon_energy_now")
    assert clean.state == "off"  # 51 > 25
    assert clean.attributes["co2_intensity_index"] == 51
    assert clean.attributes["index_threshold"] == 25


async def test_history_survives_restart(hass: HomeAssistant, mock_miso, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass)
    assert _yesterday_fetches(mock_miso) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _yesterday_fetches(mock_miso) == 1  # loaded from storage, nothing re-downloaded
    assert entry.runtime_data.history.days


async def test_history_retried_hourly(
    hass: HomeAssistant, mock_miso, fuel_mix_text, load_text, freezer
) -> None:
    """A failed FuelMix/Yesterday download is retried an hour later, not every poll."""
    mock_miso.clear_requests()
    mock_miso.get(FUEL_MIX_URL, text=fuel_mix_text)
    mock_miso.get(LOAD_URL, text=load_text)
    mock_miso.get(FUEL_MIX_YESTERDAY_URL, status=502)
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    tries = _yesterday_fetches(mock_miso)
    assert tries == 3  # three attempts within one download
    assert coordinator.history_error and not coordinator.history.days

    freezer.move_to("2026-10-31 10:50:00-05:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _yesterday_fetches(mock_miso) == tries

    freezer.move_to("2026-10-31 11:40:00-05:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _yesterday_fetches(mock_miso) == tries * 2


async def test_index_turns_on_after_first_midnight(
    hass: HomeAssistant, mock_miso, fuel_mix_text, load_text, yesterday_text, freezer
) -> None:
    """Install day: one day stored, no index. Just after midnight EST the new day arrives and the index appears."""
    freezer.move_to(FIXTURE_NOW_ISO)
    # Enable the binary sensor in the registry before setup: enabling it afterwards schedules a
    # delayed reload that would swap the coordinator out from under the clock jumps below.
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_clean_energy_now",
        config_entry=entry,
        suggested_object_id="miso_low_carbon_energy_now",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = entry.runtime_data
    assert hass.states.get("sensor.miso_carbon_intensity_index").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.miso_low_carbon_energy_now").state == "unknown"

    # FuelMix/Yesterday rolls over to Oct 31 at midnight EST (same shape, new date).
    mock_miso.clear_requests()
    mock_miso.get(FUEL_MIX_URL, text=fuel_mix_text)
    mock_miso.get(LOAD_URL, text=load_text)
    mock_miso.get(FUEL_MIX_YESTERDAY_URL, text=yesterday_text.replace("2026-10-30", "2026-10-31"))

    freezer.move_to("2026-11-01 00:10:00-05:00")  # not rolled over yet: nothing asked
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _yesterday_fetches(mock_miso) == 0

    freezer.move_to("2026-11-01 00:25:00-05:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _yesterday_fetches(mock_miso) == 1
    assert entry.runtime_data is coordinator  # no reload happened along the way
    index = hass.states.get("sensor.miso_carbon_intensity_index")
    assert int(index.state) == 51
    assert index.attributes["window_days"] == 2
    assert hass.states.get("binary_sensor.miso_low_carbon_energy_now").state == "off"


async def test_retired_entities_removed(hass: HomeAssistant, mock_miso) -> None:
    """The pre-release Hub price sensor is removed from the registry on setup."""
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options={**DEFAULT_OPTIONS, "hub": "michigan"})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_hub_price", config_entry=entry, suggested_object_id="miso_hub_price"
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get(old.entity_id) is None
    assert hass.states.get("sensor.miso_co2_intensity") is not None
