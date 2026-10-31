"""Client and parsers for MISO's public real-time data APIs.

All feeds are free JSON at public-api.misoenergy.org and need no key. They are lightly
documented and every value arrives as a string, so each field is read defensively.
- FuelMix: the latest 5-minute generation by fuel (plus imports), with MISO's total.
- FuelMix/Yesterday: every 5-minute interval of the previous day, same format; used for
  the carbon intensity index's history.
- RealTimeTotalLoad: today's 5-minute demand, the hourly day-ahead cleared load and the
  medium-term load forecast (hour ending 1-24).
- MarketPricing/GetExAnteLmp: the ex-ante real-time price at MISO's trading hubs.

Times are Eastern Standard Time all year (MISO_TZ).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
import json
import re
from typing import Any

import aiohttp

from .const import (
    FUEL_KEYS,
    FUEL_MIX_URL,
    FUEL_MIX_YESTERDAY_URL,
    HISTORY_MIN_HOURS,
    INTENSITY_FUELS,
    LMP_URL,
    LOAD_URL,
    MISO_TZ,
    REQUEST_TIMEOUT,
)

USER_AGENT = "HomeAssistant-MISO-Stats (+https://github.com/anthonysecco/miso-stats)"
HISTORY_ATTEMPTS = 3
HISTORY_RETRY_SECONDS = 2.0  # tests set this to 0
SAMPLES_PER_HOUR = 6  # an hour needs at least half of its twelve 5-minute intervals


class MisoError(Exception):
    """A feed could not be fetched or understood."""


# --- values and times --------------------------------------------------------------

# Month names parsed by hand: strptime's %b depends on the system locale.
_MONTHS = {
    m: i
    for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
_REF_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})\s*-\s*Interval\s+(\d{1,2}):(\d{2})(?::(\d{2}))?")
_STAMP_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?\s*$")
_CLOCK_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def parse_ref_id(value: Any) -> datetime | None:
    """MISO's "31-Oct-2026 - Interval 10:30 EST" reference stamp."""
    match = _REF_RE.search(str(value or ""))
    if not match:
        return None
    day, month, year, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year), _MONTHS[month.lower()], int(day), int(hour), int(minute), int(second or 0), tzinfo=MISO_TZ
        )
    except (KeyError, ValueError):
        return None


def parse_stamp(value: Any) -> datetime | None:
    """An EST timestamp: "2026-10-31 10:30:00 AM" or "2026-10-31T10:30:00"."""
    match = _STAMP_RE.match(str(value or ""))
    if not match:
        return None
    year, month, day, hour, minute, second, ampm = match.groups()
    h = int(hour)
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ampm.lower() == "pm" else 0)
    try:
        return datetime(int(year), int(month), int(day), h, int(minute), int(second or 0), tzinfo=MISO_TZ)
    except ValueError:
        return None


def _json(text: str, name: str) -> Any:
    try:
        return json.loads(text.lstrip("﻿"))
    except ValueError as err:
        raise MisoError(f"{name} is not valid JSON: {err}") from err


def _fuel_key(category: str) -> str:
    """Entity key for a MISO fuel category; categories MISO adds later get a slug."""
    return FUEL_KEYS.get(category) or re.sub(r"[^a-z0-9]+", "_", category.lower()).strip("_")


def _fuel_rows(raw: Any, name: str) -> list[dict[str, Any]]:
    fuel = raw.get("Fuel") if isinstance(raw, dict) else None
    rows = fuel.get("Type") if isinstance(fuel, dict) else None
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise MisoError(f"{name} did not contain the expected fields")
    return [r for r in rows if isinstance(r, dict)]


# --- emissions and shares ------------------------------------------------------------


def _mw(mix: Mapping[str, float], fuel: str) -> float:
    return max(0.0, mix.get(fuel) or 0.0)


def _base(mix: Mapping[str, float]) -> float:
    return sum(_mw(mix, f) for f in INTENSITY_FUELS)


def co2_intensity(mix: Mapping[str, float], coal_g: float, gas_g: float) -> float | None:
    """Estimated g CO2/kWh: coal and gas output times their factors, over coal + gas + nuclear + wind + solar.

    Other, Imports and Battery Storage are left out of both sides. None without coal and gas figures.
    """
    base = _base(mix)
    if "coal" not in mix or "natural_gas" not in mix or base <= 0:
        return None
    return (_mw(mix, "coal") * coal_g + _mw(mix, "natural_gas") * gas_g) / base


def co2_rate(mix: Mapping[str, float], coal_g: float, gas_g: float) -> float | None:
    """Estimated metric tons of CO2 per hour from coal and gas (MW x g/kWh / 1000)."""
    if "coal" not in mix or "natural_gas" not in mix:
        return None
    return (_mw(mix, "coal") * coal_g + _mw(mix, "natural_gas") * gas_g) / 1000


def share(mix: Mapping[str, float], fuels: Iterable[str]) -> float | None:
    """Percentage of coal + gas + nuclear + wind + solar output that `fuels` provide."""
    base = _base(mix)
    if base <= 0:
        return None
    return sum(_mw(mix, f) for f in fuels) / base * 100


