"""Fuel mix history behind the 0-100 carbon intensity index.

Index = (current intensity - cleanest hour) / (dirtiest hour - cleanest hour) x 100, clamped to 0-100,
where the cleanest and dirtiest hours are hourly-average intensities over the last N complete EST days
(default 14).

MISO's keyless API only serves yesterday's 5-minute fuel mix (FuelMix/Yesterday), so the window is
built up one day at a time: yesterday on install, then each new day just after midnight EST. Each day's
hourly-average output of coal, gas, nuclear, wind and solar is kept in HA storage (not intensities),
so changing the emission factors re-scores the whole window and restarts download nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import DayMix, co2_intensity
from .const import (
    DOMAIN,
    HISTORY_MIN_HOURS,
    HISTORY_READY_MINUTE,
    INDEX_MIN_HISTORY_DAYS,
    INTENSITY_FUELS,
    MAX_INDEX_DAYS,
    MISO_TZ,
)
from .singularity import LocalEmissions

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.fuel_mix_history"
LOCAL_STORAGE_KEY = f"{DOMAIN}.local_history"


@dataclass(slots=True, frozen=True)
class IndexRange:
    """Cleanest and dirtiest hour in the index window."""

    min_g: float
    min_at: datetime
    max_g: float
    max_at: datetime
    days: int


def co2_index(current: float | None, window: IndexRange | None) -> int | None:
    """Where `current` sits between the window's cleanest (0) and dirtiest (100) hour."""
    if current is None or window is None or window.max_g <= window.min_g:
        return None
    ratio = (current - window.min_g) / (window.max_g - window.min_g)
    return round(max(0.0, min(1.0, ratio)) * 100)


def yesterday_due(now: datetime) -> date:
    """The day FuelMix/Yesterday serves now: yesterday (EST), or the day before until it rolls over."""
    local = now.astimezone(MISO_TZ)
    ready = local.replace(hour=0, minute=HISTORY_READY_MINUTE, second=0, microsecond=0)
    return local.date() - timedelta(days=1 if local >= ready else 2)


class MixHistory:
    """Each past day's hourly fuel mix, persisted across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind to HA storage (call async_load before use)."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.days: dict[date, dict[int, dict[str, float]]] = {}

    async def async_load(self) -> None:
        """Read stored days; a missing or damaged file just means starting over."""
        data = await self._store.async_load() or {}
        for key, hours in (data.get("days") or {}).items():
            try:
                parsed = {
                    int(h): {f: float(mix[f]) for f in INTENSITY_FUELS} for h, mix in hours.items()
                }
                self.days[date.fromisoformat(key)] = parsed
            except (AttributeError, KeyError, TypeError, ValueError):
                continue

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "days": {
                    d.isoformat(): {str(h): mix for h, mix in sorted(hours.items())}
                    for d, hours in sorted(self.days.items())
                }
            }
        )

    def needs(self, now: datetime) -> bool:
        """Whether the day FuelMix/Yesterday currently serves is still missing."""
        return yesterday_due(now) not in self.days

    async def async_add(self, day: DayMix) -> None:
        """Store a downloaded day and drop days too old for any window."""
        self.days[day.day] = day.hours
        oldest = max(self.days) - timedelta(days=MAX_INDEX_DAYS + 1)
        for stale in [d for d in self.days if d < oldest]:
            del self.days[stale]
        await self._async_save()

    def window(self, now: datetime, window_days: int, coal_g: float, gas_g: float) -> IndexRange | None:
        """Cleanest and dirtiest hour over the last `window_days` complete days (None until enough days)."""
        today = now.astimezone(MISO_TZ).date()
        first = today - timedelta(days=window_days)
        days = {d: v for d, v in self.days.items() if first <= d < today}
        if len(days) < INDEX_MIN_HISTORY_DAYS:
            return None
        hours = [
            (intensity, datetime.combine(d, time(h), MISO_TZ))
            for d, mix_by_hour in days.items()
            for h, mix in mix_by_hour.items()
            if (intensity := co2_intensity(mix, coal_g, gas_g)) is not None
        ]
        if not hours:
            return None
        low = min(hours, key=lambda x: x[0])
        high = max(hours, key=lambda x: x[0])
        return IndexRange(
            min_g=round(low[0], 1), min_at=low[1], max_g=round(high[0], 1), max_at=high[1], days=len(days)
        )


class LocalHistory:
    """Hourly consumed CO2 intensity for one area (Emissions API), behind the index when a key is set.

    Back-filled from the API on first use, then extended by every hourly fetch. Switching area
    starts over. Days are EST days, like the MISO history.
    """

    def __init__(self, hass: HomeAssistant, area: str) -> None:
        """Bind to HA storage for `area` (e.g. "state:Michigan"); call async_load before use."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, LOCAL_STORAGE_KEY)
        self.area = area
        self.hours: dict[datetime, float] = {}  # UTC hour start -> g CO2e/kWh

    async def async_load(self) -> None:
        """Read stored hours for this area; anything else (or damage) means a fresh back-fill."""
        data = await self._store.async_load() or {}
        if data.get("area") != self.area:
            return
        for key, value in (data.get("hours") or {}).items():
            try:
                self.hours[datetime.fromisoformat(key)] = float(value)
            except (TypeError, ValueError):
                continue

    async def async_add(self, events: list[LocalEmissions], now: datetime) -> int:
        """Store hours not seen before (or revised); drop hours too old for any window. Returns how many changed."""
        changed = 0
        for event in events:
            if self.hours.get(event.timestamp) != event.rate:
                self.hours[event.timestamp] = event.rate
                changed += 1
        oldest = now - timedelta(days=MAX_INDEX_DAYS + 2)
        for stale in [t for t in self.hours if t < oldest]:
            del self.hours[stale]
        if changed:
            await self._store.async_save(
                {"area": self.area, "hours": {t.isoformat(): v for t, v in sorted(self.hours.items())}}
            )
        return changed

    def needs_backfill(self, now: datetime, window_days: int) -> bool:
        """Whether the window's first day is missing (stored hours start later, or nothing is stored)."""
        first_day = datetime.combine(now.astimezone(MISO_TZ).date() - timedelta(days=window_days), time(), MISO_TZ)
        return not self.hours or min(self.hours) > first_day + timedelta(hours=1)

    def window(self, now: datetime, window_days: int) -> IndexRange | None:
        """Cleanest and dirtiest hour over the last `window_days` complete EST days (None until enough days)."""
        today = now.astimezone(MISO_TZ).date()
        first = today - timedelta(days=window_days)
        by_day: dict[date, list[tuple[float, datetime]]] = {}
        for stamp, rate in self.hours.items():
            day = stamp.astimezone(MISO_TZ).date()
            if first <= day < today:
                by_day.setdefault(day, []).append((rate, stamp))
        days = {d: hours for d, hours in by_day.items() if len(hours) >= HISTORY_MIN_HOURS}
        if len(days) < INDEX_MIN_HISTORY_DAYS:
            return None
        hours = [h for day_hours in days.values() for h in day_hours]
        low = min(hours, key=lambda x: x[0])
        high = max(hours, key=lambda x: x[0])
        return IndexRange(
            min_g=round(low[0], 1),
            min_at=low[1].astimezone(MISO_TZ),
            max_g=round(high[0], 1),
            max_at=high[1].astimezone(MISO_TZ),
            days=len(days),
        )
