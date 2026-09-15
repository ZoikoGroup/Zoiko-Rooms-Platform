"""ZR-ENG-CLR-008 Section 8 MVP slice: renter-initiated DATE_SHIFT (move-in
date, before the renter has moved in) and EXTENSION (move-out date pushed
later, after move-in). See models/booking_change_request.py for why these
are mutually exclusive states of the same agreement.

Approval drives the existing AgreementAmendment engine
(crud/agreement_amendments.py) for the actual contract re-papering step --
this module supplies the renter-initiated request/decision trail that engine
has no room for on its own (it is admin-only and starts already-classified).
A request only reaches "EFFECTIVE" once the resulting amendment is fully
re-signed (see crud/leasing.py's post-signature amendment hook) --
"AWAITING_AGREEMENT_ACTION" is the host/admin decision alone; see
app/services/booking_change_state_machine.py for the full status set and
transition graph.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import agreement_amendments as amendment_crud
from app.crud import notification as notif_crud
from app.crud.audit import log_audit_event
from app.crud.guest import get_guest_for_user
from app.crud.market_policy import resolve_market_policy
from app.crud.occupancy import _add_months
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.booking_change_request import BookingChangeRequest
from app.models.leasing import Agreement, Offer
from app.models.occupancy import Occupancy
from app.models.user_account import UserAccount
from app.schemas.leasing import BookingChangeRequestRead
from app.services.booking_change_consent import assert_proposal_unchanged, compute_proposal_hash
from app.services.booking_change_state_machine import transition

REQUEST_EXPIRY = timedelta(days=7)


def to_booking_change_request_read(bcr: BookingChangeRequest) -> BookingChangeRequestRead:
    agreement = bcr.agreement
    listing = agreement.offer.listing if agreement and agreement.offer else None
    guest = bcr.requested_by
    return BookingChangeRequestRead(
        id=bcr.id,
        agreement_id=bcr.agreement_id,
        requested_by_guest_id=bcr.requested_by_guest_id,
        change_type=bcr.change_type,
        status=bcr.status,
        original_start_date=bcr.original_start_date,
        proposed_start_date=bcr.proposed_start_date,
        original_end_date=bcr.original_end_date,
        proposed_end_date=bcr.proposed_end_date,
        additional_term_months=bcr.additional_term_months,
        target_listing_id=bcr.target_listing_id,
        resulting_application_id=bcr.resulting_application_id,
        original_monthly_rent=float(bcr.original_monthly_rent) if bcr.original_monthly_rent is not None else None,
        proposed_monthly_rent=float(bcr.proposed_monthly_rent) if bcr.proposed_monthly_rent is not None else None,
        reason=bcr.reason,
        decision_note=bcr.decision_note,
        decided_by_admin_id=bcr.decided_by_admin_id,
        decided_at=bcr.decided_at,
        resulting_amendment_id=bcr.resulting_amendment_id,
        created_at=bcr.created_at,
        expires_at=bcr.expires_at,
        authority_evidence_ref=bcr.authority_evidence_ref,
        original_deposit_amount=float(bcr.original_deposit_amount) if bcr.original_deposit_amount is not None else None,
        proposed_deposit_amount=float(bcr.proposed_deposit_amount) if bcr.proposed_deposit_amount is not None else None,
        listing_name=listing.name if listing else "",
        target_listing_name=bcr.target_listing.name if bcr.target_listing else "",
        guest_name=guest.name if guest else "",
    )


def get_booking_change_request_or_404(db: Session, bcr_id: int) -> BookingChangeRequest:
    bcr = db.get(BookingChangeRequest, bcr_id)
    if not bcr:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking change request not found")
    return bcr


def _current_start_date(offer: Offer) -> date:
    return offer.terms[-1].start_date


def _change_label(change_type: str) -> str:
    return {
        "EXTENSION": "stay extension", "SHORTENING": "stay shortening", "PREMISES_CHANGE": "room/property change",
        "FINANCIAL_CHANGE": "rent change", "TERM_SHIFT": "move-in date and term change",
        "LEGAL_ORDER_CHANGE": "legal/regulatory order change", "DEPOSIT_CHANGE": "deposit change",
    }.get(change_type, "move-in date change")


def _record_terminal_failure(
    db: Session, bcr: BookingChangeRequest, to_status: str, admin: AdminUser | None, note: str,
) -> None:
    """Shared CONFLICT/FAILED landing for a granted-but-then-failed approval:
    records the reason on the BCR, logs it to the same AuditEvent trail every
    other admin/system intervention in this codebase uses (AC-32/39 --
    replayable actor/reason/before-after, not a bespoke table), and tells the
    renter their original booking is untouched (doc Section 24 / the
    notification matrix's own wording: 'explicitly state original booking
    remains unchanged') so the next step is a fresh request, not a retry of
    this one."""
    transition(bcr, to_status, note=note)
    bcr.decision_note = note
    db.commit()
    db.refresh(bcr)

    log_audit_event(
        db, admin, f"booking_change.{to_status.lower()}", "booking_change_request", str(bcr.id),
        reason=note, before_state="AWAITING_HOST", after_state=to_status,
    )
    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} could not be completed",
        message=(
            f"Your requested {change_label} could not be completed ({note}). "
            "Your original booking is unchanged -- please submit a new request if you still want this change."
        ),
        notification_type=f"booking_change_request.{to_status.lower()}",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )


def _expire_if_overdue(db: Session, bcr: BookingChangeRequest) -> BookingChangeRequest:
    if bcr.status == "AWAITING_HOST" and bcr.expires_at <= datetime.now(timezone.utc):
        transition(bcr, "EXPIRED")
        db.commit()
        db.refresh(bcr)

        log_audit_event(
            db, None, "booking_change.expired", "booking_change_request", str(bcr.id),
            reason="expires_at passed with no host decision", before_state="AWAITING_HOST", after_state="EXPIRED",
        )
        change_label = _change_label(bcr.change_type)
        notif_crud.notify_user_by_guest(
            db, bcr.requested_by,
            title=f"Your {change_label} request expired",
            message=(
                f"Your requested {change_label} expired before your host made a decision. "
                "Your original booking is unchanged -- please submit a new request if you still want this change."
            ),
            notification_type="booking_change_request.expired",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_date_change(
    db: Session, user: UserAccount, agreement: Agreement, proposed_start_date: date, reason: str = "",
) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a fully signed agreement's move-in date can be changed this way",
        )

    offer = agreement.offer
    # The occupancy row now exists from signing onward (PENDING_MOVE_IN),
    # not just once actually moved in -- its mere existence is no longer a
    # valid "already moved in" signal, only its status is.
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if occupancy is not None and occupancy.status == "ACTIVE":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You have already moved in -- this move-in date can no longer be changed this way",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    if proposed_start_date == current_start:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date is the same as the current one")
    if proposed_start_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date cannot be in the past")

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="DATE_SHIFT", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=proposed_start_date, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Move-in date change requested",
            message=f'A tenant has requested to change their move-in date for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_term_shift(
    db: Session, user: UserAccount, agreement: Agreement, proposed_start_date: date, new_term_months: int, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 4: 'TERM_SHIFT | Both start and end move |
    Material; revalidation of inventory, pricing and legal commencement.'
    Before move-in only, like DATE_SHIFT -- the *start* date can't move once
    the renter has already moved in on it. new_term_months is absolute (the
    new term's length), not a delta -- see model docstring."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a fully signed agreement's move-in date/term can be changed this way",
        )

    offer = agreement.offer
    already_moved_in = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if already_moved_in is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You have already moved in -- this can no longer be changed this way",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    if proposed_start_date == current_start and new_term_months == current_term_months:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date and term are the same as the current ones")
    if proposed_start_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed move-in date cannot be in the past")
    if new_term_months < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New term must be at least 1 month")

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="TERM_SHIFT", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=proposed_start_date,
        original_end_date=_add_months(current_start, current_term_months),
        proposed_end_date=_add_months(proposed_start_date, new_term_months),
        additional_term_months=new_term_months, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Move-in date and term change requested",
            message=f'A tenant has requested to change both their move-in date and term for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_extension(
    db: Session, user: UserAccount, agreement: Agreement, additional_term_months: int, reason: str = "",
) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement can be extended")

    offer = agreement.offer
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if occupancy is None or occupancy.status != "ACTIVE":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An extension can only be requested once you've moved in and are an active tenant",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    new_term_months = current_term_months + additional_term_months
    proposed_end_date = _add_months(current_start, new_term_months)

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="EXTENSION", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=current_start,
        original_end_date=occupancy.expected_end_date, proposed_end_date=proposed_end_date,
        additional_term_months=additional_term_months, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Stay extension requested",
            message=f'A tenant has requested to extend their stay at "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_shortening(
    db: Session, user: UserAccount, agreement: Agreement, reduced_term_months: int, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 7.4/AC-10: shortening is only a valid amendment
    *before* move-in. Once an Occupancy exists, an early end routes to
    Section 6 termination (crud/occupancy.py:end_occupancy) instead -- this
    function refuses rather than silently reimplementing that routing or
    disguising termination economics as a harmless date edit."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement's term can be shortened this way")

    offer = agreement.offer
    # The occupancy row now exists from signing onward (PENDING_MOVE_IN),
    # not just once actually moved in -- its mere existence is no longer a
    # valid "already moved in" signal, only its status is.
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == offer.id))
    if occupancy is not None and occupancy.status == "ACTIVE":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You have already moved in -- an early end now goes through ending your tenancy, not a date-shortening request",
        )

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    new_term_months = current_term_months - reduced_term_months
    # AC-11: a zero-or-negative-length stay is a cancellation, not an
    # amendment -- not something this endpoint can route to on its own.
    if new_term_months < 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That reduction would leave no term remaining -- withdraw the booking through cancellation instead",
        )

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="SHORTENING", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=current_start,
        original_end_date=_add_months(current_start, current_term_months),
        proposed_end_date=_add_months(current_start, new_term_months),
        additional_term_months=-reduced_term_months, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Stay shortening requested",
            message=f'A tenant has requested to shorten their stay at "{listing.name}" before moving in.',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_premises_change(
    db: Session, user: UserAccount, agreement: Agreement, target_listing_id: str, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 14: 'A different room or property changes the
    subject matter of the booking and usually requires a new booking/version
    family.' Available both before and after move-in (unlike DATE_SHIFT/
    SHORTENING) -- a room swap is a legitimate ask at either stage, and the
    replacement flow (approve_change_request's PREMISES_CHANGE branch) opens
    a genuinely fresh Application rather than mutating anything in place."""
    from app.models.listing import Listing

    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement can request a room/property change")

    if target_listing_id == agreement.offer.listing_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is already your current listing")

    target_listing = db.get(Listing, target_listing_id)
    if not target_listing or target_listing.state != "PUBLISHED":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target listing not found or not currently published")

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="PREMISES_CHANGE", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=current_start,
        target_listing_id=target_listing_id, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Room/property change requested",
            message=f'A tenant is asking to move from "{listing.name}" to a different listing.',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def request_financial_change(
    db: Session, user: UserAccount, agreement: Agreement, proposed_monthly_rent: float, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 10/AC-24: renter requesting a new monthly rent.
    Gated by MarketPolicyPack.rent_change_min_interval_days -- both the doc's
    own NSW ('once in 12 months') and Ontario rent-guideline examples cite a
    minimum interval, not a blanket ban, so this checks the last EFFECTIVE
    financial change on this agreement rather than refusing every repeat."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement's rent can be changed this way")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    current_rent = float(offer.terms[-1].monthly_rent)
    if proposed_monthly_rent == current_rent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed rent is the same as the current rent")

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    policy = resolve_market_policy(db)
    last_effective = db.scalar(
        select(BookingChangeRequest)
        .where(
            BookingChangeRequest.agreement_id == agreement.id,
            BookingChangeRequest.change_type == "FINANCIAL_CHANGE",
            BookingChangeRequest.status == "EFFECTIVE",
        )
        .order_by(BookingChangeRequest.decided_at.desc())
    )
    if last_effective is not None and last_effective.decided_at is not None:
        days_since = (datetime.now(timezone.utc) - last_effective.decided_at).days
        if days_since < policy.rent_change_min_interval_days:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Rent was already changed {days_since} day(s) ago -- the resolved policy requires at least "
                f"{policy.rent_change_min_interval_days} days between rent changes (jurisdiction={policy.jurisdiction_code})",
            )

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="FINANCIAL_CHANGE", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=current_start,
        original_monthly_rent=current_rent, proposed_monthly_rent=proposed_monthly_rent, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Rent change requested",
            message=f'A tenant has requested a new monthly rent for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def _current_deposit_amount(agreement: Agreement) -> float:
    for obligation in agreement.obligations:
        if obligation.obligation_type == "DEPOSIT" and obligation.deposit_record is not None:
            return float(obligation.deposit_record.held_amount)
    return 0.0


