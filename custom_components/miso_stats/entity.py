"""Base entity for MISO Stats."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DISPLAYS_URL, DOMAIN
from .coordinator import MisoCoordinator


class MisoEntity(CoordinatorEntity[MisoCoordinator]):
    """One entity on the shared "MISO" service device."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: MisoCoordinator, description: EntityDescription) -> None:
        """Bind the description and device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="MISO",
            manufacturer="Midcontinent Independent System Operator",
            model="Real-time data APIs",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=DISPLAYS_URL,
        )
