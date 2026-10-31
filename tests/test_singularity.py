"""Emissions API (Singularity) parsers and client."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest

from custom_components.miso_stats.const import SINGULARITY_API
from custom_components.miso_stats.singularity import (
    Location,
    SingularityAuthError,
    SingularityClient,
    SingularityError,
    SingularityNoAreaError,
    marginal_from,
    parse_consumed,
    parse_lme,
    parse_lmf,
    parse_location,
    parse_nodes,
)

from .conftest import TEST_API_KEY, TEST_NODE, load_fixture, register_singularity, singularity_calls

NOW = datetime(2026, 10, 31, 15, 35, tzinfo=UTC)


def test_parse_location_and_area_ids() -> None:
    location = parse_location(load_fixture("singularity_location.json"))
    assert location == Location(
        county="Wayne", state="Michigan", lrz=7, lba="Detroit Edison Company", subregion="CENTRAL"
    )
    assert location.agg_id("state") == "Michigan"
    assert location.agg_id("county") == "Wayne (Michigan)"
    assert location.agg_id("lrz") == "7"
    assert location.agg_id("lba") == "Detroit Edison Company"
    assert location.agg_id("subregion") == "CENTRAL"
    assert location.agg_id("miso") is None
    assert Location.from_dict(location.as_dict()) == location
    assert Location.from_dict(None).agg_id("state") is None
    with pytest.raises(SingularityNoAreaError):
        parse_location(json.dumps({"data": {"latitude": 1, "longitude": 2}}))


def test_parse_consumed_latest_hour_in_g_per_kwh() -> None:
    """Captured from the live API (Michigan, Oct 31 2026): fuel mix "percentages" arrive as fractions."""
    local = parse_consumed(load_fixture("singularity_consumed.json"))
    assert local is not None
    assert local.timestamp == datetime(2026, 10, 31, 15, 0, tzinfo=UTC)
    assert (local.agg_type, local.agg_id) == ("state", "Michigan")
    assert local.rate == pytest.approx(404.72, abs=0.01)  # 892.25 lbs/MWh
    assert local.non_cfe_rate == pytest.approx(623.98, abs=0.01)  # 1375.65 lbs/MWh
    assert local.carbon_free == 35.0  # non_cfe_percentage 0.65
    assert local.fuel_mix["nuclear"] == 23.0 and local.fuel_mix["wind"] == 2.0
    assert 99 <= sum(v for k, v in local.fuel_mix.items() if k != "non_cfe") <= 101


def test_parse_consumed_whole_percentages_kept() -> None:
    """Should the API switch to whole percentages, they're used as they are."""
    event = {
        "timestamp": "2026-10-31T13:00:00Z",
        "agg_type": "state",
        "agg_id": "Michigan",
        "emissions": {"rate_co2e_lbs_per_mwh": 990.0},
        "fuel_mix": {"non_cfe_percentage": 74.2, "coal_percentage": 30.1, "natural_gas_percentage": 44.1,
                     "nuclear_percentage": 14.5, "wind_percentage": 11.3},
    }
    local = parse_consumed(json.dumps({"data": [event]}))
    assert local is not None and local.carbon_free == 25.8 and local.fuel_mix["coal"] == 30.1


def test_parse_consumed_metric_units_and_empty() -> None:
    event = {"timestamp": "2026-10-31T13:00:00Z", "agg_type": "miso", "emissions": {"rate_co2e_kgs_per_mwh": 450}}
    metric = json.dumps({"data": [event]})
    local = parse_consumed(metric)
    assert local is not None and local.rate == 450 and local.carbon_free is None
    assert parse_consumed(json.dumps({"data": []})) is None
    with pytest.raises(SingularityError):
        parse_consumed("<html>")
    with pytest.raises(SingularityError):
        parse_consumed(json.dumps({"message": "Forbidden"}))


def test_parse_marginal() -> None:
    lme, lmf = load_fixture("singularity_lme.json"), load_fixture("singularity_lmf.json")
    stamp, rate = parse_lme(lme, TEST_NODE)
    assert stamp == datetime(2026, 10, 31, 15, 25, tzinfo=UTC)
    assert rate == pytest.approx(765.66, abs=0.01)  # 1688.0 lbs/MWh
    assert parse_lmf(lmf, TEST_NODE, datetime(2026, 10, 31, 15, 20, tzinfo=UTC))["coal"] == 0.33
    marginal = marginal_from(lme, lmf, TEST_NODE)
    assert marginal is not None and marginal.fuels == {"natural_gas": 0.92, "coal": 0.11, "wind": -0.03}
    assert parse_lme(lme, "OTHER.NODE") is None
    assert marginal_from(json.dumps({"data": []}), lmf, TEST_NODE) is None


def test_parse_nodes() -> None:
    assert parse_nodes(load_fixture("singularity_nodes.json")) == ["AECI.ALTW", "ALTE.CASSVILLE", "AMIL.BALDWIN1"]


async def test_client_sends_key_and_rounds_coordinates(hass: HomeAssistant, aioclient_mock) -> None:
    register_singularity(aioclient_mock)
    client = SingularityClient(async_get_clientsession(hass), TEST_API_KEY)
    location = await client.location(42.10123, -83.29456)
    assert location.state == "Michigan"
    call = singularity_calls(aioclient_mock, "/data-availability/lookup/coordinates")[0]
    assert call[1].query["latitude"] == "42.10" and call[1].query["longitude"] == "-83.29"
    assert call[3]["x-api-key"] == TEST_API_KEY

    await client.consumed_text("state", "Michigan", NOW)
    call = singularity_calls(aioclient_mock, "/events/consumed")[0]
    assert dict(call[1].query) == {
        "agg_type": "state",
        "agg_ids": "Michigan",
        "start": "2026-10-31T09:35:00Z",
        "end": "2026-10-31T15:35:00Z",
        "resolution": "hourly",
        "units": "us",
    }
    await client.consumed_text("miso", None, NOW)
    assert "agg_ids" not in singularity_calls(aioclient_mock, "/events/consumed")[1][1].query
    with pytest.raises(SingularityError):
        await client.consumed_text("county", None, NOW)

    assert await client.nodes(NOW) == ["AECI.ALTW", "ALTE.CASSVILLE", "AMIL.BALDWIN1"]
    assert singularity_calls(aioclient_mock, "/events/lme/by-timestamp")[0][1].query["timestamp"] == (
        "2026-10-31T15:20:00Z"
    )


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, SingularityAuthError), (403, SingularityAuthError), (429, SingularityError), (500, SingularityError)],
)
async def test_client_errors(hass: HomeAssistant, aioclient_mock, status: int, error: type[Exception]) -> None:
    aioclient_mock.get(f"{SINGULARITY_API}/events/lme/by-node", status=status)
    aioclient_mock.get(f"{SINGULARITY_API}/events/lmf/by-node", status=status)
    client = SingularityClient(async_get_clientsession(hass), TEST_API_KEY)
    with pytest.raises(error):
        await client.marginal_texts(TEST_NODE, NOW)


async def test_client_location_not_found(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{SINGULARITY_API}/data-availability/lookup/coordinates", status=404)
    client = SingularityClient(async_get_clientsession(hass), TEST_API_KEY)
    with pytest.raises(SingularityNoAreaError):
        await client.location(21.3, -157.8)
