"""Sensors for MISO Stats."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import FUEL_KEYS
from .coordinator import SOURCE_MISO, SOURCE_SINGULARITY, MisoConfigEntry, MisoCoordinator, MisoData
from .entity import MisoEntity

PARALLEL_UPDATES = 0

UNIT_CO2_RATE = "t/h"
UNIT_CO2_INTENSITY = "g/kWh"

StateType = str | float | datetime | None


@dataclass(frozen=True, kw_only=True)
class MisoSensorDescription(SensorEntityDescription):
    """Describes one MISO sensor."""

    value_fn: Callable[[MisoData], StateType]
    attrs_fn: Callable[[MisoData], dict[str, Any]] | None = None
    available_fn: Callable[[MisoData], bool] = lambda _: True
    exists_fn: Callable[[MisoCoordinator], bool] = lambda _: True  # whether to create the entity at all


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


def _has_load(d: MisoData) -> bool:
    return d.load is not None


def _mw(key: str, value_fn: Callable[[MisoData], float | None], **kwargs: Any) -> MisoSensorDescription:
    """A MW power sensor."""
    return MisoSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=UnitOfPower.MEGA_WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=value_fn,
        **kwargs,
    )


def _fuel(key: str, **kwargs: Any) -> MisoSensorDescription:
    """Output of one fuel category, off by default."""
    return _mw(
        key,
        lambda d: d.fuel_mix.mw.get(key),
        available_fn=lambda d: key in d.fuel_mix.mw,
        entity_registry_enabled_default=False,
        **kwargs,
    )


def _pct(key: str, value_fn: Callable[[MisoData], float | None], **kwargs: Any) -> MisoSensorDescription:
    return MisoSensorDescription(
        key=key,
        translation_key=key,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=value_fn,
        **kwargs,
    )


def _battery_attrs(d: MisoData) -> dict[str, Any]:
    mw = d.fuel_mix.mw.get("battery_storage")
    return {"mode": None if mw is None else "charging" if mw < 0 else "discharging" if mw > 0 else "idle"}


def _generation_attrs(d: MisoData) -> dict[str, Any]:
    return {"total_with_imports_mw": d.fuel_mix.total_mw, "imports_mw": d.fuel_mix.mw.get("imports")}


def _demand(d: MisoData) -> float | None:
    point = d.load.demand if d.load else None
    return point[1] if point else None


def _demand_attrs(d: MisoData) -> dict[str, Any]:
    point = d.load.demand if d.load else None
    return {"as_of": _iso(point[0]) if point else None}


def _peak(d: MisoData) -> float | None:
    point = d.load.peak if d.load else None
    return point[1] if point else None


def _peak_attrs(d: MisoData) -> dict[str, Any]:
    point = d.load.peak if d.load else None
    return {"peak_time": _iso(point[0]) if point else None}


def _forecast_peak(d: MisoData) -> float | None:
    point = d.load.forecast_peak if d.load else None
    return point[1] if point else None


def _forecast_peak_attrs(d: MisoData) -> dict[str, Any]:
    point = d.load.forecast_peak if d.load else None
    return {"peak_hour_start": _iso(point[0]) if point else None}


def _forecast_now(d: MisoData) -> float | None:
    """Load forecast for the hour the latest demand reading falls in."""
    if d.load is None:
        return None
    point = d.load.demand
    he = point[0].hour + 1 if point else (d.load.updated.hour + 1)
    return d.load.forecast.get(he)


def _forecast_attrs(d: MisoData) -> dict[str, Any]:
    """Hourly forecast, day-ahead cleared load and actuals for charts; not recorded."""
    if d.load is None:
        return {}
    actual = d.load.hourly_actual()
    return {
        "forecast": [
            {
                "time": d.load.hour_start(he).isoformat(),
                "forecast_mw": d.load.forecast.get(he),
                "day_ahead_mw": d.load.cleared.get(he),
                "actual_mw": actual.get(he),
            }
            for he in range(1, 25)
        ]
    }


def _estimate_attrs(d: MisoData) -> dict[str, Any]:
    """The MISO-wide estimate's inputs."""
    return {"source": SOURCE_MISO, "coal_factor": d.coal_factor, "gas_factor": d.gas_factor, "estimated": True}


def _local_area(d: MisoData) -> dict[str, Any]:
    local = d.local
    if local is None:
        return {"source": SOURCE_SINGULARITY}
    return {
        "source": SOURCE_SINGULARITY,
        "area_level": local.agg_type,
        "area": local.agg_id,
        "hour": local.timestamp.isoformat(),
    }


