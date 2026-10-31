"""Client and parsers for MISO's Emissions API, hosted by Singularity Energy.

Optional: it needs a free account and API key (https://miso.singularity.energy/account).
Docs: https://miso.singularity.energy/api-docs. Limits: 5 requests/s, 100,000 requests/month.
- events/consumed: hourly location-based (consumed) CO2e rate and fuel mix for MISO, a
  subregion, local resource zone, local balancing authority, state or county.
- data-availability/lookup/coordinates: which county, state, zone and LBA a location is in.

All times are UTC. Rates are requested in US units (lbs/MWh) and converted to g/kWh; metric
fields (kgs/MWh, which equals g/kWh) are read too, should the API return them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from typing import Any

import aiohttp

from .api import USER_AGENT, _number
from .areas import lba_code
from .const import REQUEST_TIMEOUT, SINGULARITY_API

G_PER_KWH_PER_LB_PER_MWH = 0.45359237  # 1 lb/MWh = 453.59237 g / 1000 kWh
CONSUMED_LOOKBACK = timedelta(hours=6)  # consumed data lags about 90 minutes; ask for a few hours


class SingularityError(Exception):
    """The Emissions API could not be reached or understood."""


class SingularityAuthError(SingularityError):
    """The API key was rejected."""


class SingularityForbiddenError(SingularityError):
    """The key is valid but not allowed this request (HTTP 403), e.g. a data level it can't query."""


class SingularityNoAreaError(SingularityError):
    """The location isn't in any MISO area the API covers."""


def _utc(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)


def _rate_g_per_kwh(values: Mapping[str, Any], prefix: str) -> float | None:
    """A CO2e rate in g/kWh from `<prefix>_lbs_per_mwh` or `<prefix>_kgs_per_mwh`."""
    for suffix, factor in (("kgs_per_mwh", 1.0), ("kg_per_mwh", 1.0), ("lbs_per_mwh", G_PER_KWH_PER_LB_PER_MWH)):
        value = _number(values.get(f"{prefix}_{suffix}"))
        if value is not None:
            return value * factor
    return None


def _events(text: str, name: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(text)
    except ValueError as err:
        raise SingularityError(f"{name} is not valid JSON: {err}") from err
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list):
        raise SingularityError(f"{name} did not contain a data list")
    return [e for e in data if isinstance(e, dict)]


# --- location ------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Location:
    """The consumed-emissions areas a home sits in."""

    county: str | None
    state: str | None
    lrz: int | None
    lba: str | None
    subregion: str | None

    def agg_id(self, level: str) -> str | None:
        """The API's agg_ids value for an area level (None for the whole of MISO or when unknown)."""
        match level:
            case "county":
                return f"{self.county} ({self.state})" if self.county and self.state else None
            case "lrz":
                return str(self.lrz) if self.lrz is not None else None
            case "lba":
                return lba_code(self.lba)  # the lookup gives "Detroit Edison Company"; queries want "DECO"
            case "state":
                return self.state
            case "subregion":
                return self.subregion
        return None

    def as_dict(self) -> dict[str, Any]:
        """For the entry's options."""
        return {
            "county": self.county,
            "state": self.state,
            "lrz": self.lrz,
            "lba": self.lba,
            "subregion": self.subregion,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Location:
        """From the entry's options."""
        data = data or {}
        lrz = _number(data.get("lrz"))
        return cls(
            county=data.get("county") or None,
            state=data.get("state") or None,
            lrz=int(lrz) if lrz is not None else None,
            lba=data.get("lba") or None,
            subregion=data.get("subregion") or None,
        )


def parse_location(text: str) -> Location:
    """Parse a data-availability lookup."""
    try:
        raw = json.loads(text)
    except ValueError as err:
        raise SingularityError(f"location lookup is not valid JSON: {err}") from err
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict) or not data.get("state"):
        raise SingularityNoAreaError("location lookup found no MISO area for this location")
    return Location.from_dict(data)


# --- consumed emissions ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class LocalEmissions:
    """One hour of consumed emissions for an area."""

    timestamp: datetime  # start of the hour, UTC
    agg_type: str
    agg_id: str | None
    rate: float  # g CO2e/kWh
    non_cfe_rate: float | None  # g CO2e/kWh of the fossil-fuelled share alone
    fuel_mix: dict[str, float] = field(default_factory=dict)  # percentages by fuel

    @property
    def carbon_free(self) -> float | None:
        """Carbon-free share of the energy consumed, %."""
        non_cfe = self.fuel_mix.get("non_cfe")
        return None if non_cfe is None else round(100 - non_cfe, 1)

    @property
    def renewables(self) -> float | None:
        """Renewable share of the energy consumed (wind, solar, hydro, biomass), %."""
        if not self.fuel_mix:
            return None
        return round(sum(self.fuel_mix.get(f, 0.0) for f in RENEWABLE_CONSUMED), 1)


