"""Integration tests for the real review/rating backend -- eligibility gated
on an actual stay (Occupancy), duplicate prevention, and rating/review_count
recomputed from real review rows instead of the static 4.5/0 defaults.
"""

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
from tests.conftest import _make_user, auth_user_cookie

LISTING_PAYLOAD = {
    "name": "Room With A View",
    "roomType": "Private room",
    "city": "Bengaluru",
    "location": "Indiranagar",
    "pricePerNight": 700,
    "currency": "INR",
    "guests": 1,
    "bedrooms": 1,
    "bathrooms": 1,
    "size": 120,
    "description": "",
    "amenities": [],
    "images": [],
    "minStayNights": 30,
}


def _make_published_listing(db: Session) -> tuple[Listing, int]:
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()
    prop = Property(owner_party_id=party.id, address="1 Test Rd", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id="L-TESTREV",
        slug="room-with-a-view",
        name="Room With A View",
        room_type="Private room",
        city="Bengaluru",
        location="Indiranagar",
        price_per_night=700,
        guests=1,
        room_id=room.id,
        party_id=party.id,
        state="PUBLISHED",
        rating=4.5,
        review_count=0,
    )
    db.add(listing)
    db.commit()
    return listing, room.id


def _renter_with_occupancy(db: Session, listing: Listing, room_id: int, *, email: str = "renter@test.com") -> Guest:
    """A renter who actually stayed at this listing -- the only case the
    review eligibility rule is supposed to allow."""
    guest = Guest(id=f"G-{email}", name="Renter", email=email, joined_at=date.today())
    db.add(guest)
    db.flush()
    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    db.add(Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room_id, guest_id=guest.id, status="ENDED"))
    db.commit()
    return guest


class TestReviewEligibility:
    def test_renter_without_a_stay_cannot_review(self, client, db_session: Session):
        listing, _room_id = _make_published_listing(db_session)
        user = _make_user(db_session, email="never-stayed@test.com")
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 5, "comment": "Great!"},
            cookies=cookies,
        )
        assert r.status_code == 403, r.text

    def test_renter_who_stayed_can_review_and_rating_is_recomputed(self, client, db_session: Session):
        listing, room_id = _make_published_listing(db_session)
        user = _make_user(db_session, email="stayed@test.com")
        _renter_with_occupancy(db_session, listing, room_id, email=user.email)
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 5, "comment": "Loved it"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text

        db_session.refresh(listing)
        assert listing.rating == 5.0
        assert listing.review_count == 1

        r = client.get(f"/api/public/listings/{listing.id}/reviews")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["rating"] == 5

    def test_second_review_on_same_listing_is_rejected(self, client, db_session: Session):
        listing, room_id = _make_published_listing(db_session)
        user = _make_user(db_session, email="repeat@test.com")
        _renter_with_occupancy(db_session, listing, room_id, email=user.email)
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 4, "comment": "Good"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text

        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 2, "comment": "Changed my mind"},
            cookies=cookies,
        )
        assert r.status_code == 409, r.text

    def test_rating_out_of_range_is_rejected(self, client, db_session: Session):
        listing, room_id = _make_published_listing(db_session)
        user = _make_user(db_session, email="badrating@test.com")
        _renter_with_occupancy(db_session, listing, room_id, email=user.email)
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 7, "comment": "?"},
            cookies=cookies,
        )
        assert r.status_code == 400, r.text

    def test_review_requires_authentication(self, client, db_session: Session):
        listing, _room_id = _make_published_listing(db_session)
        r = client.post(
            "/api/users/rentals/reviews",
            json={"listingId": listing.id, "rating": 5, "comment": "Anon"},
        )
        assert r.status_code == 401, r.text

    def test_new_review_alert_reaches_only_admins_who_switched_it_on(self, client, db_session: Session):
        """ZR-20: 'New review alerts' (Settings > Notification Preferences) is off by default."""
        from app.models.admin_user import AdminSettings
        from app.models.notification import Notification
        from tests.conftest import _make_admin

        listing, room_id = _make_published_listing(db_session)
        user = _make_user(db_session, email="reviewer-alert@test.com")
        _renter_with_occupancy(db_session, listing, room_id, email=user.email)
        on = _make_admin(db_session, email="review-on@test.com")
        off = _make_admin(db_session, email="review-off@test.com")
        db_session.add(AdminSettings(admin_user_id=on.id, notify_reviews=True))
        db_session.commit()

        r = client.post(
            "/api/users/rentals/reviews", json={"listingId": listing.id, "rating": 4, "comment": "Nice"},
            cookies=auth_user_cookie(user),
        )
        assert r.status_code == 201, r.text
        got = lambda admin: db_session.query(Notification).filter_by(  # noqa: E731
            recipient_admin_id=admin.id, notification_type="review.new").count()
        assert got(on) == 1
        assert got(off) == 0
