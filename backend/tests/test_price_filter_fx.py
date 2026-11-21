"""Indicative rates for the Find a Room price filter (app/core/fx.py): free live sources with fallbacks."""

from __future__ import annotations

import pytest

from app.core import fx
from app.core.config import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(fx, "_live_rates", {})
    monkeypatch.setattr(fx, "_live_as_of", None)
    monkeypatch.setattr(fx, "_live_fetched_at", 0.0)
    monkeypatch.setattr(fx, "_refreshing", False)
    monkeypatch.setattr(settings, "fx_live_enabled", True)
    monkeypatch.setattr(settings, "fx_rates_json", "")
    # usd_per_unit() must not start a real background fetch during these tests.
    monkeypatch.setattr(fx, "_maybe_refresh", lambda: None)


def _source(rates, date="2026-11-20"):
    return lambda: (rates, date)


def _down():
    raise fx.httpx.ConnectError("down")


GOOD = {"USD": 1.0, "GBP": 1.31, "INR": 0.0108, "JPY": 0.0066}


def test_first_source_wins(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _source(GOOD)), ("b", _source({"GBP": 9.0, "INR": 9.0}))))
    assert fx.refresh_live_rates() is True
    assert fx.usd_per_unit()["GBP"] == 1.31
    assert fx.usd_per_unit()["JPY"] == 0.0066  # a currency the built-in table never had
    assert fx.live_rates_as_of() == "2026-11-20"


def test_second_source_used_when_the_first_is_down(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _down), ("b", _source(GOOD, "2026-11-19"))))
    assert fx.refresh_live_rates() is True
    assert fx.usd_per_unit()["GBP"] == 1.31
    assert fx.live_rates_as_of() == "2026-11-19"


def test_all_sources_down_keeps_the_built_in_rates(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _down), ("b", _down)))
    assert fx.refresh_live_rates() is False
    assert fx.usd_per_unit() == fx.DEFAULT_USD_PER_UNIT
    assert fx.live_rates_as_of() is None


def test_a_failed_refresh_keeps_the_last_good_live_rates(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _source(GOOD)),))
    fx.refresh_live_rates()
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _down),))
    assert fx.refresh_live_rates() is False
    assert fx.usd_per_unit()["GBP"] == 1.31


def test_a_response_missing_the_major_currencies_is_not_trusted(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _source({"USD": 1.0, "XXX": 5.0})), ("b", _source(GOOD))))
    assert fx.refresh_live_rates() is True
    assert fx.usd_per_unit()["GBP"] == 1.31


def test_manual_overrides_still_beat_live_rates(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _source(GOOD)),))
    fx.refresh_live_rates()
    monkeypatch.setattr(settings, "fx_rates_json", '{"GBP": 1.5}')
    assert fx.usd_per_unit()["GBP"] == 1.5


def test_live_rates_off_uses_only_the_built_in_table(monkeypatch):
    monkeypatch.setattr(fx, "LIVE_SOURCES", (("a", _source(GOOD)),))
    fx.refresh_live_rates()
    monkeypatch.setattr(settings, "fx_live_enabled", False)
    assert fx.usd_per_unit() == fx.DEFAULT_USD_PER_UNIT
    assert fx.live_rates_as_of() is None
