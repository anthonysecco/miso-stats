"""Optional Emissions API (Singularity) sensors in a running Home Assistant."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miso_stats.const import DEFAULT_OPTIONS, DOMAIN, FUEL_MIX_URL, LOAD_URL
from custom_components.miso_stats.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    FIXTURE_NOW_ISO,
    SINGULARITY_OPTIONS,
    TEST_API_KEY,
    TEST_NODE,
    load_fixture,
    register_singularity,
    singularity_calls,
)

EMISSIONS_ENTITIES = {
    "sensor.miso_local_co2_intensity",
    "sensor.miso_local_carbon_free",
    "sensor.miso_marginal_co2_intensity",
}


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_no_key_no_emissions_entities(hass: HomeAssistant, mock_miso) -> None:
    entry = await _setup(hass)
    entity_ids = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert not entity_ids & EMISSIONS_ENTITIES
    assert not [c for c in mock_miso.mock_calls if "singularity" in str(c[1])]


async def test_emissions_sensors(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    assert entry.state is ConfigEntryState.LOADED

    local = hass.states.get("sensor.miso_local_co2_intensity")
    assert float(local.state) == 449.1  # Michigan, 13:00 UTC hour, 990.0 lbs/MWh
    assert local.attributes["unit_of_measurement"] == "g/kWh"
    assert local.attributes["area_level"] == "state" and local.attributes["area"] == "Michigan"
    assert local.attributes["hour"] == "2026-10-31T13:00:00+00:00"
    assert local.attributes["fossil_rate"] == 590.4

    carbon_free = hass.states.get("sensor.miso_local_carbon_free")
    assert float(carbon_free.state) == 25.8
    assert carbon_free.attributes["fuel_mix"]["nuclear"] == 14.5

    marginal = hass.states.get("sensor.miso_marginal_co2_intensity")
    assert float(marginal.state) == 765.7  # AECI.ALTW, 15:25 UTC, 1688.0 lbs/MWh
    assert marginal.attributes["cp_node"] == TEST_NODE
    assert marginal.attributes["marginal_fuels"] == {"natural_gas": 0.92, "coal": 0.11, "wind": -0.03}

    # The free feeds are unaffected.
    assert float(hass.states.get("sensor.miso_co2_intensity").state) == 446.0


async def test_consumed_hourly_marginal_every_poll(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    coordinator = entry.runtime_data
    assert len(singularity_calls(mock_singularity, "/events/consumed")) == 1
    assert len(singularity_calls(mock_singularity, "/events/lme/by-node")) == 1

    freezer.move_to("2026-10-31 10:50:00-05:00")
    await coordinator.async_refresh()
    assert len(singularity_calls(mock_singularity, "/events/consumed")) == 1  # still within the hour
    assert len(singularity_calls(mock_singularity, "/events/lme/by-node")) == 2

    freezer.move_to("2026-10-31 11:36:00-05:00")
    await coordinator.async_refresh()
    assert len(singularity_calls(mock_singularity, "/events/consumed")) == 2


async def test_no_node_no_marginal_sensor(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    options = {k: v for k, v in SINGULARITY_OPTIONS.items() if k != "cp_node"}
    await _setup(hass, **options)
    assert hass.states.get("sensor.miso_local_co2_intensity") is not None
    assert hass.states.get("sensor.miso_marginal_co2_intensity") is None
    assert not singularity_calls(mock_singularity, "/events/lme/by-node")


async def test_rejected_key_raises_repair(hass: HomeAssistant, mock_miso, freezer) -> None:
    """A rejected key leaves the free sensors working and opens a Repairs issue, cleared on success."""
    freezer.move_to(FIXTURE_NOW_ISO)
    register_singularity(mock_miso, status=401)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.miso_local_co2_intensity").state == STATE_UNAVAILABLE
    assert float(hass.states.get("sensor.miso_co2_intensity").state) == 446.0
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_api_key") is not None
    assert "singularity" in entry.runtime_data.data.errors

    mock_miso.clear_requests()
    mock_miso.get(FUEL_MIX_URL, text=load_fixture("fuelmix.json"))
    mock_miso.get(LOAD_URL, text=load_fixture("load.json"))
    register_singularity(mock_miso)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_api_key") is None
    assert float(hass.states.get("sensor.miso_local_co2_intensity").state) == 449.1


async def test_removing_key_removes_entities(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.miso_marginal_co2_intensity") is not None

    hass.config_entries.async_update_entry(entry, options=dict(DEFAULT_OPTIONS))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for entity_id in EMISSIONS_ENTITIES:
        assert registry.async_get(entity_id) is None


async def test_diagnostics_redact_key(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["options"]["api_key"] == "**REDACTED**"
    assert TEST_API_KEY not in str(diag)
    assert diag["emissions_api"]["configured"] is True
    assert diag["emissions_api"]["local"]["rate_g_per_kwh"] == 449.1
    assert diag["emissions_api"]["marginal"]["rate_g_per_kwh"] == 765.7
    assert diag["raw"]["consumed"]["data"][0]["agg_id"] == "Michigan"
