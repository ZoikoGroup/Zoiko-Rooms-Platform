# NOTE: every function below combines the legacy short-stay Booking table
# with the real self-service rental lifecycle (confirmed rent for revenue,
# Occupancy for occupancy/bookings-by-type), so a renter who went through the
# actual Application -> Offer -> Agreement -> Occupancy flow shows up in these
# admin charts too, not just legacy admin-created bookings. Application rows
# are deliberately not counted here: they're not yet a completed rental or
# a confirmed payment, so including them would inflate "bookings"/"revenue"
# with unconfirmed activity.
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.booking import Booking
from app.models.finance import Obligation, PaymentAllocation, SimulatedPayment
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.rental_payment import RentalPaymentObligation, RentalPaymentRecord
from app.schemas.analytics import BookingsByTypePoint, OccupancyByCityPoint, RevenueSummary, RevenueTrendPoint
from app.services.fx import REPORTING_CURRENCY, to_usd

PROPERTY_TYPE_LABELS = {
    "private_room": "Private Rooms",
}

class _MonthlyRevenue:
    """Revenue per (year, month) in US dollars (services/fx.py), plus what
    went into it."""

    def __init__(self) -> None:
        self.usd: dict[tuple[int, int], float] = {}
        self.events: dict[tuple[int, int], set[tuple[str, object]]] = {}
        self.currencies: set[str] = set()
        # Currencies with no configured rate -- left out of the totals rather
        # than counted as if they were dollars.
        self.unconverted: set[str] = set()

    def add(self, at: datetime, amount: float, currency: str | None, event: tuple[str, object]) -> None:
        code = (currency or "").strip().upper()
        usd = to_usd(amount, code)
        if usd is None:
            self.unconverted.add(code or "UNKNOWN")
            return
        self.currencies.add(code)
        bucket = (at.year, at.month)
        self.usd[bucket] = self.usd.get(bucket, 0.0) + usd
        self.events.setdefault(bucket, set()).add(event)


def _revenue_by_month(db: Session) -> _MonthlyRevenue:
    """Merges legacy Booking revenue (nights * price, at booking creation
    time) with real rental-lifecycle rent, bucketed by month at confirmation
    time: SimulatedPayment amounts allocated to RENT obligations, plus
    recipient-confirmed RentalPaymentRecords against RENT obligations (the
    ZR-PAY-002 record path, which never creates a SimulatedPayment).
    Deposits are excluded on both paths -- held, not earned. Every amount is
    converted to US dollars at the configured rate, so rupees, pounds and
    dollars add up to a real total. Both the Revenue Trend chart
    (revenue_trend) and the Total Revenue card (revenue_summary) read from
    here, so they always agree. Rows are bucketed in Python rather than with
    Postgres' date_trunc so this also runs on the SQLite test database."""
    out = _MonthlyRevenue()

    for booking in db.scalars(
        select(Booking).options(joinedload(Booking.listing)).where(Booking.payment_status == "paid")
    ):
        currency = booking.listing.currency if booking.listing is not None else None
        out.add(booking.created_at, float(booking.total_amount), currency, ("booking", booking.id))

    payment_rows = db.execute(
        select(SimulatedPayment.id, SimulatedPayment.confirmed_at, PaymentAllocation.amount_allocated, Obligation.currency)
        .join(PaymentAllocation, PaymentAllocation.payment_id == SimulatedPayment.id)
        .join(Obligation, Obligation.id == PaymentAllocation.obligation_id)
        .where(
            SimulatedPayment.status == "SUCCEEDED",
            SimulatedPayment.confirmed_at.is_not(None),
            Obligation.obligation_type == "RENT",
        )
    ).all()
    for payment_id, confirmed_at, amount, currency in payment_rows:
        out.add(confirmed_at, float(amount), currency, ("payment", payment_id))

    record_rows = db.execute(
        select(
            RentalPaymentRecord.id, RentalPaymentRecord.confirmed_at, RentalPaymentRecord.confirmed_amount,
            RentalPaymentObligation.currency,
        )
        .join(RentalPaymentObligation, RentalPaymentObligation.id == RentalPaymentRecord.obligation_id)
        .where(
            RentalPaymentRecord.status == "CONFIRMED",
            RentalPaymentRecord.confirmed_at.is_not(None),
            RentalPaymentObligation.obligation_type == "RENT",
        )
    ).all()
    for record_id, confirmed_at, amount, currency in record_rows:
        out.add(confirmed_at, float(amount or 0), currency, ("record", record_id))
    return out


def revenue_trend(db: Session, months: int = 6) -> list[RevenueTrendPoint]:
    """The last `months` months of _revenue_by_month (US dollars), for the chart."""
    monthly = _revenue_by_month(db)
    ordered = sorted(monthly.usd)[-months:]
    return [
        RevenueTrendPoint(
            month=datetime(year, month, 1).strftime("%b"),
            revenue=round(monthly.usd[(year, month)], 2),
            bookings=len(monthly.events[(year, month)]),
        )
        for year, month in ordered
    ]


def revenue_summary(db: Session) -> RevenueSummary:
    """ZR-73: the Total Revenue card, from the same data as the Revenue Trend
    chart (legacy paid bookings + confirmed rent) -- the card used to sum only
    legacy bookings and showed 0 while the chart showed this month's rent.
    All amounts are in US dollars (services/fx.py). change_pct compares this
    calendar month with last month; None when last month had no revenue (no
    real comparison to show)."""
    monthly = _revenue_by_month(db)
    now = datetime.now(timezone.utc)
    this_month = (now.year, now.month)
    last_month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    this_amount = monthly.usd.get(this_month, 0.0)
    last_amount = monthly.usd.get(last_month, 0.0)
    change_pct = round((this_amount - last_amount) / last_amount * 100, 1) if last_amount else None
    return RevenueSummary(
        total_revenue=round(sum(monthly.usd.values()), 2),
        this_month_revenue=round(this_amount, 2),
        last_month_revenue=round(last_amount, 2),
        change_pct=change_pct,
        paid_events=sum(len(e) for e in monthly.events.values()),
        currency=REPORTING_CURRENCY,
        source_currencies=sorted(monthly.currencies),
        unconverted_currencies=sorted(monthly.unconverted),
        fx_rates_as_of=settings.fx_rates_as_of,
    )


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
