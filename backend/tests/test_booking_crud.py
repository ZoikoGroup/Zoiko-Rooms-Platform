"""Unit tests for app/crud/booking.py (previously 46% covered) -- the legacy
short-stay booking flow's own guest-resolution, availability, and 30-night
minimum validation, distinct from the real rental lifecycle these tests
elsewhere exercise (Application -> Offer -> Agreement -> Occupancy)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud import booking as crud
from app.models.guest import Guest
from app.models.listing import Listing
from app.schemas.booking import BookingCreate, NewGuestInput


def _make_listing(db: Session, *, listing_id: str = "L-BOOKTEST") -> Listing:
    listing = Listing(
        id=listing_id, slug=listing_id.lower(), name="Booking Test Listing", room_type="Private room",
        city="Bengaluru", location="Koramangala", price_per_night=1000, guests=2,
        rating=4.5, review_count=0, state="PUBLISHED",
    )
    db.add(listing)
    db.commit()
    return listing


class TestCreateBooking:
    def test_creates_a_booking_with_a_new_guest(self, db_session: Session):
        _make_listing(db_session)
        data = BookingCreate(
            listing_id="L-BOOKTEST",
            new_guest=NewGuestInput(name="Jane Renter", email="jane@test.com"),
            check_in=date.today() + timedelta(days=5),
            check_out=date.today() + timedelta(days=40),
            guests=1,
        )
        booking = crud.create_booking(db_session, data)
        assert booking.guest_name == "Jane Renter"
        assert booking.listing_name == "Booking Test Listing"
        assert booking.nights == 35

    def test_reuses_an_existing_guest_id(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST2")
        guest = Guest(id="G-BOOKTEST", name="Existing Guest", email="existing@test.com", joined_at=date.today())
        db_session.add(guest)
        db_session.commit()

        data = BookingCreate(
            listing_id="L-BOOKTEST2", guest_id="G-BOOKTEST",
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        booking = crud.create_booking(db_session, data)
        assert booking.guest_name == "Existing Guest"

    def test_rejects_both_guest_id_and_new_guest(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST3")
        data = BookingCreate(
            listing_id="L-BOOKTEST3", guest_id="G-WHATEVER",
            new_guest=NewGuestInput(name="X", email="x@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 400

    def test_rejects_neither_guest_id_nor_new_guest(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST4")
        data = BookingCreate(
            listing_id="L-BOOKTEST4",
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 400

    def test_unknown_guest_id_is_404(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST5")
        data = BookingCreate(
            listing_id="L-BOOKTEST5", guest_id="G-NOBODY",
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 404

    def test_unknown_listing_is_404(self, db_session: Session):
        data = BookingCreate(
            listing_id="L-NOPE", new_guest=NewGuestInput(name="X", email="x@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 404

    def test_check_out_before_check_in_is_rejected(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST6")
        data = BookingCreate(
            listing_id="L-BOOKTEST6", new_guest=NewGuestInput(name="X", email="x@test.com"),
            check_in=date.today() + timedelta(days=40), check_out=date.today() + timedelta(days=5), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 400

    def test_stays_under_30_nights_are_rejected(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST7")
        data = BookingCreate(
            listing_id="L-BOOKTEST7", new_guest=NewGuestInput(name="X", email="x@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=20), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 400

    def test_unpublished_listing_rejects_booking(self, db_session: Session):
        listing = Listing(
            id="L-BOOKTEST8", slug="l-booktest8", name="Draft Listing", room_type="Private room",
            city="Bengaluru", location="Koramangala", price_per_night=1000, guests=2,
            rating=4.5, review_count=0, state="DRAFT",
        )
        db_session.add(listing)
        db_session.commit()
        data = BookingCreate(
            listing_id="L-BOOKTEST8", new_guest=NewGuestInput(name="X", email="x@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, data)
        assert exc.value.status_code == 409

    def test_overlapping_dates_on_the_same_listing_are_rejected(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST9")
        first = BookingCreate(
            listing_id="L-BOOKTEST9", new_guest=NewGuestInput(name="First", email="first@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        crud.create_booking(db_session, first)

        overlapping = BookingCreate(
            listing_id="L-BOOKTEST9", new_guest=NewGuestInput(name="Second", email="second@test.com"),
            check_in=date.today() + timedelta(days=20), check_out=date.today() + timedelta(days=55), guests=1,
        )
        with pytest.raises(HTTPException) as exc:
            crud.create_booking(db_session, overlapping)
        assert exc.value.status_code == 409

    def test_a_cancelled_bookings_dates_free_up_the_listing(self, db_session: Session):
        _make_listing(db_session, listing_id="L-BOOKTEST10")
        first = BookingCreate(
            listing_id="L-BOOKTEST10", new_guest=NewGuestInput(name="First", email="first10@test.com"),
            check_in=date.today() + timedelta(days=5), check_out=date.today() + timedelta(days=40), guests=1,
        )
        first_booking = crud.create_booking(db_session, first)
        from app.models.booking import Booking

        db_session.query(Booking).filter(Booking.id == first_booking.id).update({"status": "cancelled"})
        db_session.commit()

        second = BookingCreate(
            listing_id="L-BOOKTEST10", new_guest=NewGuestInput(name="Second", email="second10@test.com"),
            check_in=date.today() + timedelta(days=20), check_out=date.today() + timedelta(days=55), guests=1,
        )
        booking = crud.create_booking(db_session, second)
        assert booking.guest_name == "Second"


class TestListBookings:
    def test_list_bookings_orders_newest_first(self, db_session: Session):
        from datetime import datetime, timezone

        from app.models.booking import Booking

        _make_listing(db_session, listing_id="L-BOOKLIST")
        for i, name in enumerate(["Older", "Newer"]):
            offset = i * 60  # keep the two stays' date ranges well apart so they don't conflict
            data = BookingCreate(
                listing_id="L-BOOKLIST", new_guest=NewGuestInput(name=name, email=f"{name.lower()}@test.com"),
                check_in=date.today() + timedelta(days=5 + offset), check_out=date.today() + timedelta(days=40 + offset),
                guests=1, status="confirmed" if i == 0 else "pending",
            )
            booking = crud.create_booking(db_session, data)
            # Both rows can otherwise land on the same created_at instant (SQLite
            # test DB, no meaningful clock granularity between two calls in the
            # same test) -- pin explicit, clearly-ordered timestamps instead of
            # relying on wall-clock timing.
            db_session.query(Booking).filter(Booking.id == booking.id).update(
                {"created_at": datetime(2026, 1, 1 + i, tzinfo=timezone.utc)}
            )
        db_session.commit()

        rows = crud.list_bookings(db_session)
        names = [r.guest_name for r in rows if r.listing_id == "L-BOOKLIST"]
        assert names[0] == "Newer"
        assert names[1] == "Older"
