"""Diagnostics for MISO Stats (public data; the Emissions API key is redacted)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .coordinator import MisoConfigEntry

TO_REDACT = {CONF_API_KEY}


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
        "options": async_redact_data(dict(entry.options), TO_REDACT),
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
            "feed_errors": data.errors,
        },
        "history": {
            "days": sorted(d.isoformat() for d in history),
            "hours_per_day": {d.isoformat(): len(h) for d, h in sorted(history.items())},
            "last_error": coordinator.history_error,
        },
        "emissions_api": {
            "configured": coordinator.singularity is not None,
            "carbon_source": None if data is None else data.source,
            "miso_wide_estimate": None if data is None else data.estimate,
            "local_level": coordinator.local_level,
            "location": coordinator.location.as_dict(),
            "cp_node": coordinator.cp_node,
            "local_history": None
            if coordinator.local_history is None
            else {
                "area": coordinator.local_history.area,
                "hours": len(coordinator.local_history.hours),
                "first": min(coordinator.local_history.hours).isoformat() if coordinator.local_history.hours else None,
                "backfill_error": coordinator.backfill_error,
            },
            "local": None
            if data is None or data.local is None
            else {
                "hour": data.local.timestamp.isoformat(),
                "area": f"{data.local.agg_type}: {data.local.agg_id}",
                "rate_g_per_kwh": round(data.local.rate, 1),
                "carbon_free": data.local.carbon_free,
                "renewables": data.local.renewables,
            },
            "marginal": None
            if data is None or data.marginal is None
            else {
                "interval": data.marginal.timestamp.isoformat(),
                "rate_g_per_kwh": round(data.marginal.rate, 1),
                "fuels": data.marginal.fuels,
            },
        },
        "raw": {name: _json_or_text(text) for name, text in coordinator.raw.items()},
    }
