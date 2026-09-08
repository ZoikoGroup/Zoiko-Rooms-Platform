"""Tests for the renter self-service agreement signing flow -- the renter has
their own UserAccount login (unlike the docstring on the older admin-attested
sign_agreement path assumed), so they can view and sign their own agreement
directly instead of an admin recording it on their behalf.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_application_with_offer(db: Session, *, agreement_status: str | None = None):
    """A DECIDED application with an ACCEPTED offer for a renter who has their
    own UserAccount, and (optionally) an Agreement at the given status.
    Returns (application, offer, agreement_or_none, renter_user, admin)."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()

    prop = Property(owner_party_id=owner_party.id, address="1 Test St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    admin = _make_admin(db, email="host-admin@test.com", role="super_admin")

    renter_user = _make_user(db, email="renter@test.com")
    renter_guest = Guest(
        id="G-RENTERSIGN", name="Renter", email="renter@test.com", joined_at=date.today(),
        user_account_id=renter_user.id,
    )
    db.add(renter_guest)
    db.flush()
    renter_user.party_id = None  # renter has no party of their own -- guest_id is the link used by ownership checks

    listing = Listing(
        id="L-SIGNTEST", slug="signtest", name="Sign Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=0.0, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id,
        state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=renter_guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=renter_guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()

    agreement = None
    if agreement_status is not None:
        agreement = Agreement(offer_id=offer.id, status=agreement_status)
        db.add(agreement)
        db.flush()

    db.commit()
    return application, offer, agreement, renter_user, admin


class TestGetApplicationAgreement:
    def test_returns_none_before_agreement_exists(self, client, db_session: Session):
        application, _offer, _agreement, renter_user, _admin = _make_application_with_offer(db_session)

        resp = client.get(
            f"/api/users/rentals/applications/{application.id}/agreement",
            cookies=auth_user_cookie(renter_user),
        )
        assert resp.status_code == 200
        assert resp.json() is None

    def test_returns_agreement_once_sent(self, client, db_session: Session):
        application, _offer, agreement, renter_user, _admin = _make_application_with_offer(
            db_session, agreement_status="SENT"
        )

        resp = client.get(
            f"/api/users/rentals/applications/{application.id}/agreement",
            cookies=auth_user_cookie(renter_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == agreement.id
        assert body["status"] == "SENT"

    def test_other_renter_cannot_view(self, client, db_session: Session):
        application, _offer, _agreement, _renter_user, _admin = _make_application_with_offer(
            db_session, agreement_status="SENT"
        )
        other_user = _make_user(db_session, email="other-renter@test.com")
        db_session.commit()

        resp = client.get(
            f"/api/users/rentals/applications/{application.id}/agreement",
            cookies=auth_user_cookie(other_user),
        )
        assert resp.status_code == 403


class TestSignApplicationAgreement:
    def test_renter_cannot_sign_before_agreement_sent(self, client, db_session: Session):
        application, _offer, _agreement, renter_user, _admin = _make_application_with_offer(
            db_session, agreement_status="DRAFT"
        )

        resp = client.post(
            f"/api/users/rentals/applications/{application.id}/agreement/sign",
            cookies=auth_user_cookie(renter_user),
        )
        assert resp.status_code == 409

    def test_renter_sign_records_timestamp_but_stays_sent_until_provider_signs(self, client, db_session: Session):
        application, _offer, agreement, renter_user, _admin = _make_application_with_offer(
            db_session, agreement_status="SENT"
        )

        resp = client.post(
            f"/api/users/rentals/applications/{application.id}/agreement/sign",
            cookies=auth_user_cookie(renter_user),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["signedByRenterAt"] is not None
        assert body["signedByProviderAt"] is None
        assert body["status"] == "SENT"

    def test_agreement_flips_to_signed_once_both_parties_have_signed(self, client, db_session: Session):
        application, offer, agreement, renter_user, admin = _make_application_with_offer(
            db_session, agreement_status="SENT"
        )

        provider_resp = client.post(
            f"/api/leasing/agreements/{agreement.id}/sign",
            json={"asParty": "provider"},
            cookies=auth_admin_cookie(admin),
        )
        assert provider_resp.status_code == 200
        assert provider_resp.json()["status"] == "SENT"

        renter_resp = client.post(
            f"/api/users/rentals/applications/{application.id}/agreement/sign",
            cookies=auth_user_cookie(renter_user),
        )
        assert renter_resp.status_code == 200
        assert renter_resp.json()["status"] == "SIGNED"

    def test_other_renter_cannot_sign(self, client, db_session: Session):
        application, _offer, _agreement, _renter_user, _admin = _make_application_with_offer(
            db_session, agreement_status="SENT"
        )
        other_user = _make_user(db_session, email="other-renter-2@test.com")
        db_session.commit()

        resp = client.post(
            f"/api/users/rentals/applications/{application.id}/agreement/sign",
            cookies=auth_user_cookie(other_user),
        )
        assert resp.status_code == 403

    def test_cannot_sign_when_no_agreement_yet(self, client, db_session: Session):
        application, _offer, _agreement, renter_user, _admin = _make_application_with_offer(db_session)

        resp = client.post(
            f"/api/users/rentals/applications/{application.id}/agreement/sign",
            cookies=auth_user_cookie(renter_user),
        )
        assert resp.status_code == 404
