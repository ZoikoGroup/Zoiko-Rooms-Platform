"""Unit tests for app/crud/booking.py -- the legacy short-stay booking model's
read-only listing, distinct from the real rental lifecycle these tests
elsewhere exercise (Application -> Offer -> Agreement -> Occupancy).
Creation was intentionally removed (ZR-ENG-CLR-001 Section 1): this model
predates the real booking pipeline and a new row here would bypass every
gate (RoomHold, jurisdiction, overlap, identity) that pipeline enforces."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.crud import booking as crud
from app.crud.ids import dicebear_avatar, new_id
from app.models.booking import Booking
from app.models.guest import Guest
from app.models.listing import Listing


def _make_listing(db: Session, *, listing_id: str = "L-BOOKTEST") -> Listing:
    listing = Listing(
        id=listing_id, slug=listing_id.lower(), name="Booking Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=1000, guests=2,
        rating=4.5, review_count=0, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return listing


def _make_booking(
    db: Session, *, listing_id: str, name: str, check_in: date, check_out: date, created_at: datetime,
) -> Booking:
    guest = Guest(
        id=new_id("G"), name=name, email=f"{name.lower()}@test.com", avatar=dicebear_avatar(name),
        joined_at=date.today(), status="active",
    )
    db.add(guest)
    db.flush()
    booking = Booking(
        id=new_id("BK"), listing_id=listing_id, guest_id=guest.id, check_in=check_in, check_out=check_out,
        guests=1, status="confirmed", payment_status="unpaid",
    )
    db.add(booking)
    db.commit()
    db.query(Booking).filter(Booking.id == booking.id).update({"created_at": created_at})
    db.commit()
    return booking


class TestListBookings:
    def test_list_bookings_orders_newest_first(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKLIST")
        _make_booking(
            db_session, listing_id="L-BOOKLIST", name="Older",
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        _make_booking(
            db_session, listing_id="L-BOOKLIST", name="Newer",
            check_in=date.today() + timedelta(days=65), check_out=date.today() + timedelta(days=100),
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        rows = crud.list_bookings(db_session)
        names = [r.guest_name for r in rows if r.listing_id == "L-BOOKLIST"]
        assert names[0] == "Newer"
        assert names[1] == "Older"
