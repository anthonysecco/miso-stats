"""Diagnostics for MISO Stats (public data only; nothing to redact)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import MisoConfigEntry


def _json_or_text(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: MisoConfigEntry) -> dict[str, Any]:
    """Options, derived values, the index history and the last raw payloads."""
    coordinator = entry.runtime_data
    data = coordinator.data
    history = coordinator.history.days
    return {
        "options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "derived": None
        if data is None
        else {
            "fuel_mix_mw": data.fuel_mix.mw,
            "co2_intensity": data.co2_intensity,
            "co2_emissions": data.co2_emissions,
            "renewables": data.renewables,
            "carbon_free": data.carbon_free,
            "co2_intensity_index": data.co2_index,
            "index_window": None
            if data.index_range is None
            else {
                "min": data.index_range.min_g,
                "min_hour": data.index_range.min_at.isoformat(),
                "max": data.index_range.max_g,
                "max_hour": data.index_range.max_at.isoformat(),
                "days": data.index_range.days,
            },
            "low_carbon_now": data.low_carbon_now,
            "hub": data.hub,
            "feed_errors": data.errors,
        },
        "history": {
            "days": sorted(d.isoformat() for d in history),
            "hours_per_day": {d.isoformat(): len(h) for d, h in sorted(history.items())},
            "last_error": coordinator.history_error,
        },
        "raw": {name: _json_or_text(text) for name, text in coordinator.raw.items()},
    }
