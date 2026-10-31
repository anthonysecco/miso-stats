"""With an Emissions API (Singularity) key, the carbon numbers come from it for the local area."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miso_stats.const import DEFAULT_OPTIONS, DOMAIN, FUEL_MIX_URL, LOAD_URL, SINGULARITY_API
from custom_components.miso_stats.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    FIXTURE_NOW_ISO,
    SINGULARITY_OPTIONS,
    SINGULARITY_PATHS,
    TEST_API_KEY,
    TEST_NODE,
    load_fixture,
    register_singularity,
    singularity_calls,
)

KEY_ONLY_ENTITIES = {"sensor.miso_co2_intensity_estimate", "sensor.miso_marginal_co2_intensity"}


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options={**DEFAULT_OPTIONS, **options})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _synthetic_hours() -> str:
    """Hourly Michigan events from Oct 28 00:00 to Oct 31 15:00 UTC; rate = 700 + 10 x UTC hour lbs/MWh."""
    start = datetime(2026, 10, 28, tzinfo=UTC)
    events = []
    for i in range(3 * 24 + 16):
        stamp = start + timedelta(hours=i)
        events.append(
            {
                "timestamp": stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "agg_type": "state",
                "agg_id": "Michigan",
                "emissions": {"rate_co2e_lbs_per_mwh": 700 + 10 * stamp.hour},
                "fuel_mix": {"non_cfe_percentage": 0.65, "wind_percentage": 0.1, "nuclear_percentage": 0.25},
            }
        )
    return json.dumps({"data": events})


async def test_no_key_uses_miso_estimates(hass: HomeAssistant, mock_miso) -> None:
    entry = await _setup(hass)
    entity_ids = {e.entity_id for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}
    assert not entity_ids & KEY_ONLY_ENTITIES
    intensity = hass.states.get("sensor.miso_co2_intensity")
    assert float(intensity.state) == 446.0
    assert intensity.attributes["source"] == "miso" and intensity.attributes["estimated"] is True
    assert not [c for c in mock_miso.mock_calls if "singularity" in str(c[1])]


async def test_key_makes_carbon_numbers_local(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    assert entry.state is ConfigEntryState.LOADED

    intensity = hass.states.get("sensor.miso_co2_intensity")
    assert float(intensity.state) == 404.7  # Michigan, 15:00 UTC hour, 892.25 lbs/MWh (captured)
    assert intensity.attributes["source"] == "singularity"
    assert intensity.attributes["area_level"] == "state" and intensity.attributes["area"] == "Michigan"
    assert intensity.attributes["hour"] == "2026-10-31T15:00:00+00:00"
    assert intensity.attributes["fossil_rate"] == 624.0

    carbon_free = hass.states.get("sensor.miso_carbon_free")
    assert float(carbon_free.state) == 35.0  # non_cfe_percentage 0.65 is a fraction
    assert carbon_free.attributes["fuel_mix"]["nuclear"] == 23.0
    assert carbon_free.attributes["miso_wide"] == 32.6
    renewables = hass.states.get("sensor.miso_renewables")
    assert float(renewables.state) == 13.1  # wind 2.0 + solar 9.0 + hydro 0.8 + biomass 1.3
    assert renewables.attributes["miso_wide"] == 20.2

    # The MISO-wide estimate stays available for comparison.
    estimate = hass.states.get("sensor.miso_co2_intensity_estimate")
    assert float(estimate.state) == 446.0
    assert estimate.attributes["friendly_name"] == "MISO CO2 intensity estimate"

    marginal = hass.states.get("sensor.miso_marginal_co2_intensity")
    assert float(marginal.state) == 765.7  # AECI.ALTW, 15:25 UTC, 1688.0 lbs/MWh
    assert marginal.attributes["cp_node"] == TEST_NODE
    assert marginal.attributes["marginal_fuels"] == {"natural_gas": 0.92, "coal": 0.11, "wind": -0.03}

    # Demand still comes from MISO.
    assert float(hass.states.get("sensor.miso_demand").state) == 72619


async def test_local_index_backfilled(hass: HomeAssistant, mock_miso, freezer) -> None:
    """The index window is back-filled from the API's hourly history and built from local rates."""
    freezer.move_to(FIXTURE_NOW_ISO)
    for path, fixture in SINGULARITY_PATHS.items():
        if path != "/events/consumed":
            mock_miso.get(f"{SINGULARITY_API}{path}", text=load_fixture(fixture))
    mock_miso.get(f"{SINGULARITY_API}/events/consumed", text=_synthetic_hours())
    entry = await _setup(hass, **SINGULARITY_OPTIONS, low_carbon_index_threshold=70)
    assert len(singularity_calls(mock_miso, "/events/consumed")) == 4  # hourly fetch + 3 weekly back-fill chunks

    index = hass.states.get("sensor.miso_carbon_intensity_index")
    # Window: Oct 28-30 EST, cleanest 700 lbs/MWh (317.5 g/kWh), dirtiest 930 (421.8); now 850 (385.6).
    assert index.attributes["source"] == "singularity"
    assert index.attributes["window_days"] == 3
    assert index.attributes["window_min"] == 317.5 and index.attributes["window_max"] == 421.8
    assert int(index.state) == 65
    assert entry.runtime_data.data.low_carbon_now is True  # 65 <= 70

    freezer.move_to("2026-10-31 10:50:00-05:00")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(singularity_calls(mock_miso, "/events/consumed")) == 4  # back-filled once; hourly not due


