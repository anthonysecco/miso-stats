"""Polling coordinator, derived values and the carbon intensity index.

Two sources:
- MISO's keyless feeds (always): fuel mix, demand and load forecast, plus an estimated
  MISO-wide CO2 intensity.
- MISO's Emissions API, run by Singularity (only with an API key): when a key is set, every
  carbon number (CO2 intensity, carbon-free and renewable shares, the index and Low Carbon
  Energy Now) comes from it, for the chosen local area. Marginal emissions need a pricing node.
"""

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
from .history import IndexRange, LocalHistory, MixHistory, co2_index
from .singularity import (
    LocalEmissions,
    Location,
    Marginal,
    SingularityAuthError,
    SingularityClient,
    SingularityError,
    marginal_from,
    parse_consumed_events,
)

ISSUE_INVALID_KEY = "invalid_api_key"
SOURCE_MISO = "miso"
SOURCE_SINGULARITY = "singularity"
BACKFILL_CHUNK = timedelta(days=7)  # local history back-fill: one request per week of hours

type MisoConfigEntry = ConfigEntry[MisoCoordinator]


@dataclass(slots=True)
class MisoData:
    """Everything the entities read, refreshed together."""

    fuel_mix: FuelMix
    load: Load | None
    coal_factor: float
    gas_factor: float
    source: str  # where the carbon numbers come from: "miso" (estimate) or "singularity" (local)
    co2_intensity: float | None
    renewables: float | None
    carbon_free: float | None
    low_carbon_now: bool | None
    estimate: float | None  # MISO-wide estimated intensity (the headline value without a key)
    miso_renewables: float | None
    miso_carbon_free: float | None
    co2_emissions: float | None  # MISO-wide, estimated t/h
    co2_index: int | None = None
    index_range: IndexRange | None = None
    errors: dict[str, str] = field(default_factory=dict)
    local: LocalEmissions | None = None
    marginal: Marginal | None = None


def options_with_defaults(options: dict[str, Any]) -> dict[str, Any]:
    """Entry options over the defaults."""
    return {**DEFAULT_OPTIONS, **options}


def has_api_key(options: dict[str, Any]) -> bool:
    """Whether the optional Emissions API (Singularity) is configured."""
    return bool(str(options.get(CONF_API_KEY) or "").strip())


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def derive(
    fuel_mix: FuelMix,
    load: Load | None,
    options: dict[str, Any],
    index_range: IndexRange | None = None,
    local: LocalEmissions | None = None,
) -> MisoData:
    """Everything the entities read, from one set of feeds and the index window.

    With an API key the carbon numbers come from `local` (None while it's unavailable, never
    the MISO estimate: the index window is local too). Without one they're MISO-wide estimates.
    """
    opts = options_with_defaults(options)
    coal_g, gas_g = float(opts[CONF_COAL_FACTOR]), float(opts[CONF_GAS_FACTOR])
    estimate = _round(co2_intensity(fuel_mix.mw, coal_g, gas_g))
    miso_renewables = _round(share(fuel_mix.mw, RENEWABLE_FUELS))
    miso_carbon_free = _round(share(fuel_mix.mw, CARBON_FREE_FUELS))
    if has_api_key(opts):
        source = SOURCE_SINGULARITY
        intensity = _round(local.rate) if local else None
        renewables = local.renewables if local else None
        carbon_free = local.carbon_free if local else None
    else:
        source, intensity, renewables, carbon_free = SOURCE_MISO, estimate, miso_renewables, miso_carbon_free
    # Low carbon: the index (0 = cleanest hour of recent days) at or below the threshold.
    # Unknown until enough history is in.
    index = co2_index(intensity, index_range)
    return MisoData(
        fuel_mix=fuel_mix,
        load=load,
        coal_factor=coal_g,
        gas_factor=gas_g,
        source=source,
        co2_intensity=intensity,
        renewables=renewables,
        carbon_free=carbon_free,
        low_carbon_now=index <= float(opts[CONF_LOW_CARBON_INDEX]) if index is not None else None,
        estimate=estimate,
        miso_renewables=miso_renewables,
        miso_carbon_free=miso_carbon_free,
        co2_emissions=_round(co2_rate(fuel_mix.mw, coal_g, gas_g), 0),
        co2_index=index,
        index_range=index_range,
        local=local,
    )


def _fresh(previous: Load | None, now: datetime) -> Load | None:
    """Keep the last good demand reading while it's recent; drop it once stale."""
    if previous is None or now - previous.updated > STALE_AFTER:
        return None
    return previous