def request_deposit_change(
    db: Session, user: UserAccount, agreement: Agreement, proposed_deposit_amount: float, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 4/11/DEPOSIT_CHANGE: request/decide only -- see
    model docstring for why approval never touches DepositRecord.held_amount.
    The renter is asking; if the admin approves, they still have to go
    action the actual amount change through the Deposit Records screen
    (crud/finance.py's release_deposit/submit_deposit_claim), same as any
    other deposit change on this platform."""
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != agreement.offer.guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")

    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement's deposit can be discussed this way")
    if proposed_deposit_amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed deposit amount cannot be negative")

    current_deposit = _current_deposit_amount(agreement)
    if proposed_deposit_amount == current_deposit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Proposed deposit amount is the same as the current one")

    open_request = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.agreement_id == agreement.id, BookingChangeRequest.status == "AWAITING_HOST",
        )
    )
    if open_request is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A change request is already pending for this agreement")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=guest.id, change_type="DEPOSIT_CHANGE", status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=current_start,
        original_deposit_amount=current_deposit, proposed_deposit_amount=proposed_deposit_amount, reason=reason,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.commit()
    db.refresh(bcr)

    listing = offer.listing
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Deposit change requested",
            message=f'A tenant has requested a deposit change for "{listing.name}".',
            notification_type="booking_change_request.submitted",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def approve_deposit_change(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str = "") -> BookingChangeRequest:
    """Only ever reached via approve_change_request's dispatch, which has
    already done the AWAITING_HOST/proposal-hash/access checks -- same
    convention as _approve_premises_change. Distinct from the amendment-
    engine path: a DEPOSIT_CHANGE approval never drives this codebase's own
    amendment engine (Section 2 remains the sole authority on the actual
    money), so this jumps straight to EFFECTIVE and tells the admin to go
    execute the real change through the Deposit Records screen."""
    transition(bcr, "EFFECTIVE")
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    db.commit()
    db.refresh(bcr)

    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="Your deposit change request was approved",
        message=(
            "Your requested deposit change was approved in principle. Section 2's deposit rules govern the actual "
            "amount, instrument and custody -- your host/admin will process this separately."
        ),
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def admin_create_legal_order_change(
    db: Session, admin: AdminUser, agreement: Agreement, *,
    proposed_start_date: date | None = None, new_term_months: int | None = None,
    proposed_monthly_rent: float | None = None, authority_evidence_ref: str, reason: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 4/LEGAL_ORDER_CHANGE: admin-initiated, driven by
    a court/regulator/statutory order rather than a renter's request -- see
    model docstring for why this skips AWAITING_HOST/AWAITING_RENTER
    entirely and runs the amendment engine immediately. At least one of
    proposed_start_date/new_term_months/proposed_monthly_rent must be given
    (whatever the order actually mandates); unset ones are left untouched."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if not authority_evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An authority evidence reference is required for a legal-order change")
    if agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a fully signed agreement can be amended this way")
    if proposed_start_date is None and new_term_months is None and proposed_monthly_rent is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least one of proposedStartDate, newTermMonths, proposedMonthlyRent is required")
    if new_term_months is not None and new_term_months < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "newTermMonths must be at least 1")
    if proposed_monthly_rent is not None and proposed_monthly_rent <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "proposedMonthlyRent must be greater than zero")

    offer = agreement.offer
    current_start = _current_start_date(offer)
    current_term_months = offer.terms[-1].term_months
    current_rent = float(offer.terms[-1].monthly_rent)
    final_start = proposed_start_date if proposed_start_date is not None else current_start
    final_term = new_term_months if new_term_months is not None else current_term_months

    bcr = BookingChangeRequest(
        agreement_id=agreement.id, requested_by_guest_id=offer.guest_id, change_type="LEGAL_ORDER_CHANGE",
        status="AWAITING_HOST",
        original_start_date=current_start, proposed_start_date=final_start,
        original_end_date=_add_months(current_start, current_term_months),
        proposed_end_date=_add_months(final_start, final_term),
        additional_term_months=final_term if new_term_months is not None else None,
        original_monthly_rent=current_rent if proposed_monthly_rent is not None else None,
        proposed_monthly_rent=proposed_monthly_rent,
        authority_evidence_ref=authority_evidence_ref, reason=reason,
        decided_by_admin_id=admin.id,
        expires_at=datetime.now(timezone.utc) + REQUEST_EXPIRY,
    )
    bcr.proposal_hash = compute_proposal_hash(bcr)
    db.add(bcr)
    db.flush()

    proposed_terms: dict = {}
    if proposed_start_date is not None:
        proposed_terms["startDate"] = final_start.isoformat()
    if new_term_months is not None:
        proposed_terms["termMonths"] = final_term
    if proposed_monthly_rent is not None:
        proposed_terms["monthlyRent"] = float(proposed_monthly_rent)

    default_reason = f"Legal/regulatory order (evidence: {authority_evidence_ref})"
    amendment = _run_amendment_engine_or_record_failure(db, bcr, admin, proposed_terms, reason or default_reason)

    transition(bcr, "AWAITING_AGREEMENT_ACTION")
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.resulting_amendment_id = amendment.id
    db.commit()
    db.refresh(bcr)

    log_audit_event(
        db, admin, "booking_change.legal_order_change", "booking_change_request", str(bcr.id),
        reason=f"evidence={authority_evidence_ref}; {reason}"[:500],
        before_state="AWAITING_HOST", after_state="AWAITING_AGREEMENT_ACTION",
    )
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="A legal or regulatory order requires a change to your agreement",
        message=(
            "Your agreement is being amended to comply with a legal/regulatory order. "
            "Please review and re-sign the updated agreement." + (f" Details: {reason}" if reason else "")
        ),
        notification_type="booking_change_request.legal_order_change",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def _complete_premises_change_if_applicable(db: Session, new_agreement: Agreement) -> None:
    """Called from crud/leasing.py right after ANY agreement reaches SIGNED --
    a no-op unless that agreement's Application is the replacement side of a
    PREMISES_CHANGE migration (see request_premises_change /
    approve_change_request's PREMISES_CHANGE branch). This is the actual
    'replacement commit policy permits release' moment (Section 8 Edge
    Cases: 'preserve old booking until replacement commit policy permits
    release') -- the original booking is only ended/voided now, never at
    BCR-approval time, so a renter who never finishes signing/paying for the
    new listing keeps their original tenancy untouched."""
    from app.crud.occupancy import end_occupancy

    if new_agreement.offer is None:
        return
    bcr = db.scalar(
        select(BookingChangeRequest).where(
            BookingChangeRequest.resulting_application_id == new_agreement.offer.application_id,
            BookingChangeRequest.change_type == "PREMISES_CHANGE",
            BookingChangeRequest.status == "AWAITING_AGREEMENT_ACTION",
        )
    )
    if bcr is None:
        return

    old_agreement = bcr.agreement
    old_occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == old_agreement.offer_id))
    deciding_admin = db.get(AdminUser, bcr.decided_by_admin_id) if bcr.decided_by_admin_id else None
    if old_occupancy is not None and old_occupancy.status == "ACTIVE" and deciding_admin is not None:
        # ZR-ENG-CLR-006 AC-05: this is an early ending (the whole point of a
        # premises-change migration is moving before the original tenancy's
        # natural end), but it's not Host fiat -- the renter requested this
        # migration and an admin already approved the BookingChangeRequest
        # that authorizes it. already_adjudicated is the dedicated, non-
        # public-facing escape from that guard for exactly this case (see
        # crud/occupancy.py:end_occupancy's own docstring).
        end_occupancy(db, old_occupancy, deciding_admin, basis="PREMISES_CHANGE_MIGRATION", already_adjudicated=True)
    elif old_agreement.status not in ("VOID", "EXPIRED"):
        old_agreement.status = "VOID"

    transition(bcr, "EFFECTIVE")
    db.commit()

    log_audit_event(
        db, deciding_admin, "booking_change.effective", "booking_change_request", str(bcr.id),
        reason="New agreement fully signed", before_state="AWAITING_AGREEMENT_ACTION", after_state="EFFECTIVE",
    )
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="Your room/property change is now effective",
        message="Your new agreement is fully signed and your original booking has been closed out.",
        notification_type="booking_change_request.effective",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )


def _approve_premises_change(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str) -> BookingChangeRequest:
    """No amendment engine involved -- a different room/property is a
    different subject matter (Section 14), so this opens a genuinely fresh
    Application (auto-approved, since the admin decision already happened
    right here) on the target listing and lets the ordinary leasing pipeline
    (offer terms -> accept -> agreement -> sign -> pay) run its own real
    compliance/pricing checks for that listing. The original booking stays
    fully intact until that new agreement reaches SIGNED (see
    _complete_premises_change_if_applicable) -- AWAITING_AGREEMENT_ACTION here
    only means 'the host agreed to let this renter apply', not that anything
    has moved."""
    from app.crud.leasing import decide_application, submit_application
    from app.models.listing import Listing
    from app.schemas.leasing import ApplicationCreate, ApplicationDecide

    agreement = bcr.agreement
    target_listing = db.get(Listing, bcr.target_listing_id)
    if not target_listing or target_listing.state != "PUBLISHED":
        # Section 24 edge case: the target listing was pulled between the
        # renter's request and this approval.
        _record_terminal_failure(db, bcr, "CONFLICT", admin, "Target listing is no longer published")
        raise HTTPException(status.HTTP_409_CONFLICT, "Target listing is no longer published")

    try:
        application = submit_application(db, ApplicationCreate(
            listing_id=target_listing.id, guest_id=bcr.requested_by_guest_id,
            message=f"Room/property change migration from listing {agreement.offer.listing_id} (request #{bcr.id})",
        ))
        decide_application(db, application, admin, ApplicationDecide(
            decision="APPROVED", reason_code="PREMISES_CHANGE_MIGRATION",
            note=decision_note or "Pre-approved: renter's existing tenancy is being migrated to this listing.",
        ))
    except HTTPException as exc:
        # Section 24 edge case: e.g. the target listing became ineligible
        # between the renter's request and this approval.
        _record_terminal_failure(db, bcr, "CONFLICT", admin, str(exc.detail))
        raise
    except Exception as exc:
        _record_terminal_failure(db, bcr, "FAILED", admin, str(exc))
        raise

    transition(bcr, "AWAITING_AGREEMENT_ACTION")
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    bcr.resulting_application_id = application.id
    db.commit()
    db.refresh(bcr)

    # Audit: the approve route already logs "booking_change_request.approve".
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title="Your room/property change was approved",
        message=(
            f'You can now proceed on "{target_listing.name}" -- your host will send offer terms next. '
            "Your current tenancy stays active until that new agreement is fully signed."
        ),
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def _proposed_terms_and_default_reason(bcr: BookingChangeRequest) -> tuple[dict, str]:
    """The amendment-engine term key(s) + a fallback reason for whichever
    fields are *currently* on the BCR -- shared by the original-proposal
    approval path and the accept-alternative path, since both ultimately run
    the exact same amendment engine over whatever proposed_* values the BCR
    holds at that moment."""
    agreement = bcr.agreement
    if bcr.change_type in ("EXTENSION", "SHORTENING"):
        # additional_term_months is a signed delta (negative for SHORTENING),
        # so this one line correctly covers both directions.
        current_term_months = agreement.offer.terms[-1].term_months
        proposed_terms = {"termMonths": current_term_months + bcr.additional_term_months}
        default_reason = "Renter-requested stay extension" if bcr.change_type == "EXTENSION" else "Renter-requested stay shortening"
    elif bcr.change_type == "FINANCIAL_CHANGE":
        proposed_terms = {"monthlyRent": float(bcr.proposed_monthly_rent)}
        default_reason = "Renter-requested rent change"
    elif bcr.change_type == "TERM_SHIFT":
        # additional_term_months is the new term's ABSOLUTE length here, not
        # a delta -- see model docstring.
        proposed_terms = {"startDate": bcr.proposed_start_date.isoformat(), "termMonths": bcr.additional_term_months}
        default_reason = "Renter-requested move-in date and term change"
    else:
        proposed_terms = {"startDate": bcr.proposed_start_date.isoformat()}
        default_reason = "Renter-requested move-in date change"
    return proposed_terms, default_reason


def _run_amendment_engine_or_record_failure(
    db: Session, bcr: BookingChangeRequest, admin: AdminUser, proposed_terms: dict, default_reason: str,
):
    """Runs the same 4-call amendment-engine sequence used by every
    amendment-path change type; on failure records CONFLICT/FAILED via
    _record_terminal_failure (Section 24 edge case) before re-raising."""
    try:
        amendment = amendment_crud.request_amendment(db, bcr.agreement, admin, reason=bcr.reason or default_reason)
        amendment = amendment_crud.classify_amendment(db, amendment, admin, "MATERIAL_CHANGE")
        amendment = amendment_crud.propose_terms(db, amendment, admin, proposed_terms)
        amendment = amendment_crud.approve_amendment(db, amendment, admin)
        return amendment
    except HTTPException as exc:
        _record_terminal_failure(db, bcr, "CONFLICT", admin, str(exc.detail))
        raise
    except Exception as exc:
        _record_terminal_failure(db, bcr, "FAILED", admin, str(exc))
        raise


def approve_change_request(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str = "") -> BookingChangeRequest:
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "AWAITING_HOST":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only an AWAITING_HOST request can be approved (current status: {bcr.status})")
    # AC-16: the renter's submission is their consent to these exact
    # proposed terms -- refuse to approve if they've since been altered.
    assert_proposal_unchanged(bcr)

    if bcr.change_type == "PREMISES_CHANGE":
        return _approve_premises_change(db, bcr, admin, decision_note)
    if bcr.change_type == "DEPOSIT_CHANGE":
        return approve_deposit_change(db, bcr, admin, decision_note)

    proposed_terms, default_reason = _proposed_terms_and_default_reason(bcr)
    amendment = _run_amendment_engine_or_record_failure(db, bcr, admin, proposed_terms, default_reason)

    transition(bcr, "AWAITING_AGREEMENT_ACTION")
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    bcr.resulting_amendment_id = amendment.id
    db.commit()
    db.refresh(bcr)

    # Audit: the approve route (api/routes/leasing.py) already logs
    # "booking_change_request.approve" once this call returns successfully --
    # logging again here would duplicate that row. The CONFLICT/FAILED
    # branches above DO need their own log_audit_event call (via
    # _record_terminal_failure), since an exception there means the route's
    # own logging line is never reached.
    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} was approved",
        message=f"Your requested {change_label} was approved -- please review and re-sign the updated agreement.",
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def decline_change_request(db: Session, bcr: BookingChangeRequest, admin: AdminUser, decision_note: str = "") -> BookingChangeRequest:
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "AWAITING_HOST":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only an AWAITING_HOST request can be declined (current status: {bcr.status})")

    transition(bcr, "REJECTED")
    bcr.decided_by_admin_id = admin.id
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.decision_note = decision_note
    db.commit()
    db.refresh(bcr)

    # Audit: the decline route already logs "booking_change_request.decline".
    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} was declined",
        message=f"Your requested {change_label} was declined." + (f" Reason: {decision_note}" if decision_note else ""),
        notification_type="booking_change_request.declined",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def propose_alternative_terms(
    db: Session, bcr: BookingChangeRequest, admin: AdminUser, *,
    proposed_start_date: date | None = None, additional_term_months: int | None = None,
    proposed_monthly_rent: float | None = None, decision_note: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 18 Host Review Wireframe: 'Alternative proposal
    -- Creates a new proposal/version and invalidates prior consent where
    material terms change.' Not offered for PREMISES_CHANGE -- a host can't
    meaningfully counter-propose a *different target listing* within this
    mechanism; the right move there is to decline and let the renter submit
    a fresh request against whatever listing they actually want.

    Overwrites this BCR's own proposed_* field(s) and recomputes
    proposal_hash from the new values -- exactly the AC-16 "material change
    invalidates prior consent" mechanism, since the renter's original
    consent (captured at submission) never covered these new terms. Lands
    at AWAITING_RENTER rather than AWAITING_AGREEMENT_ACTION for the same
    reason: nothing is agreed until the renter consents to *these* terms."""
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "AWAITING_HOST":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only an AWAITING_HOST request can get an alternative proposal (current status: {bcr.status})")
    if bcr.change_type == "PREMISES_CHANGE":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alternative proposals aren't supported for room/property change requests -- decline this one and ask the renter to submit a new request instead")
    if bcr.change_type == "DEPOSIT_CHANGE":
        # Approving a DEPOSIT_CHANGE never drives this codebase's own
        # amendment engine (see approve_deposit_change) -- there are no
        # "terms" here for a host to counter-propose through this mechanism.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Alternative proposals aren't supported for deposit change requests -- decline this one and ask the renter to submit a new request instead")

    if bcr.change_type == "DATE_SHIFT":
        if proposed_start_date is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "proposedStartDate is required for this change type")
        if proposed_start_date == bcr.proposed_start_date:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the same date the renter already proposed")
        bcr.proposed_start_date = proposed_start_date
    elif bcr.change_type in ("EXTENSION", "SHORTENING"):
        if additional_term_months is None or additional_term_months == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "additionalTermMonths is required for this change type")
        if additional_term_months == bcr.additional_term_months:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the same term change the renter already proposed")
        current_term_months = agreement.offer.terms[-1].term_months
        new_term_months = current_term_months + additional_term_months
        if new_term_months < 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That reduction would leave no term remaining")
        bcr.additional_term_months = additional_term_months
        bcr.proposed_end_date = _add_months(bcr.original_start_date, new_term_months)
    elif bcr.change_type == "TERM_SHIFT":
        if proposed_start_date is None or additional_term_months is None or additional_term_months < 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "proposedStartDate and a new term of at least 1 month are both required for this change type")
        if proposed_start_date == bcr.proposed_start_date and additional_term_months == bcr.additional_term_months:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the same date and term the renter already proposed")
        bcr.proposed_start_date = proposed_start_date
        bcr.additional_term_months = additional_term_months
        bcr.proposed_end_date = _add_months(proposed_start_date, additional_term_months)
    else:  # FINANCIAL_CHANGE
        if proposed_monthly_rent is None or proposed_monthly_rent <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "proposedMonthlyRent is required for this change type")
        if proposed_monthly_rent == bcr.proposed_monthly_rent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That's the same rent the renter already proposed")
        bcr.proposed_monthly_rent = proposed_monthly_rent

    bcr.proposal_hash = compute_proposal_hash(bcr)
    bcr.decision_note = decision_note
    bcr.decided_by_admin_id = admin.id
    bcr.expires_at = datetime.now(timezone.utc) + REQUEST_EXPIRY
    transition(bcr, "AWAITING_RENTER")
    db.commit()
    db.refresh(bcr)

    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your host proposed different terms for your {change_label}",
        message=(
            f"Your host proposed alternative terms for your {change_label} request. "
            "Review and accept or decline them." + (f" Note: {decision_note}" if decision_note else "")
        ),
        notification_type="booking_change_request.alternative_proposed",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def accept_alternative_terms(db: Session, user: UserAccount, bcr: BookingChangeRequest) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != bcr.requested_by_guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This request does not belong to you")
    if bcr.status != "AWAITING_RENTER":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a request awaiting your response can be accepted (current status: {bcr.status})")

    admin = db.get(AdminUser, bcr.decided_by_admin_id) if bcr.decided_by_admin_id else None
    if admin is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "No host on record for this alternative proposal")

    proposed_terms, default_reason = _proposed_terms_and_default_reason(bcr)
    default_reason = f"Renter accepted host's alternative proposal ({default_reason.split(' ', 1)[-1]})"
    amendment = _run_amendment_engine_or_record_failure(db, bcr, admin, proposed_terms, default_reason)

    transition(bcr, "AWAITING_AGREEMENT_ACTION")
    bcr.decided_at = datetime.now(timezone.utc)
    bcr.resulting_amendment_id = amendment.id
    db.commit()
    db.refresh(bcr)

    log_audit_event(
        db, None, "booking_change.alternative_accepted", "booking_change_request", str(bcr.id),
        reason="Renter accepted the host's alternative terms", before_state="AWAITING_RENTER", after_state="AWAITING_AGREEMENT_ACTION",
    )
    change_label = _change_label(bcr.change_type)
    notif_crud.notify_user_by_guest(
        db, bcr.requested_by,
        title=f"Your {change_label} was approved",
        message="You accepted your host's alternative terms -- please review and re-sign the updated agreement.",
        notification_type="booking_change_request.approved",
        related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
    )
    return bcr


