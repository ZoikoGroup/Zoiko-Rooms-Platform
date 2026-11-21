"""Find a Room price filter: currency offered to a visitor, from their location (never fixed to USD)."""

from __future__ import annotations

import pytest

from app.core import visitor_currency as vc
from app.core.config import settings


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(settings, "ip_geolocation_url", "")
    monkeypatch.setattr(settings, "ip_geolocation_fallback_url", "")
    monkeypatch.setattr(vc, "_geoip_reader", lambda: None)
    vc._country_cache.clear()


def test_country_header_wins():
    assert vc.detect_visitor_currency({"cf-ipcountry": "GB"}, "8.8.8.8") == {"currency": "GBP", "country": "GB", "source": "header"}


def test_euro_countries_and_india():
    assert vc.currency_for_country("DE") == "EUR"
    assert vc.currency_for_country("in") == "INR"


def test_country_we_hold_no_rate_for_is_not_offered():
    # JPY is a real currency but has no indicative rate, so the filter couldn't compare rooms with it.
    assert vc.currency_for_country("JP") is None


def test_unknown_cloudflare_marker_is_ignored():
    assert vc.detect_visitor_currency({"cf-ipcountry": "XX"}, "127.0.0.1")["currency"] is None


def test_browser_region_is_the_last_resort():
    assert vc.detect_visitor_currency({}, "127.0.0.1", "us") == {"currency": "USD", "country": "US", "source": "browser"}


def test_nothing_usable_returns_null_not_a_default():
    assert vc.detect_visitor_currency({}, "127.0.0.1") == {"currency": None, "country": None, "source": None}


def test_ip_lookup_used_for_public_addresses_only(monkeypatch):
    monkeypatch.setattr(settings, "ip_geolocation_url", "https://geo.example/{ip}")
    calls = []

    class Resp:
        status_code = 200
        text = "AU"

    monkeypatch.setattr(vc.httpx, "get", lambda url, **kw: calls.append(url) or Resp())
    assert vc.detect_visitor_currency({"x-forwarded-for": "8.8.8.8"}, "10.0.0.1")["currency"] == "AUD"
    assert calls == ["https://geo.example/8.8.8.8"]
    vc.detect_visitor_currency({}, "192.168.1.5")  # private: never looked up
    assert len(calls) == 1


@pytest.mark.parametrize("tz,currency", [
    ("Asia/Kolkata", "INR"), ("Europe/London", "GBP"), ("America/Chicago", "USD"), ("America/Toronto", "CAD"),
    ("Australia/Sydney", "AUD"), ("Europe/Paris", "EUR"), ("Asia/Dubai", "AED"),
])
def test_timezone_picks_the_currency(tz, currency):
    assert vc.detect_visitor_currency({}, "127.0.0.1", None, tz)["currency"] == currency


def test_timezone_beats_a_misleading_language_region():
    # An Indian laptop set to en-US: the time zone says India.
    out = vc.detect_visitor_currency({}, "127.0.0.1", "US", "Asia/Kolkata")
    assert (out["currency"], out["source"]) == ("INR", "browser")


def test_unknown_or_malformed_timezone_is_ignored():
    assert vc.detect_visitor_currency({}, "127.0.0.1", None, "../../etc")["currency"] is None
    assert vc.detect_visitor_currency({}, "127.0.0.1", None, "America/Sao_Paulo")["currency"] is None


# --- the free sources back each other up --------------------------------------------------------------------


class _FakeDb:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        return self.table.get(ip)


def test_offline_database_answers_without_any_network_call(monkeypatch):
    monkeypatch.setattr(vc, "_geoip_reader", lambda: _FakeDb({"81.2.139.142": {"country": {"iso_code": "GB"}}}))
    monkeypatch.setattr(settings, "ip_geolocation_url", "https://geo.example/{ip}")
    monkeypatch.setattr(vc.httpx, "get", lambda *a, **k: pytest.fail("the online service must not be called"))
    out = vc.detect_visitor_currency({"x-forwarded-for": "81.2.139.142"}, "10.0.0.1")
    assert out == {"currency": "GBP", "country": "GB", "source": "ip"}


def test_missing_database_falls_back_to_the_online_lookup(monkeypatch):
    monkeypatch.setattr(settings, "ip_geolocation_url", "https://geo.example/{ip}")

    class Resp:
        status_code = 200
        text = "IN"

    monkeypatch.setattr(vc.httpx, "get", lambda *a, **k: Resp())
    assert vc.detect_visitor_currency({"x-forwarded-for": "49.36.0.1"}, "10.0.0.1")["currency"] == "INR"


def test_second_online_service_is_used_when_the_first_is_down(monkeypatch):
    monkeypatch.setattr(settings, "ip_geolocation_url", "https://down.example/{ip}")
    monkeypatch.setattr(settings, "ip_geolocation_fallback_url", "https://up.example/{ip}")

    class Resp:
        status_code = 200
        text = '{"ip": "8.8.8.8", "country": "AU"}'

    def fake_get(url, **kw):
        if "down.example" in url:
            raise vc.httpx.ConnectError("boom")
        return Resp()

    monkeypatch.setattr(vc.httpx, "get", fake_get)
    assert vc.detect_visitor_currency({"x-forwarded-for": "8.8.8.8"}, "10.0.0.1")["currency"] == "AUD"


def test_every_ip_source_down_still_falls_back_to_the_browser(monkeypatch):
    monkeypatch.setattr(settings, "ip_geolocation_url", "https://down.example/{ip}")
    monkeypatch.setattr(vc.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(vc.httpx.ConnectError("boom")))
    out = vc.detect_visitor_currency({"x-forwarded-for": "8.8.8.8"}, "10.0.0.1", None, "Europe/London")
    assert (out["currency"], out["source"]) == ("GBP", "browser")


def test_country_currency_comes_from_cldr_and_falls_back_to_the_table(monkeypatch):
    assert vc._currency_of("CH") == "CHF"  # CLDR answers for any country
    import builtins

    real_import = builtins.__import__

    def no_babel(name, *a, **k):
        if name.startswith("babel"):
            raise ImportError("no babel")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_babel)
    assert vc._currency_of("GB") == "GBP"  # the built-in table still answers
