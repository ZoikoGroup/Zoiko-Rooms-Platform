"""Integration tests for ZR-ENG-CLR-010 AC-35/36/37 claim-to-source-record
linkage: app/crud/disputes.py's new _resolve_source_record, auto-called
from open_case/add_claim, and the new source_record_type/source_record_id
columns on app/models/dispute.py's DisputeResolutionClaim.

Covers: a PROPERTY_CONDITION claim auto-links to the occupancy's most
recent OPEN HabitabilityIncident (preferring OPEN over a RESOLVED one); a
BOOKING_AGREEMENT claim auto-links to the agreement's most recent
non-terminal BookingChangeRequest; a SUBLET_OCCUPANCY claim auto-links to
the occupancy's most recent pending SubletRequest; a family with no
source-record concept (DEPOSIT) and a family with no matching record at
all both leave both fields null; add_claim (not just open_case) resolves
the same way; AC-35/37/38's source_record_snapshot freezes the specific
fields each AC names (sublet arrangement/consent, booking-change proposal
hash, occupancy handover evidence) at link time; a LOCKOUT/ACCESS/HOLDOVER
claim code prefers linking to an OccupancyHandoverEvent over a
SubletRequest when handover evidence exists."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.booking_change_request import BookingChangeRequest
from app.models.habitability_incident import HabitabilityIncident
from app.models.leasing import Agreement
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.party import Party
from app.models.sublet_request import SubletRequest
from tests.conftest import auth_user_cookie
from tests.test_disputes import _make_occupancy_with_parties


class TestPropertyConditionLinksToHabitabilityIncident:
    def test_links_to_the_open_incident_over_a_resolved_one(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost1@test.com", renter_email="slrenter1@test.com")

        resolved = HabitabilityIncident(
            room_id=occ.room_id, occupancy_id=occ.id, reported_by_guest_id=occ.guest_id,
            severity="H1", description="Old, already-fixed issue", status="RESOLVED",
        )
        db_session.add(resolved)
        db_session.flush()
        open_incident = HabitabilityIncident(
            room_id=occ.room_id, occupancy_id=occ.id, reported_by_guest_id=occ.guest_id,
            severity="H2", description="Current, unresolved issue", status="OPEN",
        )
        db_session.add(open_incident)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "PROPERTY_CONDITION"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "HABITABILITY_INCIDENT"
        assert claim["sourceRecordId"] == str(open_incident.id)

    def test_no_incident_at_all_leaves_both_fields_null(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost2@test.com", renter_email="slrenter2@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "PROPERTY_CONDITION"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] is None
        assert claim["sourceRecordId"] is None


class TestBookingAgreementLinksToBookingChangeRequest:
    def test_links_to_the_most_recent_non_terminal_request(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost3@test.com", renter_email="slrenter3@test.com")
        agreement = Agreement(offer_id=occ.offer_id, status="SIGNED")
        db_session.add(agreement)
        db_session.flush()

        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        rejected = BookingChangeRequest(
            agreement_id=agreement.id, requested_by_guest_id=occ.guest_id, change_type="EXTENSION",
            status="REJECTED", original_start_date=date(2026, 1, 1), proposed_start_date=date(2026, 1, 1),
            expires_at=expires_at,
        )
        db_session.add(rejected)
        db_session.flush()
        pending = BookingChangeRequest(
            agreement_id=agreement.id, requested_by_guest_id=occ.guest_id, change_type="EXTENSION",
            status="AWAITING_HOST", original_start_date=date(2026, 1, 1), proposed_start_date=date(2026, 1, 1),
            expires_at=expires_at,
        )
        db_session.add(pending)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "BOOKING_AGREEMENT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "BOOKING_CHANGE_REQUEST"
        assert claim["sourceRecordId"] == str(pending.id)

    def test_no_agreement_at_all_leaves_both_fields_null(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost4@test.com", renter_email="slrenter4@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "BOOKING_AGREEMENT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] is None
        assert claim["sourceRecordId"] is None


class TestSubletOccupancyLinksToSubletRequest:
    def test_links_to_the_most_recent_pending_request(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost5@test.com", renter_email="slrenter5@test.com")
        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.flush()

        approved = SubletRequest(
            current_occupancy_id=occ.id, proposed_renter_party_id=proposed_party.id, status="approved",
        )
        db_session.add(approved)
        db_session.flush()
        pending = SubletRequest(
            current_occupancy_id=occ.id, proposed_renter_party_id=proposed_party.id, status="pending_admin_review",
        )
        db_session.add(pending)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "OTHER", "claimFamily": "SUBLET_OCCUPANCY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "SUBLET_REQUEST"
        assert claim["sourceRecordId"] == str(pending.id)


class TestUnrelatedFamiliesStayUnlinked:
    def test_deposit_claims_never_get_a_source_record(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost6@test.com", renter_email="slrenter6@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] is None
        assert claim["sourceRecordId"] is None


class TestAddClaimAlsoResolves:
    def test_add_claim_links_a_property_condition_claim_added_after_case_open(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost7@test.com", renter_email="slrenter7@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]

        incident = HabitabilityIncident(
            room_id=occ.room_id, occupancy_id=occ.id, reported_by_guest_id=occ.guest_id,
            severity="H1", description="Leak", status="OPEN",
        )
        db_session.add(incident)
        db_session.commit()

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "OTHER", "claimFamily": "PROPERTY_CONDITION"},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        assert r.json()["sourceRecordType"] == "HABITABILITY_INCIDENT"
        assert r.json()["sourceRecordId"] == str(incident.id)


class TestSourceRecordSnapshot:
    """AC-35/37: the specific fields each AC names are frozen onto the
    claim, not just an ID pointer."""

    def test_sublet_snapshot_preserves_arrangement_type_and_consent_evidence(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost8@test.com", renter_email="slrenter8@test.com")
        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.flush()
        sublet = SubletRequest(
            current_occupancy_id=occ.id, proposed_renter_party_id=proposed_party.id, status="pending_admin_review",
            arrangement_type="SUBLEASE_PARTIAL", authority_evidence_ref="consent-letter.pdf",
        )
        db_session.add(sublet)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "SUBLET_CONSENT", "claimFamily": "SUBLET_OCCUPANCY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "SUBLET_REQUEST"
        snapshot = claim["sourceRecordSnapshot"]
        assert snapshot["arrangement_type"] == "SUBLEASE_PARTIAL"
        assert snapshot["authority_evidence_ref"] == "consent-letter.pdf"

    def test_booking_agreement_snapshot_preserves_the_exact_proposal_hash(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost9@test.com", renter_email="slrenter9@test.com")
        agreement = Agreement(offer_id=occ.offer_id, status="SIGNED")
        db_session.add(agreement)
        db_session.flush()
        bcr = BookingChangeRequest(
            agreement_id=agreement.id, requested_by_guest_id=occ.guest_id, change_type="EXTENSION",
            status="AWAITING_HOST", original_start_date=date(2026, 1, 1), proposed_start_date=date(2026, 1, 1),
            proposal_hash="fixed-test-hash", expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db_session.add(bcr)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "TERM_INTERPRETATION", "claimFamily": "BOOKING_AGREEMENT"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        snapshot = r.json()["claims"][0]["sourceRecordSnapshot"]
        assert snapshot["proposal_hash"] == "fixed-test-hash"
        assert snapshot["status"] == "AWAITING_HOST"


class TestOccupancyAccessLinksToHandoverEvent:
    """AC-38: a LOCKOUT/ACCESS/HOLDOVER claim preserves Section 9
    possession/access evidence -- linking to an OccupancyHandoverEvent
    over a SubletRequest when handover evidence exists for the occupancy."""

    def test_a_lockout_claim_links_to_the_handover_event_not_a_sublet_request(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost10@test.com", renter_email="slrenter10@test.com")
        handover = OccupancyHandoverEvent(
            occupancy_id=occ.id, event_type="POSSESSION_DELIVERED", actor_kind="provider_admin", evidence_ref="key-log.pdf",
        )
        db_session.add(handover)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "OCCUPANCY_HANDOVER_EVENT"
        assert claim["sourceRecordId"] == str(handover.id)
        assert claim["sourceRecordSnapshot"]["evidence_ref"] == "key-log.pdf"

    def test_a_lockout_claim_falls_back_to_a_sublet_request_with_no_handover_evidence(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost11@test.com", renter_email="slrenter11@test.com")
        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.flush()
        sublet = SubletRequest(current_occupancy_id=occ.id, proposed_renter_party_id=proposed_party.id, status="pending_admin_review")
        db_session.add(sublet)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "ACCESS", "claimFamily": "SUBLET_OCCUPANCY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        claim = r.json()["claims"][0]
        assert claim["sourceRecordType"] == "SUBLET_REQUEST"
        assert claim["sourceRecordId"] == str(sublet.id)

    def test_a_non_access_sublet_claim_code_still_prefers_the_sublet_request(self, client, db_session: Session):
        _host, renter, occ = _make_occupancy_with_parties(db_session, host_email="slhost12@test.com", renter_email="slrenter12@test.com")
        handover = OccupancyHandoverEvent(occupancy_id=occ.id, event_type="POSSESSION_DELIVERED", actor_kind="provider_admin")
        db_session.add(handover)
        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.flush()
        sublet = SubletRequest(current_occupancy_id=occ.id, proposed_renter_party_id=proposed_party.id, status="pending_admin_review")
        db_session.add(sublet)
        db_session.commit()

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occ.id, "claim": {"claimCode": "SUBLET_CONSENT", "claimFamily": "SUBLET_OCCUPANCY"}},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        assert r.json()["claims"][0]["sourceRecordType"] == "SUBLET_REQUEST"