def decline_alternative_terms(db: Session, user: UserAccount, bcr: BookingChangeRequest) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != bcr.requested_by_guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This request does not belong to you")
    if bcr.status != "AWAITING_RENTER":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a request awaiting your response can be declined (current status: {bcr.status})")

    transition(bcr, "WITHDRAWN")
    db.commit()
    db.refresh(bcr)

    log_audit_event(
        db, None, "booking_change.alternative_declined", "booking_change_request", str(bcr.id),
        reason="Renter declined the host's alternative terms", before_state="AWAITING_RENTER", after_state="WITHDRAWN",
    )
    listing = bcr.agreement.offer.listing
    if listing and listing.party_id:
        change_label = _change_label(bcr.change_type)
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=f"A tenant declined your alternative {change_label} terms",
            message=f'The tenant declined your alternative terms for "{listing.name}" -- the request has been withdrawn.',
            notification_type="booking_change_request.alternative_declined",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def withdraw_change_request(db: Session, user: UserAccount, bcr: BookingChangeRequest) -> BookingChangeRequest:
    guest = get_guest_for_user(db, user)
    if not guest or guest.id != bcr.requested_by_guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This request does not belong to you")
    bcr = _expire_if_overdue(db, bcr)
    if bcr.status != "AWAITING_HOST":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only an AWAITING_HOST request can be withdrawn (current status: {bcr.status})")

    transition(bcr, "WITHDRAWN")
    db.commit()
    db.refresh(bcr)

    # Audit: the withdraw route already logs "booking_change_request.withdraw".
    listing = bcr.agreement.offer.listing
    if listing and listing.party_id:
        change_label = _change_label(bcr.change_type)
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=f"A tenant withdrew their {change_label} request",
            message=f'The pending {change_label} request for "{listing.name}" was withdrawn -- no action needed.',
            notification_type="booking_change_request.withdrawn",
            related_entity_type="booking_change_request", related_entity_id=str(bcr.id),
        )
    return bcr


