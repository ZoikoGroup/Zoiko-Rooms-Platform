"""Integration tests for the new ZR-ENG-CLR-010 (Section 10) dispute domain:
app/models/dispute.py, app/crud/disputes.py, app/services/dispute_forum_resolver.py,
app/services/dispute_state_machine.py and app/api/routes/disputes.py.

Covers the Phase 1 scope: multi-claim cases, fail-closed forum resolution
(AC-42), the AC-6 "admin cannot decide an external-only claim" gate, and
AC-24/AC-52 close-case guards -- the scenarios called out in the approved
plan's own Verification section."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.leasing import Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_occupancy_with_parties(db: Session, *, host_email: str, renter_email: str) -> tuple[UserAccount, UserAccount, Occupancy]:
    """Builds a minimal Party(host) -> Property -> Room -> Listing and a
    Guest(renter, linked to a UserAccount) -> Application -> Offer ->
    Occupancy chain -- same shape tests/test_availability.py already uses,
    trimmed to what dispute intake/ownership checks need."""
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()

    host_user = _make_user(db, email=host_email)
    host_user.party_id = party.id
    db.flush()

    prop = Property(owner_party_id=party.id, address="123 Real Street", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()

    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id=f"L-{host_email}", slug=f"listing-{host_email}", name="Test Listing", room_type="private_room",
        city="Bengaluru", location="123 Real Street", price_per_night=500, currency="INR", guests=1,
        state="PUBLISHED", room_id=room.id,
    )
    db.add(listing)
    db.flush()

    renter_user = _make_user(db, email=renter_email)
    guest = Guest(id=f"G-{renter_email}", name="Test Renter", email=renter_email, joined_at=date.today(), user_account_id=renter_user.id)
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    occupancy = Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id, status="ACTIVE")
    db.add(occupancy)
    db.commit()

    return host_user, renter_user, occupancy


class TestRenterIntakeAndForumResolution:
    def test_deposit_claim_resolves_to_a2_and_triages(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host1@test.com", renter_email="renter1@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={
                "occupancyId": occupancy.id,
                "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600, "requestedRemedy": "Refund deduction"},
            },
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["status"] == "TRIAGED"
        assert body["severity"] == "SEV-2"
        assert len(body["claims"]) == 1
        assert body["claims"][0]["authorityClass"] == "A2"
        assert body["claims"][0]["resolverConfidence"] == "RESOLVED"

    def test_unrecognized_claim_family_fails_closed_to_legal_review(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host2@test.com", renter_email="renter2@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={
                "occupancyId": occupancy.id,
                "claim": {"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY", "requestedRemedy": "Investigate"},
            },
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        # AC-42: never invent a generic forum -- PROTECTED_SAFETY has no MVP
        # authority mapping, so the case must fail closed, not default to A0/A1.
        assert body["status"] == "LEGAL_REVIEW_REQUIRED"
        assert body["claims"][0]["authorityClass"] is None
        assert body["claims"][0]["resolverConfidence"] == "LEGAL_REVIEW_REQUIRED"

    def test_safety_flag_forces_a6_and_sev0_regardless_of_family(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host3@test.com", renter_email="renter3@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={
                "occupancyId": occupancy.id,
                "claim": {"claimCode": "ILLEGAL_LOCKOUT", "claimFamily": "SUBLET_OCCUPANCY", "safetyFlag": True, "requestedRemedy": "Restore access"},
            },
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["severity"] == "SEV-0"
        assert body["claims"][0]["authorityClass"] == "A6"

    def test_renter_cannot_open_a_case_on_someone_elses_occupancy(self, client, db_session: Session):
        _host, _renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host4@test.com", renter_email="renter4@test.com")
        other_renter = _make_user(db_session, email="intruder@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT"}},
            cookies=auth_user_cookie(other_renter),
        )
        assert r.status_code == 403, r.text

    def test_second_claim_on_same_case_is_independently_resolved(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host5@test.com", renter_email="renter5@test.com")

        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "DISCRIMINATION", "claimFamily": "PROTECTED_SAFETY"},
            cookies=auth_user_cookie(renter),
        )
        assert r.status_code == 201, r.text

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=auth_user_cookie(renter))
        body = r.json()
        # Case-level status reflects the fail-closed claim, but the first
        # claim's own resolution is untouched by the second claim's outcome.
        assert body["status"] == "LEGAL_REVIEW_REQUIRED"
        assert len(body["claims"]) == 2
        deposit_claim = next(c for c in body["claims"] if c["claimFamily"] == "DEPOSIT")
        assert deposit_claim["authorityClass"] == "A2"


class TestHostIntake:
    def test_host_opens_a_platform_fee_case_a0(self, client, db_session: Session):
        host, _renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host6@test.com", renter_email="renter6@test.com")

        r = client.post(
            "/api/users/hosting/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 90}},
            cookies=auth_user_cookie(host),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["claims"][0]["authorityClass"] == "A0"
        assert body["severity"] == "SEV-3"

    def test_host_cannot_open_a_case_on_a_different_hosts_occupancy(self, client, db_session: Session):
        _host, _renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host7@test.com", renter_email="renter7@test.com")
        other_host_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(other_host_party)
        db_session.flush()
        other_host = _make_user(db_session, email="otherhost@test.com")
        other_host.party_id = other_host_party.id
        db_session.commit()

        r = client.post(
            "/api/users/hosting/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "DAMAGE", "claimFamily": "PROPERTY_CONDITION"}},
            cookies=auth_user_cookie(other_host),
        )
        assert r.status_code == 403, r.text


class TestAdminDecisionAndCloseGuards:
    def test_admin_cannot_internally_decide_an_external_only_claim(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host8@test.com", renter_email="renter8@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        claim_id = r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="admin-disputes1@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "evidence_clear"},
            cookies=auth_admin_cookie(admin),
        )
        # AC-6: an A2 (deposit-scheme) claim is external-only -- Admin cannot decide it.
        assert r.status_code == 409, r.text

    def test_close_case_blocked_while_a_claim_is_open(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host9@test.com", renter_email="renter9@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="admin-disputes2@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text

    def test_a0_claim_full_decide_then_close_happy_path(self, client, db_session: Session):
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host10@test.com", renter_email="renter10@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]
        claim_id = r.json()["claims"][0]["id"]

        admin = _make_admin(db_session, email="admin-disputes3@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/claims/{claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"},
            cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "UPHELD"

        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "CLOSED"
        assert body["closedAt"] is not None

    def test_force_close_refused_without_a_reason(self, client, db_session: Session):
        """QA-Q45: the AC-24 guard is still the default -- omitting
        force_close_reason behaves exactly as before."""
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host11@test.com", renter_email="renter11@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="admin-disputes4@test.com", role="super_admin")
        r = client.post(f"/api/admin/disputes/{case_id}/close", json={"forceCloseReason": ""}, cookies=auth_admin_cookie(admin))
        assert r.status_code == 409, r.text

    def test_force_close_refused_when_the_pending_claim_is_not_externally_referred(self, client, db_session: Session):
        """A non-externally-referred (still internally-worked) claim can
        never be skipped via force_close_reason -- only genuine
        awaiting-an-external-scheme claims qualify."""
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host12@test.com", renter_email="renter12@test.com")
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=auth_user_cookie(renter),
        )
        case_id = r.json()["id"]

        admin = _make_admin(db_session, email="admin-disputes5@test.com", role="super_admin")
        r = client.post(
            f"/api/admin/disputes/{case_id}/close", json={"forceCloseReason": "stuck externally"}, cookies=auth_admin_cookie(admin),
        )
        assert r.status_code == 409, r.text

    def test_force_close_succeeds_while_a_claim_awaits_an_open_external_proceeding(self, client, db_session: Session):
        """QA-Q45: partial closure -- one claim resolved internally, the
        other stuck at an open external proceeding with no ETA. The case
        closes and records why."""
        _host, renter, occupancy = _make_occupancy_with_parties(db_session, host_email="host13@test.com", renter_email="renter13@test.com")
        renter_cookies = auth_user_cookie(renter)
        r = client.post(
            "/api/users/rentals/disputes",
            json={"occupancyId": occupancy.id, "claim": {"claimCode": "PLATFORM_FEE", "claimFamily": "ZOIKO_SERVICE", "amount": 50}},
            cookies=renter_cookies,
        )
        case_id = r.json()["id"]
        internal_claim_id = r.json()["claims"][0]["id"]

        r = client.post(
            f"/api/users/rentals/disputes/{case_id}/claims",
            json={"claimCode": "DEDUCTION", "claimFamily": "DEPOSIT", "amount": 600},
            cookies=renter_cookies,
        )
        assert r.status_code == 201, r.text
        external_claim_id = r.json()["id"]

        admin = _make_admin(db_session, email="admin-disputes6@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)
        r = client.post(
            f"/api/admin/disputes/claims/{internal_claim_id}/decision",
            json={"outcome": "UPHELD", "reasonCode": "fee_waived"}, cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(
            f"/api/admin/disputes/{case_id}/external-proceedings",
            json={"authorityType": "DEPOSIT_SCHEME", "claimIds": [external_claim_id]},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text

        # Still refused without an explicit reason.
        r = client.post(f"/api/admin/disputes/{case_id}/close", cookies=admin_cookies)
        assert r.status_code == 409, r.text

        r = client.post(
            f"/api/admin/disputes/{case_id}/close",
            json={"forceCloseReason": "Deposit scheme has no published ETA; closing internally-resolved matters now"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "CLOSED"
        assert body["partialClosureReason"] == "Deposit scheme has no published ETA; closing internally-resolved matters now"

        r = client.get(f"/api/users/rentals/disputes/{case_id}", cookies=renter_cookies)
        claims_by_id = {c["id"]: c for c in r.json()["claims"]}
        assert claims_by_id[external_claim_id]["status"] == "EXTERNAL_REFERRAL"
        assert claims_by_id[internal_claim_id]["status"] == "UPHELD"
