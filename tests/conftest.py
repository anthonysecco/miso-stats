"""Shared fixtures for MISO Stats tests."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from custom_components.miso_stats import api
from custom_components.miso_stats.const import (
    FUEL_MIX_URL,
    FUEL_MIX_YESTERDAY_URL,
    LOAD_URL,
    MISO_TZ,
)

FIXTURES = Path(__file__).parent / "fixtures"
# The live feeds were captured at 10:35 EST on Oct 31, 2026; fuelmix_yesterday.json is Oct 30.
FIXTURE_NOW = datetime(2026, 10, 31, 10, 35, tzinfo=MISO_TZ)
FIXTURE_NOW_ISO = "2026-10-31 10:35:00-05:00"
YESTERDAY = date(2026, 10, 30)


def load_fixture(name: str) -> str:
    """Read a saved MISO response."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch) -> None:
    """History download retries don't sleep in tests."""
    monkeypatch.setattr(api, "HISTORY_RETRY_SECONDS", 0)


@pytest.fixture
def fuel_mix_text() -> str:
    """FuelMix fixture."""
    return load_fixture("fuelmix.json")


@pytest.fixture
def load_text() -> str:
    """RealTimeTotalLoad fixture."""
    return load_fixture("load.json")


@pytest.fixture
def yesterday_text() -> str:
    """FuelMix/Yesterday fixture (Oct 30, 2026)."""
    return load_fixture("fuelmix_yesterday.json")


@pytest.fixture
def mock_miso(aioclient_mock, fuel_mix_text, load_text, yesterday_text):
    """Every feed answers with the saved fixtures."""
    aioclient_mock.get(FUEL_MIX_URL, text=fuel_mix_text)
    aioclient_mock.get(LOAD_URL, text=load_text)
    aioclient_mock.get(FUEL_MIX_YESTERDAY_URL, text=yesterday_text)
    return aioclient_mock
