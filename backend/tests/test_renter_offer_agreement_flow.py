"""The renter previously had no way to see their own offer, accept/decline it
themselves, or sign their own agreement -- every one of those steps was
admin-only, with an admin attesting the renter's "signature" on their behalf.
These tests exercise the new renter-facing endpoints end to end."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.authority_record import AuthorityRecord
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.notification import Notification
from app.models.occupancy_classification import OccupancyClassification
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie
from tests.test_application_workflow import _make_verified_renter_with_published_listing


def _make_agreement_eligible(db: Session, listing_id: str) -> None:
    """Agreement creation additionally requires an active market release, a
    verified authority record for the room, and a resolved occupancy
    classification -- satisfy all three so the eligibility check passes."""
    listing = db.get(Listing, listing_id)
    room = db.get(Room, listing.room_id)

    release = MarketRelease(jurisdiction="IN-TEST", status="active")
    db.add(release)
    db.flush()
    listing.market_release_id = release.id

    db.add(
        AuthorityRecord(
            party_id=listing.party_id or 1,
            room_id=room.id,
            authority_type="lease_agreement",
            status="verified",
        )
    )
    db.add(OccupancyClassification(room_id=room.id, classification="shared_residential_room", review_state="APPROVED"))
    db.commit()


def _submit_and_approve_application(client, db_session: Session, user, listing_id):
    cookies = auth_user_cookie(user)
    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "Interested", "desiredMoveIn": None},
        cookies=cookies,
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]

    super_admin = _make_admin(db_session, email="decider@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(super_admin)
    r = client.post(
        f"/api/leasing/applications/{application_id}/decide",
        json={"decision": "APPROVED"},
        cookies=admin_cookies,
    )
    assert r.status_code == 200, r.text
    return application_id, admin_cookies


class TestRenterOfferFlow:
    def test_renter_can_view_accept_offer_and_sign_own_agreement(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="renter-offer@test.com")
        user_cookies = auth_user_cookie(user)
        application_id, admin_cookies = _submit_and_approve_application(client, db_session, user, listing_id)

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        offer_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={
                "monthlyRent": 500,
                "depositAmount": 500,
                "startDate": (date.today() + timedelta(days=5)).isoformat(),
                "termMonths": 6,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        # Before the offer is sent, the renter can't act on it yet.
        r = client.get(f"/api/users/rentals/applications/{application_id}/offer", cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "DRAFT"

        r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "offer.sent",
            )
        )
        assert notification is not None

        # The renter accepts their own offer -- not an admin acting for them.
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"

        _make_agreement_eligible(db_session, listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        agreement_id = r.json()["id"]

        r = client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)
        assert r.status_code == 200, r.text

        agreement_notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "agreement.sent",
            )
        )
        assert agreement_notification is not None

        # The renter signs their own agreement.
        r = client.post(f"/api/users/rentals/agreements/{agreement_id}/sign", cookies=user_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["signedByRenterAt"] is not None
        assert r.json()["status"] == "SENT"  # provider hasn't signed yet

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SIGNED"

        signed_notification = db_session.scalar(
            select(Notification).where(
                Notification.recipient_user_id == user.id,
                Notification.notification_type == "agreement.signed",
            )
        )
        assert signed_notification is not None

    def test_renter_cannot_accept_someone_elses_offer(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="renter-offer2@test.com")
        application_id, admin_cookies = _submit_and_approve_application(client, db_session, user, listing_id)

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]
        client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 500, "depositAmount": 500, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)

        other_user = _make_user(db_session, email="not-the-renter@test.com")
        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(other_user))
        assert r.status_code == 403, r.text

    def test_offer_cannot_be_accepted_before_it_is_sent(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="renter-offer3@test.com")
        user_cookies = auth_user_cookie(user)
        application_id, admin_cookies = _submit_and_approve_application(client, db_session, user, listing_id)

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies)
        assert r.status_code == 409, r.text


class TestAdminCannotActForARenterWithARealAccount:
    """An admin may still accept/decline offers and sign agreements on behalf of
    a walk-in guest with no Zoiko login -- but for a renter who does have an
    account, that consent has to come from the renter's own session, not an
    admin attesting it happened."""

    def test_admin_cannot_accept_offer_for_a_renter_with_an_account(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="renter-restrict1@test.com")
        application_id, admin_cookies = _submit_and_approve_application(client, db_session, user, listing_id)

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]
        client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 500, "depositAmount": 500, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)

        r = client.post(f"/api/leasing/offers/{offer_id}/accept", cookies=admin_cookies)
        assert r.status_code == 409, r.text

    def test_admin_cannot_sign_as_renter_for_a_renter_with_an_account(self, client, db_session: Session):
        user, listing_id = _make_verified_renter_with_published_listing(db_session, email="renter-restrict2@test.com")
        user_cookies = auth_user_cookie(user)
        application_id, admin_cookies = _submit_and_approve_application(client, db_session, user, listing_id)

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]
        client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 500, "depositAmount": 500, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=user_cookies)

        _make_agreement_eligible(db_session, listing_id)
        r = client.post(f"/api/leasing/offers/{offer_id}/agreement", cookies=admin_cookies)
        agreement_id = r.json()["id"]
        client.post(f"/api/leasing/agreements/{agreement_id}/send", cookies=admin_cookies)

        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "renter"},
            cookies=admin_cookies,
        )
        assert r.status_code == 409, r.text

        # Signing as provider is unaffected -- that's always legitimately admin-side.
        r = client.post(
            f"/api/leasing/agreements/{agreement_id}/sign",
            json={"asParty": "provider"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

    def test_admin_can_still_accept_offer_for_a_walk_in_guest(self, client, db_session: Session):
        from tests.test_application_workflow import Party, Property, Room, Listing
        from app.models.identity_verification import IdentityVerification

        provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
        db_session.add(provider_party)
        db_session.flush()
        prop = Property(owner_party_id=provider_party.id, address="1 Walkin St", city="Bengaluru", status="active")
        db_session.add(prop)
        db_session.flush()
        room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
        db_session.add(room)
        db_session.flush()
        listing = Listing(
            id="L-WALKIN1", slug="walkin1", name="Walk-in Listing", room_type="Private room",
            city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
            rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None, room_id=room.id,
            state="PUBLISHED",
        )
        db_session.add(listing)
        db_session.commit()

        super_admin = _make_admin(db_session, email="walkin-admin@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        r = client.post(
            "/api/leasing/applications",
            json={"listingId": listing.id, "newGuest": {"name": "Walk-in Guest", "email": "walkin-guest@test.com"}},
            cookies=admin_cookies,
        )
        assert r.status_code == 201, r.text
        application_id = r.json()["id"]

        r = client.post(
            f"/api/leasing/applications/{application_id}/decide",
            json={"decision": "APPROVED"},
            cookies=admin_cookies,
        )
        assert r.status_code == 200, r.text

        r = client.post(f"/api/leasing/applications/{application_id}/offers", cookies=admin_cookies)
        offer_id = r.json()["id"]
        client.post(
            f"/api/leasing/offers/{offer_id}/terms",
            json={"monthlyRent": 500, "depositAmount": 500, "startDate": (date.today() + timedelta(days=5)).isoformat(), "termMonths": 6},
            cookies=admin_cookies,
        )
        client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)

        r = client.post(f"/api/leasing/offers/{offer_id}/accept", cookies=admin_cookies)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "ACCEPTED"
