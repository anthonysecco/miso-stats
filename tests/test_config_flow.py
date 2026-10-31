"""Config and options flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miso_stats.const import DEFAULT_OPTIONS, DOMAIN, FUEL_MIX_URL


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
