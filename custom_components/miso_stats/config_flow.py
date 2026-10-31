"""Config and options flow for MISO Stats."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .api import MisoClient, MisoError, parse_fuel_mix
from .const import (
    CONF_COAL_FACTOR,
    CONF_GAS_FACTOR,
    CONF_HUB,
    CONF_INDEX_DAYS,
    CONF_LOW_CARBON_INDEX,
    CONF_SCAN_INTERVAL,
    DEFAULT_OPTIONS,
    DOMAIN,
    HUBS,
    LOGGER,
    MAX_EMISSION_FACTOR,
    MAX_INDEX_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_INDEX_DAYS,
    MIN_SCAN_INTERVAL,
)
from .coordinator import MisoConfigEntry


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


HUB_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=list(HUBS), mode=SelectSelectorMode.DROPDOWN, translation_key=CONF_HUB)
)

OPTION_FIELDS = {
    CONF_HUB: HUB_SELECTOR,
    CONF_SCAN_INTERVAL: _number(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, 1, "min"),
    CONF_COAL_FACTOR: _number(0, MAX_EMISSION_FACTOR, 1, "g/kWh"),
    CONF_GAS_FACTOR: _number(0, MAX_EMISSION_FACTOR, 1, "g/kWh"),
    CONF_LOW_CARBON_INDEX: _number(0, 100, 1, "%"),
    CONF_INDEX_DAYS: _number(MIN_INDEX_DAYS, MAX_INDEX_DAYS, 1, "d"),
}


def options_schema(current: dict[str, Any]) -> vol.Schema:
    """Options form, pre-filled with the current values."""
    values = {**DEFAULT_OPTIONS, **current}
    return vol.Schema({vol.Required(key, default=values[key]): field for key, field in OPTION_FIELDS.items()})


class MisoStatsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add MISO Stats (one per installation; no credentials needed)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the price hub, then check that MISO answers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = MisoClient(async_get_clientsession(self.hass))
            try:
                parse_fuel_mix(await client.fuel_mix_text())
            except MisoError as err:
                LOGGER.warning("MISO FuelMix check failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                options = {**DEFAULT_OPTIONS, CONF_HUB: user_input.get(CONF_HUB, DEFAULT_OPTIONS[CONF_HUB])}
                return self.async_create_entry(title="MISO", data={}, options=options)
        schema = vol.Schema({vol.Required(CONF_HUB, default=DEFAULT_OPTIONS[CONF_HUB]): HUB_SELECTOR})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: MisoConfigEntry) -> MisoStatsOptionsFlow:
        """Hub, polling interval, emission factors and carbon index settings."""
        return MisoStatsOptionsFlow()


class MisoStatsOptionsFlow(OptionsFlowWithReload):
    """Edit the options; the entry reloads on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show or save the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=options_schema(dict(self.config_entry.options)))
