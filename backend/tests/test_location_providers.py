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
    # The adapter's fallback behaviour is under test here, independent of a
    # deployment's .env (which may run Google only).
    monkeypatch.setattr(settings, "location_fallback_enabled", True)
    monkeypatch.setattr(settings, "location_fallback_providers", "mapbox,here")
    monkeypatch.setattr(settings, "location_provider", "google")
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


def test_google_reverse_refusal_is_an_outage_not_no_address(monkeypatch):
    """A refused reverse geocode (API not enabled / key blocked) must surface
    as unavailable -- not as "nothing at this pin"."""
    _keys(monkeypatch, google="g-key")
    monkeypatch.setattr(settings, "location_fallback_enabled", False)
    _route(monkeypatch, lambda url, kw: _Resp({"status": "REQUEST_DENIED", "results": [],
                                               "error_message": "This API is not activated"}))
    with pytest.raises(loc.LocationUnavailable):
        loc.reverse(16.92, 80.36)


def test_health_check_names_each_blocked_google_api_and_the_fix(monkeypatch):
    _keys(monkeypatch, google="g-key")
    monkeypatch.setattr(settings, "location_fallback_enabled", False)

    def handler(url, kw):
        if "places.googleapis.com" in url:
            return _Resp({"error": {"status": "PERMISSION_DENIED", "message": "blocked",
                                    "details": [{"reason": "API_KEY_SERVICE_BLOCKED"}]}}, status_code=403)
        if "geocode" in url:
            return _Resp({"status": "REQUEST_DENIED", "error_message": "This API is not activated"})
        return _Resp(GOOGLE_PREMISE)

    _route(monkeypatch, handler)
    health = loc.diagnose()
    rows = {r["api"]: r for r in health["google"]}
    assert rows["Address Validation API"]["ok"] is True
    assert rows["Places API (New)"]["reason"] == "API_KEY_SERVICE_BLOCKED"
    assert "API restrictions" in rows["Places API (New)"]["fix"]
    assert rows["Geocoding API"]["reason"] == "REQUEST_DENIED" and "Enable" in rows["Geocoding API"]["fix"]
    assert health["fallbacks"] == []  # Google only
    assert "g-key" not in str(health)


def test_health_check_without_a_key(monkeypatch):
    _keys(monkeypatch)
    health = loc.diagnose()
    assert health["google"][0]["reason"] == "NOT_CONFIGURED"


def test_a_premise_with_a_different_house_number_is_only_the_area(monkeypatch):
    """Google snaps an unknown Indian door number ("2-599") to the nearest
    building it knows ("15-2") and still calls it ROOFTOP -- that point is
    only the area, not the property."""
    _keys(monkeypatch, google="g")
    nearby = {"status": "OK", "results": [{
        "geometry": {"location": {"lat": 16.9227, "lng": 80.3363}, "location_type": "ROOFTOP"},
        "types": ["premise"], "place_id": "g-near", "formatted_address": "W8FP+3HR, 15-2, Madupalli, Telangana 507203, India",
        "address_components": [{"long_name": "15-2", "types": ["premise"]}]}]}

    def handler(url, kw):
        if "addressvalidation" in url:
            return _Resp({"result": {
                "verdict": {"addressComplete": True, "validationGranularity": "PREMISE", "geocodeGranularity": "PREMISE"},
                "address": {"postalAddress": {"regionCode": "IN", "addressLines": ["2-599"], "postalCode": "507203"}},
                "geocode": {"location": {"latitude": 16.9227, "longitude": 80.3363}, "placeId": "g-near"}}})
        return _Resp(nearby)

    _route(monkeypatch, handler)
    result = loc.validate(ADDRESS)
    assert result.location.precision == "APPROXIMATE" and result.location.house_number_matched is False


