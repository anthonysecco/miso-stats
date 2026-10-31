"""Constants for MISO Stats."""

from __future__ import annotations

from datetime import timedelta, timezone
import logging

DOMAIN = "miso_stats"
LOGGER = logging.getLogger(__package__)

API_BASE = "https://public-api.misoenergy.org/api"
FUEL_MIX_URL = f"{API_BASE}/FuelMix"
FUEL_MIX_YESTERDAY_URL = f"{API_BASE}/FuelMix/Yesterday"
LOAD_URL = f"{API_BASE}/RealTimeTotalLoad"
DISPLAYS_URL = "https://www.misoenergy.org/markets-and-operations/real-time--market-data/markets-displays/"

# MISO's Emissions API, hosted by Singularity Energy (free account and API key; 5 req/s, 100k req/month).
SINGULARITY_API = "https://api.miso.singularity.energy/v1"
SINGULARITY_ACCOUNT_URL = "https://miso.singularity.energy/account"

ATTRIBUTION = "Data from MISO's public real-time data APIs (unofficial, not affiliated with MISO)"
# MISO runs on Eastern Standard Time all year (no daylight saving): "10:30 EST" is 15:30 UTC.
MISO_TZ = timezone(timedelta(hours=-5), "EST")
REQUEST_TIMEOUT = 30  # seconds
# A demand feed that fails keeps its last values this long before going unavailable.
STALE_AFTER = timedelta(minutes=30)

# Fuel categories as MISO's FuelMix feed names them, mapped to entity keys.
FUEL_KEYS = {
    "Coal": "coal",
    "Natural Gas": "natural_gas",
    "Nuclear": "nuclear",
    "Wind": "wind",
    "Solar": "solar",
    "Battery Storage": "battery_storage",
    "Other": "other",
    "Imports": "imports",
}
# Fuels behind the CO2 estimate and the shares. Other (mixed hydro, oil, biomass...),
# Imports (unknown origin) and Battery Storage (re-dispatched energy) are left out.
INTENSITY_FUELS = ("coal", "natural_gas", "nuclear", "wind", "solar")
CARBON_FREE_FUELS = ("nuclear", "wind", "solar")
RENEWABLE_FUELS = ("wind", "solar")

# Options
CONF_SCAN_INTERVAL = "scan_interval"  # minutes
CONF_COAL_FACTOR = "coal_emission_factor"  # g CO2/kWh
CONF_GAS_FACTOR = "gas_emission_factor"  # g CO2/kWh
CONF_LOW_CARBON_INDEX = "low_carbon_index_threshold"  # carbon intensity index, 0-100
CONF_INDEX_DAYS = "index_days"  # days of history behind the carbon intensity index
# Optional Singularity (MISO Emissions API) settings
CONF_API_KEY = "api_key"
CONF_LOCAL_LEVEL = "local_level"  # area level for consumed emissions
CONF_CP_NODE = "cp_node"  # commercial pricing node for marginal emissions (optional)
CONF_LOCATION = "location"  # area IDs looked up from the home location at setup

# Consumed-emissions area levels, from the most local; "state" is the default.
LOCAL_LEVELS = ["county", "lba", "lrz", "state", "subregion", "miso"]
DEFAULT_LOCAL_LEVEL = "state"
LOCAL_REFRESH = timedelta(hours=1)  # consumed data is hourly, published about 90 minutes late
LOCAL_STALE_AFTER = timedelta(hours=4)
MARGINAL_STALE_AFTER = timedelta(minutes=30)

DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 60
DEFAULT_COAL_FACTOR = 1000
DEFAULT_GAS_FACTOR = 400
MAX_EMISSION_FACTOR = 2000
DEFAULT_LOW_CARBON_INDEX = 25
DEFAULT_INDEX_DAYS = 14
MIN_INDEX_DAYS = 7
MAX_INDEX_DAYS = 30
INDEX_MIN_HISTORY_DAYS = 2  # the index stays unavailable until this many days are stored
HISTORY_READY_MINUTE = 20  # FuelMix/Yesterday rolls over at midnight EST; ask from 00:20 EST
HISTORY_RETRY_HOURS = 1  # wait this long before asking again after a failed or stale download
HISTORY_MIN_HOURS = 20  # a stored day needs at least this many complete hours

DEFAULT_OPTIONS = {
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    CONF_COAL_FACTOR: DEFAULT_COAL_FACTOR,
    CONF_GAS_FACTOR: DEFAULT_GAS_FACTOR,
    CONF_LOW_CARBON_INDEX: DEFAULT_LOW_CARBON_INDEX,
    CONF_INDEX_DAYS: DEFAULT_INDEX_DAYS,
}


def scan_interval(minutes: int) -> timedelta:
    """Clamp the configured interval to the supported range."""
    return timedelta(minutes=max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, int(minutes))))
