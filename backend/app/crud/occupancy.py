from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.crud.eligibility import check_move_in_eligibility
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.finance import OBLIGATION_TYPE_TO_PLANE, Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.room import Room
from app.schemas.occupancy import OccupancyRead
from app.services import inventory as inventory_service


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
        notice_given_at=occupancy.notice_given_at,
        liability_end_date=occupancy.liability_end_date,
        termination_effective_date=occupancy.termination_effective_date,
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
    inventory_service.mark_hold_occupied(db, source_type="offer", source_id=offer.id)
    db.commit()
    db.refresh(occupancy)

    listing = offer.listing
    guest = db.get(Guest, occupancy.guest_id)
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest,
            title="You're moved in!",
            message=f"Your move-in for \"{offer.listing.name}\" is confirmed.",
            notification_type="occupancy.move_in_confirmed",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Move-in confirmed",
            message=f'A tenant has moved in to "{listing.name}".',
            notification_type="occupancy.move_in_confirmed_for_host",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
    return occupancy


def list_occupancies_for(db: Session, admin: AdminUser) -> list[Occupancy]:
    query = (
        select(Occupancy)
        .options(
            joinedload(Occupancy.listing),
            joinedload(Occupancy.room).joinedload(Room.property),
            joinedload(Occupancy.guest),
        )
        .order_by(Occupancy.created_at.desc())
    )
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

    # ZR-ENG-CLR-005 AC-02/AC-07: source the recurring amount from the
    # agreement's PaymentSchedule when one exists, so this obligation is
    # traceable to the same versioned plan the first one was -- not "whatever
    # the last row happened to say". Behaviorally identical today (nothing in
    # this codebase ever changes per-period rent, so schedule.amount and
    # rent_obligations[-1].amount are always equal), but falls back to the
    # old logic when no schedule exists (e.g. an obligation created outside
    # the agreement path) rather than ever erroring on its absence.
    schedule = None
    if occupancy.offer.agreement:
        schedule = db.scalar(
            select(PaymentSchedule).where(
                PaymentSchedule.agreement_id == occupancy.offer.agreement.id, PaymentSchedule.status == "ACTIVE",
            )
        )
    amount = schedule.amount if schedule else rent_obligations[-1].amount

    obligation = Obligation(
        obligation_type="RENT",
        money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=amount,
        due_date=next_due,
        occupancy_id=occupancy.id,
        schedule_id=schedule.id if schedule else None,
    )
    db.add(obligation)
    db.commit()
    db.refresh(obligation)
    return obligation


def end_occupancy(
    db: Session, occupancy: Occupancy, admin: AdminUser, correlation_id: str = "",
    *, notice_given_at: datetime | None = None, liability_end_date: date | None = None,
    termination_effective_date: date | None = None, move_out_date: date | None = None, basis: str = "OTHER",
) -> Occupancy:
    """ZR-ENG-CLR-004 AC-20/10.2: 'Final occupancy date, rent liability end
    date and physical move-out date may differ and must be separately
    stored.' move_out_date always gets set (today, unless the caller
    supplies an actual physical-departure date); liability_end_date and
    termination_effective_date default to that same date when not given
    explicitly -- the common case where all three dates coincide -- but a
    caller with a genuine notice-period/early-exit workflow can pass distinct
    values for each.

    Section 13.1 termination_record: also creates the dedicated,
    independently-queryable evidence row the spec names -- Occupancy's own
    flat columns above stay as a denormalized convenience for reads that
    only need the current occupancy, this is the authoritative record."""
    from app.models.leasing import Agreement
    from app.models.termination_record import TerminationRecord

    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    resolved_move_out_date = move_out_date or date.today()
    occupancy.status = "ENDED"
    occupancy.move_out_date = resolved_move_out_date
    occupancy.notice_given_at = notice_given_at
    occupancy.liability_end_date = liability_end_date or resolved_move_out_date
    occupancy.termination_effective_date = termination_effective_date or resolved_move_out_date
    occupancy.ended_at = datetime.now(timezone.utc)

    agreement = db.query(Agreement).filter(Agreement.offer_id == occupancy.offer_id).first()
    if agreement is not None:
        db.add(TerminationRecord(
            occupancy_id=occupancy.id, agreement_id=agreement.id, basis=basis,
            notice_given_at=notice_given_at, liability_end_date=occupancy.liability_end_date,
            termination_effective_date=occupancy.termination_effective_date,
            physical_move_out_date=resolved_move_out_date, created_by_admin_id=admin.id,
        ))

    # Frees the room's Inventory Service hold -- a new tenant can now be
    # held/booked for this same room (see services/inventory.py).
    inventory_service.release_hold(
        db, source_type="offer", source_id=occupancy.offer_id, reason="occupancy_ended",
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(occupancy)

    listing = occupancy.listing
    guest = db.get(Guest, occupancy.guest_id)
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest,
            title="Move-out recorded",
            message=f'Your tenancy at "{listing.name}" has ended.',
            notification_type="occupancy.ended",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Move-out recorded",
            message=f'A tenancy at "{listing.name}" has ended.',
            notification_type="occupancy.ended_for_host",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
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
