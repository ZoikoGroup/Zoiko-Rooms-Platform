"""Indicative exchange rates for the Find a Room price filter (never used for payments)."""

from __future__ import annotations

from app.core import fx
from app.core.config import settings


def test_public_endpoint_serves_indicative_rates_without_login(client):
    body = client.get("/api/public/exchange-rates").json()
    assert body["base"] == "USD" and body["indicative"] is True
    rates = body["usdPerUnit"]
    assert rates["USD"] == 1.0 and rates["GBP"] > 1 and 0 < rates["INR"] < 0.1


def test_overrides_replace_and_add_currencies(monkeypatch):
    monkeypatch.setattr(settings, "fx_rates_json", '{"GBP": 1.5, "sgd": 0.74}')
    rates = fx.usd_per_unit()
    assert rates["GBP"] == 1.5 and rates["SGD"] == 0.74
    assert rates["INR"] == fx.DEFAULT_USD_PER_UNIT["INR"]  # untouched


def test_bad_overrides_are_ignored_not_fatal(monkeypatch):
    for bad in ("not json", "[1, 2]", '{"GBP": "x", "TOOLONG": 1, "EUR": -2, "INR": 0}'):
        monkeypatch.setattr(settings, "fx_rates_json", bad)
        assert fx.usd_per_unit() == fx.DEFAULT_USD_PER_UNIT


def test_as_of_is_reported_when_set(client, monkeypatch):
    monkeypatch.setattr(settings, "fx_rates_as_of", "2026-10-29")
    assert client.get("/api/public/exchange-rates").json()["asOf"] == "2026-10-29"
