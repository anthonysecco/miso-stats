"""Polling coordinator, derived values and the carbon intensity index."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FuelMix,
    HubPrice,
    Load,
    MisoClient,
    MisoError,
    Prices,
    co2_intensity,
    co2_rate,
    parse_fuel_mix,
    parse_lmp,
    parse_load,
    share,
)
from .const import (
    CARBON_FREE_FUELS,
    CONF_COAL_FACTOR,
    CONF_GAS_FACTOR,
    CONF_HUB,
    CONF_INDEX_DAYS,
    CONF_LOW_CARBON_INDEX,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIONS,
    DOMAIN,
    HISTORY_RETRY_HOURS,
    HUBS,
    LOGGER,
    MAX_INDEX_DAYS,
    MIN_INDEX_DAYS,
    RENEWABLE_FUELS,
    STALE_AFTER,
    scan_interval,
)
from .history import IndexRange, MixHistory, co2_index

type MisoConfigEntry = ConfigEntry[MisoCoordinator]


@dataclass(slots=True)
class MisoData:
    """Everything the entities read, refreshed together."""

    fuel_mix: FuelMix
    load: Load | None
    prices: Prices | None
    hub: str  # MISO's node name, e.g. "INDIANA.HUB"
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

    @property
    def hub_price(self) -> HubPrice | None:
        """Price at the configured hub."""
        return self.prices.hubs.get(self.hub) if self.prices else None


def options_with_defaults(options: dict[str, Any]) -> dict[str, Any]:
    """Entry options over the defaults."""
    return {**DEFAULT_OPTIONS, **options}


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def derive(
    fuel_mix: FuelMix,
    load: Load | None,
    prices: Prices | None,
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
        prices=prices,
        hub=HUBS.get(opts[CONF_HUB], HUBS[DEFAULT_OPTIONS[CONF_HUB]]),
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


def _fresh[T: (Load, Prices)](previous: T | None, now: datetime) -> T | None:
    """Keep a feed's last good copy while it's recent; drop it once stale."""
    if previous is None or previous.updated is None or now - previous.updated > STALE_AFTER:
        return None
    return previous


class MisoCoordinator(DataUpdateCoordinator[MisoData]):
    """Fetches FuelMix, RealTimeTotalLoad and the hub prices every poll; yesterday's mix once a day."""

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
        self.raw: dict[str, str | None] = {"fuel_mix": None, "load": None, "lmp": None}
        self._load: Load | None = None
        self._prices: Prices | None = None
        self.history = MixHistory(hass)
        self._history_task: asyncio.Task[None] | None = None
        self._history_tried: datetime | None = None
        self.history_error: str | None = None

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
                self.data.prices,
                dict(self.config_entry.options),
                self._window(dt_util.now()),
            )
            data.errors = self.data.errors
            self.data = data
            self.async_update_listeners()

    async def _async_update_data(self) -> MisoData:
        now = dt_util.now()
        try:
            fuel_text, load_text, lmp_text = await self.client.fetch()
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
            self._load = _fresh(self._load, now)
        try:
            if isinstance(lmp_text, MisoError):
                raise lmp_text
            self.raw["lmp"] = lmp_text
            self._prices = parse_lmp(lmp_text)
        except MisoError as err:
            errors["lmp"] = str(err)
            self._prices = _fresh(self._prices, now)
        if errors:
            LOGGER.debug("Some MISO feeds failed this poll: %s", errors)

        data = derive(fuel_mix, self._load, self._prices, dict(self.config_entry.options), self._window(now))
        data.errors = errors
        self._maybe_fetch_history(now)
        return data
