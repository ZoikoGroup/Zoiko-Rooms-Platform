# NOTE: every function below combines the legacy short-stay Booking table
# with the real self-service rental lifecycle (confirmed rent for revenue,
# Occupancy for occupancy/bookings-by-type), so a renter who went through the
# actual Application -> Offer -> Agreement -> Occupancy flow shows up in these
# admin charts too, not just legacy admin-created bookings. Application rows
# are deliberately not counted here: they're not yet a completed rental or
# a confirmed payment, so including them would inflate "bookings"/"revenue"
# with unconfirmed activity.
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.booking import Booking
from app.models.finance import Obligation, PaymentAllocation, SimulatedPayment
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord
from app.schemas.analytics import BookingsByTypePoint, OccupancyByCityPoint, RevenueTrendPoint

PROPERTY_TYPE_LABELS = {
    "private_room": "Private Rooms",
}

def revenue_trend(db: Session, months: int = 6) -> list[RevenueTrendPoint]:
    """Merges legacy Booking revenue (nights * price, at booking creation
    time) with real rental-lifecycle rent, bucketed by month at confirmation
    time: SimulatedPayment amounts allocated to RENT obligations, plus
    recipient-confirmed RentalPaymentRecords against RENT obligations (the
    ZR-PAY-002 record path, which never creates a SimulatedPayment).
    Deposits are excluded on both paths -- held, not earned -- matching
    crud/booking.py's totalAmount so the dashboard's Total Revenue card and
    this chart agree. Rows are bucketed in Python rather than with Postgres'
    date_trunc so this also runs on the SQLite test database."""
    revenue: dict[tuple[int, int], float] = {}
    events: dict[tuple[int, int], set[tuple[str, object]]] = {}

    def _add(at: datetime, amount: float, event: tuple[str, object]) -> None:
        bucket = (at.year, at.month)
        revenue[bucket] = revenue.get(bucket, 0.0) + amount
        events.setdefault(bucket, set()).add(event)

    for booking in db.scalars(
        select(Booking).options(joinedload(Booking.listing)).where(Booking.payment_status == "paid")
    ):
        _add(booking.created_at, float(booking.total_amount), ("booking", booking.id))

    payment_rows = db.execute(
        select(SimulatedPayment.id, SimulatedPayment.confirmed_at, PaymentAllocation.amount_allocated)
        .join(PaymentAllocation, PaymentAllocation.payment_id == SimulatedPayment.id)
        .join(Obligation, Obligation.id == PaymentAllocation.obligation_id)
        .where(
            SimulatedPayment.status == "SUCCEEDED",
            SimulatedPayment.confirmed_at.is_not(None),
            Obligation.obligation_type == "RENT",
        )
    ).all()
    for payment_id, confirmed_at, amount in payment_rows:
        _add(confirmed_at, float(amount), ("payment", payment_id))

    record_rows = db.execute(
        select(RentalPaymentRecord.id, RentalPaymentRecord.confirmed_at, RentalPaymentRecord.confirmed_amount)
        .join(RentalPaymentObligation, RentalPaymentObligation.id == RentalPaymentRecord.obligation_id)
        .where(
            RentalPaymentRecord.status == "CONFIRMED",
            RentalPaymentRecord.confirmed_at.is_not(None),
            RentalPaymentObligation.obligation_type == "RENT",
        )
    ).all()
    for record_id, confirmed_at, amount in record_rows:
        _add(confirmed_at, float(amount or 0), ("record", record_id))

    ordered = sorted(revenue)[-months:]
    return [
        RevenueTrendPoint(
            month=datetime(year, month, 1).strftime("%b"),
            revenue=round(revenue[(year, month)], 2),
            bookings=len(events[(year, month)]),
        )
        for year, month in ordered
    ]


def bookings_by_type(db: Session) -> list[BookingsByTypePoint]:
    """Combines legacy Booking counts with real Occupancy counts per listing
    property type -- a renter who went through the current lifecycle instead
    of the legacy admin Booking flow must count here too."""
    counts: dict[str, int] = {}

    booking_rows = db.execute(
        select(Listing.property_type, func.count(Booking.id).label("value"))
        .join(Listing, Listing.id == Booking.listing_id)
        .group_by(Listing.property_type)
    ).all()
    for row in booking_rows:
        counts[row.property_type] = counts.get(row.property_type, 0) + row.value

    occupancy_rows = db.execute(
        select(Listing.property_type, func.count(Occupancy.id).label("value"))
        .join(Listing, Listing.id == Occupancy.listing_id)
        .group_by(Listing.property_type)
    ).all()
    for row in occupancy_rows:
        counts[row.property_type] = counts.get(row.property_type, 0) + row.value

    return [
        BookingsByTypePoint(type=PROPERTY_TYPE_LABELS.get(property_type, property_type), value=value)
        for property_type, value in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ]


def occupancy_by_city(db: Session, limit: int = 6) -> list[OccupancyByCityPoint]:
    # No per-night room inventory/calendar table exists, so true occupancy (booked
    # nights / available nights) isn't computable. As a dynamic proxy we use active
    # claims (legacy bookings + real occupancies) per engaged listing in the city,
    # capped at 100%, as a load indicator. Previously counted legacy Bookings only,
    # so a city rented entirely through the self-service flow reported 0%.
    active_booking_statuses = ("confirmed", "pending", "completed")
    active_occupancy_statuses = ("PENDING_MOVE_IN", "ACTIVE")

    active_count_by_city: dict[str, int] = {}
    engaged_listings_by_city: dict[str, set[str]] = {}

    def _record(listing_id: str, city: str) -> None:
        active_count_by_city[city] = active_count_by_city.get(city, 0) + 1
        engaged_listings_by_city.setdefault(city, set()).add(listing_id)

    booking_rows = db.execute(
        select(Listing.id, Listing.city)
        .join(Booking, Booking.listing_id == Listing.id)
        .where(Booking.status.in_(active_booking_statuses))
    ).all()
    for listing_id, city in booking_rows:
        _record(listing_id, city)

    occupancy_rows = db.execute(
        select(Listing.id, Listing.city)
        .join(Occupancy, Occupancy.listing_id == Listing.id)
        .where(Occupancy.status.in_(active_occupancy_statuses))
    ).all()
    for listing_id, city in occupancy_rows:
        _record(listing_id, city)

    ranked = sorted(active_count_by_city.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        OccupancyByCityPoint(
            city=city,
            occupancy=min(100, round(100 * active_count / max(1, len(engaged_listings_by_city[city])))),
        )
        for city, active_count in ranked
    ]
