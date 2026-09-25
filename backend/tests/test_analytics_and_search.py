"""Tests for the analytics/search fix to the legacy-Booking-only blind spot
(Anil task list section 4): a renter who went through the real
Application -> Offer -> Agreement -> Occupancy lifecycle, with zero legacy
Booking rows, must still show up in admin dashboards and global search --
previously these only ever looked at Booking.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.guest import Guest
from app.models.leasing import Agreement, Application, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.property import Property
from app.models.room import Room
from tests.conftest import _make_admin, auth_admin_cookie


def _make_listing_with_active_occupancy(
    db: Session, *, city: str = "Pune", property_type: str = "private_room", guest_name: str = "Real Renter"
) -> tuple[str, Occupancy]:
    """A listing with zero legacy Bookings but one real ACTIVE Occupancy --
    exactly the case the legacy-Booking-only analytics/search missed."""
    owner_party = Party(party_type="provider", status="active", jurisdiction="IN")
    db.add(owner_party)
    db.flush()

    prop = Property(owner_party_id=owner_party.id, address="1 Real St", city=city, status="active")
    db.add(prop)
    db.flush()
    room = Room(property_id=prop.id, room_type=property_type, size=100, has_ensuite=True, status="active")
    db.add(room)
    db.flush()

    listing = Listing(
        id=f"L-{guest_name.replace(' ', '')}", slug=guest_name.lower().replace(" ", "-"),
        name=f"Listing for {guest_name}", room_type="Private room", property_type=property_type,
        city=city, location="Somewhere", price_per_night=500, guests=1,
        rating=4.5, review_count=0, party_id=owner_party.id, owner_id=None, room_id=room.id,
        state="PUBLISHED",
    )
    db.add(listing)
    db.flush()

    guest = Guest(id=f"G-{guest_name.replace(' ', '')}", name=guest_name, email=f"{guest_name.replace(' ', '').lower()}@test.com", joined_at=date.today())
    db.add(guest)
    db.flush()

    application = Application(listing_id=listing.id, guest_id=guest.id, status="DECIDED")
    db.add(application)
    db.flush()
    offer = Offer(application_id=application.id, listing_id=listing.id, guest_id=guest.id, status="ACCEPTED")
    db.add(offer)
    db.flush()
    agreement = Agreement(offer_id=offer.id, status="SIGNED")
    db.add(agreement)
    db.flush()
    occupancy = Occupancy(offer_id=offer.id, listing_id=listing.id, room_id=room.id, guest_id=guest.id, status="ACTIVE")
    db.add(occupancy)
    db.commit()

    return listing.id, occupancy


class TestOccupancyByCityIncludesRealLifecycle:
    def test_city_with_only_a_real_occupancy_no_legacy_bookings_is_not_zero(self, client, db_session: Session):
        _make_listing_with_active_occupancy(db_session, city="Pune")

        admin = _make_admin(db_session, email="super@test.com", role="super_admin")
        r = client.get("/api/analytics/occupancy-by-city", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        row = next((item for item in r.json() if item["city"] == "Pune"), None)
        assert row is not None, r.json()
        assert row["occupancy"] == 100


class TestBookingsByTypeIncludesRealLifecycle:
    def test_property_type_with_only_a_real_occupancy_is_counted(self, client, db_session: Session):
        _make_listing_with_active_occupancy(db_session, property_type="private_room")

        admin = _make_admin(db_session, email="super2@test.com", role="super_admin")
        r = client.get("/api/analytics/bookings-by-type", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        row = next((item for item in r.json() if item["type"] == "Private Rooms"), None)
        assert row is not None, r.json()
        assert row["value"] >= 1


class TestGlobalSearchIncludesRealLifecycle:
    def test_super_admin_can_find_a_real_application_and_occupancy(self, client, db_session: Session):
        _make_listing_with_active_occupancy(db_session, guest_name="Searchable Renter")

        admin = _make_admin(db_session, email="super3@test.com", role="super_admin")
        r = client.get("/api/search?q=Searchable", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        types_found = {item["type"] for item in r.json()}
        assert "application" in types_found, r.json()
        assert "occupancy" in types_found, r.json()

    def test_plain_admin_cannot_see_renter_identifying_search_results(self, client, db_session: Session):
        _make_listing_with_active_occupancy(db_session, guest_name="Private Renter")

        admin = _make_admin(db_session, email="plain@test.com", role="admin")
        r = client.get("/api/search?q=Private", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        types_found = {item["type"] for item in r.json()}
        assert types_found.isdisjoint({"guest", "booking", "application", "occupancy"})


class TestRevenueTrendIncludesConfirmedRentRecords:
    def test_recipient_confirmed_rent_counts_and_deposit_does_not(self, client, db_session: Session):
        """The ZR-PAY-002 record path confirms rent without ever creating a
        SimulatedPayment -- that rent must still reach the revenue chart,
        while a confirmed deposit (held, not earned) must not."""
        from datetime import datetime, timezone

        from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord

        _listing_id, occupancy = _make_listing_with_active_occupancy(db_session, guest_name="Rent Payer")
        offer = db_session.get(Offer, occupancy.offer_id)
        recipient_party_id = offer.listing.party_id
        now = datetime.now(timezone.utc)

        for obligation_type, amount in (("RENT", 129), ("DEPOSIT", 100)):
            obligation = RentalPaymentObligation(
                obligation_type=obligation_type, agreement_id=offer.agreement.id,
                tenant_guest_id=occupancy.guest_id, recipient_party_id=recipient_party_id,
                amount=amount, currency="GBP", due_date=date.today(), status="CONFIRMED",
            )
            db_session.add(obligation)
            db_session.flush()
            db_session.add(
                RentalPaymentRecord(
                    obligation_id=obligation.id, status="CONFIRMED", declared_amount=amount,
                    declared_currency="GBP", declared_date=date.today(),
                    declared_by_guest_id=occupancy.guest_id, confirmed_by_party_id=recipient_party_id,
                    confirmed_at=now, confirmed_amount=amount,
                )
            )
        db_session.commit()

        admin = _make_admin(db_session, email="super3@test.com", role="super_admin")
        r = client.get("/api/analytics/revenue-trend", cookies=auth_admin_cookie(admin))
        assert r.status_code == 200, r.text

        row = next((item for item in r.json() if item["month"] == now.strftime("%b")), None)
        assert row is not None, r.json()
        assert row["revenue"] == 129
        assert row["bookings"] == 1
