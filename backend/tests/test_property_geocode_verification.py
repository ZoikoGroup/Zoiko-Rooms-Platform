"""Property verification only completes automatically when the property's
address is found on a map (services/geocoding.py): street/house level, in
the country of the property's region. Anything else goes to manual review."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud import property_verification as crud
from app.models.property_verification import PropertyVerification
from app.services import geocoding
from app.services.geocoding import geocode_address as real_geocode_address  # captured before conftest patches it
from tests.conftest import _make_admin
from tests.test_property_verification import _declare, _make_host_with_room


def _stub(status: str, *, precision: str = "HOUSE", country: str = "IN", detail: str = ""):
    def fake(address, city, landmark, jurisdiction_code):
        return geocoding.GeocodeResult(
            status, "test", f"{address}, {city}", latitude=12.97, longitude=77.59,
            formatted_address=f"{address}, {city}", precision=precision, country_code=country, detail=detail,
        )
    return fake


def _run_auto_verify(db: Session, record: PropertyVerification) -> PropertyVerification:
    record.created_at = datetime.now(timezone.utc) - timedelta(seconds=crud.AUTO_VERIFY_DELAY_SECONDS + 5)
    db.commit()
    crud.verify_due_property_verifications(db, room_id=record.room_id)
    db.refresh(record)
    return record


@pytest.fixture()
def host_room(db_session: Session):
    # Auto-verification is attributed to the seed system admin (crud/payment_provider.py:get_system_admin).
    _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
    return _make_host_with_room(db_session, email="pv-geo-host@test.com")


class TestAutoVerificationNeedsTheMap:
    def test_address_found_on_map_is_auto_verified(self, db_session: Session, host_room, monkeypatch):
        user, room = host_room
        monkeypatch.setattr(geocoding, "geocode_address", _stub(geocoding.FOUND))
        record = _declare(db_session, user, room, evidence_ref="Property Address: 1 Verify Way, Bengaluru")

        assert record.geocode_status == "FOUND"
        assert record.geocode_latitude == 12.97 and record.geocode_longitude == 77.59
        assert record.verifier_notes == crud.AUTO_VERIFY_PENDING_NOTE
        record = _run_auto_verify(db_session, record)
        assert record.status == "verified"
        assert "address found on the map (house level)" in record.verifier_notes
        assert record.google_maps_url.startswith("https://www.google.com/maps/search/?api=1&query=12.970000,77.590000")

    @pytest.mark.parametrize("status,precision,detail", [
        (geocoding.NOT_FOUND, "", "The address could not be found on the map"),
        (geocoding.IMPRECISE, "LOCALITY", "Only the locality was found, not the street or building"),
        (geocoding.COUNTRY_MISMATCH, "HOUSE", "The address was found in United Kingdom, but the property is listed under IN"),
        (geocoding.UNAVAILABLE, "", "OpenStreetMap geocoding request failed"),
    ])
    def test_anything_but_found_goes_to_manual_review(self, db_session: Session, host_room, monkeypatch, status, precision, detail):
        user, room = host_room
        monkeypatch.setattr(geocoding, "geocode_address", _stub(status, precision=precision, detail=detail))
        record = _declare(db_session, user, room)

        assert record.geocode_status == status
        assert record.verifier_notes == crud.REVIEW_PENDING_NOTE
        record = _run_auto_verify(db_session, record)
        assert record.status == "pending"  # never auto-verified

    def test_legacy_pending_rows_without_a_map_check_are_not_auto_verified(self, db_session: Session, host_room):
        user, room = host_room
        record = _declare(db_session, user, room)
        record.geocode_status = None  # submitted before the map check existed
        db_session.commit()
        assert _run_auto_verify(db_session, record).status == "pending"

    def test_admin_can_still_verify_after_a_map_miss(self, db_session: Session, host_room, monkeypatch):
        user, room = host_room
        monkeypatch.setattr(geocoding, "geocode_address", _stub(geocoding.NOT_FOUND))
        record = _declare(db_session, user, room)
        admin = _make_admin(db_session, email="pv-geo-reviewer@test.com", role="super_admin")
        record = crud.verify_property_verification(db_session, record, admin, notes="Checked on site")
        assert record.status == "verified"


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)

    def json(self):
        return self._payload


class TestGeocodingService:
    """The legacy room-level check goes through the ZR-PROPERTY-VERIFY-001
    adapter (Google primary, Mapbox / HERE fallback)."""

    @staticmethod
    def _google(monkeypatch, payload):
        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
        seen = {}

        def fake_request(method, url, **k):
            # Address Validation first; the house-number check may follow up
            # with a geocode lookup, so keep every URL.
            seen.setdefault("urls", []).append(url)
            seen["url"] = seen["urls"][0]
            return _FakeResponse(payload)

        monkeypatch.setattr(httpx, "request", fake_request)
        return seen

    def test_house_level_in_the_right_country_is_found(self, monkeypatch):
        seen = self._google(monkeypatch, {"result": {
            "verdict": {"addressComplete": True, "validationGranularity": "PREMISE", "geocodeGranularity": "PREMISE"},
            "address": {"formattedAddress": "10 Downing Street, London SW1A 2AA, UK",
                        "postalAddress": {"regionCode": "GB", "addressLines": ["10 Downing Street"], "locality": "London"}},
            "geocode": {"location": {"latitude": 51.5, "longitude": -0.12}, "placeId": "p1"},
        }})
        result = real_geocode_address("10 Downing Street", "London", None, "England")
        assert "addressvalidation.googleapis.com" in seen["url"]
        assert result.status == geocoding.FOUND and result.precision == "HOUSE" and result.country_code == "GB"
        assert result.provider == "google"
        assert result.query == "10 Downing Street, London, United Kingdom"

    def test_address_in_another_country_is_a_mismatch(self, monkeypatch):
        self._google(monkeypatch, {"result": {
            "verdict": {"addressComplete": True, "validationGranularity": "PREMISE", "geocodeGranularity": "PREMISE"},
            "address": {"formattedAddress": "Madhira, India", "postalAddress": {"regionCode": "IN"}},
            "geocode": {"location": {"latitude": 17.1, "longitude": 80.3}},
        }})
        result = real_geocode_address("Madupally Madhira", "Khammam", None, "England")
        assert result.status == geocoding.COUNTRY_MISMATCH
        assert "India" in result.detail

    def test_city_level_match_is_imprecise(self, monkeypatch):
        self._google(monkeypatch, {"result": {
            "verdict": {"validationGranularity": "LOCALITY", "geocodeGranularity": "LOCALITY"},
            "address": {"postalAddress": {"regionCode": "GB"}},
            "geocode": {"location": {"latitude": 51.5, "longitude": -0.12}},
        }})
        assert real_geocode_address("Nowhere Lane 999", "London", None, "England").status == geocoding.IMPRECISE

    def test_no_result_is_not_found(self, monkeypatch):
        self._google(monkeypatch, {"result": {"verdict": {}, "address": {}}})
        assert real_geocode_address("xyz", "abc", None, "England").status == geocoding.NOT_FOUND

    def test_network_failure_is_unavailable_not_an_error(self, monkeypatch):
        monkeypatch.setattr(settings, "google_maps_api_key", "test-key")

        def boom(*a, **k):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(httpx, "request", boom)
        assert real_geocode_address("1 Road", "London", None, "England").status == geocoding.UNAVAILABLE

    def test_no_provider_configured_is_unavailable(self, monkeypatch):
        assert real_geocode_address("1 Road", "London", None, "England").status == geocoding.UNAVAILABLE

    def test_google_maps_url(self):
        assert geocoding.google_maps_url(51.5, -0.12) == "https://www.google.com/maps/search/?api=1&query=51.500000,-0.120000"
        assert geocoding.google_maps_url(None, None, "1 Road, London") == "https://www.google.com/maps/search/?api=1&query=1+Road%2C+London"
