"""ZR-ENG-CLR-006 Section 7: renter-initiated early termination.
UNILATERAL_CAUSE_CODES/HOST_UNILATERAL_CAUSE_CODES auto-resolve an effective
date outright; EVIDENCE_GATED_CAUSE_CODES auto-resolve only once the renter
attaches evidence (see models/termination_case.py's own docstrings for
exactly why); everything else falls closed to PENDING_REVIEW rather than
fabricating a notice period or liability outcome (Section 2 doctrine: 'Fail
closed on unsupported legal pathways') -- a cause code itself is still
rejected with a 400 if it's outside TERMINATION_CAUSE_CODES entirely, or
belongs to the other party (RENTER_ONLY_CAUSE_CODES/HOST_ONLY_CAUSE_CODES)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy, to_termination_policy_snapshot
from app.crud.occupancy import has_co_tenants
from app.crud.party import assert_provider_access, assert_provider_access_any, party_id_for_listing
from app.crud.provider_actor import ProviderActor, admin_id, event_actor, user_id
from app.models.user_account import UserAccount
from app.models.market_policy import MarketPolicyPack
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.leasing import Agreement
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.termination_case import (
    EVIDENCE_GATED_CAUSE_CODES,
    HOST_ONLY_CAUSE_CODES,
    HOST_UNILATERAL_CAUSE_CODES,
    IMMEDIATE_CAUSE_CODES,
    MUTUAL_CAUSE_CODES,
    NOTICE_CAUSE_CODES,
    RENTER_ONLY_CAUSE_CODES,
    SENSITIVE_CAUSE_CODES,
    TERMINATION_CAUSE_CODES,
    UNILATERAL_CAUSE_CODES,
    MitigationRecord,
    TerminationCase,
    TerminationDecision,
)
from app.schemas.termination import (
    MitigationRecordCreate,
    TerminationCaseCreate,
    TerminationCasePreviewRead,
    TerminationCasePreviewRequest,
    TerminationCaseRead,
    TerminationCaseResponseRead,
)


def _record_decision(
    db: Session, case: TerminationCase, *, basis: str, authority: str,
    effective_termination_at: date | None, approved_by_admin_id: int | None = None,
    approved_by_user_id: int | None = None,
    external_order_ref: str = "", reason: str = "",
) -> TerminationDecision:
    """ZR-ENG-CLR-006 Section 19's own termination_decision entity -- an
    append-only history row for every point this build actually decides an
    effective date/pathway outcome, alongside (not instead of) the case's
    own flattened current-value columns."""
    decision = TerminationDecision(
        termination_case_id=case.id, effective_termination_at=effective_termination_at,
        decision_basis=basis, authority=authority, approved_by_admin_id=approved_by_admin_id,
        approved_by_user_id=approved_by_user_id,
        external_order_ref=external_order_ref, reason=reason,
    )
    db.add(decision)
    db.flush()
    return decision


_OPEN_CASE_STATUSES = ("OPENED", "SURRENDER_PROPOSED", "PENDING_REVIEW", "EFFECTIVE_DATE_SET", "AWAITING_RESPONSE", "CONTESTED")


def _min_notice_days(occupancy: Occupancy) -> int:
    from app.services.policy import get_policy

    return int(get_policy(occupancy.listing.market_release if occupancy.listing else None, "termination.contested_min_notice_days"))


def _align_to_rent_cycle(raw_date: date) -> date:
    """ZR-ENG-CLR-006 Section 10: 'rent-cycle alignment -- some markets align
    termination to rent cycle; others do not.' Rounds up to the last day of
    raw_date's calendar month -- a reasonable placeholder cycle boundary for
    this build's only real cadence (MONTHLY), not a verified market rule.
    Only reached when a market pack turns on align_termination_to_rent_cycle
    (off by default -- see models/market_policy.py's own field docstring)."""
    if raw_date.month == 12:
        next_month_first = date(raw_date.year + 1, 1, 1)
    else:
        next_month_first = date(raw_date.year, raw_date.month + 1, 1)
    return next_month_first - timedelta(days=1)


def _resolve_earliest_effective_date(cause_code: str, policy: MarketPolicyPack) -> date:
    """ZR-ENG-CLR-006 Section 10's notice engine, trimmed to this increment's
    one cause-driven rule: immediate for a Host-fault/habitability cause,
    market-pack-resolved notice days otherwise (AC-03: never hard-coded)."""
    today = date.today()
    if cause_code in IMMEDIATE_CAUSE_CODES:
        return today
    assert cause_code in NOTICE_CAUSE_CODES
    raw_date = today + timedelta(days=policy.termination_notice_days)
    if policy.align_termination_to_rent_cycle:
        return _align_to_rent_cycle(raw_date)
    return raw_date


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
    data: TerminationCaseCreate, policy: MarketPolicyPack, *, auto_cause_codes: tuple[str, ...], joint_tenancy: bool = False,
    min_notice_days: int = 28,
) -> tuple[str, date | None, date | None]:
    """Returns (status, earliest_effective_date, effective_termination_date)
    for a newly-opened case. Four pathways, per models/termination_case.py's
    own docstring: consent-gated (MUTUAL_CAUSE_CODES), evidence-gated-
    immediate (EVIDENCE_GATED_CAUSE_CODES with at least one evidence_refs
    entry supplied), auto-computed (whichever set the caller passes --
    UNILATERAL_CAUSE_CODES for a renter, HOST_UNILATERAL_CAUSE_CODES for a
    Host), or PENDING_REVIEW (everything else this build can't compute a
    date for -- AC-35).

    ZR-ENG-CLR-006 AC-25: joint_tenancy=True (this occupancy has one or
    more OccupancyCoTenant rows) always falls to PENDING_REVIEW instead,
    ahead of every other check -- including a MUTUAL_SURRENDER proposal,
    which would otherwise let just one tenant and the Host agree to end a
    tenancy other co-tenants never consented to. No jurisdiction-general
    rule exists here for whether one joint tenant's own notice ends the
    whole tenancy, only their own interest, or neither."""
    if data.cause_code in MUTUAL_CAUSE_CODES:
        proposed_date = _require_proposed_effective_date(data)
        # With co-tenants every tenant must agree too, not just one of them.
        return ("AWAITING_RESPONSE" if joint_tenancy else "SURRENDER_PROPOSED"), proposed_date, None
    evidence_now = data.cause_code in EVIDENCE_GATED_CAUSE_CODES and bool(data.evidence_refs)
    if not joint_tenancy:
        if evidence_now:
            today = date.today()
            return "EFFECTIVE_DATE_SET", today, today
        if data.cause_code in auto_cause_codes:
            effective_date = _resolve_earliest_effective_date(data.cause_code, policy)
            return "EFFECTIVE_DATE_SET", effective_date, effective_date
    # Everything else is decided by the parties (crud/termination_responses.py):
    # a proposed date the other side(s) accept, counter or contest. A date
    # the rules could compute is the default proposal; a contested ground
    # must name its own, at least the minimum notice away.
    if evidence_now:
        default = date.today()
    elif data.cause_code in auto_cause_codes:
        default = _resolve_earliest_effective_date(data.cause_code, policy)
    else:
        default = None
    if default is not None and data.proposed_effective_date is None:
        return "AWAITING_RESPONSE", default, None
    if data.proposed_effective_date is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Propose the date the tenancy should end")
    if data.proposed_effective_date < date.today():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The proposed end date cannot be in the past")
    if default is None and data.proposed_effective_date < date.today() + timedelta(days=min_notice_days):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"For this reason the end date must be at least {min_notice_days} days away",
        )
    return "AWAITING_RESPONSE", data.proposed_effective_date, None


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
    if case.status == "AWAITING_RESPONSE":
        date_phrase = f"proposed for {case.earliest_effective_date.isoformat()} -- respond by {case.response_due_at.date().isoformat()}"
    elif case.status == "PENDING_REVIEW":
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
    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
    case_status, effective_date, final_date = _resolve_case_opening(
        data, policy, auto_cause_codes=UNILATERAL_CAUSE_CODES, joint_tenancy=has_co_tenants(db, occupancy.id),
        min_notice_days=_min_notice_days(occupancy),
    )
    agreement = _get_agreement_or_409(db, occupancy)

    now = datetime.now(timezone.utc)
    case = TerminationCase(
        occupancy_id=occupancy.id,
        agreement_id=agreement.id,
        initiator_guest_id=guest.id,
        cause_code=data.cause_code,
        status=case_status,
        notes=data.notes,
        notice_created_at=now,
        # PORTAL service is instantaneous -- there's no separate delivery
        # step this build waits on (see models/termination_case.py's own
        # notice_served_at field docstring).
        # The portal copy is delivered at submission whatever the method.
        notice_served_at=now,
        notice_method=data.notice_method,
        evidence_refs=data.evidence_refs,
        policy_snapshot=to_termination_policy_snapshot(policy),
        earliest_effective_date=effective_date,
        effective_termination_date=final_date,
    )
    db.add(case)
    db.flush()
    # ZR-ENG-CLR-006 Section 20.2: the domain-event outbox this codebase
    # already has (crud/events.py), applied to termination cases for the
    # first time -- emitted in the same transaction as the case row itself.
    emit_event(
        db, "termination.case_opened", "termination_case", str(case.id), {"occupancyId": occupancy.id, "causeCode": case.cause_code},
        actor_kind="guest", actor_id=str(guest.id), new_state=case.status,
    )
    emit_event(
        db, "termination.policy_resolved", "termination_case", str(case.id), {"policyPackId": policy.id, "policyPackVersion": policy.version},
        actor_kind="guest", actor_id=str(guest.id),
    )
    if case_status == "EFFECTIVE_DATE_SET":
        basis = "AUTO_RESOLVED_EVIDENCE_GATED" if data.cause_code in EVIDENCE_GATED_CAUSE_CODES else "AUTO_RESOLVED_UNILATERAL"
        _record_decision(db, case, basis=basis, authority="system", effective_termination_at=final_date)
    if case_status == "AWAITING_RESPONSE":
        from app.crud.termination_responses import start_response_round

        start_response_round(db, case, proposer=guest.id)
    db.commit()
    db.refresh(case)

    _notify_case_opened(db, case, occupancy, notify_party=True)
    return case


