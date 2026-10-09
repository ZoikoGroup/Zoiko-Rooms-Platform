"""ZR-PROPERTY-VERIFY-001 completion: fraud / duplicate signals go to a
reviewer (Sections 14, 20) while document problems stay host-fixable;
reviewer case assignment and four-eyes for high-risk cases (16); evidence
scanning, tamper signal and soft delete (14); authority reopens when a
submit changes the address (13.3); expiring-soon notice and expiry pausing
listings (7, 11.2); per-country form labels and no thresholds sent to hosts
(8, 17); mandatory version on writes (13.3); per-adjustment pin history and
the step reason codes / events (6.4, 13.1, 15)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.authority_verification import AuthorityVerification
from app.models.domain_event import DomainEvent
from app.models.listing import Listing
from app.models.property_location import PropertyLocationEvidence, PropertyLocationVerification
from app.services import property_location_service as svc
from tests.conftest import _make_admin, auth_admin_cookie, auth_user_cookie
from tests.test_property_location_verification import (  # noqa: F401 -- fixtures
    BASE, Flow, _host, _pdf, provider, uploads,
)

DOC = _pdf("Property tax record", "2-599 Madupally, Madhira", "PIN 507116")


@pytest.fixture()
def host(db_session):
    _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
    return _host(db_session, "complete-host@test.com")


def _events(db_session, verification_id):
    return list(db_session.scalars(select(DomainEvent.event_type).where(
        DomainEvent.resource_type == svc.RESOURCE, DomainEvent.resource_id == str(verification_id))))


def _reused_case(client, db_session, host):
    """Two hosts upload the same document -> the second goes to review."""
    user, prop, _room = host
    Flow(client, user, prop).happy_path(evidence=DOC)
    other = _host(db_session, "complete-other@test.com")
    flow = Flow(client, other[0], other[1])
    body = flow.happy_path(evidence=DOC)
    assert "POSSIBLE_DUPLICATE" in body["reasonCodes"]  # same address: the host answers first
    flow._send("POST", f"/{body['id']}/duplicate-answer",
               json={"answer": "NOT_SAME_PROPERTY", "note": "Different flat in the same building"})
    return flow.submit().json()


class TestRiskSignalsGoToReview:
    def test_reused_evidence_is_reviewed_not_host_cleared(self, client, db_session, host, provider, uploads):
        body = _reused_case(client, db_session, host)
        assert body["state"] == "MANUAL_REVIEW", body["reasonCodes"]
        assert "EVIDENCE_REUSED" in body["reasonCodes"]
        assert "PROPERTY_MANUAL_REVIEW_STARTED" in _events(db_session, body["id"])

    def test_a_not_the_same_property_answer_is_checked_by_a_reviewer(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        body = Flow(client, user, prop).happy_path(evidence=DOC)
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.duplicate_of_property_id, v.duplicate_host_answer = 999, "NOT_SAME_PROPERTY"
        state, _existence, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "MANUAL_REVIEW" and "DUPLICATE_REVIEW" in codes

    def test_document_problems_stay_with_the_host_even_with_a_risk_signal(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        body = Flow(client, user, prop).happy_path(evidence=_pdf("Unrelated text"))
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.reason_codes = ["NEARBY_PROPERTY_CONFLICT"]
        state, _existence, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "ACTION_REQUIRED" and "EVIDENCE_MISMATCH" in codes and "NEARBY_PROPERTY_CONFLICT" in codes

    def test_edited_document_and_failed_scan_are_reviewed(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        body = Flow(client, user, prop).happy_path(evidence=DOC)
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.evidence[0].tamper_signal = True
        state, _existence, codes = svc.decide(v, svc.get_pack(db_session, "IN"))
        assert state == "MANUAL_REVIEW" and "EVIDENCE_REVIEW" in codes

    def test_previously_rejected_property_is_reviewed(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        db_session.add(PropertyLocationVerification(property_id=prop.id, party_id=user.party_id, country_code="IN",
                                                    state="REJECTED"))
        db_session.commit()
        body = Flow(client, user, prop).happy_path(evidence=DOC)
        assert body["state"] == "MANUAL_REVIEW" and "PREVIOUSLY_REJECTED" in body["reasonCodes"]


class TestReviewerAssignmentAndFourEyes:
    def test_case_is_locked_to_its_reviewer_and_high_risk_needs_two(self, client, db_session, host, provider, uploads):
        body = _reused_case(client, db_session, host)
        vid = body["id"]
        evidence_id = db_session.get(PropertyLocationVerification, vid).evidence[0].id
        a1 = _make_admin(db_session, email="complete-r1@test.com", role="super_admin")
        a2 = _make_admin(db_session, email="complete-r2@test.com", role="super_admin")
        doc = f"/api/property-verifications/{vid}/evidence/{evidence_id}/document"
        assert client.post(f"/api/property-verifications/{vid}/assign", json={},
                           cookies=auth_admin_cookie(a1)).status_code == 200
        assert client.get(doc, cookies=auth_admin_cookie(a2)).status_code == 403
        approve = {"decision": "APPROVE", "reasonCode": "REVIEW_EVIDENCE_CORROBORATED"}
        url = f"/api/property-verifications/{vid}/review"
        assert client.post(url, json=approve, cookies=auth_admin_cookie(a2)).status_code == 403
        first = client.post(url, json=approve, cookies=auth_admin_cookie(a1)).json()
        assert first["state"] == "MANUAL_REVIEW"  # reused evidence: a second reviewer must approve
        assert client.post(url, json=approve, cookies=auth_admin_cookie(a1)).status_code == 409
        assert client.post(url, json=approve, cookies=auth_admin_cookie(a2)).json()["state"] == "VERIFIED"
        assert "PROPERTY_REVIEW_ASSIGNED" in _events(db_session, vid)


class TestEvidenceHandling:
    def test_removed_evidence_keeps_its_hash_for_reuse_detection(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start(); flow.address(); flow.confirm_address(); flow.location(); flow.unit()
        assert flow.evidence(DOC).status_code == 201
        evidence_id = flow.body["evidence"][0]["id"]
        assert flow._send("DELETE", f"/{flow.body['id']}/evidence/{evidence_id}").status_code == 200
        removed = db_session.get(PropertyLocationEvidence, evidence_id)
        db_session.refresh(removed)
        assert removed.removed_at is not None and removed.stored_filename is None and removed.sha256
        assert list(uploads.iterdir()) == []
        v = db_session.get(PropertyLocationVerification, flow.body["id"])
        db_session.refresh(v)
        assert v.evidence == []
        other = _host(db_session, "complete-reuse@test.com")
        body = Flow(client, other[0], other[1]).happy_path(evidence=DOC)
        assert "EVIDENCE_REUSED" in body["reasonCodes"]

    def test_unsafe_files_are_rejected(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start(); flow.address(); flow.confirm_address(); flow.location(); flow.unit()
        scripted = DOC.replace(b"%%EOF", b"/JavaScript (x)\n%%EOF")
        assert flow.evidence(scripted).status_code == 400


class TestAuthorityAndExpiry:
    def test_a_submit_that_changes_the_address_reopens_authority(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        prop.address = "1 Old Road"
        av = AuthorityVerification(property_id=prop.id, party_id=user.party_id, relationship_type="OWNER",
                                   country_code="IN", state="VERIFIED",
                                   expires_at=datetime.now(timezone.utc) + timedelta(days=90))
        db_session.add(av)
        db_session.commit()
        Flow(client, user, prop).happy_path(evidence=DOC)
        db_session.refresh(av)
        assert av.state == "REVOKED" and av.revocation_reason_code == "PROPERTY_ADDRESS_CHANGED"

    def test_expiring_notice_once_then_expiry_pauses_listings(self, client, db_session, host, provider, uploads):
        user, prop, room = host
        body = Flow(client, user, prop).happy_path(evidence=DOC)
        listing = Listing(name="Room", room_type="private_room", city="Madhira", location="x", latitude=16.9,
                          longitude=80.3, price_per_night=10, guests=1, room_id=room.id, party_id=user.party_id,
                          slug=f"r-{room.id}", id=f"lst-exp-{room.id}", state="PUBLISHED")
        db_session.add(listing)
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        db_session.commit()
        svc.sweep_expired(db_session)
        svc.sweep_expired(db_session)
        assert _events(db_session, v.id).count("PROPERTY_VERIFICATION_EXPIRING") == 1
        v.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.commit()
        assert svc.sweep_expired(db_session) == 1
        db_session.refresh(listing)
        assert v.state == "EXPIRED" and listing.state == "SUSPENDED"

    def test_expiring_soon_follows_the_pack(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        body = Flow(client, user, prop).happy_path(evidence=DOC)
        svc.get_pack(db_session, "IN").expiring_soon_days = 10
        v = db_session.get(PropertyLocationVerification, body["id"])
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=20)
        db_session.commit()
        assert svc.effective_state(v) == "VERIFIED"
        v.expires_at = datetime.now(timezone.utc) + timedelta(days=5)
        db_session.commit()
        assert svc.effective_state(v) == "EXPIRING_SOON"


class TestHostContractAndConcurrency:
    def test_host_policy_has_local_labels_but_no_thresholds(self, client, db_session, host):
        user, _prop, _room = host
        policy = client.get(f"{BASE}/policy?country=IN", cookies=auth_user_cookie(user)).json()
        assert policy["addressLabels"]["postal_code"] == "PIN code"
        assert policy["addressFieldOrder"][0] == "address_line_1"
        assert "pinMoveReviewMeters" not in policy and "evidenceRetentionDays" not in policy

    def test_writes_must_carry_the_version(self, client, db_session, host, provider, uploads):
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start()
        r = client.put(f"{BASE}/{flow.body['id']}/address", cookies=auth_user_cookie(user),
                       json={"address": {"addressLine1": "2-599 Madupally", "locality": "Madhira",
                                         "administrativeArea": "Telangana", "postalCode": "507116",
                                         "countryCode": "IN"}, "entryMode": "MANUAL"})
        assert r.status_code == 428

    def test_going_back_and_resubmitting_the_saved_address_works(self, client, db_session, host, provider, uploads):
        """Regression: step 1 pre-fills from the saved standardized address,
        which carries a display-only `formatted` line -- resubmitting it must
        not 422."""
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start()
        assert flow.address().status_code == 200
        saved = {**flow.body["canonicalAddress"]}
        assert "formatted" in saved
        r = flow._send("PUT", f"/{flow.body['id']}/address", json={"address": saved, "entryMode": "MANUAL"})
        assert r.status_code == 200, r.text

    def test_every_pin_adjustment_is_kept(self, client, db_session, host, provider, uploads):
        from tests.test_property_location_verification import LAT, LNG

        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start(); flow.address(); flow.confirm_address()
        flow.location("adjust", latitude=LAT + 0.00005, longitude=LNG, reason="Entrance")
        flow.location("adjust", latitude=LAT + 0.00008, longitude=LNG, reason="Gate")
        v = db_session.get(PropertyLocationVerification, flow.body["id"])
        db_session.refresh(v)
        assert [h["reason"] for h in v.pin_adjust_history] == ["Entrance", "Gate"]
        assert v.pin_adjust_history[0]["device"].get("ipHash") is not None

    def test_step_codes_and_events(self, client, db_session, host, provider, uploads):
        provider.precision = "APPROXIMATE"
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start(); flow.address()
        assert "LOW_LOCATION_CONFIDENCE" in flow.body["reasonCodes"]
        flow.confirm_address()
        assert "ADDRESS_CONFIRMED" in _events(db_session, flow.body["id"])

    def test_unknown_house_number_means_the_host_places_the_marker(self, client, db_session, host, provider, uploads,
                                                                   monkeypatch):
        """Indian door numbers are often missing from map data: Google's point
        is a nearby building, so it can't be confirmed as the property, and a
        long move within the area is expected -- not "too far"."""
        from app.services import location as loc
        from tests.test_property_location_verification import LAT, LNG

        original_validate = provider.validate

        def nearby_building(address):
            result = original_validate(address)
            result.location.precision, result.location.house_number_matched = "APPROXIMATE", False
            return result

        monkeypatch.setattr(loc, "validate", nearby_building)
        user, prop, _room = host
        flow = Flow(client, user, prop)
        flow.start(); flow.address()
        assert "HOUSE_NUMBER_NOT_ON_MAP" in flow.body["reasonCodes"]
        flow.confirm_address()
        r = flow.location("confirm")
        assert r.status_code == 409  # "This is correct" would record another building
        body = flow.location("adjust", latitude=LAT + 0.018, longitude=LNG, reason="My house is in the colony").json()
        assert body["pinStatus"] == "ADJUSTED"  # ~2 km from an area-level point: expected, not review
        provider.reverse_postal = "500001"
        body = flow.location("adjust", latitude=LAT + 0.0181, longitude=LNG, reason="moved").json()
        assert body["pinStatus"] == "REVIEW_REQUIRED"  # another postal code

    def test_a_small_nudge_across_a_uk_postcode_is_not_another_address(self, client, db_session, provider, uploads):
        """UK postcodes cover ~15 houses: a 5 m nudge to the front door can
        reverse-geocode to the neighbouring unit postcode (NW1 6XE ->
        NW1 6XF) -- still the same address. A real move into another
        postcode area (W1U) needs review."""
        from tests.test_property_location_verification import LAT, LNG

        _make_admin(db_session, email=settings.seed_admin_email, role="super_admin")
        user, prop, _room = _host(db_session, "complete-uk@test.com")
        prop.jurisdiction_code = "England"
        db_session.commit()
        provider.reverse_postal = "NW1 6XF"
        flow = Flow(client, user, prop)
        flow.start()
        flow.address({"addressLine1": "221B Baker Street", "locality": "London", "postalCode": "NW1 6XE",
                      "countryCode": "GB"})
        flow.confirm_address()
        body = flow.location("adjust", latitude=LAT + 0.00004, longitude=LNG, reason="Front door").json()
        assert body["pinStatus"] == "ADJUSTED", body
        provider.reverse_postal = "W1U 6QQ"
        body = flow.location("adjust", latitude=LAT + 0.0004, longitude=LNG, reason="moved").json()
        assert body["pinStatus"] == "REVIEW_REQUIRED"

    def test_contradictory_region_or_postcode_must_be_corrected(self):
        assert svc._components_conflict({"postal_code": "507116", "administrative_area": "Telangana"},
                                        {"postal_code": "500001", "administrative_area": "Telangana"})
        assert not svc._components_conflict({"locality": "Madira"}, {"locality": "Madhira"})


class TestLocationFirstStart:
    def test_reverse_fills_the_address_from_the_hosts_point(self, client, db_session, host, provider):
        user, _prop, _room = host
        r = client.post("/api/location/reverse", json={"latitude": 16.92, "longitude": 80.36},
                        cookies=auth_user_cookie(user))
        assert r.status_code == 200 and r.json()["found"] is True
        assert r.json()["address"]["postalCode"] == "507116"
        bad = client.post("/api/location/reverse", json={"latitude": 123, "longitude": 0}, cookies=auth_user_cookie(user))
        assert bad.status_code == 400
