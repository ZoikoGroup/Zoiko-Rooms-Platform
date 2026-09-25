from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.crud.eligibility import check_move_in_eligibility
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, assert_provider_access_any, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.user_account import UserAccount
from app.models.finance import CADENCE_INTERVAL_DAYS, OBLIGATION_TYPE_TO_PLANE, Obligation, PaymentSchedule
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy, OccupancyCoTenant
from app.models.occupancy_activation import OccupancyHandoverEvent
from app.models.room import Room
from app.schemas.occupancy import OccupancyRead
from app.services import inventory as inventory_service


def get_reassigned_via_sublet_request_id(db: Session, occupancy_id: int) -> int | None:
    """ZR-SUB-003 Section 3: the same fact crud/sublet.py:_assert_sublet_permitted
    already enforces (an occupancy that changed hands once via ASSIGNMENT_FULL/
    REPLACEMENT_OCCUPANT can't be sublet onward again) -- exposed for read
    surfaces (admin OccupancyRead, the renter's own UserOccupancyRead) so
    neither UI offers an action the backend will just 409 on."""
    from app.models.sublet_request import REPLACING_ARRANGEMENT_TYPES, SubletRequest

    return db.scalar(
        select(SubletRequest.id).where(
            SubletRequest.current_occupancy_id == occupancy_id,
            SubletRequest.status == "approved",
            SubletRequest.arrangement_type.in_(REPLACING_ARRANGEMENT_TYPES),
        ).limit(1)
    )


def to_occupancy_read(db: Session, occupancy: Occupancy) -> OccupancyRead:
    reassigned_via = get_reassigned_via_sublet_request_id(db, occupancy.id)
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
        reassigned_via_sublet_request_id=reassigned_via,
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


