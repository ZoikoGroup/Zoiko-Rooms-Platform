"""ZR-PROPERTY-VERIFY-001 Section 3 provider stack: Google Maps Platform
primary, Mapbox and HERE as pre-qualified fallbacks behind the Zoiko
adapter, manual path when none is configured. No OpenStreetMap."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import settings
from app.services import location as loc

ADDRESS = loc.CanonicalAddress(address_line_1="2-599 Madupally", locality="Madhira", administrative_area="Telangana",
                               postal_code="507203", country_code="IN")


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


def _route(monkeypatch, handler):
    calls = []

    def fake(method_or_url, url=None, **kwargs):
        target = url or method_or_url
        calls.append(target)
        return handler(target, kwargs)

    monkeypatch.setattr(httpx, "request", fake)
    monkeypatch.setattr(httpx, "get", lambda url, **k: fake(url, **k))
    return calls


def _keys(monkeypatch, google="", mapbox="", here=""):
    monkeypatch.setattr(settings, "google_maps_api_key", google)
    monkeypatch.setattr(settings, "mapbox_access_token", mapbox)
    monkeypatch.setattr(settings, "here_api_key", here)


GOOGLE_PREMISE = {"result": {
    "verdict": {"addressComplete": True, "validationGranularity": "PREMISE", "geocodeGranularity": "PREMISE"},
    "address": {"formattedAddress": "2-599 Madupally, Madhira, Telangana 507203, India",
                "postalAddress": {"regionCode": "IN", "addressLines": ["2-599 Madupally"], "locality": "Madhira",
                                  "administrativeArea": "Telangana", "postalCode": "507203"}},
    "geocode": {"location": {"latitude": 16.92, "longitude": 80.36}, "placeId": "g1"},
}}
MAPBOX_TOWN = {"features": [{"geometry": {"coordinates": [80.36, 16.92]}, "properties": {
    "feature_type": "place", "full_address": "Madhira, Telangana, India",
    "context": {"place": {"name": "Madhira"}, "country": {"country_code": "in"}}}}]}
HERE_HOUSE = {"items": [{"resultType": "houseNumber", "houseNumberType": "PA", "position": {"lat": 16.921, "lng": 80.362},
                         "scoring": {"queryScore": 0.95},
                         "address": {"label": "2-599 Madupally, Madhira", "houseNumber": "2-599", "street": "Madupally",
                                     "city": "Madhira", "state": "Telangana", "postalCode": "507203", "countryCode": "IND"}}]}


def test_no_credentials_means_manual_path(monkeypatch):
    _keys(monkeypatch)
    assert loc.capabilities() == {"provider": "none", "providers": [], "autocomplete": False, "configured": False}
    with pytest.raises(loc.LocationUnavailable):
        loc.validate(ADDRESS)


def test_google_is_primary(monkeypatch):
    _keys(monkeypatch, google="g", mapbox="m", here="h")
    calls = _route(monkeypatch, lambda url, kw: _Resp(GOOGLE_PREMISE))
    result = loc.validate(ADDRESS)
    assert result.provider == "google" and result.status == "VALIDATED"
    assert result.location.precision == "ROOFTOP"
    assert all("googleapis.com" in c for c in calls)
    assert loc.capabilities()["providers"] == ["google", "mapbox", "here"]


def test_google_outage_falls_back_to_mapbox(monkeypatch):
    _keys(monkeypatch, google="g", mapbox="m")

    def handler(url, kw):
        if "googleapis.com" in url:
            return _Resp({}, 503)
        return _Resp(MAPBOX_TOWN)

    _route(monkeypatch, handler)
    result = loc.validate(ADDRESS)
    assert result.provider == "mapbox"
    assert result.status == "PARTIAL" and result.location.precision == "APPROXIMATE"
    assert result.canonical.address_line_1 == "2-599 Madupally"  # host's address kept on an area-only match


def test_coverage_gap_moves_to_here(monkeypatch):
    _keys(monkeypatch, google="g", here="h")

    def handler(url, kw):
        if "googleapis.com" in url:
            return _Resp({"result": {"verdict": {}, "address": {}}})
        return _Resp(HERE_HOUSE)

    _route(monkeypatch, handler)
    result = loc.validate(ADDRESS)
    assert result.provider == "here" and result.status == "VALIDATED"
    assert result.location.precision == "ROOFTOP" and result.canonical.country_code == "IN"


def test_all_providers_down_is_unavailable(monkeypatch):
    _keys(monkeypatch, google="g", mapbox="m", here="h")
    _route(monkeypatch, lambda url, kw: _Resp({}, 500))
    with pytest.raises(loc.LocationUnavailable):
        loc.validate(ADDRESS)


def test_suggestion_ids_route_retrieve_back_to_their_provider(monkeypatch):
    _keys(monkeypatch, mapbox="m")

    def handler(url, kw):
        if url.endswith("/suggest"):
            return _Resp({"suggestions": [{"mapbox_id": "abc", "name": "2-599 Madupally", "place_formatted": "Madhira"}]})
        assert url.endswith("/retrieve/abc")
        return _Resp({"features": [{"geometry": {"coordinates": [80.36, 16.92]}, "properties": {
            "feature_type": "address", "coordinates": {"latitude": 16.92, "longitude": 80.36, "accuracy": "rooftop"},
            "context": {"address": {"address_number": "2-599", "street_name": "Madupally"},
                        "place": {"name": "Madhira"}, "country": {"country_code": "in"}}}}]})

    _route(monkeypatch, handler)
    items = loc.suggest("2-599 Madu", "IN", "s1")
    assert items[0].id == "mapbox:abc"
    address, location, code = loc.retrieve(items[0].id, "s1")
    assert code == "mapbox" and address.address_line_1 == "2-599 Madupally" and location.precision == "ROOFTOP"


def test_address_validation_unsupported_country_falls_back_to_geocoding(monkeypatch):
    _keys(monkeypatch, google="g")

    def handler(url, kw):
        if "addressvalidation" in url:
            return _Resp({"error": {"message": "Unsupported region code"}}, 400)
        return _Resp({"status": "OK", "results": [{
            "geometry": {"location": {"lat": 16.92, "lng": 80.36}, "location_type": "ROOFTOP"},
            "types": ["street_address"], "place_id": "g2"}]})

    _route(monkeypatch, handler)
    result = loc.validate(ADDRESS)
    assert result.provider == "google" and result.status == "PARTIAL"
    assert result.location.precision == "ROOFTOP" and result.canonical.address_line_1 == "2-599 Madupally"
