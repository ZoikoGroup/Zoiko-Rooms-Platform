from datetime import date as date_

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.crud import notification as notif_crud
from app.crud.ids import dicebear_avatar, new_id
from app.crud.listing import is_listing_available
from app.models.booking import Booking
from app.models.guest import Guest
from app.models.listing import Listing
from app.schemas.booking import BookingCreate, BookingRead


def list_bookings(db: Session) -> list[BookingRead]:
    bookings = db.scalars(
        select(Booking).options(joinedload(Booking.listing), joinedload(Booking.guest)).order_by(Booking.created_at.desc())
    )
    return [to_booking_read(b) for b in bookings]


def _resolve_guest(db: Session, data: BookingCreate) -> Guest:
    if data.guest_id and data.new_guest:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Provide either an existing guestId or newGuest, not both")

    if data.guest_id:
        guest = db.get(Guest, data.guest_id)
        if not guest:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")
        return guest

    if data.new_guest:
        guest = Guest(
            id=new_id("G"),
            name=data.new_guest.name,
            email=data.new_guest.email,
            phone=data.new_guest.phone,
            avatar=dicebear_avatar(data.new_guest.name),
            location=data.new_guest.location,
            joined_at=date_.today(),
            status="active",
        )
        db.add(guest)
        db.flush()
        return guest

    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Either guestId or newGuest is required")


def _assert_available(db: Session, listing_id: str, check_in: date_, check_out: date_) -> None:
    overlapping = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.listing_id == listing_id,
            Booking.status != "cancelled",
            Booking.check_in < check_out,
            Booking.check_out > check_in,
        )
    )
    if overlapping:
        raise HTTPException(status.HTTP_409_CONFLICT, "This property is already booked for the selected dates")


def create_booking(db: Session, data: BookingCreate) -> BookingRead:
    listing = db.get(Listing, data.listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if data.check_out <= data.check_in:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Check-out date must be after check-in date")

    nights = (data.check_out - data.check_in).days
    if nights < 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Minimum stay must be at least 30 nights")

    listing = db.get(Listing, data.listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if not is_listing_available(db, listing):
        raise HTTPException(status.HTTP_409_CONFLICT, "Listing is not currently available for booking")

    guest = _resolve_guest(db, data)
    _assert_available(db, data.listing_id, data.check_in, data.check_out)

    booking = Booking(
        id=new_id("BK"),
        listing_id=data.listing_id,
        guest_id=guest.id,
        check_in=data.check_in,
        check_out=data.check_out,
        guests=data.guests,
        status=data.status,
        payment_status=data.payment_status,
    )
    db.add(booking)
    db.flush()

    notif_crud.notify_user_by_guest(
        db, guest,
        title="Booking confirmed",
        message=f"Your booking for {listing.name} ({data.check_in.isoformat()} to {data.check_out.isoformat()}) is confirmed.",
        notification_type="booking.created",
        related_entity_type="booking", related_entity_id=booking.id,
    )
    notif_crud.notify_user_by_party(
        db, listing.party_id,
        title="New booking recorded",
        message=f"A booking was recorded for {listing.name} ({data.check_in.isoformat()} to {data.check_out.isoformat()}).",
        notification_type="booking.created",
        related_entity_type="booking", related_entity_id=booking.id,
    )

    db.commit()
    db.refresh(booking)
    booking.listing = listing
    booking.guest = guest
    return to_booking_read(booking)


def to_booking_read(booking: Booking) -> BookingRead:
    return BookingRead(
        id=booking.id,
        listing_id=booking.listing_id,
        listing_name=booking.listing.name,
        property_type=booking.listing.property_type,
        guest_name=booking.guest.name,
        guest_email=booking.guest.email,
        guest_avatar=booking.guest.avatar,
        check_in=booking.check_in,
        check_out=booking.check_out,
        nights=booking.nights,
        guests=booking.guests,
        total_amount=booking.total_amount,
        status=booking.status,
        payment_status=booking.payment_status,
        created_at=booking.created_at,
    )
