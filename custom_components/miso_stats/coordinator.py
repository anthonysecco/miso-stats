"""Polling coordinator, derived values and the carbon intensity index."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FuelMix,
    Load,
    MisoClient,
    MisoError,
    co2_intensity,
    co2_rate,
    parse_fuel_mix,
    parse_load,
    share,
)
from .const import (
    CARBON_FREE_FUELS,
    CONF_API_KEY,
    CONF_COAL_FACTOR,
    CONF_CP_NODE,
    CONF_GAS_FACTOR,
    CONF_INDEX_DAYS,
    CONF_LOCAL_LEVEL,
    CONF_LOCATION,
    CONF_LOW_CARBON_INDEX,
    CONF_SCAN_INTERVAL,
    DEFAULT_LOCAL_LEVEL,
    DEFAULT_OPTIONS,
    DOMAIN,
    HISTORY_RETRY_HOURS,
    LOCAL_REFRESH,
    LOCAL_STALE_AFTER,
    LOGGER,
    MARGINAL_STALE_AFTER,
    MAX_INDEX_DAYS,
    MIN_INDEX_DAYS,
    RENEWABLE_FUELS,
    STALE_AFTER,
    scan_interval,
)
from .history import IndexRange, MixHistory, co2_index
from .singularity import (
    LocalEmissions,
    Location,
    Marginal,
    SingularityAuthError,
    SingularityClient,
    SingularityError,
    marginal_from,
    parse_consumed,
)

ISSUE_INVALID_KEY = "invalid_api_key"

type MisoConfigEntry = ConfigEntry[MisoCoordinator]


@dataclass(slots=True)
class MisoData:
    """Everything the entities read, refreshed together."""

    fuel_mix: FuelMix
    load: Load | None
    coal_factor: float
    gas_factor: float
    co2_intensity: float | None
    co2_emissions: float | None
    renewables: float | None
    carbon_free: float | None
    low_carbon_now: bool | None
    co2_index: int | None = None
    index_range: IndexRange | None = None
    errors: dict[str, str] = field(default_factory=dict)
    # Optional, from MISO's Emissions API (Singularity) when an API key is set
    local: LocalEmissions | None = None
    marginal: Marginal | None = None


def options_with_defaults(options: dict[str, Any]) -> dict[str, Any]:
    """Entry options over the defaults."""
    return {**DEFAULT_OPTIONS, **options}


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def derive(
    fuel_mix: FuelMix,
    load: Load | None,
    options: dict[str, Any],
    index_range: IndexRange | None = None,
) -> MisoData:
    """Everything the entities read, from one set of feeds and the index window."""
    opts = options_with_defaults(options)
    coal_g, gas_g = float(opts[CONF_COAL_FACTOR]), float(opts[CONF_GAS_FACTOR])
    intensity = _round(co2_intensity(fuel_mix.mw, coal_g, gas_g))
    # Low carbon: the index (0 = cleanest hour of recent days) at or below the threshold.
    # Unknown until enough history is in.
    index = co2_index(intensity, index_range)
    return MisoData(
        fuel_mix=fuel_mix,
        load=load,
        coal_factor=coal_g,
        gas_factor=gas_g,
        co2_intensity=intensity,
        co2_emissions=_round(co2_rate(fuel_mix.mw, coal_g, gas_g), 0),
        renewables=_round(share(fuel_mix.mw, RENEWABLE_FUELS)),
        carbon_free=_round(share(fuel_mix.mw, CARBON_FREE_FUELS)),
        low_carbon_now=index <= float(opts[CONF_LOW_CARBON_INDEX]) if index is not None else None,
        co2_index=index,
        index_range=index_range,
    )


def _fresh(previous: Load | None, now: datetime) -> Load | None:
    """Keep the last good demand reading while it's recent; drop it once stale."""
    if previous is None or now - previous.updated > STALE_AFTER:
        return None
    return previous


def has_api_key(options: dict[str, Any]) -> bool:
    """Whether the optional Emissions API (Singularity) is configured."""
    return bool(str(options.get(CONF_API_KEY) or "").strip())


