"""Config and options flow for MISO Stats."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import MisoClient, MisoError, parse_fuel_mix
from .const import (
    CONF_API_KEY,
    CONF_COAL_FACTOR,
    CONF_GAS_FACTOR,
    CONF_INDEX_DAYS,
    CONF_LOCAL_LEVEL,
    CONF_LOCATION,
    CONF_LOW_CARBON_INDEX,
    CONF_SCAN_INTERVAL,
    DEFAULT_LOCAL_LEVEL,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOCAL_LEVELS,
    LOGGER,
    MAX_EMISSION_FACTOR,
    MAX_INDEX_DAYS,
    MAX_SCAN_INTERVAL,
    MIN_INDEX_DAYS,
    MIN_SCAN_INTERVAL,
    SINGULARITY_ACCOUNT_URL,
)
from .coordinator import MisoConfigEntry
from .singularity import (
    Location,
    SingularityAuthError,
    SingularityClient,
    SingularityError,
    SingularityForbiddenError,
    SingularityNoAreaError,
    parse_consumed,
)

SINGULARITY_KEYS = (CONF_API_KEY, CONF_LOCAL_LEVEL, CONF_LOCATION, "cp_node")  # "cp_node": pre-release builds


def _number(minimum: float, maximum: float, step: float, unit: str) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, unit_of_measurement=unit, mode=NumberSelectorMode.BOX
        )
    )


API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

OPTION_FIELDS = {
    CONF_SCAN_INTERVAL: _number(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, 1, "min"),
    CONF_COAL_FACTOR: _number(0, MAX_EMISSION_FACTOR, 1, "g/kWh"),
    CONF_GAS_FACTOR: _number(0, MAX_EMISSION_FACTOR, 1, "g/kWh"),
    CONF_LOW_CARBON_INDEX: _number(0, 100, 1, "%"),
    CONF_INDEX_DAYS: _number(MIN_INDEX_DAYS, MAX_INDEX_DAYS, 1, "d"),
}


def options_schema(current: dict[str, Any]) -> vol.Schema:
    """Options form, pre-filled with the current values, plus the optional API key."""
    values = {**DEFAULT_OPTIONS, **current}
    fields: dict[Any, Any] = {vol.Required(key, default=values[key]): f for key, f in OPTION_FIELDS.items()}
    key = str(current.get(CONF_API_KEY) or "")
    fields[vol.Optional(CONF_API_KEY, description={"suggested_value": key} if key else None)] = API_KEY_SELECTOR
    return vol.Schema(fields)


def _location_placeholders(location: Location) -> dict[str, str]:
    return {
        "county": location.county or "?",
        "state": location.state or "?",
        "lrz": str(location.lrz) if location.lrz is not None else "?",
        "lba": location.lba or "?",
        "lba_code": location.agg_id("lba") or "?",
        "subregion": location.subregion or "?",
        "account_url": SINGULARITY_ACCOUNT_URL,
    }


class SingularitySetup:
    """Shared by the config and options flows: check a key, find the home's areas, then pick the level."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start without a key."""
        self.hass = hass
        self.api_key: str | None = None
        self.location: Location | None = None

    def client(self) -> SingularityClient:
        """A client for the key being set up."""
        assert self.api_key
        return SingularityClient(async_get_clientsession(self.hass), self.api_key)

    async def check_key(self, api_key: str) -> str | None:
        """Validate the key with a location lookup for the home; returns an error code or None."""
        self.api_key = api_key.strip()
        try:
            self.location = await self.client().location(self.hass.config.latitude, self.hass.config.longitude)
        except (SingularityAuthError, SingularityForbiddenError) as err:
            LOGGER.warning("MISO Emissions API did not accept the key: %s", err)
            return "invalid_auth"
        except SingularityNoAreaError:
            return "outside_miso"
        except SingularityError as err:
            LOGGER.warning("MISO Emissions API check failed: %s", err)
            return "cannot_connect_singularity"
        return None

    def schema(self, current: dict[str, Any]) -> vol.Schema:
        """The local area level."""
        return vol.Schema(
            {
                vol.Required(
                    CONF_LOCAL_LEVEL, default=current.get(CONF_LOCAL_LEVEL, DEFAULT_LOCAL_LEVEL)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=LOCAL_LEVELS, mode=SelectSelectorMode.DROPDOWN, translation_key=CONF_LOCAL_LEVEL
                    )
                ),
            }
        )

    async def check_choice(self, user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Fetch once with the chosen level; returns the options to store and any errors."""
        assert self.location is not None
        level = user_input[CONF_LOCAL_LEVEL]
        agg_id = self.location.agg_id(level)
        errors: dict[str, str] = {}
        try:
            if parse_consumed(await self.client().consumed_text(level, agg_id, dt_util.utcnow())) is None:
                LOGGER.warning("The Emissions API returned no recent local emissions for %s %s", level, agg_id)
                errors[CONF_LOCAL_LEVEL] = "level_unavailable"
        except SingularityAuthError:
            errors["base"] = "invalid_auth"
        except SingularityError as err:  # incl. HTTP 403: the key works but can't query this level
            LOGGER.warning("No local emissions for %s %s: %s", level, agg_id, err)
            errors[CONF_LOCAL_LEVEL] = "level_unavailable"
        options = {
            CONF_API_KEY: self.api_key,
            CONF_LOCAL_LEVEL: level,
            CONF_LOCATION: self.location.as_dict(),
        }
        return options, errors


class MisoStatsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add MISO Stats (one per installation; an Emissions API key is optional)."""

    VERSION = 1

    def __init__(self) -> None:
        """Start a flow."""
        self._singularity: SingularitySetup | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Check that MISO answers; with an API key, go on to the emissions settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = MisoClient(async_get_clientsession(self.hass))
            try:
                parse_fuel_mix(await client.fuel_mix_text())
            except MisoError as err:
                LOGGER.warning("MISO FuelMix check failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                api_key = str(user_input.get(CONF_API_KEY) or "").strip()
                if not api_key:
                    return self.async_create_entry(title="MISO", data={}, options=dict(DEFAULT_OPTIONS))
                self._singularity = SingularitySetup(self.hass)
                if not (error := await self._singularity.check_key(api_key)):
                    return await self.async_step_singularity()
                errors["base"] = error
        schema = vol.Schema({vol.Optional(CONF_API_KEY): API_KEY_SELECTOR})
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"account_url": SINGULARITY_ACCOUNT_URL},
        )

    async def async_step_singularity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The area level for local emissions."""
        setup = self._singularity
        assert setup is not None and setup.location is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = await setup.check_choice(user_input)
            if not errors:
                return self.async_create_entry(title="MISO", data={}, options={**DEFAULT_OPTIONS, **options})
        return self.async_show_form(
            step_id="singularity",
            data_schema=setup.schema(user_input or {}),
            errors=errors,
            description_placeholders=_location_placeholders(setup.location),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: MisoConfigEntry) -> MisoStatsOptionsFlow:
        """Polling, emission factors, carbon index and the optional Emissions API."""
        return MisoStatsOptionsFlow()


class MisoStatsOptionsFlow(OptionsFlowWithReload):
    """Edit the options; the entry reloads on save."""

    def __init__(self) -> None:
        """Start a flow."""
        self._options: dict[str, Any] = {}
        self._singularity: SingularitySetup | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show or save the options; a (new) API key leads on to the emissions settings."""
        current = dict(self.config_entry.options)
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = str(user_input.pop(CONF_API_KEY, "") or "").strip()
            self._options = {k: v for k, v in user_input.items() if k not in SINGULARITY_KEYS}
            if not api_key:
                return self.async_create_entry(data=self._options)  # no key: Emissions API settings dropped
            self._singularity = SingularitySetup(self.hass)
            if not (error := await self._singularity.check_key(api_key)):
                return await self.async_step_singularity()
            errors["base"] = error
        return self.async_show_form(
            step_id="init",
            data_schema=options_schema(current),
            errors=errors,
            description_placeholders={"account_url": SINGULARITY_ACCOUNT_URL},
        )

    async def async_step_singularity(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The area level, pre-filled with the current choice."""
        setup = self._singularity
        assert setup is not None and setup.location is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = await setup.check_choice(user_input)
            if not errors:
                return self.async_create_entry(data={**self._options, **options})
        return self.async_show_form(
            step_id="singularity",
            data_schema=setup.schema(user_input or dict(self.config_entry.options)),
            errors=errors,
            description_placeholders=_location_placeholders(setup.location),
        )
