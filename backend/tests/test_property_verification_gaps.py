"""ZR-PROPERTY-VERIFY-001 remaining items: authority reopens on a material
address change (13.3), stale property edits get 409 (13.3 / Section 20),
per-country public precision (10), the host's possible-duplicate answer
(Screen 4), pin-move device metadata (6.4), impossible postal codes and
nearby-property conflicts (14), and the Section 18 metrics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.listing import to_public_listing_read
from app.models.authority_verification import AuthorityVerification
from app.models.listing import Listing
from app.models.property import Property
from app.models.property_location import PropertyLocationVerification
from app.services import property_location_service as svc
from tests.conftest import _make_admin, auth_user_cookie
from tests.test_property_location_verification import (  # noqa: F401 -- fixtures
    ADDRESS, LAT, LNG, Flow, _host, provider, uploads,
)


@pytest.fixture()
def host(db_session):
    _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
    return _host(db_session, "gaps-host@test.com")


class TestAddressChangeAndConcurrency:
    def _put(self, client, user, prop, address, **headers):
        return client.put(f"/api/users/hosting/properties/{prop.id}", cookies=auth_user_cookie(user), headers=headers,
                          json={"address": address, "city": prop.city, "jurisdictionCode": prop.jurisdiction_code})

    def test_material_address_change_reopens_verified_authority(self, client, db_session, host):
        user, prop, _room = host
        v = AuthorityVerification(property_id=prop.id, party_id=user.party_id, relationship_type="OWNER", country_code="IN",
                                  pack_version=1, state="VERIFIED", expires_at=datetime.now(timezone.utc) + timedelta(days=90))
        db_session.add(v)
        db_session.commit()
        r = self._put(client, user, prop, "99 Completely Different Road")
        assert r.status_code == 200, r.text
        db_session.refresh(v)
        assert v.state == "REVOKED" and v.revocation_reason_code == "PROPERTY_ADDRESS_CHANGED"

    def test_stale_edit_is_refused(self, client, db_session, host):
        user, prop, _room = host
        version = prop.location_version
        assert self._put(client, user, prop, "1 New Road", **{"If-Match": str(version)}).status_code == 200
        db_session.refresh(prop)
        assert prop.location_version == version + 1
        r = self._put(client, user, prop, "2 Other Road", **{"If-Match": str(version)})
        assert r.status_code == 409

    def test_property_read_carries_location_version(self, client, db_session, host):
        user, prop, _room = host
        r = self._put(client, user, prop, prop.address)
        assert r.json()["locationVersion"] == prop.location_version


class TestPublicPrecision:
    def test_public_coordinates_follow_the_property_precision(self, db_session, host):
        _user, prop, room = host
        listing = Listing(name="Room", room_type="private_room", city="Madhira", location="2-599 Madupally",
                          latitude=16.923456, longitude=80.362345, price_per_night=40, guests=1, room_id=room.id,
                          party_id=prop.owner_party_id, slug="gaps-precision", id="lst-gaps-precision")
        db_session.add(listing)
        prop.public_location_decimals = 1
        db_session.commit()
        read = to_public_listing_read(listing)
        assert read.latitude == 16.9 and read.longitude == 80.4 and read.location == ""
        prop.public_location_decimals = 9  # never finer than 3 decimals
        db_session.commit()
        assert to_public_listing_read(listing).latitude == 16.923


class TestDuplicateAnswerAndSignals:
    def _to_unit(self, client, host):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start()
        assert flow.address().status_code == 200
        assert flow.confirm_address().status_code == 200
        assert flow.location().status_code == 200
        return flow

    def test_host_answers_a_possible_duplicate(self, client, db_session, host, provider, uploads):
        flow = self._to_unit(client, host)
        flow.unit()
        v = db_session.get(PropertyLocationVerification, flow.body["id"])
        v.duplicate_of_property_id = 999
        db_session.commit()
        flow.body["version"] = v.version
        r = flow._send("POST", f"/{v.id}/duplicate-answer", json={"answer": "NOT_SAME_PROPERTY"})
        assert r.status_code == 400  # a "different property" answer needs a note
        r = flow._send("POST", f"/{v.id}/duplicate-answer", json={"answer": "SAME_PROPERTY"})
        assert r.status_code == 200, r.text
        assert r.json()["duplicateHostAnswer"] == "SAME_PROPERTY"
        db_session.refresh(v)
        state, _existence, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "SAME_PROPERTY_DECLARED" in codes

    def test_no_duplicate_means_no_answer(self, client, db_session, host, provider, uploads):
        flow = self._to_unit(client, host)
        flow.unit()
        r = flow._send("POST", f"/{flow.body['id']}/duplicate-answer", json={"answer": "SAME_PROPERTY"})
        assert r.status_code == 409

    def test_pin_adjustment_records_device_metadata(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start()
        flow.address()
        flow.confirm_address()
        r = flow._send("POST", f"/{flow.body['id']}/confirm-location", headers={"User-Agent": "pytest-device"},
                       json={"action": "adjust", "latitude": LAT + 0.0001, "longitude": LNG, "reason": "Entrance"})
        assert r.status_code == 200, r.text
        v = db_session.get(PropertyLocationVerification, flow.body["id"])
        assert v.pin_adjust_device["userAgent"] == "pytest-device" and v.pin_adjust_device["adjustment"] == 1

    def test_impossible_postal_code_needs_action(self, db_session):
        v = PropertyLocationVerification(property_id=1, party_id=1, country_code="IN", pack_version=1,
                                         canonical_address={**{"country_code": "IN", "postal_code": "50711"}},
                                         geocode_status="RESOLVED", location_precision="ROOFTOP", address_status="VALIDATED")
        state, _e, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "POSTAL_CODE_INVALID" in codes

    def test_another_owners_property_at_the_same_spot_goes_to_review(self, client, db_session, host, provider, uploads):
        other_user, other_prop, _r = _host(db_session, "gaps-other@test.com")
        db_session.add(PropertyLocationVerification(
            property_id=other_prop.id, party_id=other_user.party_id, country_code="IN", pack_version=1, state="VERIFIED",
            confirmed_latitude=LAT, confirmed_longitude=LNG, address_fingerprint="someone-else"))
        db_session.commit()
        user, prop, _room = host
        body = Flow(client, user, prop).happy_path()
        assert body["state"] == "ACTION_REQUIRED"
        assert "NEARBY_PROPERTY_CONFLICT" in body["reasonCodes"]


class TestMetrics:
    def test_metrics_include_abandonment_and_provider_breakdown(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start()
        flow.address()
        v = db_session.get(PropertyLocationVerification, flow.body["id"])
        v.updated_at = datetime.now(timezone.utc) - timedelta(days=10)
        db_session.commit()
        m = svc.metrics(db_session)
        assert m["abandoned_by_step"].get("address") == 1
        assert "by_provider" in m and "provider_error_count" in m
        assert db_session.get(Property, prop.id) is not None
