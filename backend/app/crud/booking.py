from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.booking import Booking
from app.schemas.booking import BookingRead


def list_bookings(db: Session) -> list[BookingRead]:
    bookings = db.scalars(
        select(Booking).options(joinedload(Booking.listing), joinedload(Booking.guest)).order_by(Booking.created_at.desc())
    )
    return [to_booking_read(b) for b in bookings]


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
