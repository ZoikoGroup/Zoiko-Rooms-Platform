from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.eligibility import check_move_in_eligibility
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.schemas.occupancy import OccupancyRead


def to_occupancy_read(occupancy: Occupancy) -> OccupancyRead:
    return OccupancyRead(
        id=occupancy.id,
        offer_id=occupancy.offer_id,
        listing_id=occupancy.listing_id,
        listing_name=occupancy.listing.name,
        room_id=occupancy.room_id,
        property_address=occupancy.room.property.address if occupancy.room and occupancy.room.property else "",
        property_city=occupancy.room.property.city if occupancy.room and occupancy.room.property else "",
        guest_id=occupancy.guest_id,
        guest_name=occupancy.guest.name,
        status=occupancy.status,
        move_in_date=occupancy.move_in_date,
        expected_end_date=occupancy.expected_end_date,
        move_out_date=occupancy.move_out_date,
        requested_move_out_date=occupancy.requested_move_out_date,
        move_out_requested_at=occupancy.move_out_requested_at,
        created_at=occupancy.created_at,
        ended_at=occupancy.ended_at,
    )


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def get_occupancy_or_404(db: Session, occupancy_id: int) -> Occupancy:
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    return occupancy


def confirm_move_in(db: Session, agreement: Agreement, admin: AdminUser) -> Occupancy:
    offer = agreement.offer
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))

    existing = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if existing:
        return existing

    reasons = check_move_in_eligibility(db, agreement)
    if reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not eligible to confirm move-in", "reasons": reasons})

    latest_terms = offer.terms[-1]
    today = date.today()
    occupancy = Occupancy(
        offer_id=offer.id,
        listing_id=offer.listing_id,
        room_id=offer.listing.room_id,
        guest_id=offer.guest_id,
        status="ACTIVE",
        move_in_date=today,
        expected_end_date=_add_months(latest_terms.start_date, latest_terms.term_months),
    )
    db.add(occupancy)
    db.flush()
    notif_crud.notify_user_by_guest(
        db, offer.guest,
        title="Move-in confirmed",
        message=f"Your move-in for {offer.listing.name} has been confirmed. Welcome!",
        notification_type="occupancy.move_in_confirmed",
        related_entity_type="occupancy", related_entity_id=str(occupancy.id),
    )
    db.commit()
    db.refresh(occupancy)
    return occupancy


def list_occupancies_for(db: Session, admin: AdminUser) -> list[Occupancy]:
    query = select(Occupancy).order_by(Occupancy.created_at.desc())
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Occupancy.listing_id).where(Listing.owner_id == admin.id)
    return list(db.scalars(query))


def generate_next_rent_obligation(db: Session, occupancy: Occupancy, admin: AdminUser) -> Obligation | None:
    """Idempotent: no scheduler exists in this stack, so recurring rent is generated
    on demand -- automatically right after the current period's rent obligation is
    marked paid, or manually via an admin action. Calling this twice for the same
    period never creates a duplicate obligation, and it refuses to run past the
    lease's expected end date without a renewal step."""
    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy is not active")

    # The very first rent obligation is created at agreement stage (before the
    # occupancy exists) and so is linked via agreement_id, not occupancy_id --
    # recurring generation has to look at both to find the last scheduled period.
    agreement_obligations = occupancy.offer.agreement.obligations if occupancy.offer.agreement else []
    all_obligations = list(occupancy.obligations) + list(agreement_obligations)
    rent_obligations = sorted(
        [o for o in all_obligations if o.obligation_type == "RENT"],
        key=lambda o: o.due_date,
    )
    if not rent_obligations:
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy has no initial rent obligation to schedule from")

    last_due = rent_obligations[-1].due_date
    next_due = _add_months(last_due, 1)

    if occupancy.expected_end_date and next_due > occupancy.expected_end_date:
        return None

    already_exists = any(o.due_date == next_due for o in rent_obligations)
    if already_exists:
        return None

    obligation = Obligation(
        obligation_type="RENT",
        money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=rent_obligations[-1].amount,
        due_date=next_due,
        occupancy_id=occupancy.id,
    )
    db.add(obligation)
    db.flush()
    notif_crud.notify_user_by_guest(
        db, occupancy.guest,
        title="Rent due",
        message=f"Rent of {obligation.amount:.2f} for {occupancy.listing.name} is due on {next_due.isoformat()}.",
        notification_type="occupancy.rent_due",
        related_entity_type="obligation", related_entity_id=str(obligation.id),
    )
    db.commit()
    db.refresh(obligation)
    return obligation


def end_occupancy(db: Session, occupancy: Occupancy, admin: AdminUser) -> Occupancy:
    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    occupancy.status = "ENDED"
    occupancy.move_out_date = date.today()
    occupancy.ended_at = datetime.now(timezone.utc)
    notif_crud.notify_user_by_guest(
        db, occupancy.guest,
        title="Tenancy ended",
        message=f"Your tenancy at {occupancy.listing.name} has ended.",
        notification_type="occupancy.ended",
        related_entity_type="occupancy", related_entity_id=str(occupancy.id),
    )
    db.commit()
    db.refresh(occupancy)
    return occupancy


def request_move_out(db: Session, occupancy: Occupancy, desired_date: date) -> Occupancy:
    """Renter's own notice of intent to vacate. Doesn't end the occupancy
    directly -- that stays an admin action (end_occupancy), since move-out
    also involves deposit inspection/release handled through Finance. This
    just records the request and notifies whoever can act on it, closing the
    "no way to vacate" gap: previously a renter had no way to signal intent to
    move out at all."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can have a move-out requested")
    if desired_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Desired move-out date cannot be in the past")

    occupancy.requested_move_out_date = desired_date
    occupancy.move_out_requested_at = datetime.now(timezone.utc)

    notif_crud.notify_user_by_party(
        db, party_id_for_listing(occupancy.listing),
        title="Move-out requested",
        message=f"{occupancy.guest.name} requested to move out of {occupancy.listing.name} on {desired_date.isoformat()}.",
        notification_type="occupancy.move_out_requested",
        related_entity_type="occupancy", related_entity_id=str(occupancy.id),
    )
    notif_crud.notify_all_super_admins(
        db,
        title="Move-out requested",
        message=f"{occupancy.guest.name} requested to move out of {occupancy.listing.name} on {desired_date.isoformat()}.",
        notification_type="occupancy.move_out_requested",
        related_entity_type="occupancy", related_entity_id=str(occupancy.id),
    )

    db.commit()
    db.refresh(occupancy)
    return occupancy


def list_occupancies_missing_upcoming_rent(db: Session, admin: AdminUser) -> list[Occupancy]:
    """Manual substitute for a cron tick -- surfaces active occupancies with no
    upcoming PENDING rent obligation, since generation is triggered by admin action
    rather than a background job."""
    query = select(Occupancy).where(Occupancy.status == "ACTIVE")
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Occupancy.listing_id).where(Listing.owner_id == admin.id)
    occupancies = db.scalars(query).all()
    today = date.today()
    missing = []
    for occupancy in occupancies:
        upcoming = [
            o for o in occupancy.obligations
            if o.obligation_type == "RENT" and o.status == "PENDING" and o.due_date >= today
        ]
        if not upcoming:
            missing.append(occupancy)
    return missing