def _intensity_attrs(d: MisoData) -> dict[str, Any]:
    if d.source != SOURCE_SINGULARITY:
        return _estimate_attrs(d)
    attrs = _local_area(d)
    if d.local and d.local.non_cfe_rate is not None:
        attrs["fossil_rate"] = round(d.local.non_cfe_rate, 1)
    return attrs


def _share_attrs(miso_wide: Callable[[MisoData], float | None]) -> Callable[[MisoData], dict[str, Any]]:
    def attrs(d: MisoData) -> dict[str, Any]:
        if d.source != SOURCE_SINGULARITY:
            return {"source": SOURCE_MISO}
        result = _local_area(d)
        if d.local is not None:
            result["fuel_mix"] = {k: v for k, v in d.local.fuel_mix.items() if k != "non_cfe"}
        result["miso_wide"] = miso_wide(d)
        return result

    return attrs


def _index_attrs(d: MisoData) -> dict[str, Any]:
    r = d.index_range
    if r is None:
        return {"source": d.source}
    return {
        "source": d.source,
        "co2_intensity": d.co2_intensity,
        "window_min": r.min_g,
        "window_min_hour": r.min_at.isoformat(),
        "window_max": r.max_g,
        "window_max_hour": r.max_at.isoformat(),
        "window_days": r.days,
    }


SENSORS: tuple[MisoSensorDescription, ...] = (
    # --- emissions and shares (enabled) ---
    MisoSensorDescription(
        key="co2_intensity",
        translation_key="co2_intensity",
        native_unit_of_measurement=UNIT_CO2_INTENSITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.co2_intensity,
        attrs_fn=_intensity_attrs,
        available_fn=lambda d: d.co2_intensity is not None,
    ),
    MisoSensorDescription(
        key="co2_intensity_index",
        translation_key="co2_intensity_index",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.co2_index,
        attrs_fn=_index_attrs,
        available_fn=lambda d: d.index_range is not None,
    ),
    _pct(
        "renewables",
        lambda d: d.renewables,
        attrs_fn=_share_attrs(lambda d: d.miso_renewables),
        available_fn=lambda d: d.renewables is not None,
    ),
    _pct(
        "carbon_free",
        lambda d: d.carbon_free,
        attrs_fn=_share_attrs(lambda d: d.miso_carbon_free),
        available_fn=lambda d: d.carbon_free is not None,
    ),
    # With a key the headline intensity is local; the MISO-wide estimate stays here for comparison.
    MisoSensorDescription(
        key="co2_intensity_estimate",
        translation_key="co2_intensity_estimate",
        native_unit_of_measurement=UNIT_CO2_INTENSITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.estimate,
        attrs_fn=_estimate_attrs,
        exists_fn=lambda c: c.singularity is not None,
    ),
    MisoSensorDescription(
        key="co2_emissions",
        translation_key="co2_emissions",
        native_unit_of_measurement=UNIT_CO2_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.co2_emissions,
        attrs_fn=_estimate_attrs,
        entity_registry_enabled_default=False,
    ),
    # --- demand (RealTimeTotalLoad) ---
    _mw("demand", _demand, attrs_fn=_demand_attrs, available_fn=_has_load),
    _mw(
        "todays_peak_demand",
        _peak,
        attrs_fn=_peak_attrs,
        available_fn=_has_load,
        entity_registry_enabled_default=False,
    ),
    _mw(
        "forecast_peak_demand_today",
        _forecast_peak,
        attrs_fn=_forecast_peak_attrs,
        available_fn=_has_load,
        entity_registry_enabled_default=False,
    ),
    _mw(
        "load_forecast",
        _forecast_now,
        attrs_fn=_forecast_attrs,
        available_fn=_has_load,
        entity_registry_enabled_default=False,
    ),
    # --- supply mix (FuelMix), off by default ---
    _mw(
        "generation",
        lambda d: d.fuel_mix.generation,
        attrs_fn=_generation_attrs,
        entity_registry_enabled_default=False,
    ),
    *(_fuel(key) for key in FUEL_KEYS.values() if key != "battery_storage"),
    _fuel("battery_storage", attrs_fn=_battery_attrs),
    # --- diagnostics ---
    MisoSensorDescription(
        key="data_time",
        translation_key="data_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.fuel_mix.updated,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MisoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        MisoSensor(coordinator, description) for description in SENSORS if description.exists_fn(coordinator)
    )


class MisoSensor(MisoEntity, SensorEntity):
    """A MISO value."""

    entity_description: MisoSensorDescription
    _unrecorded_attributes = frozenset({"forecast", "fuel_mix"})

    @property
    def available(self) -> bool:
        """Unavailable when the feed it depends on is."""
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra context for automations and cards."""
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
