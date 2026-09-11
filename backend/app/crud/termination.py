"""ZR-ENG-CLR-006 Section 7: renter-initiated early termination. Only the
UNILATERAL_CAUSE_CODES subset is resolvable today (see
models/termination_case.py's own docstrings for exactly why); everything
else fails closed with a 400 rather than fabricating a notice period or
liability outcome (Section 2 doctrine: 'Fail closed on unsupported legal
pathways')."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.market_policy import resolve_market_policy, to_policy_snapshot
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.market_policy import MarketPolicyPack
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.termination_case import (
    HOST_ONLY_CAUSE_CODES,
    HOST_UNILATERAL_CAUSE_CODES,
    IMMEDIATE_CAUSE_CODES,
    MUTUAL_CAUSE_CODES,
    NOTICE_CAUSE_CODES,
    RENTER_ONLY_CAUSE_CODES,
    SENSITIVE_CAUSE_CODES,
    TERMINATION_CAUSE_CODES,
    UNILATERAL_CAUSE_CODES,
    TerminationCase,
)
from app.schemas.termination import (
    TerminationCaseCreate,
    TerminationCaseDecision,
    TerminationCaseRead,
    TerminationCaseTribunalLiability,
)

_OPEN_CASE_STATUSES = ("OPENED", "SURRENDER_PROPOSED", "PENDING_REVIEW", "EFFECTIVE_DATE_SET")


def _resolve_earliest_effective_date(cause_code: str, policy: MarketPolicyPack) -> date:
    """ZR-ENG-CLR-006 Section 10's notice engine, trimmed to this increment's
    one cause-driven rule: immediate for a Host-fault/habitability cause,
    market-pack-resolved notice days otherwise (AC-03: never hard-coded)."""
    today = date.today()
    if cause_code in IMMEDIATE_CAUSE_CODES:
        return today
    assert cause_code in NOTICE_CAUSE_CODES
    return today + timedelta(days=policy.termination_notice_days)


def _require_proposed_effective_date(data: TerminationCaseCreate) -> date:
    """ZR-ENG-CLR-006 Section 7.1 Step 9: a MUTUAL_SURRENDER proposal names
    its own date rather than having one computed -- but it still can't be
    missing or retroactive."""
    if data.proposed_effective_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A proposed effective date is required for a mutual surrender")
    if data.proposed_effective_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The proposed effective date cannot be in the past")
    return data.proposed_effective_date


def _resolve_case_opening(
    data: TerminationCaseCreate, policy: MarketPolicyPack, *, auto_cause_codes: tuple[str, ...],
) -> tuple[str, date | None, date | None]:
    """Returns (status, earliest_effective_date, effective_termination_date)
    for a newly-opened case. Three pathways, per models/termination_case.py's
    own docstring: consent-gated (MUTUAL_CAUSE_CODES), auto-computed
    (whichever set the caller passes -- UNILATERAL_CAUSE_CODES for a renter,
    HOST_UNILATERAL_CAUSE_CODES for a Host), or PENDING_REVIEW (everything
    else this build can't compute a date for -- AC-35)."""
    if data.cause_code in MUTUAL_CAUSE_CODES:
        proposed_date = _require_proposed_effective_date(data)
        return "SURRENDER_PROPOSED", proposed_date, None
    if data.cause_code in auto_cause_codes:
        effective_date = _resolve_earliest_effective_date(data.cause_code, policy)
        return "EFFECTIVE_DATE_SET", effective_date, effective_date
    return "PENDING_REVIEW", None, None


def _assert_no_open_case(db: Session, occupancy_id: int) -> None:
    existing_open = db.scalar(
        select(TerminationCase).where(
            TerminationCase.occupancy_id == occupancy_id, TerminationCase.status.in_(_OPEN_CASE_STATUSES),
        )
    )
    if existing_open is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This occupancy already has an open termination case")


def _get_agreement_or_409(db: Session, occupancy: Occupancy) -> Agreement:
    agreement = db.scalar(select(Agreement).where(Agreement.offer_id == occupancy.offer_id))
    if agreement is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No agreement found for this occupancy")
    return agreement


def _notify_case_opened(db: Session, case: TerminationCase, occupancy: Occupancy, *, notify_party: bool) -> None:
    listing = db.get(Listing, occupancy.listing_id)
    if not listing:
        return
    if case.status == "PENDING_REVIEW":
        date_phrase = "under review -- no effective date has been set yet"
    elif case.status == "SURRENDER_PROPOSED":
        date_phrase = f"proposed for {case.earliest_effective_date.isoformat()}"
    else:
        date_phrase = f"effective {case.earliest_effective_date.isoformat()}"
    if notify_party and listing.party_id:
        title = "A renter has requested to end their tenancy"
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=title,
            message=f'A termination notice was submitted for "{listing.name}", {date_phrase}.',
            notification_type="termination_case.opened",
            related_entity_type="termination_case", related_entity_id=str(case.id),
        )
    else:
        title = "Your host has started ending your tenancy"
        notif_crud.notify_user_by_guest(
            db, occupancy.guest,
            title=title,
            message=f'A termination case was opened for "{listing.name}", {date_phrase}.',
            notification_type="termination_case.opened",
            related_entity_type="termination_case", related_entity_id=str(case.id),
        )


