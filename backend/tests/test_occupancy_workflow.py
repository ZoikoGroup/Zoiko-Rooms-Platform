"""Direct tests for confirm_move_in()/end_occupancy()'s own business logic --
eligibility gating, idempotency, provider/admin authorization, and correct
notification behavior. test_availability.py already covers occupancy's effect
on listing visibility, and test_notification_coverage.py already covers
notification-type/routing wiring in isolation via direct crud calls -- this
file is the one direct, end-to-end (real API) test of confirm_move_in and
end_occupancy themselves that was missing (see the Section 9 gap analysis).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity_verification import IdentityVerification
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.notification import Notification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_renter_offer_agreement_flow import _make_agreement_eligible, _submit_and_approve_application


def _make_host_and_verified_renter_with_published_listing(db: Session, *, suffix: str):
    """Same shape as test_application_workflow.py's
    _make_verified_renter_with_published_listing, but the provider party also
    has a real UserAccount -- needed here (unlike that file) because these
    tests assert on the HOST's own notifications, not just the renter's."""
    renter_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(renter_party)
    db.flush()
    renter_user = _make_user(db, email=f"occ-renter-{suffix}@test.com")
    renter_user.party_id = renter_party.id
    db.flush()
    db.add(
        IdentityVerification(
            party_id=renter_party.id, document_type="passport", document_category="identity", status="verified",
        )
    )

    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(provider_party)
    db.flush()
    host_user = _make_user(db, email=f"occ-host-{suffix}@test.com")
    host_user.party_id = provider_party.id
    db.flush()

    prop = Property(owner_party_id=provider_party.id, address="1 Occupancy St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    listing = Listing(
        id=f"L-OCC-{suffix}", slug=f"occ-{suffix}", name=f"Occupancy Test Listing {suffix}",
        room_type="Private room", city="Bengaluru", location="Koramangala", price_per_night=500,
        guests=1, rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None,
        room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return host_user, renter_user, listing.id


def _build_signed_agreement(client, db: Session, suffix: str, *, pay_obligations: bool = True):
    """Drives the real API from a published listing through a SIGNED agreement
    -- confirm_move_in's precondition -- mirroring
    test_renter_offer_agreement_flow.py's own end-to-end path exactly (offer
    sent/accepted by the renter's own session, agreement sent/signed by both
    sides). pay_obligations=False leaves the initial RENT+DEPOSIT obligations
    PENDING, for the eligibility-rejection test."""
    host_user, renter_user, listing_id = _make_host_and_verified_renter_with_published_listing(db, suffix=suffix)
    renter_cookies = auth_user_cookie(renter_user)
    application_id, admin_cookies = _submit_and_approve_application(client, db, renter_user, listing_id)

    r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(
        f"/api/leasing/offers/{offer_id}/terms",
        json={"monthlyRent": 500, "depositAmount": 500, "startDate": date.today().isoformat(), "termMonths": 6},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=renter_cookies)
    assert r.status_code == 200, r.text

    _make_agreement_eligible(db, listing_id)
    r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
    assert r.status_code == 200, r.text
    agreement_id = r.json()["id"]

    r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=renter_cookies)
    assert r.status_code == 200, r.text

    r = client.post(f"/api/leasing/agreements/{agreement_id}/sign", json={"asParty": "provider"}, cookies=admin_cookies)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SIGNED"

    agreement = db.get(Agreement, agreement_id)
    if pay_obligations:
        for obligation in agreement.obligations:
            obligation.status = "PAID"
        db.commit()
        db.refresh(agreement)

    return agreement, host_user, renter_user, listing_id, admin_cookies


class TestConfirmMoveIn:
    def test_successful_move_in_creates_one_active_occupancy(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "success")

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ACTIVE"
        assert body["moveInDate"] == date.today().isoformat()
        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1

    def test_existing_occupancy_is_returned_idempotently(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "idempotent")

        r1 = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r1.status_code == 200, r1.text
        first_occupancy_id = r1.json()["id"]

        r2 = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r2.status_code == 200, r2.text
        assert r2.json()["id"] == first_occupancy_id

        occupancies = db_session.scalars(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)).all()
        assert len(occupancies) == 1

    def test_move_in_rejected_when_eligibility_fails(self, client, db_session: Session):
        # Obligations left PENDING (never paid) -> check_move_in_eligibility must reject.
        agreement, _host, _renter, _listing_id, admin_cookies = _build_signed_agreement(
            client, db_session, "ineligible", pay_obligations=False,
        )

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)

        assert r.status_code == 409, r.text
        assert "not fully paid" in r.text.lower() or "not eligible" in r.text.lower()
        assert db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)) is None

    def test_authorization_is_enforced(self, client, db_session: Session):
        agreement, _host, _renter, _listing_id, _admin_cookies = _build_signed_agreement(client, db_session, "authz")
        outsider = _make_admin(db_session, email="occ-confirm-outsider@test.com", role="admin")
        db_session.commit()

        r = client.post(
            f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=auth_admin_cookie(outsider),
        )

        assert r.status_code == 403, r.text
        assert db_session.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id)) is None

    def test_renter_and_host_notifications_are_generated_with_exactly_one_renter_notice(self, client, db_session: Session):
        agreement, host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db_session, "notify")

        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        occupancy_id = r.json()["id"]

        renter_notices = db_session.scalars(
            select(Notification).where(
                Notification.recipient_user_id == renter.id,
                Notification.notification_type == "occupancy.move_in_confirmed",
            )
        ).all()
        assert len(renter_notices) == 1, "exactly one renter move-in notification must exist"
        assert renter_notices[0].related_entity_id == str(occupancy_id)

        host_notice = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host.id,
                Notification.notification_type == "occupancy.move_in_confirmed_for_host",
            )
        )
        assert host_notice is not None
        assert host_notice.related_entity_id == str(occupancy_id)


class TestEndOccupancy:
    def _active_occupancy(self, client, db: Session, suffix: str):
        agreement, host, renter, _listing_id, admin_cookies = _build_signed_agreement(client, db, suffix)
        r = client.post(f"/api/occupancy/agreements/{agreement.id}/confirm-move-in", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        return r.json()["id"], host, renter, admin_cookies

    def test_active_occupancy_ended_by_authorized_provider_becomes_ended(self, client, db_session: Session):
        occupancy_id, _host, _renter, admin_cookies = self._active_occupancy(client, db_session, "end-ok")

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=admin_cookies)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ENDED"
        assert body["moveOutDate"] == date.today().isoformat()

        occupancy = db_session.get(Occupancy, occupancy_id)
        assert occupancy.status == "ENDED"
        assert occupancy.move_out_date == date.today()
        assert occupancy.ended_at is not None

    def test_end_occupancy_notifies_renter_and_host(self, client, db_session: Session):
        occupancy_id, host, renter, admin_cookies = self._active_occupancy(client, db_session, "end-notify")

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        assert db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == renter.id, Notification.notification_type == "occupancy.ended",
            )
        ) is not None
        assert db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == host.id, Notification.notification_type == "occupancy.ended_for_host",
            )
        ) is not None

    def test_unauthorized_admin_cannot_end_another_providers_occupancy(self, client, db_session: Session):
        occupancy_id, _host, _renter, _admin_cookies = self._active_occupancy(client, db_session, "end-authz")
        outsider = _make_admin(db_session, email="occ-end-outsider@test.com", role="admin")
        db_session.commit()

        r = client.post(f"/api/occupancy/{occupancy_id}/end", cookies=auth_admin_cookie(outsider))

        assert r.status_code == 403, r.text
        occupancy = db_session.get(Occupancy, occupancy_id)
        assert occupancy.status == "ACTIVE"