# --- FuelMix ---------------------------------------------------------------------------


@dataclass(slots=True)
class FuelMix:
    """The latest 5-minute fuel mix."""

    raw: dict[str, Any]
    updated: datetime | None
    total_mw: float | None
    mw: dict[str, float]

    @property
    def generation(self) -> float:
        """Output of every category except imports (batteries signed)."""
        return sum(v for k, v in self.mw.items() if k != "imports")


def parse_fuel_mix(text: str) -> FuelMix:
    """Parse the FuelMix feed."""
    raw = _json(text, "FuelMix")
    mw: dict[str, float] = {}
    stamps: list[datetime] = []
    for row in _fuel_rows(raw, "FuelMix"):
        value = _number(row.get("ACT"))
        category = str(row.get("CATEGORY") or "").strip()
        if value is None or not category:
            continue
        mw[_fuel_key(category)] = value
        if stamp := parse_stamp(row.get("INTERVALEST")):
            stamps.append(stamp)
    if "coal" not in mw or "natural_gas" not in mw:
        raise MisoError("FuelMix did not contain the expected fuels")
    updated = parse_ref_id(raw.get("RefId")) or (min(stamps) if stamps else None)
    return FuelMix(raw=raw, updated=updated, total_mw=_number(raw.get("TotalMW")), mw=mw)


@dataclass(slots=True, frozen=True)
class DayMix:
    """Hourly-average output of the intensity fuels over one EST day (hour 0 = 00:00-01:00)."""

    day: date
    hours: dict[int, dict[str, float]]


def parse_fuel_mix_day(text: str) -> DayMix:
    """Hourly averages from FuelMix/Yesterday (or /Today): 5-minute rows for one EST day.

    Hours need at least half their 5-minute rows for every intensity fuel; the day needs
    HISTORY_MIN_HOURS such hours.
    """
    raw = _json(text, "FuelMix/Yesterday")
    samples: dict[date, dict[int, dict[str, list[float]]]] = {}
    for row in _fuel_rows(raw, "FuelMix/Yesterday"):
        key = _fuel_key(str(row.get("CATEGORY") or "").strip())
        value = _number(row.get("ACT"))
        stamp = parse_stamp(row.get("INTERVALEST"))
        if key not in INTENSITY_FUELS or value is None or stamp is None:
            continue
        samples.setdefault(stamp.date(), {}).setdefault(stamp.hour, {}).setdefault(key, []).append(value)
    if not samples:
        raise MisoError("FuelMix/Yesterday had no usable rows")
    day = max(samples, key=lambda d: sum(len(v) for h in samples[d].values() for v in h.values()))
    hours = {
        hour: {f: round(sum(fuels[f]) / len(fuels[f]), 1) for f in INTENSITY_FUELS}
        for hour, fuels in samples[day].items()
        if all(len(fuels.get(f, ())) >= SAMPLES_PER_HOUR for f in INTENSITY_FUELS)
    }
    if len(hours) < HISTORY_MIN_HOURS:
        raise MisoError(f"FuelMix/Yesterday had only {len(hours)} complete hours for {day}")
    return DayMix(day=day, hours=dict(sorted(hours.items())))


# --- RealTimeTotalLoad -------------------------------------------------------------------


@dataclass(slots=True)
class Load:
    """Today's demand curve and load forecast (EST day)."""

    raw: dict[str, Any]
    updated: datetime
    day: date
    five_min: list[tuple[datetime, float]] = field(default_factory=list)
    forecast: dict[int, float] = field(default_factory=dict)  # hour ending 1-24 -> MW
    cleared: dict[int, float] = field(default_factory=dict)  # hour ending 1-24 -> MW (day-ahead)

    def hour_start(self, hour_ending: int) -> datetime:
        """Start of an hour-ending slot of this day."""
        return datetime.combine(self.day, time(hour_ending - 1), MISO_TZ)

    @property
    def demand(self) -> tuple[datetime, float] | None:
        """Latest 5-minute demand."""
        return self.five_min[-1] if self.five_min else None

    @property
    def peak(self) -> tuple[datetime, float] | None:
        """Highest 5-minute demand so far today."""
        return max(self.five_min, key=lambda p: p[1]) if self.five_min else None

    @property
    def forecast_peak(self) -> tuple[datetime, float] | None:
        """Highest hourly load forecast today, with the start of that hour."""
        if not self.forecast:
            return None
        he = max(self.forecast, key=lambda h: self.forecast[h])
        return self.hour_start(he), self.forecast[he]

    def hourly_actual(self) -> dict[int, float]:
        """Average 5-minute demand per hour ending (complete hours only)."""
        buckets: dict[int, list[float]] = {}
        for when, value in self.five_min:
            buckets.setdefault(when.hour + 1, []).append(value)
        return {he: round(sum(v) / len(v)) for he, v in buckets.items() if len(v) >= SAMPLES_PER_HOUR}