def open_termination_case(db: Session, occupancy: Occupancy, guest: Guest, data: TerminationCaseCreate) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 7.1 Steps 1-7: the renter's 'End my stay' notice.
    Occupancy status is untouched here: ending it is a separate, later step
    (see crud/occupancy.py:end_occupancy), matching Section 7.1 Step 11's
    'Move-out/vacant possession is confirmed separately.'"""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can have a termination requested")
    if occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")

    if data.cause_code not in TERMINATION_CAUSE_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized cause code '{data.cause_code}'")
    if data.cause_code in HOST_ONLY_CAUSE_CODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{data.cause_code}' describes a Host/platform-initiated action -- a renter cannot invoke it",
        )
    _assert_no_open_case(db, occupancy.id)
    policy = resolve_market_policy(db)
    case_status, effective_date, final_date = _resolve_case_opening(data, policy, auto_cause_codes=UNILATERAL_CAUSE_CODES)
    agreement = _get_agreement_or_409(db, occupancy)

    case = TerminationCase(
        occupancy_id=occupancy.id,
        agreement_id=agreement.id,
        initiator_guest_id=guest.id,
        cause_code=data.cause_code,
        status=case_status,
        notes=data.notes,
        policy_snapshot=to_policy_snapshot(policy),
        earliest_effective_date=effective_date,
        effective_termination_date=final_date,
    )
    db.add(case)
    db.flush()
    # ZR-ENG-CLR-006 Section 20.2: the domain-event outbox this codebase
    # already has (crud/events.py), applied to termination cases for the
    # first time -- emitted in the same transaction as the case row itself.
    emit_event(db, "termination.case_opened", "termination_case", str(case.id), {"occupancyId": occupancy.id, "causeCode": case.cause_code})
    db.commit()
    db.refresh(case)

    _notify_case_opened(db, case, occupancy, notify_party=True)
    return case


def open_host_termination_case(db: Session, occupancy: Occupancy, admin: AdminUser, data: TerminationCaseCreate) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 8: 'Start termination / possession process,'
    not 'Cancel renter.' HOST_UNILATERAL_CAUSE_CODES/MUTUAL_CAUSE_CODES
    resolve automatically; every other Host pathway in Section 8.1's table
    (RENTER_BREACH_ALLEGED, OWNER_MOVE_IN/SALE/OTHER_GROUND, SAFETY_
    EMERGENCY) still opens a case, just PENDING_REVIEW -- see
    models/termination_case.py's own docstring for why."""
    assert_provider_access(db, admin, party_id_for_listing(occupancy.listing))
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can have a termination started")

    if data.cause_code not in TERMINATION_CAUSE_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized cause code '{data.cause_code}'")
    if data.cause_code in RENTER_ONLY_CAUSE_CODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{data.cause_code}' describes the renter's own right -- a Host cannot invoke it on the renter's behalf",
        )
    _assert_no_open_case(db, occupancy.id)
    policy = resolve_market_policy(db)
    case_status, effective_date, final_date = _resolve_case_opening(data, policy, auto_cause_codes=HOST_UNILATERAL_CAUSE_CODES)
    agreement = _get_agreement_or_409(db, occupancy)

    case = TerminationCase(
        occupancy_id=occupancy.id,
        agreement_id=agreement.id,
        initiator_admin_id=admin.id,
        cause_code=data.cause_code,
        status=case_status,
        notes=data.notes,
        policy_snapshot=to_policy_snapshot(policy),
        earliest_effective_date=effective_date,
        effective_termination_date=final_date,
    )
    db.add(case)
    db.flush()
    emit_event(db, "termination.case_opened", "termination_case", str(case.id), {"occupancyId": occupancy.id, "causeCode": case.cause_code})
    db.commit()
    db.refresh(case)

    _notify_case_opened(db, case, occupancy, notify_party=False)
    return case


