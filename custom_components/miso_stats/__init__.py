"""MISO Stats: Midcontinent grid conditions from MISO's public real-time data APIs."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LOGGER
from .coordinator import MisoConfigEntry, MisoCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Entities pre-release builds created that this version no longer provides.
RETIRED_ENTITIES: tuple[tuple[Platform, str], ...] = ((Platform.SENSOR, "hub_price"),)


def _remove_retired_entities(hass: HomeAssistant, entry: MisoConfigEntry) -> None:
    registry = er.async_get(hass)
    for platform, key in RETIRED_ENTITIES:
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}"):
            LOGGER.info("Removing retired MISO Stats entity %s", entity_id)
            registry.async_remove(entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: MisoConfigEntry) -> bool:
    """Set up MISO Stats from a config entry."""
    coordinator = MisoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _remove_retired_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MisoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