def _hourly(items: Any, wrapper: str, hour_key: str, value_key: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for item in items if isinstance(items, list) else []:
        inner = item.get(wrapper) if isinstance(item, dict) else None
        if not isinstance(inner, dict):
            continue
        he, value = _number(inner.get(hour_key)), _number(inner.get(value_key))
        if he is not None and value is not None and 1 <= he <= 24:
            out[int(he)] = value
    return out


def parse_load(text: str) -> Load:
    """Parse the RealTimeTotalLoad feed."""
    raw = _json(text, "RealTimeTotalLoad")
    info = raw.get("LoadInfo") if isinstance(raw, dict) else None
    if not isinstance(info, dict):
        raise MisoError("RealTimeTotalLoad did not contain the expected fields")
    updated = parse_ref_id(info.get("RefId"))
    if updated is None:
        raise MisoError("RealTimeTotalLoad has no readable RefId")
    load = Load(raw=raw, updated=updated, day=updated.date())
    previous: time | None = None
    for item in info.get("FiveMinTotalLoad") or []:
        inner = item.get("Load") if isinstance(item, dict) else None
        if not isinstance(inner, dict):
            continue
        match = _CLOCK_RE.match(str(inner.get("Time") or ""))
        value = _number(inner.get("Value"))
        if not match or value is None:
            continue
        try:
            clock = time(int(match.group(1)), int(match.group(2)))
        except ValueError:
            continue
        if previous is not None and clock < previous:
            break  # rows run 00:00 upward; anything after a wrap isn't today's
        previous = clock
        load.five_min.append((datetime.combine(load.day, clock, MISO_TZ), value))
    load.forecast = _hourly(info.get("MediumTermLoadForecast"), "Forecast", "HourEnding", "LoadForecast")
    load.cleared = _hourly(info.get("ClearedMW"), "ClearedMWHourly", "Hour", "Value")
    if not load.five_min and not load.forecast:
        raise MisoError("RealTimeTotalLoad had no demand or forecast values")
    return load


# --- GetExAnteLmp ------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class HubPrice:
    """Ex-ante LMP at one hub, $/MWh."""

    lmp: float
    loss: float | None
    congestion: float | None


@dataclass(slots=True)
class Prices:
    """The latest ex-ante hub prices."""

    raw: dict[str, Any]
    updated: datetime | None
    hubs: dict[str, HubPrice]


def parse_lmp(text: str) -> Prices:
    """Parse the GetExAnteLmp feed."""
    raw = _json(text, "GetExAnteLmp")
    data = raw.get("LMPData") if isinstance(raw, dict) else None
    exante = data.get("ExAnteLMP") if isinstance(data, dict) else None
    rows = exante.get("Hub") if isinstance(exante, dict) else None
    if isinstance(rows, dict):
        rows = [rows]
    hubs: dict[str, HubPrice] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name, lmp = str(row.get("name") or "").strip(), _number(row.get("LMP"))
        if name and lmp is not None:
            hubs[name] = HubPrice(lmp=lmp, loss=_number(row.get("loss")), congestion=_number(row.get("congestion")))
    if not hubs:
        raise MisoError("GetExAnteLmp did not contain any hub prices")
    return Prices(raw=raw, updated=parse_ref_id(data.get("RefId")), hubs=hubs)


# --- HTTP ----------------------------------------------------------------------------------


class MisoClient:
    """Fetches MISO's feeds with one shared timeout."""

    def __init__(self, session: aiohttp.ClientSession, timeout: float = REQUEST_TIMEOUT) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _get(self, url: str) -> str:
        try:
            async with self._session.get(
                url, timeout=self._timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            ) as resp:
                resp.raise_for_status()
                return await resp.text(encoding="utf-8")
        except TimeoutError as err:
            raise MisoError(f"Timed out fetching {url}") from err
        except aiohttp.ClientError as err:
            raise MisoError(f"Error fetching {url}: {err}") from err

    async def fuel_mix_text(self) -> str:
        """Raw FuelMix."""
        return await self._get(FUEL_MIX_URL)

    async def history_day(self) -> DayMix:
        """Yesterday's hourly fuel mix from FuelMix/Yesterday (a few tries; it's ~250 KB)."""
        for attempt in range(HISTORY_ATTEMPTS):
            try:
                text = await self._get(FUEL_MIX_YESTERDAY_URL)
                break
            except MisoError:
                if attempt == HISTORY_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(HISTORY_RETRY_SECONDS * (attempt + 1))
        return parse_fuel_mix_day(text)

    async def fetch(self) -> tuple[str, str | MisoError, str | MisoError]:
        """FuelMix, RealTimeTotalLoad and GetExAnteLmp in parallel.

        A FuelMix error raises; load and price errors are returned in their place.
        """
        results = await asyncio.gather(
            self._get(FUEL_MIX_URL), self._get(LOAD_URL), self._get(LMP_URL), return_exceptions=True
        )
        fuel, load, lmp = (
            r if not isinstance(r, BaseException) or isinstance(r, MisoError) else MisoError(str(r)) for r in results
        )
        if isinstance(fuel, MisoError):
            raise fuel
        return fuel, load, lmp
