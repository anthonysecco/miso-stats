"""Fetch MISO's real feeds and run them through the integration's parsers.

Catches upstream format changes. Run from the repo root: python scripts/check_live.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.miso_stats.api import (
    DayMix,
    MisoClient,
    MisoError,
    co2_intensity,
    parse_fuel_mix,
    parse_lmp,
    parse_load,
)
from custom_components.miso_stats.const import HUBS, INTENSITY_FUELS, MISO_TZ
from custom_components.miso_stats.coordinator import derive

MAX_AGE = timedelta(minutes=30)


async def main() -> int:
    async with aiohttp.ClientSession() as session:
        client = MisoClient(session)
        fuel_text, load_text, lmp_text = await client.fetch()
        try:
            past: DayMix | MisoError = await client.history_day()
        except MisoError as err:
            past = err
    problems: list[str] = []
    now = datetime.now(MISO_TZ)

    mix = parse_fuel_mix(fuel_text)
    missing = [f for f in (*INTENSITY_FUELS, "imports") if f not in mix.mw]
    if missing:
        problems.append(f"FuelMix missing categories: {', '.join(missing)}")
    if mix.updated is None:
        problems.append("FuelMix RefId did not parse")
    elif now - mix.updated > MAX_AGE:
        problems.append(f"FuelMix is stale: {mix.updated:%H:%M} EST")

    load = None
    if isinstance(load_text, MisoError):
        problems.append(f"RealTimeTotalLoad: {load_text}")
    else:
        try:
            load = parse_load(load_text)
        except MisoError as err:
            problems.append(f"RealTimeTotalLoad: {err}")
        else:
            if len(load.forecast) < 24:
                problems.append(f"RealTimeTotalLoad has only {len(load.forecast)} forecast hours")
            if load.demand is None:
                problems.append("RealTimeTotalLoad has no 5-minute demand")

    prices = None
    if isinstance(lmp_text, MisoError):
        problems.append(f"GetExAnteLmp: {lmp_text}")
    else:
        try:
            prices = parse_lmp(lmp_text)
        except MisoError as err:
            problems.append(f"GetExAnteLmp: {err}")
        else:
            gone = [name for name in HUBS.values() if name not in prices.hubs]
            if gone:
                problems.append(f"GetExAnteLmp missing hubs: {', '.join(gone)}")

    data = derive(mix, load, prices, {})
    demand = load.demand[1] if load and load.demand else None
    print(
        f"{mix.updated:%Y-%m-%d %H:%M} EST demand={demand} MW generation={mix.generation:.0f} MW "
        f"co2={data.co2_intensity} g/kWh renewables={data.renewables}% carbon_free={data.carbon_free}% "
        f"{data.hub}={data.hub_price.lmp if data.hub_price else None} $/MWh"
    )
    if isinstance(past, MisoError):
        problems.append(f"FuelMix/Yesterday: {past}")
    else:
        hourly = {h: co2_intensity(m, 1000, 400) for h, m in past.hours.items()}
        low, high = min(hourly, key=hourly.get), max(hourly, key=hourly.get)
        print(
            f"yesterday {past.day}: cleanest {hourly[low]:.1f} g/kWh at {low}:00 EST, "
            f"dirtiest {hourly[high]:.1f} g/kWh at {high}:00 ({len(past.hours)} hours)"
        )
        if past.day < now.date() - timedelta(days=2):
            problems.append(f"FuelMix/Yesterday served {past.day}, which is more than a day old")
    for p in problems:
        print("PROBLEM:", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
