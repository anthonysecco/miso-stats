"""Parser and derived-value tests (no Home Assistant instance needed)."""

from __future__ import annotations

from datetime import date, datetime
import json

import pytest

from custom_components.miso_stats.api import (
    MisoError,
    co2_intensity,
    co2_rate,
    parse_fuel_mix,
    parse_fuel_mix_day,
    parse_load,
    parse_ref_id,
    parse_stamp,
    share,
)
from custom_components.miso_stats.const import CARBON_FREE_FUELS, MISO_TZ, RENEWABLE_FUELS
from custom_components.miso_stats.coordinator import derive
from custom_components.miso_stats.history import IndexRange, co2_index, yesterday_due

from .conftest import YESTERDAY

SLOT = datetime(2026, 10, 31, 10, 30, tzinfo=MISO_TZ)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("31-Oct-2026 - Interval 10:30 EST", SLOT),
        ("1-Nov-2026 - Interval 0:05 EST", datetime(2026, 11, 1, 0, 5, tzinfo=MISO_TZ)),
        ("31-Oct-2026 - Interval 10:33:00 EST", datetime(2026, 10, 31, 10, 33, tzinfo=MISO_TZ)),
        ("not a stamp", None),
        ("31-Foo-2026 - Interval 10:30 EST", None),
        (None, None),
    ],
)
def test_parse_ref_id(text: str | None, expected: datetime | None) -> None:
    assert parse_ref_id(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-31 10:30:00 AM", SLOT),
        ("2026-10-31 12:00:00 AM", datetime(2026, 10, 31, 0, 0, tzinfo=MISO_TZ)),
        ("2026-10-31 12:05:00 PM", datetime(2026, 10, 31, 12, 5, tzinfo=MISO_TZ)),
        ("2026-10-31 11:55:00 PM", datetime(2026, 10, 31, 23, 55, tzinfo=MISO_TZ)),
        ("2026-10-31T10:30:00", SLOT),
        ("2026-10-31 13:00:00 PM", None),
        ("yesterday", None),
    ],
)
def test_parse_stamp(text: str, expected: datetime | None) -> None:
    assert parse_stamp(text) == expected


def test_miso_time_is_est_all_year() -> None:
    """MISO doesn't observe daylight saving: 10:30 "EST" on Oct 31 is 15:30 UTC."""
    assert SLOT.utcoffset().total_seconds() == -5 * 3600


def test_parse_fuel_mix(fuel_mix_text: str) -> None:
    mix = parse_fuel_mix(fuel_mix_text)
    assert mix.updated == SLOT
    assert mix.total_mw == 70349
    assert mix.mw == {
        "coal": 19727,
        "natural_gas": 25417,
        "nuclear": 8335,
        "wind": 8042,
        "solar": 5498,
        "battery_storage": 93,
        "other": 397,
        "imports": 2840,
    }
    assert mix.generation == 67509  # everything but imports
    assert sum(mix.mw.values()) == mix.total_mw


def test_parse_fuel_mix_new_category_and_errors(fuel_mix_text: str) -> None:
    raw = json.loads(fuel_mix_text)
    raw["Fuel"]["Type"].append({"INTERVALEST": "2026-10-31 10:30:00 AM", "CATEGORY": "Hydro Power", "ACT": "512"})
    assert parse_fuel_mix(json.dumps(raw)).mw["hydro_power"] == 512
    with pytest.raises(MisoError):
        parse_fuel_mix("<html>maintenance</html>")
    with pytest.raises(MisoError):
        parse_fuel_mix(json.dumps({"RefId": "x", "Fuel": {"Type": []}}))
    with pytest.raises(MisoError):
        parse_fuel_mix(json.dumps({"Fuel": {"Type": [{"CATEGORY": "Wind", "ACT": "1"}]}}))


def test_emissions_and_shares(fuel_mix_text: str) -> None:
    """Oct 31 10:30 EST: about 446 g/kWh, 20 % renewable, 33 % carbon-free."""
    mw = parse_fuel_mix(fuel_mix_text).mw
    assert co2_intensity(mw, 1000, 400) == pytest.approx(446.05, abs=0.01)
    assert co2_rate(mw, 1000, 400) == pytest.approx(29893.8)
    assert share(mw, RENEWABLE_FUELS) == pytest.approx(20.20, abs=0.01)
    assert share(mw, CARBON_FREE_FUELS) == pytest.approx(32.64, abs=0.01)
    # Other, Imports and Battery Storage don't move the estimate.
    assert co2_intensity({**mw, "other": 9999, "imports": 9999, "battery_storage": -500}, 1000, 400) == pytest.approx(
        446.05, abs=0.01
    )
    # Factors are options.
    assert co2_intensity(mw, 900, 350) == pytest.approx((19727 * 900 + 25417 * 350) / 67019)
    assert co2_intensity({"wind": 5}, 1000, 400) is None
    assert co2_intensity({"coal": 0, "natural_gas": 0}, 1000, 400) is None
    assert share({}, RENEWABLE_FUELS) is None