def correct_change_request_metadata(
    db: Session, bcr: BookingChangeRequest, admin: AdminUser, *,
    corrected_reason: str | None = None, corrected_decision_note: str | None = None,
    evidence_ref: str, correction_note: str = "",
) -> BookingChangeRequest:
    """ZR-ENG-CLR-008 Section 9/ADMIN_CORRECTION: 'Typo, formatting, metadata
    correction that does not alter legal/economic meaning -- non-material if
    objectively corrective and fully audited.' Section 9's own table is
    explicit about the boundary: 'Rent amount, term length, property,
    occupant identity, deposit, payment schedule -- No, material amendment
    controls apply.' So this deliberately only ever touches this BCR's own
    two free-text fields (reason/decision_note) -- never status, dates,
    term, rent, or target listing -- and works on a request in ANY status
    (including terminal ones), since a typo fix doesn't need or trigger a
    state transition at all. Not modeled as a new BOOKING_CHANGE_TYPES entry
    creating a fresh row: there is no natural 'proposed_*' shape for
    'corrected the display text on request #X', and forcing one in would be
    schema you'd have to invent fields for rather than something this
    aggregate is actually about."""
    agreement = bcr.agreement
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if not evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An evidence reference is required for an admin correction")
    if corrected_reason is None and corrected_decision_note is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to correct -- provide a corrected reason and/or decision note")

    diff_parts = []
    if corrected_reason is not None and corrected_reason != bcr.reason:
        diff_parts.append(f"reason: {bcr.reason!r} -> {corrected_reason!r}")
        bcr.reason = corrected_reason
    if corrected_decision_note is not None and corrected_decision_note != bcr.decision_note:
        diff_parts.append(f"decision_note: {bcr.decision_note!r} -> {corrected_decision_note!r}")
        bcr.decision_note = corrected_decision_note
    if not diff_parts:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to correct -- the provided text matches what's already there")

    db.commit()
    db.refresh(bcr)

    # AuditEvent.reason is a bounded VARCHAR -- the full before/after values
    # are still on the BCR row itself (its own fields are the same size
    # limit), so a truncated audit summary doesn't lose the actual record.
    audit_reason = f"evidence={evidence_ref}; {correction_note}; " + "; ".join(diff_parts)
    log_audit_event(
        db, admin, "booking_change.admin_correction", "booking_change_request", str(bcr.id),
        reason=audit_reason[:500],
    )
    return bcr


def list_change_requests_for_guest(db: Session, user: UserAccount) -> list[BookingChangeRequest]:
    guest = get_guest_for_user(db, user)
    if not guest:
        return []
    query = (
        select(BookingChangeRequest)
        .where(BookingChangeRequest.requested_by_guest_id == guest.id)
        .order_by(BookingChangeRequest.created_at.desc())
    )
    return list(db.scalars(query))


def list_change_requests_for_admin(db: Session, admin: AdminUser) -> list[BookingChangeRequest]:
    from app.models.listing import Listing

    query = (
        select(BookingChangeRequest)
        .join(Agreement, Agreement.id == BookingChangeRequest.agreement_id)
        .join(Offer, Offer.id == Agreement.offer_id)
    )
    if admin.role != "super_admin":
        query = query.join(Listing, Listing.id == Offer.listing_id).where(Listing.owner_id == admin.id)
    query = query.order_by(BookingChangeRequest.created_at.desc())
    return list(db.scalars(query))
