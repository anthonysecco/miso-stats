"""Config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miso_stats.const import DEFAULT_OPTIONS, DOMAIN, FUEL_MIX_URL, SINGULARITY_API

from .conftest import (
    FIXTURE_NOW_ISO,
    SINGULARITY_OPTIONS,
    TEST_API_KEY,
    TEST_NODE,
    load_fixture,
    register_singularity,
)


async def test_user_flow(hass: HomeAssistant, mock_miso) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "MISO"
    assert result["options"] == DEFAULT_OPTIONS


async def test_user_flow_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(FUEL_MIX_URL, text="<html>down for maintenance</html>")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_single_instance(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data={}, options=dict(DEFAULT_OPTIONS)).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant, mock_miso) -> None:
    # A pre-release entry still carrying the old "hub" option.
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={**DEFAULT_OPTIONS, "hub": "michigan"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.data.co2_intensity == 446.0

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert "hub" not in result["data_schema"].schema
    new = {**DEFAULT_OPTIONS, "coal_emission_factor": 900}
    result = await hass.config_entries.options.async_configure(result["flow_id"], new)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == new  # saving drops the old hub option
    # reloaded with the new factor
    data = entry.runtime_data.data
    assert data.co2_intensity == pytest.approx((19727 * 900 + 25417 * 400) / 67019, abs=0.05)
    assert float(hass.states.get("sensor.miso_co2_intensity").state) == data.co2_intensity


async def test_user_flow_with_api_key(hass: HomeAssistant, mock_singularity, freezer) -> None:
    """A key leads to the emissions step, which shows the home's areas and saves level, node and areas."""
    freezer.move_to(FIXTURE_NOW_ISO)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": f" {TEST_API_KEY} "})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "singularity"
    assert result["description_placeholders"]["state"] == "Michigan"
    assert result["description_placeholders"]["lrz"] == "7"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"local_level": "state", "cp_node": TEST_NODE}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {**DEFAULT_OPTIONS, **SINGULARITY_OPTIONS}  # key stored trimmed


async def test_user_flow_rejected_key(hass: HomeAssistant, mock_miso) -> None:
    register_singularity(mock_miso, status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": "wrong"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_area_without_data(hass: HomeAssistant, mock_miso, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    location, nodes = load_fixture("singularity_location.json"), load_fixture("singularity_nodes.json")
    mock_miso.get(f"{SINGULARITY_API}/data-availability/lookup/coordinates", text=location)
    mock_miso.get(f"{SINGULARITY_API}/events/lme/by-timestamp", text=nodes)
    mock_miso.get(f"{SINGULARITY_API}/events/consumed", text='{"data": []}')
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"api_key": TEST_API_KEY})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"local_level": "lba"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"local_level": "level_unavailable"}


async def test_options_add_then_remove_api_key(hass: HomeAssistant, mock_singularity, freezer) -> None:
    freezer.move_to(FIXTURE_NOW_ISO)
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=dict(DEFAULT_OPTIONS))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULT_OPTIONS, "api_key": TEST_API_KEY}
    )
    assert result["step_id"] == "singularity"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"local_level": "lrz"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["api_key"] == TEST_API_KEY
    assert entry.options["local_level"] == "lrz"
    assert "cp_node" not in entry.options
    assert hass.states.get("sensor.miso_local_co2_intensity") is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], dict(DEFAULT_OPTIONS))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == DEFAULT_OPTIONS  # key, areas and level dropped
    assert hass.states.get("sensor.miso_local_co2_intensity") is None