def test_parse_fuel_mix_day(yesterday_text: str) -> None:
    day = parse_fuel_mix_day(yesterday_text)
    assert day.day == YESTERDAY
    assert sorted(day.hours) == list(range(24))
    assert day.hours[10] == {
        "coal": 17640.2,
        "natural_gas": 23842.5,
        "nuclear": 8189.2,
        "wind": 10570.0,
        "solar": 11859.8,
    }
    intensities = {h: co2_intensity(mix, 1000, 400) for h, mix in day.hours.items()}
    assert min(intensities, key=intensities.get) == 10  # cleanest: 10-11 am, sunny
    assert max(intensities, key=intensities.get) == 17  # dirtiest: 5-6 pm, after sunset
    assert intensities[10] == pytest.approx(376.9, abs=0.1)
    assert intensities[17] == pytest.approx(511.8, abs=0.1)


def test_parse_fuel_mix_day_needs_complete_hours(yesterday_text: str) -> None:
    raw = json.loads(yesterday_text)
    raw["Fuel"]["Type"] = [r for r in raw["Fuel"]["Type"] if r["INTERVALEST"].endswith("AM")]  # half a day
    with pytest.raises(MisoError):
        parse_fuel_mix_day(json.dumps(raw))


def test_parse_load(load_text: str) -> None:
    load = parse_load(load_text)
    assert load.updated == SLOT
    assert load.day == date(2026, 10, 31)
    assert len(load.five_min) == 127  # 00:00 through 10:30
    assert load.demand == (SLOT, 72619)
    assert load.peak == (SLOT, 72619)
    assert load.forecast_peak == (datetime(2026, 10, 31, 15, 0, tzinfo=MISO_TZ), 72302)  # hour ending 16
    assert load.forecast[1] == 62568 and load.cleared[24] == 61347
    actual = load.hourly_actual()
    # Hour ending 11 (10:00-10:30 so far) has 7 of its 12 readings: enough to count.
    assert sorted(actual) == list(range(1, 12))
    assert actual[1] == round(sum(v for t, v in load.five_min if t.hour == 0) / 12)
    assert load.hour_start(1) == datetime(2026, 10, 31, 0, 0, tzinfo=MISO_TZ)


def test_parse_load_errors() -> None:
    with pytest.raises(MisoError):
        parse_load("[]")
    with pytest.raises(MisoError):
        parse_load(json.dumps({"LoadInfo": {"RefId": "garbage"}}))
    with pytest.raises(MisoError):
        parse_load(json.dumps({"LoadInfo": {"RefId": "31-Oct-2026 - Interval 10:30 EST"}}))


def test_derive_from_fixtures(fuel_mix_text: str, load_text: str) -> None:
    data = derive(parse_fuel_mix(fuel_mix_text), parse_load(load_text), {})
    assert data.co2_intensity == 446.0
    assert data.co2_emissions == 29894
    assert data.renewables == 20.2
    assert data.carbon_free == 32.6
    assert data.co2_index is None  # no history range passed
    assert data.low_carbon_now is None


def test_derive_index_and_threshold(fuel_mix_text: str) -> None:
    window = IndexRange(min_g=376.9, min_at=SLOT, max_g=511.8, max_at=SLOT, days=2)
    # Options left over from pre-release builds (the old "hub") are ignored.
    data = derive(parse_fuel_mix(fuel_mix_text), None, {"hub": "michigan"}, window)
    assert data.co2_index == 51  # 446.0 on a 376.9-511.8 scale
    assert data.low_carbon_now is False  # 51 > 25
    looser = derive(parse_fuel_mix(fuel_mix_text), None, {"low_carbon_index_threshold": 60}, window)
    assert looser.low_carbon_now is True


def test_co2_index() -> None:
    window = IndexRange(min_g=300, min_at=SLOT, max_g=500, max_at=SLOT, days=14)
    assert co2_index(400, window) == 50
    assert co2_index(250, window) == 0  # cleaner than anything in the window
    assert co2_index(600, window) == 100  # dirtier than anything in the window
    assert co2_index(None, window) is None
    assert co2_index(400, None) is None
    assert co2_index(400, IndexRange(min_g=400, min_at=SLOT, max_g=400, max_at=SLOT, days=2)) is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 31, 10, 35, tzinfo=MISO_TZ), date(2026, 10, 30)),
        (datetime(2026, 11, 1, 0, 10, tzinfo=MISO_TZ), date(2026, 10, 30)),  # not rolled over yet
        (datetime(2026, 11, 1, 0, 20, tzinfo=MISO_TZ), date(2026, 10, 31)),
        # 00:30 EDT on Nov 1 is still 23:30 EST on Oct 31
        (datetime.fromisoformat("2026-11-01T00:30:00-04:00"), date(2026, 10, 30)),
    ],
)
def test_yesterday_due(now: datetime, expected: date) -> None:
    assert yesterday_due(now) == expected