def withdraw_termination_case(db: Session, case: TerminationCase, guest: Guest) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 22 edge case: 'Termination notice withdrawn --
    only if legally/contractually permitted ... preserve original notice
    record.' Permitted any time before the occupancy has actually ended;
    the case row is never deleted, only flipped to WITHDRAWN."""
    if case.initiator_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
    if case.status == "TERMINATED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This occupancy has already been ended -- the case cannot be withdrawn")
    if case.status == "WITHDRAWN":
        raise HTTPException(status.HTTP_409_CONFLICT, "This case has already been withdrawn")

    case.status = "WITHDRAWN"
    case.withdrawn_at = datetime.now(timezone.utc)
    emit_event(db, "termination.case_withdrawn", "termination_case", str(case.id), {"occupancyId": case.occupancy_id})
    db.commit()
    db.refresh(case)
    return case


def _notify_case_decided(db: Session, case: TerminationCase, *, approved: bool) -> None:
    """Both parties were waiting on a PENDING_REVIEW outcome regardless of
    who initiated -- unlike a mutual-surrender proposal, this isn't a
    two-party negotiation where only the proposer needs telling."""
    listing = db.get(Listing, case.occupancy.listing_id)
    if not listing:
        return
    verb = "approved" if approved else "rejected"
    notif_crud.notify_user_by_guest(
        db, case.occupancy.guest,
        title=f"Your termination case was {verb}",
        message=f'Your termination request for "{listing.name}" was {verb} after review.',
        notification_type="termination_case.decided",
        related_entity_type="termination_case", related_entity_id=str(case.id),
    )
    if listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=f"A termination case was {verb}",
            message=f'A termination case for "{listing.name}" was {verb} after review.',
            notification_type="termination_case.decided",
            related_entity_type="termination_case", related_entity_id=str(case.id),
        )


def decide_termination_case(db: Session, case: TerminationCase, admin: AdminUser, data: TerminationCaseDecision) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 20.1 POST /termination-cases/{id}/decision --
    the only way a PENDING_REVIEW case moves forward (AC-35). Super-Admin-
    only, the same override tier AC-05's active-occupancy guard and
    write_off_host_recovery already reserve for a comparably consequential,
    evidence-dependent decision (AC-29: role authorization + mandatory
    reason). Approving sets the date the Super Admin determined lawful --
    this build computes nothing for these causes itself, see
    models/termination_case.py:RENTER_ONLY_CAUSE_CODES/HOST_ONLY_CAUSE_CODES;
    rejecting means the alleged pathway was determined not to apply."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Super Admin can decide a termination case pending review")
    if case.status != "PENDING_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This case is not pending review (status: {case.status})")
    if not data.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to decide a termination case")

    if data.approve:
        if data.effective_termination_date is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "An effective termination date is required to approve this case")
        case.status = "EFFECTIVE_DATE_SET"
        case.earliest_effective_date = data.effective_termination_date
        case.effective_termination_date = data.effective_termination_date
        emit_event(
            db, "termination.effective_date_set", "termination_case", str(case.id),
            {"occupancyId": case.occupancy_id, "effectiveDate": data.effective_termination_date.isoformat()},
        )
    else:
        case.status = "REJECTED_PATHWAY"
        emit_event(db, "termination.pathway_rejected", "termination_case", str(case.id), {"occupancyId": case.occupancy_id})

    db.commit()
    db.refresh(case)
    _notify_case_decided(db, case, approved=data.approve)
    return case


def _assert_is_responding_party(case: TerminationCase, *, guest: Guest | None, admin: AdminUser | None) -> None:
    """The other party from whoever proposed -- a proposer 'accepting' their
    own offer isn't a real negotiation, so it's rejected the same as an
    unrelated stranger would be."""
    if guest is not None:
        if case.occupancy.guest_id != guest.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
        if case.initiator_guest_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You proposed this surrender -- the Host must respond to it")
    elif admin is not None:
        if case.initiator_admin_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You proposed this surrender -- the renter must respond to it")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A responding party is required")


def _notify_surrender_response(db: Session, case: TerminationCase, *, accepted: bool) -> None:
    """Tells the proposer what the other party decided."""
    listing = db.get(Listing, case.occupancy.listing_id)
    if not listing:
        return
    verb = "accepted" if accepted else "declined"
    if case.initiator_guest_id is not None:
        notif_crud.notify_user_by_guest(
            db, case.occupancy.guest,
            title=f"Your host {verb} your mutual surrender proposal",
            message=f'Your proposal to end "{listing.name}" was {verb}.',
            notification_type="termination_case.surrender_responded",
            related_entity_type="termination_case", related_entity_id=str(case.id),
        )
    elif listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title=f"Your renter {verb} your mutual surrender proposal",
            message=f'Your proposal to end "{listing.name}" was {verb}.',
            notification_type="termination_case.surrender_responded",
            related_entity_type="termination_case", related_entity_id=str(case.id),
        )


def accept_mutual_surrender(
    db: Session, case: TerminationCase, *, guest: Guest | None = None, admin: AdminUser | None = None,
) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 7.1 Step 9/Section 8.1 MUTUAL_SURRENDER_PROPOSAL:
    the other party's affirmative acceptance is what actually makes the
    proposed date binding -- see models/termination_case.py:MUTUAL_CAUSE_CODES
    for why there's no auto-accept path."""
    if admin is not None:
        assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    _assert_is_responding_party(case, guest=guest, admin=admin)
    if case.status != "SURRENDER_PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This case is not awaiting a surrender response (status: {case.status})")

    case.status = "EFFECTIVE_DATE_SET"
    effective_date = case.earliest_effective_date
    case.effective_termination_date = effective_date
    emit_event(
        db, "termination.mutual_surrender_accepted", "termination_case", str(case.id),
        {"occupancyId": case.occupancy_id, "effectiveDate": effective_date.isoformat()},
    )
    db.commit()
    db.refresh(case)
    _notify_surrender_response(db, case, accepted=True)
    return case


