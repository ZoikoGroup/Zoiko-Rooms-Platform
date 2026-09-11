from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.crud.eligibility import check_move_in_eligibility
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.finance import CADENCE_INTERVAL_DAYS, OBLIGATION_TYPE_TO_PLANE, Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.occupancy_activation import OccupancyHandoverEvent
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


def _next_due_date(last_due: date, cadence: str, custom_interval_days: int | None = None) -> date:
    """ZR-ENG-CLR-005 AC-06: the next RENT obligation's due date, one full
    period after `last_due`, per the schedule's cadence. MONTHLY keeps the
    exact calendar-month arithmetic this codebase already used before AC-06
    (day-of-month preserved, clamped at month end); FORTNIGHTLY/WEEKLY are a
    fixed number of days later (models/finance.py:CADENCE_INTERVAL_DAYS);
    CUSTOM reads its interval from the schedule row itself rather than a
    fixed constant. Never called for UPFRONT -- callers must short-circuit
    before reaching here, since an UPFRONT schedule has nothing left to
    schedule after its one lump-sum obligation."""
    if cadence == "CUSTOM":
        return last_due + timedelta(days=custom_interval_days)
    if cadence in CADENCE_INTERVAL_DAYS:
        return last_due + timedelta(days=CADENCE_INTERVAL_DAYS[cadence])
    return _add_months(last_due, 1)


def get_occupancy_or_404(db: Session, occupancy_id: int) -> Occupancy:
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")
    return occupancy


def confirm_move_in(db: Session, agreement: Agreement, admin: AdminUser) -> Occupancy:
    offer = agreement.offer
    assert_provider_access(db, admin, party_id_for_listing(offer.listing))

    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending occupancy found for this agreement")
    if occupancy.status == "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy is already active")
    if occupancy.status == "ENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy has already ended and cannot be reactivated")
    if occupancy.status != "PENDING_MOVE_IN":
        raise HTTPException(status.HTTP_409_CONFLICT, "Occupancy is not awaiting move-in")

    reasons = check_move_in_eligibility(db, agreement)
    if reasons:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "Not eligible to confirm move-in", "reasons": reasons})

    occupancy.status = "ACTIVE"
    occupancy.move_in_date = date.today()
    db.flush()

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

    # The route commits this state transition together with its persisted
    # activation decision, audit row, and outbox events.
    db.flush()
    return occupancy


def record_handover_event(
    db: Session, occupancy: Occupancy, *, event_type: str, actor_kind: str,
    actor_admin_id: int | None = None, actor_user_id: int | None = None,
    evidence_ref: str = "", notes: str = "", correlation_id: str = "",
) -> OccupancyHandoverEvent:
    """Create one immutable evidence event, or return an exact retry safely."""
    existing = db.scalar(
        select(OccupancyHandoverEvent).where(
            OccupancyHandoverEvent.occupancy_id == occupancy.id,
            OccupancyHandoverEvent.event_type == event_type,
        )
    )
    if existing:
        compatible = (
            existing.actor_kind == actor_kind
            and existing.actor_admin_id == actor_admin_id
            and existing.actor_user_id == actor_user_id
            and existing.evidence_ref == evidence_ref
            and existing.notes == notes
        )
        if compatible:
            return existing
        raise HTTPException(status.HTTP_409_CONFLICT, "Conflicting handover evidence already exists")
    if occupancy.status != "PENDING_MOVE_IN":
        raise HTTPException(status.HTTP_409_CONFLICT, "Handover evidence can only be recorded for pending move-in occupancy")
    event = OccupancyHandoverEvent(
        occupancy_id=occupancy.id,
        event_type=event_type,
        actor_kind=actor_kind,
        actor_admin_id=actor_admin_id,
        actor_user_id=actor_user_id,
        evidence_ref=evidence_ref,
        notes=notes,
        correlation_id=correlation_id,
    )
    db.add(event)
    db.flush()
    return event


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

    # ZR-ENG-CLR-005 AC-02/AC-07/AC-06: source both the recurring amount and
    # the cadence from the agreement's PaymentSchedule when one exists, so
    # this obligation is traceable to the same versioned plan the first one
    # was generated from -- not "whatever the last row happened to say" and
    # not an assumed calendar month. Falls back to the old
    # every-calendar-month-at-the-last-amount logic when no schedule exists
    # (e.g. an obligation created outside the agreement path) rather than
    # ever erroring on its absence.
    schedule = None
    if occupancy.offer.agreement:
        schedule = db.scalar(
            select(PaymentSchedule).where(
                PaymentSchedule.agreement_id == occupancy.offer.agreement.id, PaymentSchedule.status == "ACTIVE",
            )
        )
    # ZR-ENG-CLR-005 AC-06: an UPFRONT schedule's one obligation already
    # covers the entire term -- there is nothing left to schedule, ever.
    if schedule and schedule.cadence == "UPFRONT":
        return None

    amount = schedule.amount if schedule else rent_obligations[-1].amount
    next_due = _next_due_date(
        last_due, schedule.cadence if schedule else "MONTHLY", schedule.custom_interval_days if schedule else None,
    )

    if occupancy.expected_end_date and next_due > occupancy.expected_end_date:
        return None

    already_exists = any(o.due_date == next_due for o in rent_obligations)
    if already_exists:
        return None

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

    # ZR-ENG-CLR-005 Section 13.1: same best-effort placement as
    # crud/finance.py::confirm_payment's receipt hook -- an invoice-rendering
    # failure must never undo or fail an already-committed obligation. An
    # admin/renter can always regenerate it on demand via
    # get_or_create_rent_invoice (called again from the download routes) if
    # this best-effort call somehow didn't run. Imported locally -- crud.finance
    # imports services.booking_orchestrator, which imports this module, so a
    # module-level import here would be circular.
    from app.crud.finance import get_or_create_rent_invoice
    try:
        get_or_create_rent_invoice(db, obligation)
    except Exception:
        pass

    return obligation