_DEPOSIT_DISCLAIMER = (
    "Your security deposit is handled entirely separately from this termination and refund estimate (Section 2/14: "
    "early termination never automatically forfeits it). Any deduction requires its own evidenced claim."
)
_ALTERNATIVES_NOTE = (
    "You may also be able to end this tenancy on different terms: propose a mutual surrender with your Host for an "
    "agreed date, or ask about assignment/sublet where your market and agreement allow it -- both can carry a "
    "different cost outcome than the estimate above."
)


def preview_termination_case(db: Session, occupancy: Occupancy, guest: Guest, data: TerminationCasePreviewRequest) -> TerminationCasePreviewRead:
    """ZR-ENG-CLR-006 Section 7.1 Step 5: the renter's own cost/pathway
    preview BEFORE they submit a real notice (Step 6) -- nothing here is
    persisted (no TerminationCase, notice or evidence record is created).
    Reuses the exact same resolution path open_termination_case itself uses
    (_resolve_case_opening against the same auto_cause_codes) so the preview
    can never drift from what actually submitting would produce, and the
    same _compute_policy_liability/_rent_obligations_for_case the real
    Refund Entitlement Engine uses (crud/refund_entitlement.py) against a
    transient, never-added-to-session TerminationCase built only to carry
    the fields those functions read -- this occupancy's real, already-paid
    obligations are what get classified, not a fabricated forecast."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can preview a termination")
    if occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This occupancy does not belong to you")
    if data.cause_code not in TERMINATION_CAUSE_CODES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized cause code '{data.cause_code}'")
    if data.cause_code in HOST_ONLY_CAUSE_CODES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{data.cause_code}' describes a Host/platform-initiated action -- a renter cannot invoke it",
        )

    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
    agreement = _get_agreement_or_409(db, occupancy)
    joint_tenancy = has_co_tenants(db, occupancy.id)
    # Mirrors _resolve_case_opening exactly, but MUTUAL_SURRENDER's own
    # "a proposed date is required" validation is skipped here -- a preview
    # with no date yet in mind should still describe the pathway (host
    # consent required, no computable estimate) rather than 400.
    if joint_tenancy:
        # AC-25: every tenant (and the host) answers the proposal.
        resolved_status = "AWAITING_RESPONSE"
        earliest_effective_date = data.proposed_effective_date
    elif data.cause_code in MUTUAL_CAUSE_CODES:
        resolved_status = "SURRENDER_PROPOSED"
        earliest_effective_date = data.proposed_effective_date
    elif data.cause_code in EVIDENCE_GATED_CAUSE_CODES and data.evidence_refs:
        resolved_status = "EFFECTIVE_DATE_SET"
        earliest_effective_date = date.today()
    elif data.cause_code in UNILATERAL_CAUSE_CODES:
        resolved_status = "EFFECTIVE_DATE_SET"
        earliest_effective_date = _resolve_earliest_effective_date(data.cause_code, policy)
    else:
        resolved_status = "AWAITING_RESPONSE"
        earliest_effective_date = data.proposed_effective_date

    result = TerminationCasePreviewRead(
        cause_code=data.cause_code,
        resolved_status=resolved_status,
        # The host answers any proposal (and, with co-tenants, so does everyone else).
        requires_host_consent=resolved_status in ("SURRENDER_PROPOSED", "AWAITING_RESPONSE"),
        requires_evidence_to_resolve_now=(
            not joint_tenancy and data.cause_code in EVIDENCE_GATED_CAUSE_CODES and not data.evidence_refs
        ),
        earliest_effective_date=earliest_effective_date,
        estimated_earned_rent=None,
        estimated_refundable_unearned_rent=None,
        estimated_liability_amount=None,
        estimated_liability_note=(
            "No cost estimate can be computed yet -- this pathway needs a determined effective date first "
            "(a Host/Super-Admin review, or a Host-accepted surrender date)."
        ) if earliest_effective_date is None else "",
        estimated_mitigation_credit=None,
        estimated_net_refund=None,
        deposit_disclaimer=_DEPOSIT_DISCLAIMER,
        alternatives_note=_ALTERNATIVES_NOTE,
    )
    if earliest_effective_date is None:
        return result

    from app.crud.refund_entitlement import _compute_policy_liability, _rent_obligations_for_case

    transient_case = TerminationCase(
        occupancy_id=occupancy.id, agreement_id=agreement.id, cause_code=data.cause_code,
        policy_snapshot=to_termination_policy_snapshot(policy), effective_termination_date=earliest_effective_date,
    )
    transient_case.agreement = agreement
    transient_case.occupancy = occupancy

    earned = 0.0
    refundable_unearned = 0.0
    for obligation in _rent_obligations_for_case(transient_case):
        net_paid = round(float(sum(a.amount_allocated for a in obligation.allocations)), 2)
        if net_paid <= 0:
            continue
        if obligation.due_date <= earliest_effective_date:
            earned = round(earned + net_paid, 2)
        else:
            refundable_unearned = round(refundable_unearned + net_paid, 2)

    liability_amount, liability_note, mitigation_credit, _mitigation_note = _compute_policy_liability(db, transient_case)
    result.estimated_earned_rent = earned
    result.estimated_refundable_unearned_rent = refundable_unearned
    result.estimated_liability_amount = liability_amount
    result.estimated_liability_note = liability_note
    result.estimated_mitigation_credit = mitigation_credit
    result.estimated_net_refund = round(max(0.0, refundable_unearned - liability_amount), 2)
    return result


def open_host_termination_case(db: Session, occupancy: Occupancy, admin: ProviderActor, data: TerminationCaseCreate) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 8: 'Start termination / possession process,'
    not 'Cancel renter.' HOST_UNILATERAL_CAUSE_CODES/MUTUAL_CAUSE_CODES
    resolve automatically; every other Host pathway in Section 8.1's table
    (RENTER_BREACH_ALLEGED, OWNER_MOVE_IN/SALE/OTHER_GROUND, SAFETY_
    EMERGENCY) still opens a case, just PENDING_REVIEW -- see
    models/termination_case.py's own docstring for why."""
    assert_provider_access_any(db, admin, party_id_for_listing(occupancy.listing))
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
    policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
    case_status, effective_date, final_date = _resolve_case_opening(
        data, policy, auto_cause_codes=HOST_UNILATERAL_CAUSE_CODES, joint_tenancy=has_co_tenants(db, occupancy.id),
        min_notice_days=_min_notice_days(occupancy),
    )
    agreement = _get_agreement_or_409(db, occupancy)

    now = datetime.now(timezone.utc)
    case = TerminationCase(
        occupancy_id=occupancy.id,
        agreement_id=agreement.id,
        initiator_admin_id=admin_id(admin),
        initiator_host_user_id=user_id(admin),
        cause_code=data.cause_code,
        status=case_status,
        notes=data.notes,
        notice_created_at=now,
        # The portal copy is delivered at submission whatever the method.
        notice_served_at=now,
        notice_method=data.notice_method,
        evidence_refs=data.evidence_refs,
        policy_snapshot=to_termination_policy_snapshot(policy),
        earliest_effective_date=effective_date,
        effective_termination_date=final_date,
    )
    db.add(case)
    db.flush()
    emit_event(
        db, "termination.case_opened", "termination_case", str(case.id), {"occupancyId": occupancy.id, "causeCode": case.cause_code},
        actor_kind=event_actor(admin)[0], actor_id=event_actor(admin)[1], new_state=case.status,
    )
    emit_event(
        db, "termination.policy_resolved", "termination_case", str(case.id), {"policyPackId": policy.id, "policyPackVersion": policy.version},
        actor_kind=event_actor(admin)[0], actor_id=event_actor(admin)[1],
    )
    if case_status == "EFFECTIVE_DATE_SET":
        basis = "AUTO_RESOLVED_EVIDENCE_GATED" if data.cause_code in EVIDENCE_GATED_CAUSE_CODES else "AUTO_RESOLVED_UNILATERAL"
        _record_decision(db, case, basis=basis, authority="system", effective_termination_at=final_date)
    if case_status == "AWAITING_RESPONSE":
        from app.crud.termination_responses import start_response_round

        start_response_round(db, case, proposer="HOST")
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

    previous_status = case.status
    case.status = "WITHDRAWN"
    case.withdrawn_at = datetime.now(timezone.utc)
    emit_event(
        db, "termination.case_withdrawn", "termination_case", str(case.id), {"occupancyId": case.occupancy_id},
        actor_kind="guest", actor_id=str(guest.id), previous_state=previous_status, new_state=case.status,
    )
    db.commit()
    db.refresh(case)
    return case