class MisoCoordinator(DataUpdateCoordinator[MisoData]):
    """Fetches FuelMix and RealTimeTotalLoad every poll; yesterday's mix once a day."""

    config_entry: MisoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: MisoConfigEntry) -> None:
        """Set up from the entry's options."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_OPTIONS[CONF_SCAN_INTERVAL])),
        )
        self.client = MisoClient(async_get_clientsession(hass))
        self.raw: dict[str, str | None] = {"fuel_mix": None, "load": None}
        self._load: Load | None = None
        self.history = MixHistory(hass)
        self._history_task: asyncio.Task[None] | None = None
        self._history_tried: datetime | None = None
        self.history_error: str | None = None
        # Optional Emissions API (Singularity)
        options = options_with_defaults(dict(entry.options))
        self.singularity: SingularityClient | None = None
        if has_api_key(options):
            self.singularity = SingularityClient(async_get_clientsession(hass), str(options[CONF_API_KEY]).strip())
            self.raw.update(consumed=None, lme=None, lmf=None)
        self.local_level: str = str(options.get(CONF_LOCAL_LEVEL) or DEFAULT_LOCAL_LEVEL)
        self.location = Location.from_dict(options.get(CONF_LOCATION))
        self.cp_node: str | None = str(options.get(CONF_CP_NODE) or "").strip() or None
        self._local: LocalEmissions | None = None
        self._local_fetched: datetime | None = None
        self._marginal: Marginal | None = None

    @property
    def options(self) -> dict[str, Any]:
        """Entry options over the defaults."""
        return options_with_defaults(dict(self.config_entry.options))

    @property
    def index_days(self) -> int:
        """Days of history behind the carbon intensity index."""
        return max(MIN_INDEX_DAYS, min(MAX_INDEX_DAYS, int(self.options[CONF_INDEX_DAYS])))

    def _window(self, now: datetime) -> IndexRange | None:
        opts = self.options
        return self.history.window(now, self.index_days, float(opts[CONF_COAL_FACTOR]), float(opts[CONF_GAS_FACTOR]))

    async def _async_setup(self) -> None:
        """Load the stored fuel mix history before the first update."""
        await self.history.async_load()

    def _maybe_fetch_history(self, now: datetime) -> None:
        """Download yesterday's mix in the background when it's missing (at most hourly after a miss)."""
        if self._history_task is not None and not self._history_task.done():
            return
        if self._history_tried and now - self._history_tried < timedelta(hours=HISTORY_RETRY_HOURS):
            return
        if not self.history.needs(now):
            return
        self._history_tried = now
        self._history_task = self.config_entry.async_create_background_task(
            self.hass, self._async_fetch_history(), f"{DOMAIN} fuel mix history"
        )

    async def _async_fetch_history(self) -> None:
        try:
            day = await self.client.history_day()
        except MisoError as err:
            self.history_error = str(err)
            LOGGER.debug("Yesterday's fuel mix not available yet: %s", err)
            return
        self.history_error = None
        await self.history.async_add(day)
        if not self.history.needs(dt_util.now()):
            self._history_tried = None  # got the day we wanted: no retry wait
        if self.data is not None:
            # Refresh the index (and the low-carbon signal built on it) without waiting for the next poll.
            data = derive(
                self.data.fuel_mix,
                self.data.load,
                dict(self.config_entry.options),
                self._window(dt_util.now()),
            )
            data.errors = self.data.errors
            data.local, data.marginal = self.data.local, self.data.marginal
            self.data = data
            self.async_update_listeners()

    async def _async_update_singularity(self, now: datetime, errors: dict[str, str]) -> None:
        """Consumed emissions once an hour and marginal emissions every poll, from the Emissions API.

        Failures never fail the whole update: the affected sensors keep their last value while it's
        recent. A rejected key raises a Repairs issue until a call succeeds again.
        """
        client = self.singularity
        if client is None:
            return
        try:
            if self._local_fetched is None or now - self._local_fetched >= LOCAL_REFRESH:
                text = await client.consumed_text(self.local_level, self.location.agg_id(self.local_level), now)
                self.raw["consumed"] = text
                self._local = parse_consumed(text) or self._local
                self._local_fetched = now
            if self.cp_node:
                lme_text, lmf_text = await client.marginal_texts(self.cp_node, now)
                self.raw["lme"], self.raw["lmf"] = lme_text, lmf_text
                self._marginal = marginal_from(lme_text, lmf_text, self.cp_node) or self._marginal
        except SingularityAuthError as err:
            errors["singularity"] = str(err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_INVALID_KEY,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_INVALID_KEY,
            )
        except SingularityError as err:
            errors["singularity"] = str(err)
            LOGGER.debug("MISO Emissions API failed this poll: %s", err)
        else:
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_INVALID_KEY)
        if self._local is not None and now - self._local.timestamp > LOCAL_STALE_AFTER:
            self._local = None
        if self._marginal is not None and now - self._marginal.timestamp > MARGINAL_STALE_AFTER:
            self._marginal = None

    async def _async_update_data(self) -> MisoData:
        now = dt_util.now()
        try:
            fuel_text, load_text = await self.client.fetch()
            fuel_mix = parse_fuel_mix(fuel_text)
        except MisoError as err:
            raise UpdateFailed(str(err)) from err
        self.raw["fuel_mix"] = fuel_text

        errors: dict[str, str] = {}
        try:
            if isinstance(load_text, MisoError):
                raise load_text
            self.raw["load"] = load_text
            self._load = parse_load(load_text)
        except MisoError as err:
            errors["load"] = str(err)
            LOGGER.debug("MISO load feed failed this poll: %s", err)
            self._load = _fresh(self._load, now)

        # Start the history download before computing this poll's result: background tasks start
        # eagerly, so a download that completes without waiting must be in the window used here,
        # or this poll's result would overwrite the index it just produced.
        await self._async_update_singularity(now, errors)
        self._maybe_fetch_history(now)
        data = derive(fuel_mix, self._load, dict(self.config_entry.options), self._window(now))
        data.errors = errors
        data.local, data.marginal = self._local, self._marginal
        return data