def add_co_tenant(db: Session, occupancy: Occupancy, admin: AdminUser, guest_id: str) -> OccupancyCoTenant:
    """ZR-ENG-CLR-006 AC-25 -- see models/occupancy.py:OccupancyCoTenant's
    own docstring. Adding one changes how crud/termination.py routes a
    later termination case for this occupancy; it does not itself alter
    who Occupancy.guest_id is."""
    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    if guest_id == occupancy.guest_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This guest is already the occupancy's own tenant of record")
    guest = db.get(Guest, guest_id)
    if guest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Guest not found")
    existing = db.scalar(
        select(OccupancyCoTenant).where(
            OccupancyCoTenant.occupancy_id == occupancy.id, OccupancyCoTenant.guest_id == guest_id,
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This guest is already a co-tenant on this occupancy")

    co_tenant = OccupancyCoTenant(occupancy_id=occupancy.id, guest_id=guest_id, added_by_admin_id=admin.id)
    db.add(co_tenant)
    db.commit()
    db.refresh(co_tenant)
    return co_tenant


def list_co_tenants_for_occupancy(db: Session, occupancy: Occupancy) -> list[OccupancyCoTenant]:
    return list(
        db.scalars(
            select(OccupancyCoTenant).where(OccupancyCoTenant.occupancy_id == occupancy.id).order_by(OccupancyCoTenant.added_at)
        )
    )


def has_co_tenants(db: Session, occupancy_id: int) -> bool:
    """ZR-ENG-CLR-006 AC-25: read by crud/termination.py to decide whether
    a case can auto-resolve at all -- see OccupancyCoTenant's own
    docstring for why a joint occupancy always falls to PENDING_REVIEW."""
    return db.scalar(
        select(OccupancyCoTenant.id).where(OccupancyCoTenant.occupancy_id == occupancy_id).limit(1)
    ) is not None


def confirm_move_in(db: Session, agreement: Agreement, actor: AdminUser | UserAccount) -> Occupancy:
    """actor is AdminUser | UserAccount, not AdminUser-only -- ZR-ENG-CLR-011
    Section 10/ZR-ENG-CLR-004 Section 4.3 posture (see
    assert_provider_access_any's own docstring): confirming move-in is a
    Host commercial action, not exclusively an admin-portal one. A Zoiko
    platform admin (via api/routes/occupancy.py) and a self-service Host's
    own UserAccount (via api/routes/user_hosting.py) both reach here."""
    offer = agreement.offer
    assert_provider_access_any(db, actor, party_id_for_listing(offer.listing))

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
    # AC-18: expected_end_date is recomputed here from the offer's current
    # terms, not left at whatever value was set when the occupancy row was
    # first created (at signing) -- an amendment approved between signing
    # and move-in (crud/agreement_amendments.py persists a new OfferTerms
    # row precisely so this stays in sync) must be reflected, not the stale
    # original start_date/term_months.
    latest_terms = offer.terms[-1]
    occupancy.expected_end_date = _add_months(latest_terms.start_date, latest_terms.term_months)
    # The room hold moves BOOKED -> OCCUPIED only once the tenant actually
    # moves in (mark_hold_booked already ran at offer-acceptance time) --
    # this call used to live in the occupancy-creation step before the
    # PENDING_MOVE_IN lifecycle change moved that step to signing, and was
    # never carried over to confirm_move_in.
    inventory_service.mark_hold_occupied(db, source_type="offer", source_id=offer.id)
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
    from app.models.occupancy_activation import MOVE_OUT_HANDOVER_EVENT_TYPES

    if event_type in MOVE_OUT_HANDOVER_EVENT_TYPES:
        if occupancy.status != "ACTIVE":
            raise HTTPException(status.HTTP_409_CONFLICT, "Move-out evidence can only be recorded for an active occupancy")
    elif occupancy.status != "PENDING_MOVE_IN":
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


def list_occupancies_for_room_owned_by(db: Session, user: UserAccount, room: Room) -> list[Occupancy]:
    """Host self-service counterpart to list_occupancies_for above -- that one
    is AdminUser+Listing.owner_id-scoped (the legacy provider-admin console);
    a self-service host authenticates as a UserAccount instead, so this uses
    the same room.property.owner_party_id ownership check already
    established for authority/property-verification host routes in
    api/routes/user_hosting.py, rather than Listing.owner_id."""
    if not user.party_id or not room.property or room.property.owner_party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view occupancies for your own room")
    return list(
        db.scalars(select(Occupancy).where(Occupancy.room_id == room.id).order_by(Occupancy.created_at.desc()))
    )


def generate_next_rent_obligation(db: Session, occupancy: Occupancy, admin: AdminUser | None) -> Obligation | None:
    """Idempotent: no scheduler exists in this stack, so recurring rent is generated
    on demand -- automatically right after the current period's rent obligation is
    marked paid, or manually via an admin action. Calling this twice for the same
    period never creates a duplicate obligation, and it refuses to run past the
    lease's expected end date without a renewal step.

    admin=None is the cross-domain-bridge caller (crud/rental_payment.py:
    _sync_legacy_obligation_from_confirmation, triggered by the NEW
    ZR-PAY-LINK-003 domain's own record confirming, not by an admin acting)
    -- skips the ownership check below the same way
    services/booking_orchestrator.py:generate_downstream_rent already
    swallows a real admin's own ownership mismatch here (line ~55-57)
    rather than letting it block an already-confirmed payment; every other
    caller keeps passing a real admin and is unaffected."""
    if admin is not None:
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
    currency = schedule.currency if schedule else rent_obligations[-1].currency
    next_due = _next_due_date(
        last_due, schedule.cadence if schedule else "MONTHLY", schedule.custom_interval_days if schedule else None,
    )

    if occupancy.expected_end_date and next_due > occupancy.expected_end_date:
        # Section 9 gap: opt-in holdover billing -- see
        # models/market_policy.py:holdover_allowed's own docstring for why
        # this stays a no-op (the pre-existing, tested behavior) unless the
        # resolved jurisdiction has explicitly turned holdover on.
        from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy

        policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
        if not policy.holdover_allowed:
            return None
        amount = round(float(amount) * float(policy.holdover_rent_multiple), 2)

    already_exists = any(o.due_date == next_due for o in rent_obligations)
    if already_exists:
        return None

    obligation = Obligation(
        obligation_type="RENT",
        money_plane=OBLIGATION_TYPE_TO_PLANE["RENT"],
        amount=amount,
        currency=currency,
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

    # ZR-PAY-002 Section 4/6: the record/evidence-layer counterpart to the
    # custody-based Obligation above -- see models/rental_payment.py's own
    # module docstring. Same best-effort placement as the rent-invoice hook.
    try:
        from app.crud.rental_payment import create_obligation as create_rental_payment_obligation
        from app.crud.rental_payment import resolve_rent_recipient_party_id

        recipient_party_id = resolve_rent_recipient_party_id(db, occupancy.room) if occupancy.room else None
        if recipient_party_id is not None:
            create_rental_payment_obligation(
                db, obligation_type="RENT", tenant_guest_id=occupancy.guest_id, recipient_party_id=recipient_party_id,
                amount=amount, currency=currency, due_date=next_due, occupancy_id=occupancy.id,
            )
    except Exception:
        pass

    return obligation


def end_occupancy(
    db: Session, occupancy: Occupancy, admin: AdminUser, correlation_id: str = "",
    *, notice_given_at: datetime | None = None, liability_end_date: date | None = None,
    termination_effective_date: date | None = None, move_out_date: date | None = None, basis: str = "OTHER",
    termination_case_id: int | None = None, override_reason: str = "", already_adjudicated: bool = False,
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
    unchanged.

    already_adjudicated is the third, narrower escape from that guard --
    deliberately absent from OccupancyEndRequest (the public /end endpoint's
    own schema), so no Host request body can ever set it. It exists only for
    a trusted internal caller that already went through its own lawful,
    renter-initiated approval process before ever reaching here -- today,
    crud/booking_change_requests.py's PREMISES_CHANGE migration completing
    once the replacement agreement is fully signed. That is not 'Host
    fiat' (the renter requested the move, an admin already approved it);
    it just isn't shaped like a termination_case either."""
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
    elif not already_adjudicated:
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
    # Section 9 gap: if the renter already gave real move-out notice through
    # the handshake above, use its own timestamp rather than leaving
    # notice_given_at null just because this caller didn't pass one
    # explicitly -- never overrides an explicitly-supplied value.
    if notice_given_at is None:
        notice_event = db.scalar(
            select(OccupancyHandoverEvent).where(
                OccupancyHandoverEvent.occupancy_id == occupancy.id,
                OccupancyHandoverEvent.event_type == "MOVE_OUT_NOTICE_GIVEN",
            )
        )
        if notice_event is not None:
            notice_given_at = notice_event.created_at

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


def _pre_move_in_monthly_rent(db: Session, agreement: Agreement) -> float | None:
    """Same ACTIVE-PaymentSchedule read as
    crud/refund_entitlement.py:_monthly_rent_amount -- duplicated, not
    cross-imported, matching this codebase's own convention for this exact
    kind of small private helper (see crud/sublet.py's _add_months for the
    same pattern)."""
    schedule = db.scalar(
        select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement.id, PaymentSchedule.status == "ACTIVE")
    )
    return float(schedule.amount) if schedule else None


def _resolve_pre_move_in_cancellation_fee(db: Session, occupancy: Occupancy, agreement: Agreement) -> tuple[float, str]:
    """Section 7 gap: the free-cancellation-window + fee-outside-it rule
    that previously didn't exist at all for a pre-move-in booking."""
    from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy

    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
    hours_since_booking = (datetime.now(timezone.utc) - occupancy.created_at).total_seconds() / 3600
    if hours_since_booking <= policy.pre_move_in_free_cancellation_hours:
        return 0.0, (
            f"Cancelled within the {policy.pre_move_in_free_cancellation_hours}-hour free-cancellation window -- "
            "full refund, no fee."
        )
    if policy.pre_move_in_cancellation_fee_rent_multiple <= 0:
        return 0.0, "Outside the free-cancellation window, but no cancellation fee is configured -- full refund."
    monthly_rent = _pre_move_in_monthly_rent(db, agreement)
    if monthly_rent is None:
        return 0.0, "Outside the free-cancellation window, but no active payment schedule to derive a fee from -- full refund."
    fee = round(monthly_rent * float(policy.pre_move_in_cancellation_fee_rent_multiple), 2)
    return fee, (
        f"Outside the {policy.pre_move_in_free_cancellation_hours}-hour free-cancellation window -- a "
        f"{policy.pre_move_in_cancellation_fee_rent_multiple:g}x monthly rent cancellation fee applies."
    )


def cancel_before_move_in(
    db: Session, occupancy: Occupancy, *,
    guest: "Guest | None" = None, host_party_id: int | None = None, admin: AdminUser | None = None,
    reason: str = "", correlation_id: str = "",
) -> tuple[Occupancy, dict]:
    """Section 7 gap: the whole pre-move-in cancellation path this codebase
    was missing -- previously the only way to end a PENDING_MOVE_IN
    occupancy was the generic end_occupancy (a pure status-flip with zero
    money logic and zero role-specific entry point). Exactly one of
    guest/host_party_id/admin identifies who's cancelling (mirrors
    dispute_evidence.py:upload_evidence's own uploader_count == 1 shape).
    Renter and Host may each cancel their own booking; an admin may cancel
    any. Computes a real refund via _resolve_pre_move_in_cancellation_fee,
    then actually issues it through the same request_refund/decide_refund
    pipeline every other refund in this codebase goes through -- never a
    silent status flip with the money left untouched. Returns
    (occupancy, {'fee_amount', 'fee_note', 'refunded_amount'})."""
    from app.crud import finance as finance_crud
    from app.crud.party import assert_provider_access
    from app.models.leasing import Agreement
    from app.models.termination_record import TerminationRecord
    from app.schemas.finance import RefundDecide, RefundRequestCreate

    actor_count = sum(1 for a in (guest, host_party_id, admin) if a is not None)
    if actor_count != 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Exactly one of guest, host_party_id or admin must cancel")

    if occupancy.status != "PENDING_MOVE_IN":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only a booking still pending move-in can be cancelled this way (current status: {occupancy.status})",
        )

    if guest is not None and occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This booking does not belong to you")
    if host_party_id is not None and party_id_for_listing(occupancy.listing) != host_party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This booking does not belong to your property")
    if admin is not None:
        assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))

    agreement = db.query(Agreement).filter(Agreement.offer_id == occupancy.offer_id).first()
    fee_amount, fee_note = (0.0, "No agreement found to derive a fee from -- full refund.")
    if agreement is not None:
        fee_amount, fee_note = _resolve_pre_move_in_cancellation_fee(db, occupancy, agreement)

    refunded_amount = 0.0
    remaining_fee = fee_amount
    if agreement is not None:
        # Deposit is deducted from first (a fee is naturally a forfeiture of
        # part of the security deposit in ordinary practice), then rent.
        paid_obligations = sorted(
            (o for o in agreement.obligations if o.status == "PAID" and o.obligation_type in ("DEPOSIT", "RENT")),
            key=lambda o: 0 if o.obligation_type == "DEPOSIT" else 1,
        )
        for obligation in paid_obligations:
            paid_allocation = next((a for a in obligation.allocations if a.amount_allocated > 0), None)
            if paid_allocation is None:
                continue
            obligation_amount = float(obligation.amount)
            applied_fee = min(remaining_fee, obligation_amount)
            remaining_fee = round(remaining_fee - applied_fee, 2)
            refund_amount = round(obligation_amount - applied_fee, 2)
            if refund_amount <= 0:
                continue
            refund = finance_crud.request_refund(
                db,
                RefundRequestCreate(
                    payment_id=paid_allocation.payment_id, obligation_id=obligation.id, amount=refund_amount,
                    reason=f"Pre-move-in cancellation of occupancy #{occupancy.id}: {reason or 'no reason given'}",
                    idempotency_key=f"pre-move-in-cancel-occupancy-{occupancy.id}-obligation-{obligation.id}",
                ),
                admin or _system_admin_for_self_service_refund(db),
            )
            if refund.status == "REQUESTED":
                finance_crud.decide_refund(
                    db, refund, admin or _system_admin_for_self_service_refund(db), RefundDecide(approve=True),
                )
            refunded_amount = round(refunded_amount + refund_amount, 2)

    occupancy.status = "CANCELLED"
    occupancy.move_out_date = date.today()
    occupancy.ended_at = datetime.now(timezone.utc)

    if agreement is not None:
        db.add(TerminationRecord(
            occupancy_id=occupancy.id, agreement_id=agreement.id, basis="PRE_MOVE_IN_CANCELLATION",
            liability_end_date=date.today(), termination_effective_date=date.today(),
            physical_move_out_date=date.today(), created_by_admin_id=admin.id if admin else None,
        ))

    inventory_service.release_hold(
        db, source_type="offer", source_id=occupancy.offer_id, reason="pre_move_in_cancellation",
        correlation_id=correlation_id,
    )
    db.commit()
    db.refresh(occupancy)

    listing = occupancy.listing
    occupancy_guest = db.get(Guest, occupancy.guest_id)
    if occupancy_guest:
        notif_crud.notify_user_by_guest(
            db, occupancy_guest,
            title="Your booking was cancelled",
            message=f'Your booking at "{listing.name}" was cancelled before move-in. {fee_note}',
            notification_type="occupancy.cancelled_before_move_in",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="A booking was cancelled",
            message=f'A booking at "{listing.name}" was cancelled before move-in.',
            notification_type="occupancy.cancelled_before_move_in_for_host",
            related_entity_type="occupancy", related_entity_id=str(occupancy.id),
        )
    return occupancy, {"fee_amount": fee_amount, "fee_note": fee_note, "refunded_amount": refunded_amount}


def _system_admin_for_self_service_refund(db: Session) -> AdminUser:
    """A renter or Host cancelling themselves has no AdminUser session to
    attribute the resulting RefundRequest's requested_by_admin_id to --
    same system-actor resolution crud/payment_provider.py:get_system_admin
    already uses for a webhook-triggered action with no human admin behind
    it."""
    from app.crud.payment_provider import get_system_admin

    return get_system_admin(db)


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


def list_occupancies_in_holdover(db: Session, admin: AdminUser) -> list[Occupancy]:
    """Section 9 gap: previously nothing surfaced an ACTIVE occupancy that
    ran past its own expected_end_date with no renewal/termination case --
    it was a silent no-op (see generate_next_rent_obligation's own
    holdover_allowed handling above). Surfaces it regardless of whether
    holdover_allowed is configured for its jurisdiction -- an admin needs to
    know a tenant is holding over whether or not billing is continuing for
    it, the same "manual substitute for a cron tick" pattern as
    list_occupancies_missing_upcoming_rent above."""
    query = select(Occupancy).where(Occupancy.status == "ACTIVE", Occupancy.expected_end_date < date.today())
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Occupancy.listing_id).where(Listing.owner_id == admin.id)
    return list(db.scalars(query))