def test_the_right_house_number_keeps_rooftop(monkeypatch):
    _keys(monkeypatch, google="g")
    _route(monkeypatch, lambda url, kw: _Resp({"status": "OK", "results": [{
        "geometry": {"location": {"lat": 16.92, "lng": 80.36}, "location_type": "ROOFTOP"}, "types": ["premise"],
        "place_id": "g3", "formatted_address": "2-599, Madupally, Madhira 507203",
        "address_components": [{"long_name": "2-599", "types": ["premise"]}]}]}))
    location = loc.geocode(ADDRESS)[0]
    assert location.precision == "ROOFTOP" and location.house_number_matched is True


def test_country_from_any_region_code():
    c = loc.country_for_jurisdiction
    assert [c("IN"), c("England"), c("GB-SCT"), c("Northern Ireland"), c("US-NY"), c("CA-ON"), c("DE")] == \
        ["IN", "GB", "GB", "GB", "US", "CA", "DE"]
    assert c("") == "" and c("Atlantis") == ""


def test_postal_area_is_coarse_enough_for_fine_grained_codes():
    a = loc.postal_area
    assert a("NW1 6XE", "GB") == "NW1" and a("10001-1234", "US") == "10001" and a("K1A 0B1", "CA") == "K1A"
    assert a("507203", "IN") == "507203"


def test_house_number_line_is_kept_when_google_reorders_it(monkeypatch):
    """Google returned "Kalimandir" as line 1 and moved "4-2-182, ... Bank
    Colony" to line 2 -- the property's first line must keep its number."""
    _keys(monkeypatch, google="g")
    entered = loc.CanonicalAddress(address_line_1="4-2-182, Bank Colony, Kalimandir",
                                   address_line_2="Beside Rockliff Apartment", locality="Bandlaguda Jagir",
                                   administrative_area="Telangana", postal_code="500086", country_code="IN")
    _route(monkeypatch, lambda url, kw: _Resp({"result": {
        "verdict": {"addressComplete": False, "validationGranularity": "OTHER", "geocodeGranularity": "OTHER"},
        "address": {"postalAddress": {"regionCode": "IN", "postalCode": "500086", "locality": "Bandlaguda Jagir",
                                      "addressLines": ["Kalimandir", "4-2-182, beside Rockliff Apartment, Bank Colony"]}}}}))
    result = loc.validate(entered)
    assert result.canonical.address_line_1 == "4-2-182, Bank Colony, Kalimandir"
    assert result.canonical.locality == "Bandlaguda Jagir" and result.canonical.postal_code == "500086"


def test_an_unconfirmed_address_is_kept_as_entered_and_google_only_suggests(monkeypatch):
    """Google couldn't confirm "2-599 ..., Madhira" (UNRESOLVED) but returned
    locality "Madupalli" -- that must not silently replace what the host
    entered; it's offered as a correction instead."""
    _keys(monkeypatch, google="g")
    entered = loc.CanonicalAddress(address_line_1="2-599 Muthyalamma temple", address_line_2="Madupalli",
                                   locality="Madhira", administrative_area="Telangana", postal_code="507203",
                                   country_code="IN")
    _route(monkeypatch, lambda url, kw: _Resp({"result": {
        "verdict": {"addressComplete": False, "validationGranularity": "OTHER", "geocodeGranularity": "OTHER"},
        "address": {"formattedAddress": "Muthyalamma temple, 2-599, Madupalli, Madhira, Telangana 507203, India",
                    "postalAddress": {"regionCode": "IN", "postalCode": "507203", "locality": "Madupalli",
                                      "administrativeArea": "Telangana",
                                      "addressLines": ["2-599 Muthyalamma temple", "Madupalli"]}}}}))
    result = loc.validate(entered)
    assert result.status == "UNRESOLVED"
    assert result.canonical.locality == "Madhira" and result.canonical.address_line_1 == "2-599 Muthyalamma temple"
    assert result.suggestion is not None and result.suggestion.locality == "Madupalli"