def _host_initiated(case: TerminationCase) -> bool:
    return case.initiator_admin_id is not None or case.initiator_host_user_id is not None


def _assert_is_responding_party(
    case: TerminationCase, *, guest: Guest | None, admin: AdminUser | None, host_user: UserAccount | None = None,
) -> None:
    """The other party from whoever proposed -- a proposer 'accepting' their
    own offer isn't a real negotiation, so it's rejected the same as an
    unrelated stranger would be."""
    if guest is not None:
        if case.occupancy.guest_id != guest.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This termination case does not belong to you")
        if case.initiator_guest_id is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "You proposed this surrender -- the Host must respond to it")
    elif admin is not None or host_user is not None:
        if _host_initiated(case):
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
    host_user: UserAccount | None = None,
) -> TerminationCase:
    """ZR-ENG-CLR-006 Section 7.1 Step 9/Section 8.1 MUTUAL_SURRENDER_PROPOSAL:
    the other party's affirmative acceptance is what actually makes the
    proposed date binding -- see models/termination_case.py:MUTUAL_CAUSE_CODES
    for why there's no auto-accept path."""
    if admin is not None:
        assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if host_user is not None:
        assert_provider_access_any(db, host_user, party_id_for_listing(case.occupancy.listing))
    _assert_is_responding_party(case, guest=guest, admin=admin, host_user=host_user)
    if case.status != "SURRENDER_PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This case is not awaiting a surrender response (status: {case.status})")

    previous_status = case.status
    case.status = "EFFECTIVE_DATE_SET"
    effective_date = case.earliest_effective_date
    case.effective_termination_date = effective_date
    host = admin if admin is not None else host_user
    actor_kind, actor_id = event_actor(host) if host is not None else ("guest", str(guest.id))
    emit_event(
        db, "termination.mutual_surrender_accepted", "termination_case", str(case.id),
        {"occupancyId": case.occupancy_id, "effectiveDate": effective_date.isoformat()},
        actor_kind=actor_kind, actor_id=actor_id, previous_state=previous_status, new_state=case.status,
    )
    _record_decision(
        db, case, basis="MUTUAL_SURRENDER_ACCEPTED",
        authority="host_admin" if admin is not None else "host" if host_user is not None else "renter",
        effective_termination_at=effective_date, approved_by_admin_id=admin.id if admin is not None else None,
        approved_by_user_id=host_user.id if host_user is not None else None,
    )
    db.commit()
    db.refresh(case)
    _notify_surrender_response(db, case, accepted=True)
    return case