def decline_mutual_surrender(
    db: Session, case: TerminationCase, *, guest: Guest | None = None, admin: AdminUser | None = None,
) -> TerminationCase:
    """QT-07: 'Mutual surrender offer expires/rejected; original tenancy
    remains active.' The occupancy is never touched here -- only the case's
    own status changes, freeing the occupancy to have a new case opened."""
    if admin is not None:
        assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    _assert_is_responding_party(case, guest=guest, admin=admin)
    if case.status != "SURRENDER_PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This case is not awaiting a surrender response (status: {case.status})")

    case.status = "SURRENDER_DECLINED"
    emit_event(db, "termination.mutual_surrender_declined", "termination_case", str(case.id), {"occupancyId": case.occupancy_id})
    db.commit()
    db.refresh(case)
    _notify_surrender_response(db, case, accepted=False)
    return case


def get_termination_case_or_404(db: Session, case_id: int) -> TerminationCase:
    case = db.get(TerminationCase, case_id)
    if not case:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Termination case not found")
    return case


def list_termination_cases_for_occupancy(db: Session, occupancy: Occupancy) -> list[TerminationCase]:
    return list(
        db.scalars(
            select(TerminationCase).where(TerminationCase.occupancy_id == occupancy.id).order_by(TerminationCase.created_at.desc())
        )
    )


def list_termination_cases_for_admin(db: Session, admin: AdminUser) -> list[TerminationCase]:
    """Same provider-ownership scoping as crud/occupancy.py:list_occupancies_for."""
    query = select(TerminationCase).order_by(TerminationCase.created_at.desc())
    if admin.role != "super_admin":
        query = (
            query.join(Occupancy, Occupancy.id == TerminationCase.occupancy_id)
            .join(Listing, Listing.id == Occupancy.listing_id)
            .where(Listing.owner_id == admin.id)
        )
    return list(db.scalars(query))


_REDACTED_NOTES = "[redacted -- sensitive protected-ground details, Super Admin only]"


def to_termination_case_read(case: TerminationCase, *, redact_notes: bool) -> TerminationCaseRead:
    """ZR-ENG-CLR-006 Section 21.1: called by the Host-facing list routes
    (occupancy.py) with redact_notes=(admin.role != 'super_admin') --
    the renter's own view (user_rentals.py) never redacts, since it's their
    own submission. A no-op for every cause code outside
    models.termination_case.SENSITIVE_CAUSE_CODES."""
    notes = _REDACTED_NOTES if redact_notes and case.cause_code in SENSITIVE_CAUSE_CODES else case.notes
    return TerminationCaseRead(
        id=case.id, occupancy_id=case.occupancy_id, agreement_id=case.agreement_id,
        initiator_guest_id=case.initiator_guest_id, initiator_admin_id=case.initiator_admin_id,
        cause_code=case.cause_code, status=case.status, notes=notes, notice_created_at=case.notice_created_at,
        policy_snapshot=case.policy_snapshot, earliest_effective_date=case.earliest_effective_date,
        effective_termination_date=case.effective_termination_date, withdrawn_at=case.withdrawn_at,
        tribunal_liability_amount=float(case.tribunal_liability_amount), tribunal_liability_reason=case.tribunal_liability_reason,
        created_at=case.created_at,
    )


def set_tribunal_liability(db: Session, case: TerminationCase, admin: AdminUser, data: TerminationCaseTribunalLiability) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED/Section 20.1
    -- see models/termination_case.py's own field docstring for why this is
    a Super Admin's own entry, never a computed formula. Recalculating the
    refund entitlement after this call (AC-24: a new version, not an
    overwrite) is what actually applies it to the case's own NOTICE_
    LIABILITY line."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Super Admin can set a tribunal/court-determined liability")
    if data.amount < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The liability amount cannot be negative")
    if not data.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to set a tribunal/court-determined liability")

    case.tribunal_liability_amount = data.amount
    case.tribunal_liability_reason = data.reason
    emit_event(
        db, "termination.tribunal_liability_set", "termination_case", str(case.id),
        {"occupancyId": case.occupancy_id, "amount": data.amount},
    )
    db.commit()
    db.refresh(case)
    return case