RENEWABLE_CONSUMED = ("wind", "solar", "hydro", "biomass")


def parse_consumed_events(text: str) -> list[LocalEmissions]:
    """Every hour with a rate from events/consumed, oldest first."""
    hours: dict[datetime, LocalEmissions] = {}
    for event in _events(text, "consumed events"):
        stamp = _timestamp(event.get("timestamp"))
        emissions = event.get("emissions") if isinstance(event.get("emissions"), dict) else {}
        rate = _rate_g_per_kwh(emissions, "rate_co2e")
        if stamp is None or rate is None:
            continue
        mix = event.get("fuel_mix") if isinstance(event.get("fuel_mix"), dict) else {}
        hours[stamp] = LocalEmissions(
            timestamp=stamp,
            agg_type=str(event.get("agg_type") or ""),
            agg_id=event.get("agg_id"),
            rate=rate,
            non_cfe_rate=_rate_g_per_kwh(emissions, "non_cfe_rate_co2e"),
            fuel_mix=_percentages(mix),
        )
    return [hours[t] for t in sorted(hours)]


def parse_consumed(text: str) -> LocalEmissions | None:
    """The latest hour with a rate from events/consumed (None when the window is empty)."""
    events = parse_consumed_events(text)
    return events[-1] if events else None


def _percentages(mix: Mapping[str, Any]) -> dict[str, float]:
    """Fuel mix in %, keyed by fuel. The API's `*_percentage` fields are fractions (0.65 = 65 %);
    whole percentages are recognised by their sum and left as they are."""
    values = {k.removesuffix("_percentage"): v for k, v in ((k, _number(v)) for k, v in mix.items()) if v is not None}
    fuels_total = sum(v for k, v in values.items() if k != "non_cfe")
    scale = 100 if 0 < fuels_total <= 1.5 or (not fuels_total and values.get("non_cfe", 0) <= 1) else 1
    return {k: round(v * scale, 2) for k, v in values.items()}


# --- HTTP -------------------------------------------------------------------------------


class SingularityClient:
    """Calls MISO's Emissions API with the user's key (never logged)."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str, timeout: float = REQUEST_TIMEOUT) -> None:
        self._session = session
        self._headers = {"x-api-key": api_key, "Accept": "application/json", "User-Agent": USER_AGENT}
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, path: str, params: dict[str, str]) -> str:
        try:
            async with self._session.get(
                f"{SINGULARITY_API}{path}", params=params, headers=self._headers, timeout=self._timeout
            ) as resp:
                if resp.status < 400:
                    return await resp.text(encoding="utf-8")
                detail = f"{path} returned HTTP {resp.status}: {(await resp.text())[:200]}"
                if resp.status == 401:
                    raise SingularityAuthError(detail)
                if resp.status == 403:
                    # The key works but isn't allowed this request (a data level or endpoint).
                    raise SingularityForbiddenError(detail)
                if resp.status == 404 and path.startswith("/data-availability"):
                    raise SingularityNoAreaError("location lookup found no MISO area for this location")
                if resp.status == 429:
                    raise SingularityError("rate limited by the Emissions API (HTTP 429)")
                raise SingularityError(detail)
        except TimeoutError as err:
            raise SingularityError(f"Timed out fetching {path}") from err
        except aiohttp.ClientError as err:
            raise SingularityError(f"Error fetching {path}: {err}") from err

    async def location(self, latitude: float, longitude: float) -> Location:
        """Areas for a location. Coordinates are rounded to 2 decimals (about 1 km) before sending."""
        text = await self._get(
            "/data-availability/lookup/coordinates",
            {"latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}"},
        )
        return parse_location(text)

    async def consumed_text(self, level: str, agg_id: str | None, now: datetime, start: datetime | None = None) -> str:
        """Raw consumed events (emissions and fuel mix) from `start` (default: the last few hours) to `now`."""
        params = {
            "agg_type": level,
            "start": _utc(start or now - CONSUMED_LOOKBACK),
            "end": _utc(now),
            "resolution": "hourly",
            "units": "us",
        }
        if level != "miso":
            if not agg_id:
                raise SingularityError(f"no {level} known for this location")
            params["agg_ids"] = agg_id
        return await self._get("/events/consumed", params)