class MisoCoordinator(DataUpdateCoordinator[MisoData]):
    """Fetches MISO's feeds every poll and yesterday's mix once a day; with a key, the Emissions API too."""

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
        self.local_level: str = str(options.get(CONF_LOCAL_LEVEL) or DEFAULT_LOCAL_LEVEL)
        self.location = Location.from_dict(options.get(CONF_LOCATION))
        self.cp_node: str | None = str(options.get(CONF_CP_NODE) or "").strip() or None
        self.local_history: LocalHistory | None = None
        if has_api_key(options):
            self.singularity = SingularityClient(async_get_clientsession(hass), str(options[CONF_API_KEY]).strip())
            self.raw.update(consumed=None, lme=None, lmf=None)
            area = f"{self.local_level}:{self.location.agg_id(self.local_level) or 'miso'}"
            self.local_history = LocalHistory(hass, area)
        self._local: LocalEmissions | None = None
        self._local_fetched: datetime | None = None
        self._marginal: Marginal | None = None
        self._backfill_task: asyncio.Task[None] | None = None
        self._backfill_tried: datetime | None = None
        self._backfilled = False
        self.backfill_error: str | None = None

    @property
    def options(self) -> dict[str, Any]:
        """Entry options over the defaults."""
        return options_with_defaults(dict(self.config_entry.options))

    @property
    def index_days(self) -> int:
        """Days of history behind the carbon intensity index."""
        return max(MIN_INDEX_DAYS, min(MAX_INDEX_DAYS, int(self.options[CONF_INDEX_DAYS])))

    def _window(self, now: datetime) -> IndexRange | None:
        if self.local_history is not None:
            return self.local_history.window(now, self.index_days)
        opts = self.options
        return self.history.window(now, self.index_days, float(opts[CONF_COAL_FACTOR]), float(opts[CONF_GAS_FACTOR]))

    async def _async_setup(self) -> None:
        """Load stored history before the first update."""
        await self.history.async_load()
        if self.local_history is not None:
            await self.local_history.async_load()

    def _rederive(self) -> None:
        """Recompute from the last poll's feeds after history changed, without waiting for the next poll."""
        if self.data is None:
            return
        data = derive(
            self.data.fuel_mix,
            self.data.load,
            dict(self.config_entry.options),
            self._window(dt_util.now()),
            self.data.local,
        )
        data.errors, data.marginal = self.data.errors, self.data.marginal
        self.data = data
        self.async_update_listeners()

    # --- MISO history (keyless index) -----------------------------------------------------

    def _maybe_fetch_history(self, now: datetime) -> None:
        """Download yesterday's mix in the background when it's missing (at most hourly after a miss).

        Kept even with a key, so removing the key leaves a working index.
        """
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
        if self.local_history is None:
            self._rederive()

    # --- Emissions API (Singularity) ----------------------------------------------------------

    def _maybe_backfill_local(self, now: datetime) -> None:
        """Back-fill the local index window once (retried hourly after a failure)."""
        history = self.local_history
        if history is None or self._backfilled or (self._backfill_task and not self._backfill_task.done()):
            return
        if self._backfill_tried and now - self._backfill_tried < timedelta(hours=HISTORY_RETRY_HOURS):
            return
        if not history.needs_backfill(now, self.index_days):
            self._backfilled = True
            return
        self._backfill_tried = now
        self._backfill_task = self.config_entry.async_create_background_task(
            self.hass, self._async_backfill_local(now), f"{DOMAIN} local emissions history"
        )

    async def _async_backfill_local(self, now: datetime) -> None:
        assert self.singularity is not None and self.local_history is not None
        agg_id = self.location.agg_id(self.local_level)
        end = now
        start = now - timedelta(days=self.index_days + 1)
        added = 0
        try:
            while end > start:
                chunk_start = max(start, end - BACKFILL_CHUNK)
                text = await self.singularity.consumed_text(self.local_level, agg_id, end, chunk_start)
                added += await self.local_history.async_add(parse_consumed_events(text), now)
                end = chunk_start
        except SingularityError as err:
            self.backfill_error = str(err)
            LOGGER.warning("Could not back-fill local emissions history (retrying in an hour): %s", err)
        else:
            self.backfill_error = None
            self._backfilled = True
            LOGGER.debug("Back-filled %s hours of local emissions history", added)
        if added:
            self._rederive()

    async def _async_update_singularity(self, now: datetime, errors: dict[str, str]) -> None:
        """Consumed emissions once an hour and marginal emissions every poll, from the Emissions API.

        Failures never fail the whole update: the affected sensors keep their last value while it's
        recent. A rejected key (HTTP 401) raises a Repairs issue until a call succeeds again.
        """
        client, history = self.singularity, self.local_history
        if client is None or history is None:
            return
        try:
            if self._local_fetched is None or now - self._local_fetched >= LOCAL_REFRESH:
                text = await client.consumed_text(self.local_level, self.location.agg_id(self.local_level), now)
                self.raw["consumed"] = text
                events = parse_consumed_events(text)
                if events:
                    self._local = events[-1]
                    await history.async_add(events, now)
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

    # --- poll -----------------------------------------------------------------------------

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

        await self._async_update_singularity(now, errors)
        # Start history downloads before computing this poll's result: background tasks start
        # eagerly, so a download that completes without waiting must be in the window used here,
        # or this poll's result would overwrite the index it just produced.
        self._maybe_fetch_history(now)
        self._maybe_backfill_local(now)
        data = derive(fuel_mix, self._load, dict(self.config_entry.options), self._window(now), self._local)
        data.errors = errors
        data.marginal = self._marginal
        return data
