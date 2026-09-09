"""ZR-ENG-CLR-001 Section 1, Rule 4 / AC-04 / AC-05: the Inventory Service's
atomic room hold (app/services/inventory.py, app/models/room_hold.py).

Before this existed, nothing reserved a room between an offer being accepted
and its Occupancy being created at move-in (Occupancy is only created once an
agreement is signed -- see crud/occupancy.py). Two different renters could
each get an offer on the same listing/room, and both could have their offers
accepted -- only the first to reach move-in would "win", but the second
accept would have silently succeeded too, corrupting state. These tests prove
that's no longer possible, and that a hold is correctly created/queried/
released through the real HTTP flow.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import leasing as leasing_crud
from app.crud import occupancy as occupancy_crud
from app.models.authority_record import AuthorityRecord
from app.models.finance import Obligation
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Offer
from app.models.listing import Listing
from app.models.market_release import MarketRelease
from app.models.occupancy_classification import OccupancyClassification
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.room_hold import RoomHold
from app.schemas.leasing import OfferTermsCreate
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie


def _make_listing_with_room(db: Session) -> tuple[str, int]:
    provider_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(provider_party)
    db.flush()
    prop = Property(owner_party_id=provider_party.id, address="1 Hold St", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id="L-HOLDTEST1", slug="holdtest1", name="Hold Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=provider_party.id, owner_id=None,
        room_id=room.id, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return listing.id, room.id


def _make_verified_renter(db: Session, *, email: str):
    renter_party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(renter_party)
    db.flush()
    user = _make_user(db, email=email)
    user.party_id = renter_party.id
    db.add(IdentityVerification(party_id=renter_party.id, document_type="passport", status="verified"))
    db.commit()
    return user


def _apply_and_send_offer(client, db_session: Session, user, listing_id: str, admin_cookies: dict) -> tuple[int, int]:
    """Submit an application, approve it, create + send an offer. Returns (application_id, offer_id)."""
    r = client.post(
        "/api/users/rentals/applications",
        json={"listingId": listing_id, "message": "Interested", "desiredMoveIn": None},
        cookies=auth_user_cookie(user),
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
    assert r.status_code == 200, r.text
    offer_id = r.json()["id"]

    r = client.post(f"/api/leasing/offers/{offer_id}/send", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    return application_id, offer_id


class TestAcceptingOfferCreatesHold:
    def test_accepting_offer_creates_a_held_room_hold(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="hold-admin1@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter = _make_verified_renter(db_session, email="hold-renter1@test.com")

        _application_id, offer_id = _apply_and_send_offer(client, db_session, renter, listing_id, admin_cookies)

        r = client.post(f"/api/users/rentals/offers/{offer_id}/accept", cookies=auth_user_cookie(renter))
        assert r.status_code == 200, r.text

        hold = db_session.scalar(select(RoomHold).where(RoomHold.room_id == room_id))
        assert hold is not None
        assert hold.status == "HELD"
        assert hold.source_type == "offer"
        assert hold.source_id == offer_id
        assert hold.released_at is None


class TestConcurrentAcceptanceIsBlocked:
    def test_second_offer_on_same_room_cannot_be_accepted_once_first_is(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="hold-admin2@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        renter_a = _make_verified_renter(db_session, email="hold-renter-a@test.com")
        renter_b = _make_verified_renter(db_session, email="hold-renter-b@test.com")

        _app_a, offer_a = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)
        _app_b, offer_b = _apply_and_send_offer(client, db_session, renter_b, listing_id, admin_cookies)

        r = client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter_a))
        assert r.status_code == 200, r.text

        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter_b))
        assert r.status_code == 409, r.text
        assert "already held" in r.text.lower()

        # The rejected acceptance must not have partially applied -- offer B's
        # status stays SENT, not silently ACCEPTED, and exactly one active hold
        # exists for the room.
        db_session.expire_all()
        offer_b_row = db_session.get(Offer, offer_b)
        assert offer_b_row.status == "SENT"

        active_holds = db_session.scalars(
            select(RoomHold).where(RoomHold.room_id == room_id, RoomHold.released_at.is_(None))
        ).all()
        assert len(active_holds) == 1
        assert active_holds[0].source_id == offer_a


class TestHeldRoomIsUnavailableForNewApplications:
    def test_new_application_is_rejected_once_room_is_held(self, client, db_session: Session):
        listing_id, _room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="hold-admin3@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)

        renter_a = _make_verified_renter(db_session, email="hold-renter-c@test.com")
        _app_a, offer_a = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)
        client.post(f"/api/users/rentals/offers/{offer_a}/accept", cookies=auth_user_cookie(renter_a))

        renter_c = _make_verified_renter(db_session, email="hold-renter-d@test.com")
        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "Also interested", "desiredMoveIn": None},
            cookies=auth_user_cookie(renter_c),
        )
        assert r.status_code == 409, r.text


class TestDecliningOfferBeforeAcceptanceIsANoOp:
    def test_declining_a_sent_offer_never_touches_any_hold(self, client, db_session: Session):
        """No hold exists yet at SENT (holds are only created on acceptance)
        -- declining must not error trying to release a nonexistent one, and
        the room must remain free."""
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="hold-admin4@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter_a = _make_verified_renter(db_session, email="hold-renter-e@test.com")

        _app_a, offer_a = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)
        r = client.post(f"/api/users/rentals/offers/{offer_a}/decline", cookies=auth_user_cookie(renter_a))
        assert r.status_code == 200, r.text

        assert db_session.scalar(select(RoomHold).where(RoomHold.room_id == room_id)) is None


class TestEndingOccupancyReleasesHold:
    def test_full_lifecycle_releases_hold_and_frees_room_for_a_new_tenant(self, client, db_session: Session):
        listing_id, room_id = _make_listing_with_room(db_session)
        super_admin = _make_admin(db_session, email="hold-admin5@test.com", role="super_admin")
        admin_cookies = auth_admin_cookie(super_admin)
        renter_a = _make_verified_renter(db_session, email="hold-renter-g@test.com")

        _app_a, offer_a_id = _apply_and_send_offer(client, db_session, renter_a, listing_id, admin_cookies)

        offer_a = db_session.get(Offer, offer_a_id)
        leasing_crud.add_offer_terms(
            db_session, offer_a, super_admin,
            # start_date=today, not tomorrow: ZR-ENG-CLR-004 AC-13/AC-14 --
            # move-in now requires the agreement to be *effective*, not just
            # signed (see services/agreement_effectiveness.py), which needs
            # the lease's own start_date to have already arrived.
            data=OfferTermsCreate(
                monthly_rent=500, deposit_amount=500, start_date=date.today(), term_months=6,
            ),
        )

        r = client.post(f"/api/users/rentals/offers/{offer_a_id}/accept", cookies=auth_user_cookie(renter_a))
        assert r.status_code == 200, r.text

        # Satisfy check_agreement_eligibility/check_move_in_eligibility's
        # market/authority/classification gates (unrelated to inventory holds).
        # "England" -- the one jurisdiction the ZR-ENG-CLR-004 fail-closed
        # agreement-profile resolver currently supports.
        listing = db_session.get(Listing, listing_id)
        market_release = MarketRelease(jurisdiction="England", status="active")
        db_session.add(market_release)
        db_session.flush()
        listing.market_release_id = market_release.id
        db_session.add(AuthorityRecord(party_id=listing.party_id, room_id=room_id, authority_type="lease", status="verified"))
        db_session.add(OccupancyClassification(room_id=room_id, classification="long_term_residential", review_state="APPROVED"))
        db_session.commit()

        db_session.refresh(offer_a)
        agreement = leasing_crud.create_agreement(db_session, offer_a, super_admin)

        hold = db_session.scalar(select(RoomHold).where(RoomHold.room_id == room_id, RoomHold.released_at.is_(None)))
        assert hold.status == "BOOKED"

        for obligation in agreement.obligations:
            obligation.status = "PAID"
        db_session.commit()

        agreement.status = "SIGNED"
        db_session.commit()

        occupancy = occupancy_crud.confirm_move_in(db_session, agreement, super_admin)

        db_session.expire_all()
        hold = db_session.scalar(select(RoomHold).where(RoomHold.room_id == room_id, RoomHold.released_at.is_(None)))
        assert hold.status == "OCCUPIED"

        occupancy_crud.end_occupancy(db_session, occupancy, super_admin)

        db_session.expire_all()
        active_holds = db_session.scalars(
            select(RoomHold).where(RoomHold.room_id == room_id, RoomHold.released_at.is_(None))
        ).all()
        assert active_holds == []
        released_hold = db_session.scalar(select(RoomHold).where(RoomHold.room_id == room_id))
        assert released_hold.status == "RELEASED"
        assert released_hold.release_reason == "occupancy_ended"

        # The room is free again -- a brand new renter can now be held for it.
        renter_b = _make_verified_renter(db_session, email="hold-renter-h@test.com")
        _app_b, offer_b = _apply_and_send_offer(client, db_session, renter_b, listing_id, admin_cookies)
        r = client.post(f"/api/users/rentals/offers/{offer_b}/accept", cookies=auth_user_cookie(renter_b))
        assert r.status_code == 200, r.text