async def test_consumed_hourly_marginal_every_poll(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    coordinator = entry.runtime_data
    consumed = len(singularity_calls(mock_singularity, "/events/consumed"))
    assert len(singularity_calls(mock_singularity, "/events/lme/by-node")) == 1

    freezer.move_to("2026-10-31 10:50:00-05:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(singularity_calls(mock_singularity, "/events/consumed")) == consumed  # within the hour
    assert len(singularity_calls(mock_singularity, "/events/lme/by-node")) == 2

    freezer.move_to("2026-10-31 11:36:00-05:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(singularity_calls(mock_singularity, "/events/consumed")) == consumed + 1


async def test_no_node_no_marginal_sensor(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    options = {k: v for k, v in SINGULARITY_OPTIONS.items() if k != "cp_node"}
    await _setup(hass, **options)
    assert hass.states.get("sensor.miso_co2_intensity_estimate") is not None
    assert hass.states.get("sensor.miso_marginal_co2_intensity") is None
    assert not singularity_calls(mock_singularity, "/events/lme/by-node")


async def test_rejected_key_raises_repair(hass: HomeAssistant, mock_miso, freezer) -> None:
    """HTTP 401: the carbon sensors go unavailable, the MISO estimate and demand keep working, Repairs explains."""
    freezer.move_to(FIXTURE_NOW_ISO)
    register_singularity(mock_miso, status=401)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.miso_co2_intensity").state == STATE_UNAVAILABLE
    assert float(hass.states.get("sensor.miso_co2_intensity_estimate").state) == 446.0
    assert float(hass.states.get("sensor.miso_demand").state) == 72619
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_api_key") is not None
    assert "HTTP 401" in entry.runtime_data.data.errors["singularity"]

    mock_miso.clear_requests()
    mock_miso.get(FUEL_MIX_URL, text=load_fixture("fuelmix.json"))
    mock_miso.get(LOAD_URL, text=load_fixture("load.json"))
    register_singularity(mock_miso)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_api_key") is None
    assert float(hass.states.get("sensor.miso_co2_intensity").state) == 404.7


async def test_forbidden_request_is_not_a_bad_key(hass: HomeAssistant, mock_miso, freezer) -> None:
    """HTTP 403 means the key can't make that request: reported in errors, but no Repairs issue."""
    freezer.move_to(FIXTURE_NOW_ISO)
    register_singularity(mock_miso, status=403)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "invalid_api_key") is None
    assert "HTTP 403" in entry.runtime_data.data.errors["singularity"]


async def test_removing_key_removes_entities(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    registry = er.async_get(hass)
    for entity_id in KEY_ONLY_ENTITIES:
        assert registry.async_get(entity_id) is not None

    hass.config_entries.async_update_entry(entry, options=dict(DEFAULT_OPTIONS))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for entity_id in KEY_ONLY_ENTITIES:
        assert registry.async_get(entity_id) is None
    assert hass.states.get("sensor.miso_co2_intensity").attributes["source"] == "miso"


async def test_retired_local_entities_removed(hass: HomeAssistant, mock_singularity, freezer) -> None:
    """Pre-release builds had separate Local sensors; they're merged into the main ones."""
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = MockConfigEntry(domain=DOMAIN, title="MISO", data={}, options={**DEFAULT_OPTIONS, **SINGULARITY_OPTIONS})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = [
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", config_entry=entry, suggested_object_id=f"miso_{key}"
        )
        for key in ("local_co2_intensity", "local_carbon_free")
    ]
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for item in old:
        assert registry.async_get(item.entity_id) is None


async def test_diagnostics_redact_key(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = await _setup(hass, **SINGULARITY_OPTIONS)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["options"]["api_key"] == "**REDACTED**"
    assert TEST_API_KEY not in str(diag)
    api = diag["emissions_api"]
    assert api["configured"] is True and api["carbon_source"] == "singularity"
    assert api["miso_wide_estimate"] == 446.0
    assert api["local"]["rate_g_per_kwh"] == 404.7
    assert api["local_history"]["hours"] == 4 and api["local_history"]["area"] == "state:Michigan"
    assert api["marginal"]["rate_g_per_kwh"] == 765.7
    assert diag["raw"]["consumed"]["data"][0]["agg_id"] == "Michigan"
