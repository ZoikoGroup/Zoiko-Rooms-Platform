from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.booking import Booking
from app.models.leasing import Offer
from app.models.occupancy import Occupancy
from app.schemas.booking import BookingRead

# The admin Bookings view's status vocabulary is the legacy hotel-style one
# (src/lib/types.ts BookingStatus); real rentals are mapped onto it.
OCCUPANCY_TO_BOOKING_STATUS = {
    "PENDING_MOVE_IN": "pending",
    "ACTIVE": "confirmed",
    "ENDED": "completed",
    "CANCELLED": "cancelled",
}


def list_bookings(db: Session) -> list[BookingRead]:
    """Legacy Booking rows plus every real rental (Occupancy) from the
    Application -> Offer -> Agreement -> Occupancy pipeline, newest first.
    Nothing creates legacy Booking rows anymore, so without occupancies the
    admin Bookings page and dashboard cards would always be empty."""
    bookings = db.scalars(
        select(Booking).options(joinedload(Booking.listing), joinedload(Booking.guest))
    )
    occupancies = db.scalars(
        select(Occupancy).options(
            joinedload(Occupancy.listing),
            joinedload(Occupancy.guest),
            selectinload(Occupancy.co_tenants),
            selectinload(Occupancy.obligations),
            joinedload(Occupancy.offer).joinedload(Offer.agreement),
        )
    ).unique()
    rows = [to_booking_read(b) for b in bookings] + [occupancy_to_booking_read(o) for o in occupancies]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


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


def occupancy_to_booking_read(occupancy: Occupancy) -> BookingRead:
    check_in = occupancy.move_in_date or occupancy.created_at.date()
    check_out = occupancy.move_out_date or occupancy.expected_end_date or check_in

    # Obligations hang off either the occupancy or its agreement. Only RENT
    # counts toward the amount -- a deposit is held, not earned.
    agreement = occupancy.offer.agreement if occupancy.offer else None
    obligations = {o.id: o for o in occupancy.obligations}
    if agreement is not None:
        obligations.update({o.id: o for o in agreement.obligations})
    rent = [o for o in obligations.values() if o.obligation_type == "RENT"]

    if rent and all(o.status == "REFUNDED" for o in rent):
        payment_status = "refunded"
    elif rent and all(o.status in ("PAID", "WAIVED") for o in rent):
        payment_status = "paid"
    else:
        payment_status = "unpaid"

    return BookingRead(
        id=f"OCC-{occupancy.id}",
        listing_id=occupancy.listing_id,
        listing_name=occupancy.listing.name,
        property_type=occupancy.listing.property_type,
        guest_name=occupancy.guest.name,
        guest_email=occupancy.guest.email,
        guest_avatar=occupancy.guest.avatar,
        check_in=check_in,
        check_out=check_out,
        nights=max(1, (check_out - check_in).days),
        guests=1 + len(occupancy.co_tenants),
        total_amount=float(sum(o.amount for o in rent)),
        status=OCCUPANCY_TO_BOOKING_STATUS.get(occupancy.status, "pending"),
        payment_status=payment_status,
        created_at=occupancy.created_at,
    )