def decline_mutual_surrender(
    db: Session, case: TerminationCase, *, guest: Guest | None = None, admin: AdminUser | None = None,
    host_user: UserAccount | None = None,
) -> TerminationCase:
    """QT-07: 'Mutual surrender offer expires/rejected; original tenancy
    remains active.' The occupancy is never touched here -- only the case's
    own status changes, freeing the occupancy to have a new case opened."""
    if admin is not None:
        assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if host_user is not None:
        assert_provider_access_any(db, host_user, party_id_for_listing(case.occupancy.listing))
    _assert_is_responding_party(case, guest=guest, admin=admin, host_user=host_user)
    if case.status != "SURRENDER_PROPOSED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This case is not awaiting a surrender response (status: {case.status})")

    previous_status = case.status
    case.status = "SURRENDER_DECLINED"
    host = admin if admin is not None else host_user
    actor_kind, actor_id = event_actor(host) if host is not None else ("guest", str(guest.id))
    emit_event(
        db, "termination.mutual_surrender_declined", "termination_case", str(case.id), {"occupancyId": case.occupancy_id},
        actor_kind=actor_kind, actor_id=actor_id, previous_state=previous_status, new_state=case.status,
    )
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
        initiator_host_user_id=case.initiator_host_user_id,
        cause_code=case.cause_code, status=case.status, notes=notes, notice_created_at=case.notice_created_at,
        notice_served_at=case.notice_served_at, notice_method=case.notice_method, evidence_refs=case.evidence_refs,
        policy_snapshot=case.policy_snapshot, earliest_effective_date=case.earliest_effective_date,
        effective_termination_date=case.effective_termination_date, withdrawn_at=case.withdrawn_at,
        tribunal_liability_amount=float(case.tribunal_liability_amount), tribunal_liability_reason=case.tribunal_liability_reason,
        adjudicated_effective_date=case.adjudicated_effective_date,
        adjudicated_effective_date_reason=case.adjudicated_effective_date_reason,
        notice_service_proof_ref=case.notice_service_proof_ref,
        notice_service_recorded_by_admin_id=case.notice_service_recorded_by_admin_id,
        notice_service_recorded_by_user_id=case.notice_service_recorded_by_user_id,
        notice_acknowledged_at=case.notice_acknowledged_at,
        proposer=case.proposer,
        response_round=case.response_round or 1,
        response_due_at=case.response_due_at,
        deemed_on_silence=case.deemed_on_silence if case.deemed_on_silence is not None else True,
        pending_responders=case.pending_responders,
        responses=[
            TerminationCaseResponseRead(
                response_round=r.response_round, responder=r.responder, response=r.response,
                proposed_date=r.proposed_date, note=r.note, created_at=r.created_at,
            )
            for r in case.responses
        ],
        external_outcome=case.external_outcome or "",
        external_order_ref=case.external_order_ref or "",
        created_at=case.created_at,
    )


