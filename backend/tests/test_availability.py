"""Integration tests for listing availability/occupancy consistency and
property/listing location canonicalization -- the backend gaps described in
the Anil task list: an occupied room must stop appearing as available, and a
listing's location must come from its property rather than drifting
independently.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.identity_verification import IdentityVerification
from app.models.leasing import Application, Offer
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from app.models.user_account import UserAccount
from tests.conftest import _make_admin, _make_user, auth_admin_cookie, auth_user_cookie

LISTING_PAYLOAD = {
    "name": "Sunny Room in Koramangala",
    "roomType": "Private room",
    # Deliberately different from the property's real city/address below --
    # proves the backend derives the canonical value rather than trusting this.
    "city": "Nowhere",
    "location": "Made-up address",
    "pricePerNight": 500,
    "currency": "INR",
    "guests": 1,
    "bedrooms": 1,
    "bathrooms": 1,
    "size": 100,
    "description": "A nice room",
    "amenities": ["WiFi"],
    "images": [],
    "minStayNights": 30,
    "contactName": "Test Host",
    "contactPhone": "9999999999",
    "contactEmail": "host@test.com",
}


def _make_host_with_room(db: Session, *, email: str = "host@test.com") -> tuple[UserAccount, Property, int]:
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()

    user = _make_user(db, email=email)
    user.party_id = party.id
    db.flush()

    prop = Property(owner_party_id=party.id, address="123 Real Street", city="Bengaluru", status="active")
    db.add(prop)
    db.flush()

    room = Room(property_id=prop.id, room_type="private_room", size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()
    db.commit()

    return user, prop, room.id


def _create_publish_listing(client, db_session: Session) -> tuple[str, int, dict, dict]:
    """Host creates + submits a listing, admin approves + publishes it.
    Returns (listing_id, room_id, host_cookies, admin_cookies)."""
    user, prop, room_id = _make_host_with_room(db_session)
    host_cookies = auth_user_cookie(user)

    r = client.post(
        "/api/users/hosting/listings",
        json={**LISTING_PAYLOAD, "roomId": room_id},
        cookies=host_cookies,
    )
    assert r.status_code == 201, r.text
    listing_id = r.json()["id"]

    admin = _make_admin(db_session, email="admin@test.com", role="super_admin")
    admin_cookies = auth_admin_cookie(admin)
    r = client.post(f"/api/listings/{listing_id}/publish", cookies=admin_cookies)
    assert r.status_code == 200, r.text

    return listing_id, room_id, host_cookies, admin_cookies


def _make_occupancy(
    db: Session, *, listing_id: str, room_id: int, guest_email: str = "renter@test.com", status: str = "ACTIVE"
) -> Occupancy:
    """Directly constructs the minimal Guest -> Application -> Offer -> Occupancy
    chain -- these tests are about availability derivation, not re-exercising
    the full application/offer/agreement pipeline end to end."""
    guest = Guest(id=f"G-{guest_email}", name="Test Renter", email=guest_email, joined_at=date.today())
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing_id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()

    offer = Offer(application_id=application.id, listing_id=listing_id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()

    occupancy = Occupancy(offer_id=offer.id, listing_id=listing_id, room_id=room_id, guest_id=guest.id, status=status)
    db.add(occupancy)
    db.commit()
    return occupancy


def _make_active_occupancy(db: Session, *, listing_id: str, room_id: int, guest_email: str = "renter@test.com") -> Occupancy:
    return _make_occupancy(db, listing_id=listing_id, room_id=room_id, guest_email=guest_email, status="ACTIVE")


def _make_verified_renter(db: Session, *, email: str = "applicant@test.com") -> UserAccount:
    """A USER with a verified identity, distinct from the host -- eligible to
    apply to a listing except for whatever availability check is under test."""
    party = Party(party_type="renter", status="active", jurisdiction="IN")
    db.add(party)
    db.flush()

    user = _make_user(db, email=email)
    user.party_id = party.id
    db.add(
        IdentityVerification(
            party_id=party.id,
            document_type="passport",
            document_category="identity",
            status="verified",
        )
    )
    db.commit()
    return user


class TestLocationCanonicalization:
    def test_listing_city_and_location_come_from_property_not_client_input(self, client, db_session: Session):
        user, prop, room_id = _make_host_with_room(db_session)
        cookies = auth_user_cookie(user)

        r = client.post(
            "/api/users/hosting/listings",
            json={**LISTING_PAYLOAD, "roomId": room_id},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["city"] == prop.city
        assert body["location"] == prop.address
        assert body["city"] != LISTING_PAYLOAD["city"]
        assert body["location"] != LISTING_PAYLOAD["location"]


class TestAvailabilityConsistency:
    def test_published_listing_is_public(self, client, db_session: Session):
        listing_id, _room_id, _host, _admin = _create_publish_listing(client, db_session)

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 200, r.text

        r = client.get("/api/public/listings")
        assert r.status_code == 200
        assert any(item["id"] == listing_id for item in r.json()["items"])

    def test_occupied_room_disappears_from_public_search_and_detail(self, client, db_session: Session):
        listing_id, room_id, _host, _admin = _create_publish_listing(client, db_session)
        _make_active_occupancy(db_session, listing_id=listing_id, room_id=room_id)

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 404, r.text

        r = client.get("/api/public/listings")
        assert r.status_code == 200
        assert not any(item["id"] == listing_id for item in r.json()["items"])

    def test_admin_listing_view_shows_available_false_once_occupied(self, client, db_session: Session):
        listing_id, room_id, _host, admin_cookies = _create_publish_listing(client, db_session)

        r = client.get("/api/listings", cookies=admin_cookies)
        row = next(item for item in r.json() if item["id"] == listing_id)
        assert row["available"] is True

        _make_active_occupancy(db_session, listing_id=listing_id, room_id=room_id)

        r = client.get("/api/listings", cookies=admin_cookies)
        row = next(item for item in r.json() if item["id"] == listing_id)
        assert row["available"] is False

    def test_host_my_listings_shows_available_false_once_occupied(self, client, db_session: Session):
        listing_id, room_id, host_cookies, _admin = _create_publish_listing(client, db_session)
        _make_active_occupancy(db_session, listing_id=listing_id, room_id=room_id)

        r = client.get("/api/users/hosting/listings", cookies=host_cookies)
        assert r.status_code == 200, r.text
        row = next(item for item in r.json() if item["id"] == listing_id)
        assert row["available"] is False

    def test_pending_move_in_occupancy_already_makes_room_unavailable(self, client, db_session: Session):
        """A signed lease waiting on move-in already commits the room to that
        renter -- it must not still look bookable to everyone else."""
        listing_id, room_id, _host, _admin = _create_publish_listing(client, db_session)
        _make_occupancy(db_session, listing_id=listing_id, room_id=room_id, status="PENDING_MOVE_IN")

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 404, r.text

    def test_ended_occupancy_makes_room_available_again(self, client, db_session: Session):
        listing_id, room_id, _host, _admin = _create_publish_listing(client, db_session)
        _make_occupancy(db_session, listing_id=listing_id, room_id=room_id, status="ENDED")

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 200, r.text

    def test_inactive_room_is_also_unavailable(self, client, db_session: Session):
        listing_id, room_id, _host, _admin = _create_publish_listing(client, db_session)
        room = db_session.get(Room, room_id)
        room.status = "inactive"
        db_session.commit()

        r = client.get(f"/api/public/listings/{listing_id}")
        assert r.status_code == 404, r.text


class TestAvailabilityEnforcedAcrossEntryPoints:
    """An occupied room must be rejected everywhere a new claim on it could be
    made, not just hidden from browse/search -- otherwise the single source of
    truth in crud.listing.is_listing_available is bypassed by other write
    paths that re-implement their own availability rule."""

    def test_occupied_listing_rejects_new_rental_application(self, client, db_session: Session):
        listing_id, room_id, _host, _admin = _create_publish_listing(client, db_session)
        _make_active_occupancy(db_session, listing_id=listing_id, room_id=room_id)

        applicant = _make_verified_renter(db_session)
        cookies = auth_user_cookie(applicant)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi"},
            cookies=cookies,
        )
        assert r.status_code >= 400, r.text
        assert r.status_code != 201

    def test_available_listing_still_accepts_application(self, client, db_session: Session):
        listing_id, _room_id, _host, _admin = _create_publish_listing(client, db_session)

        applicant = _make_verified_renter(db_session)
        cookies = auth_user_cookie(applicant)

        r = client.post(
            "/api/users/rentals/applications",
            json={"listingId": listing_id, "message": "hi"},
            cookies=cookies,
        )
        assert r.status_code == 201, r.text

    def test_legacy_booking_creation_endpoint_no_longer_exists(self, client, db_session: Session):
        """ZR-ENG-CLR-001 Section 1: the legacy Booking model predates the
        real Application -> Offer -> Agreement -> Occupancy pipeline and
        creating a row through it bypassed every gate (RoomHold,
        jurisdiction, overlap, identity) that pipeline enforces -- POST was
        removed entirely rather than re-implementing availability
        enforcement on a path that shouldn't exist at all."""
        listing_id, room_id, _host, admin_cookies = _create_publish_listing(client, db_session)
        _make_active_occupancy(db_session, listing_id=listing_id, room_id=room_id)

        check_in = date.today() + timedelta(days=10)
        check_out = check_in + timedelta(days=45)
        r = client.post(
            "/api/bookings",
            json={
                "listingId": listing_id,
                "newGuest": {"name": "Booker", "email": "booker@test.com"},
                "checkIn": check_in.isoformat(),
                "checkOut": check_out.isoformat(),
                "guests": 1,
            },
            cookies=admin_cookies,
        )
        assert r.status_code == 405, r.text