def end_occupancy(
    db: Session, occupancy: Occupancy, admin: AdminUser, correlation_id: str = "",
    *, notice_given_at: datetime | None = None, liability_end_date: date | None = None,
    termination_effective_date: date | None = None, move_out_date: date | None = None, basis: str = "OTHER",
    termination_case_id: int | None = None, override_reason: str = "",
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
    only need the current occupancy, this is the authoritative record.

    ZR-ENG-CLR-006 Section 7.1 Step 11/AC-05: termination_case_id, when
    given, links this evidence row back to the renter's own termination_case
    (and flips that case to TERMINATED) -- 'Move-out/vacant possession is
    confirmed separately' from the case itself, this is that confirmation
    step. Ending an occupancy EARLY (before its own expected_end_date) is
    'cancellation' under Section 6's own scope note and the spec's FINAL
    DECISION is explicit: 'An active occupancy is not cancelable by Host
    fiat.' So an early ending with no termination_case_id is only permitted
    as a Super Admin's own logged, reasoned override (AC-29's 'manual
    overrides require role authorization, reason... ' -- exactly the
    'Super Admin: exceptional controlled override' role this spec's own
    RBAC table describes) -- see the guard below. Ending AT/AFTER the
    occupancy's own expected_end_date is not a cancellation at all (the
    tenancy simply ran its course), so it stays the ordinary admin action
    unchanged."""
    from app.models.leasing import Agreement
    from app.models.termination_case import TerminationCase
    from app.models.termination_record import TerminationRecord

    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))

    case = None
    if termination_case_id is not None:
        case = db.get(TerminationCase, termination_case_id)
        if not case or case.occupancy_id != occupancy.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Termination case not found for this occupancy")
        if case.status != "EFFECTIVE_DATE_SET":
            raise HTTPException(status.HTTP_409_CONFLICT, "This termination case is not ready to be finalized")
    else:
        ending_early = occupancy.expected_end_date is not None and date.today() < occupancy.expected_end_date
        if ending_early:
            if admin.role != "super_admin":
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Ending an active occupancy early requires a termination case (ZR-ENG-CLR-006 Section 8) -- "
                    "ordinary Host/Admin accounts cannot end it directly",
                )
            if not override_reason.strip():
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "A reason is required to end an active occupancy early without a termination case (AC-29)",
                )

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
            termination_case_id=case.id if case else None,
            notice_given_at=notice_given_at, liability_end_date=occupancy.liability_end_date,
            termination_effective_date=occupancy.termination_effective_date,
            physical_move_out_date=resolved_move_out_date, created_by_admin_id=admin.id,
        ))
    if case is not None:
        case.status = "TERMINATED"

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
