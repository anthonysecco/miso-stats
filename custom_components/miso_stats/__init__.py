"""MISO Stats: Midcontinent grid conditions from MISO's public real-time data APIs."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import DOMAIN, LOGGER
from .coordinator import ISSUE_INVALID_KEY, MisoConfigEntry, MisoCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Entities pre-release builds created that this version no longer provides.
RETIRED_ENTITIES: tuple[tuple[Platform, str], ...] = (
    (Platform.SENSOR, "hub_price"),
    (Platform.SENSOR, "local_co2_intensity"),  # merged into CO2 intensity when a key is set
    (Platform.SENSOR, "local_carbon_free"),  # merged into Carbon-free when a key is set
)
# Entities that exist only with an Emissions API key (marginal also needs a pricing node).
LOCAL_ENTITIES = ("co2_intensity_estimate",)
MARGINAL_ENTITIES = ("marginal_co2_intensity",)


def _remove_unused_entities(hass: HomeAssistant, entry: MisoConfigEntry, coordinator: MisoCoordinator) -> None:
    """Drop retired entities, and the Emissions API ones once the key or node is removed."""
    unused = list(RETIRED_ENTITIES)
    if coordinator.singularity is None:
        unused += [(Platform.SENSOR, key) for key in LOCAL_ENTITIES]
    if coordinator.singularity is None or coordinator.cp_node is None:
        unused += [(Platform.SENSOR, key) for key in MARGINAL_ENTITIES]
    registry = er.async_get(hass)
    for platform, key in unused:
        if entity_id := registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}"):
            LOGGER.info("Removing unused MISO Stats entity %s", entity_id)
            registry.async_remove(entity_id)
    if coordinator.singularity is None:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_INVALID_KEY)


async def async_setup_entry(hass: HomeAssistant, entry: MisoConfigEntry) -> bool:
    """Set up MISO Stats from a config entry."""
    coordinator = MisoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    _remove_unused_entities(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MisoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