def _assert_initiating_side(case: TerminationCase, *, guest: Guest | None, host_user: UserAccount | None) -> None:
    if guest is not None:
        if case.initiator_guest_id != guest.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who gave this notice can attach proof of delivery")
    elif host_user is None or not _host_initiated(case):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person who gave this notice can attach proof of delivery")


def attach_notice_proof(
    db: Session, case: TerminationCase, *, proof_ref: str, served_at: datetime,
    guest: Guest | None = None, host_user: UserAccount | None = None,
) -> TerminationCase:
    """Proof of delivery by the notice's own off-portal method (post, hand
    delivery, prescribed form...), attached by whoever served it. The portal
    copy already made the notice served at submission; this is supporting
    evidence, recorded once."""
    if host_user is not None:
        assert_provider_access_any(db, host_user, party_id_for_listing(case.occupancy.listing))
    _assert_initiating_side(case, guest=guest, host_user=host_user)
    if case.notice_method == "PORTAL":
        raise HTTPException(status.HTTP_409_CONFLICT, "A portal notice needs no separate proof of delivery")
    if case.notice_service_proof_ref:
        raise HTTPException(status.HTTP_409_CONFLICT, "Proof of delivery has already been attached")
    if not proof_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Describe or reference the proof of delivery")

    case.notice_service_proof_ref = proof_ref.strip()[:500]
    case.notice_served_at = min(case.notice_served_at or served_at, served_at)
    case.notice_service_recorded_by_user_id = host_user.id if host_user is not None else (
        case.occupancy.guest.user_account_id if case.occupancy.guest else None
    )
    actor_kind, actor_id = ("host_user", str(host_user.id)) if host_user is not None else ("guest", str(guest.id))
    emit_event(
        db, "termination.notice_proof_attached", "termination_case", str(case.id),
        {"occupancyId": case.occupancy_id, "noticeMethod": case.notice_method}, actor_kind=actor_kind, actor_id=actor_id,
    )
    db.commit()
    db.refresh(case)
    return case


