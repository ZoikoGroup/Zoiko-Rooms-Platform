"""Tests for sublet approval/rejection (including the related-entity notification
fix) and finance reconciliation/payout safeguards -- coverage gaps identified
against the Anil task list (sections 5, 9, 10): sublet decisions must notify
with a deep-linkable entity id, reconciliation must stay super-admin-only, and
a duplicate payout run must fail loudly rather than silently succeeding twice.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.sublet_request import SubletRequest
from tests.conftest import _make_admin, _make_user, auth_admin_cookie


def _make_active_tenancy_with_sublet_request(db: Session):
    """A signed, active occupancy with 60 days remaining -- eligible for
    subletting -- plus a pending sublet request naming a verified proposed
    renter. Returns (sublet_request, current_tenant_user, proposed_user, occupancy)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()

    prop = Property(owner_party_id=owner_party.id, address="1 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    tenant_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(tenant_party)
    db.flush()
    tenant_user = _make_user(db, email="tenant@test.com")
    tenant_user.party_id = tenant_party.id
    tenant_guest = Guest(id="G-TENANT", name="Tenant", email="tenant@test.com", joined_at=date.today())
    db.add(tenant_guest)
    db.flush()

    listing = Listing(
        id="L-SUBLETTEST", slug="sublettest", name="Sublet Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id,
        state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=tenant_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=tenant_guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()
    occupancy = Occupancy(
        offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=tenant_guest.id,
        status="ACTIVE", expected_end_date=date.today() + timedelta(days=60),
    )
    db.add(occupancy)
    db.flush()

    proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(proposed_party)
    db.flush()
    proposed_user = _make_user(db, email="proposed@test.com")
    proposed_user.party_id = proposed_party.id
    db.add(
        IdentityVerification(
            party_id=proposed_party.id, document_type="passport", document_category="identity", status="verified",
        )
    )
    db.flush()

    sublet_request = SubletRequest(
        current_occupancy_id=occupancy.id,
        proposed_renter_party_id=proposed_party.id,
        status="pending_admin_review",
    )
    db.add(sublet_request)
    db.commit()

    return sublet_request, tenant_user, proposed_user, occupancy


class TestSubletDecisionNotifications:
    def test_approving_a_sublet_request_notifies_requester_with_entity_ref(self, client, db_session: Session):
        sublet_request, tenant_user, _proposed_user, occupancy = _make_active_tenancy_with_sublet_request(db_session)
        admin = _make_admin(db_session, email="super@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "approved"

        db_session.refresh(occupancy)
        assert occupancy.guest_id != "G-TENANT"  # reassigned to the proposed renter's guest record

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == tenant_user.id,
                Notification.notification_type == "sublet_request.approved",
            )
        )
        assert notification is not None
        assert notification.related_entity_type == "sublet_request"
        assert notification.related_entity_id == str(sublet_request.id)

    def test_rejecting_a_sublet_request_notifies_requester_with_entity_ref(self, client, db_session: Session):
        sublet_request, tenant_user, _proposed_user, occupancy = _make_active_tenancy_with_sublet_request(db_session)
        admin = _make_admin(db_session, email="super2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/reject", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

        db_session.refresh(occupancy)
        assert occupancy.guest_id == "G-TENANT"  # unchanged on rejection

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == tenant_user.id,
                Notification.notification_type == "sublet_request.rejected",
            )
        )
        assert notification is not None
        assert notification.related_entity_type == "sublet_request"
        assert notification.related_entity_id == str(sublet_request.id)

    def test_plain_admin_cannot_approve_sublet_requests(self, client, db_session: Session):
        sublet_request, _tenant_user, _proposed_user, _occupancy = _make_active_tenancy_with_sublet_request(db_session)
        admin = _make_admin(db_session, email="plainadmin@test.com", role="admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post(f"/api/occupancy/sublet-requests/{sublet_request.id}/approve", cookies=admin_cookies)
        assert r.status_code == 403, r.text


class TestSubletHostVisibility:
    """The listing's own host (a self-hosting UserAccount tied to the property's
    owning party) previously heard nothing about a sublet on their own room and
    saw only raw IDs when reviewing one -- both are fixed here."""

    def test_reviewer_payload_includes_room_and_people_context(self, client, db_session: Session):
        sublet_request, _tenant_user, proposed_user, _occupancy = _make_active_tenancy_with_sublet_request(db_session)
        admin = _make_admin(db_session, email="reviewer@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.get("/api/occupancy/sublet-requests", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        body = next(sr for sr in r.json() if sr["id"] == sublet_request.id)
        assert body["listingName"] == "Sublet Test Listing"
        assert body["listingCity"] == "Bengaluru"
        assert body["currentTenantName"] == "Tenant"
        assert body["proposedRenterName"] == proposed_user.full_name

    def test_host_is_notified_on_submit_and_decision(self, db_session: Session):
        from app.crud import sublet as sublet_crud
        from tests.conftest import _make_user as make_user

        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.flush()
        host_user = make_user(db_session, email="host@test.com")
        host_user.party_id = owner_party.id

        prop = Property(owner_party_id=owner_party.id, address="1 Host St", city="Bengaluru", status="active")
        db_session.add(prop)
        db_session.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(room)
        db_session.flush()

        tenant_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(tenant_party)
        db_session.flush()
        tenant_user = make_user(db_session, email="hosttest-tenant@test.com")
        tenant_user.party_id = tenant_party.id
        tenant_guest = Guest(id="G-HOSTTEST-TENANT", name="Tenant", email="hosttest-tenant@test.com", joined_at=date.today())
        db_session.add(tenant_guest)
        db_session.flush()

        listing = Listing(
            id="L-HOSTVIS", slug="hostvis", name="Host Visibility Listing", room_type="Private room",
            city="Bengaluru", location="Indiranagar", price_per_night=500, guests=1,
            rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id,
            state="PUBLISHED",
        )
        db_session.add(listing)
        db_session.flush()
        application = Application(listing_id=listing.id, guest_id=tenant_guest.id, status="DECIDED")
        db_session.add(application)
        db_session.flush()
        offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=tenant_guest.id, status="ACCEPTED")
        db_session.add(offer)
        db_session.flush()
        agreement = Agreement(offer_id=offer.id, status="SIGNED")
        db_session.add(agreement)
        db_session.flush()
        occupancy = Occupancy(
            offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=tenant_guest.id,
            status="ACTIVE", expected_end_date=date.today() + timedelta(days=60),
        )
        db_session.add(occupancy)
        db_session.commit()

        proposed_party = Party(party_type="renter", status="active", jurisdiction="IN")
        db_session.add(proposed_party)
        db_session.flush()
        make_user(db_session, email="hosttest-proposed@test.com").party_id = proposed_party.id
        db_session.add(
            IdentityVerification(
                party_id=proposed_party.id, document_type="passport", document_category="identity", status="verified"
            )
        )
        db_session.commit()

        sublet_request = sublet_crud.submit_sublet_request(db_session, tenant_user, occupancy.id, proposed_party.id)

        submitted_notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host_user.id,
                Notification.notification_type == "sublet_request.submitted",
            )
        )
        assert submitted_notification is not None
        assert submitted_notification.related_entity_id == str(sublet_request.id)

        admin = _make_admin(db_session, email="hostvis-admin@test.com", role="super_admin")
        sublet_crud.approve_sublet_request(db_session, sublet_request, admin)

        approved_notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host_user.id,
                Notification.notification_type == "sublet_request.approved",
            )
        )
        assert approved_notification is not None
        assert approved_notification.related_entity_id == str(sublet_request.id)


class TestFinanceAdminGating:
    def test_plain_admin_cannot_run_reconciliation(self, client, db_session: Session):
        admin = _make_admin(db_session, email="plainadmin2@test.com", role="admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 403, r.text

    def test_super_admin_can_run_reconciliation(self, client, db_session: Session):
        admin = _make_admin(db_session, email="superfin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        r = client.post("/api/finance/reconciliation/run", cookies=admin_cookies)
        assert r.status_code == 200, r.text


class TestDuplicatePayoutRunFailsLoudly:
    def test_second_payout_run_for_same_party_and_period_is_rejected_not_silently_succeeded(
        self, client, db_session: Session
    ):
        owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(owner_party)
        db_session.commit()

        admin = _make_admin(db_session, email="payoutadmin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(admin)

        payload = {"partyId": owner_party.id, "periodKey": "2026-01"}
        r1 = client.post("/api/finance/payouts/run", json=payload, cookies=admin_cookies)
        assert r1.status_code == 200, r1.text

        r2 = client.post("/api/finance/payouts/run", json=payload, cookies=admin_cookies)
        assert r2.status_code == 409, r2.text