def acknowledge_notice(
    db: Session, case: TerminationCase, *, guest: Guest | None = None, host_user: UserAccount | None = None,
) -> TerminationCase:
    """The receiving side confirms they got the notice: the renter for a
    host-initiated case, the host for a renter-initiated one."""
    if guest is not None:
        if case.occupancy.guest_id != guest.id or case.initiator_guest_id is not None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person this notice was given to can acknowledge it")
    elif host_user is not None:
        assert_provider_access_any(db, host_user, party_id_for_listing(case.occupancy.listing))
        if case.initiator_guest_id is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the person this notice was given to can acknowledge it")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An acknowledging party is required")
    if case.notice_acknowledged_at is None:
        case.notice_acknowledged_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(case)
    return case


_TERMINAL_CASE_STATUSES = ("TERMINATED", "WITHDRAWN", "REJECTED_PATHWAY")


def record_mitigation(db: Session, case: TerminationCase, admin: AdminUser, data: MitigationRecordCreate) -> MitigationRecord:
    """ZR-ENG-CLR-006 Section 11.2/20.1 POST /termination-cases/{id}/
    mitigation -- see models/termination_case.py:MitigationRecord's own
    docstring for exactly what this does and doesn't do: records evidence
    here; a later calculate_refund_entitlement call is what actually reads
    it back into a real MITIGATION_CREDIT, and only for the
    ACTUAL_REASONABLE_LOSS liability model (crud/refund_entitlement.py:
    _compute_mitigation_credit)."""
    assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))

    record = MitigationRecord(
        termination_case_id=case.id,
        marketed_for_reletting_at=data.marketed_for_reletting_at,
        listing_channels=data.listing_channels,
        replacement_booking_id=data.replacement_booking_id,
        replacement_occupancy_start=data.replacement_occupancy_start,
        replacement_rent_amount=data.replacement_rent_amount,
        reasonable_reletting_costs=data.reasonable_reletting_costs,
        evidence_refs=data.evidence_refs,
        notes=data.notes,
        recorded_by_admin_id=admin.id,
    )
    db.add(record)
    db.flush()
    emit_event(
        db, "termination.mitigation_updated", "termination_case", str(case.id),
        {"mitigationRecordId": record.id, "occupancyId": case.occupancy_id},
        actor_kind="admin", actor_id=str(admin.id),
    )
    db.commit()
    db.refresh(record)
    return record


def list_mitigation_records_for_case(db: Session, case: TerminationCase) -> list[MitigationRecord]:
    return list(
        db.scalars(
            select(MitigationRecord)
            .where(MitigationRecord.termination_case_id == case.id)
            .order_by(MitigationRecord.created_at.desc())
        )
    )


def list_termination_decisions_for_case(db: Session, case: TerminationCase) -> list[TerminationDecision]:
    """ZR-ENG-CLR-006 Section 19's own termination_decision entity, in the
    order they happened -- how effective_termination_date actually got
    decided over this case's life."""
    return list(
        db.scalars(
            select(TerminationDecision)
            .where(TerminationDecision.termination_case_id == case.id)
            .order_by(TerminationDecision.decision_at)
        )
    )
