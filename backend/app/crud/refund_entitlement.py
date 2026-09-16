"""ZR-ENG-CLR-006 Section 12: the Refund Entitlement Calculation Engine.
See models/refund_entitlement.py's own module docstring for the always-real
part of the calculation: which paid RENT obligations fall after the case's
effective_termination_date (refundable) versus on/before it (earned). What
used to be a single always-zero NOTICE_LIABILITY placeholder is now a real
per-market-pack liability-model computation (AC-12, _compute_policy_liability
below) and a real mitigation credit for the one model that needs one
(AC-13/14, _compute_mitigation_credit). RENTER_FEE/TAX/OTHER_CREDIT remain
honestly zero -- no renter-facing fee/tax obligation type exists anywhere in
this codebase (only RENT/DEPOSIT) and no service-recovery-credit concept
exists to compute a real value from; that gap belongs to Section 5's payment
model, not something this engine can fabricate around. This increment
calculates and persists the entitlement only -- execution (an actual
RefundRequest against Section 5's obligations) is a deliberately separate,
later step."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import finance as finance_crud
from app.crud import notification as notif_crud
from app.crud.events import emit_event
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.finance import Obligation, PaymentSchedule
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.refund_entitlement import RefundEntitlement, RefundEntitlementLineItem
from app.models.termination_case import EVIDENCE_GATED_CAUSE_CODES, IMMEDIATE_CAUSE_CODES, MitigationRecord, TerminationCase
from app.schemas.finance import RefundDecide, RefundRequestCreate


def _round2(amount) -> float:
    return round(float(amount), 2)


def _monthly_rent_amount(db: Session, case: TerminationCase) -> float | None:
    """Break-fee/contract-break/capped-compensation multiples (Section 11.1,
    MarketPolicyPack.termination_break_fee_rent_multiple) are expressed
    against "one month's rent" -- the same ACTIVE PaymentSchedule.amount
    crud/occupancy.py:generate_next_rent_obligation reads to generate each
    RENT obligation, not a re-derivation from a possibly-changed listing
    price (same AC-07 discipline PaymentSchedule's own docstring describes).
    None if the agreement somehow has no active schedule (defensive; every
    signed agreement gets one at creation -- crud/leasing.py:create_agreement)."""
    schedule = db.scalar(
        select(PaymentSchedule).where(PaymentSchedule.agreement_id == case.agreement_id, PaymentSchedule.status == "ACTIVE")
    )
    return float(schedule.amount) if schedule else None


def _compute_mitigation_credit(case: TerminationCase, raw_loss: float) -> tuple[float, str]:
    """ZR-ENG-CLR-006 Section 11.2/AC-13/AC-14: 'The departing renter's
    liability must stop or reduce when replacement rent removes the Host's
    economic loss' -- and the overlap_guard doctrine ('Prevents collecting
    both replacement rent and the same period of lost rent from departing
    renter'). Only meaningful for ACTUAL_REASONABLE_LOSS (the one model this
    build charges a real reasonable-loss amount for); every MitigationRecord
    with a recorded replacement_rent_amount reduces that raw loss, floored at
    zero -- never turned into a negative credit the renter would additionally
    be owed for."""
    replacement_rent_recovered = sum(
        float(r.replacement_rent_amount) for r in case.mitigation_records if r.replacement_rent_amount
    )
    if replacement_rent_recovered <= 0:
        return 0.0, ""
    credit = min(raw_loss, replacement_rent_recovered)
    return (
        _round2(credit),
        f"overlap_guard: {credit:.2f} of replacement rent already recovered by the Host reduces the reasonable-loss "
        "liability by the same amount -- the same rent period is never collected twice.",
    )


def _compute_policy_liability(db: Session, case: TerminationCase) -> tuple[float, str, float, str]:
    """ZR-ENG-CLR-006 Section 11.1/AC-12: the liability-model dispatch this
    engine previously didn't have -- see models/market_policy.py's own
    TERMINATION_LIABILITY_MODELS docstring for what each value means. Reads
    case.policy_snapshot (frozen at case-open time, crud/market_policy.py:
    to_termination_policy_snapshot), never the live MarketPolicyPack row --
    AC-02. Returns (liability_amount, liability_note, mitigation_credit,
    mitigation_credit_note) -- the credit pair is only ever nonzero for
    ACTUAL_REASONABLE_LOSS (Section 12.2 lists it as its own line item, not
    folded silently into the liability figure). The caller decides whether a
    Super Admin's own tribunal_liability_amount (set after case-open, so
    never part of the frozen snapshot) supersedes this estimate instead of
    adding to it."""
    snapshot = case.policy_snapshot or {}
    model = snapshot.get("termination_liability_model", "NOTICE_RENT")
    multiple = float(snapshot.get("termination_break_fee_rent_multiple") or 0.0)
    cap_multiple = snapshot.get("termination_liability_cap_rent_multiple")

    # IMMEDIATE_CAUSE_CODES (Host-fault/habitability/no-fault-event) and
    # EVIDENCE_GATED_CAUSE_CODES (a substantiated protected/statutory right)
    # are always zero-liability regardless of the market pack's own
    # configured model -- QT-05/Section 8's own doctrine that these grounds
    # never carry an early-termination charge, not something a market pack
    # can override upward.
    if model == "ZERO_LIABILITY" or case.cause_code in IMMEDIATE_CAUSE_CODES or case.cause_code in EVIDENCE_GATED_CAUSE_CODES:
        return 0.0, "Protected/Host-fault/no-fault termination pathway -- no early-termination charge applies (ZERO_LIABILITY).", 0.0, ""
    if model == "NOTICE_RENT":
        return 0.0, (
            "NOTICE_RENT model: no charge beyond rent already earned through the notice period -- unused rent after "
            "the effective termination date is refunded in full, never a fabricated 'remaining months x rent' charge."
        ), 0.0, ""

    monthly_rent = _monthly_rent_amount(db, case)
    if monthly_rent is None:
        return 0.0, f"{model} model: no active payment schedule found to derive a rent-multiple charge from.", 0.0, ""

    if model in ("STATUTORY_BREAK_FEE", "CONTRACT_BREAK_AMOUNT", "MIXED"):
        if multiple <= 0:
            return 0.0, f"{model} model: market pack has not configured a break-fee rent multiple -- no charge.", 0.0, ""
        amount = _round2(monthly_rent * multiple)
        note = f"{model} model: {multiple:g}x one month's rent ({monthly_rent:.2f}), per the resolved market-pack policy."
        return amount, note, 0.0, ""
    if model == "CAPPED_COMPENSATION":
        raw = _round2(monthly_rent * multiple) if multiple > 0 else 0.0
        if cap_multiple is not None:
            cap = _round2(monthly_rent * float(cap_multiple))
            amount = min(raw, cap)
            note = (
                f"CAPPED_COMPENSATION model: {multiple:g}x rent ({raw:.2f}) capped at {float(cap_multiple):g}x rent "
                f"({cap:.2f}), per the resolved market-pack policy."
            )
            return amount, note, 0.0, ""
        return raw, f"CAPPED_COMPENSATION model: {multiple:g}x rent, uncapped (no ceiling configured).", 0.0, ""
    if model == "ACTUAL_REASONABLE_LOSS":
        raw_loss = _round2(sum(float(r.reasonable_reletting_costs) for r in case.mitigation_records if r.reasonable_reletting_costs))
        if raw_loss <= 0:
            return 0.0, "ACTUAL_REASONABLE_LOSS model: no reasonable re-letting costs have been recorded yet.", 0.0, ""
        credit, credit_note = _compute_mitigation_credit(case, raw_loss)
        net_loss = _round2(max(0.0, raw_loss - credit))
        liability_note = f"ACTUAL_REASONABLE_LOSS model: {raw_loss:.2f} in recorded reasonable re-letting costs, net of any mitigation credit below."
        return net_loss, liability_note, credit, credit_note
    # TRIBUNAL_OR_COURT_DETERMINED with nothing entered yet -- provisional zero.
    return 0.0, f"{model} model: awaiting a competent tribunal/court determination -- no amount provisional.", 0.0, ""


def _rent_obligations_for_case(case: TerminationCase) -> list[Obligation]:
    agreement_obligations = list(case.agreement.obligations) if case.agreement else []
    occupancy_obligations = list(case.occupancy.obligations) if case.occupancy else []
    seen_ids: set[int] = set()
    combined: list[Obligation] = []
    for obligation in agreement_obligations + occupancy_obligations:
        if obligation.obligation_type == "RENT" and obligation.id not in seen_ids:
            seen_ids.add(obligation.id)
            combined.append(obligation)
    return sorted(combined, key=lambda o: o.due_date)


def calculate_refund_entitlement(db: Session, case: TerminationCase, admin: AdminUser) -> RefundEntitlement:
    """ZR-ENG-CLR-006 AC-10/AC-11/AC-24: computed independently from PSP/
    refund-execution status, itemized, and versioned -- a recalculation
    always inserts a new row rather than overwriting the last one."""
    assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if case.status == "WITHDRAWN":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot calculate a refund entitlement for a withdrawn case")
    if case.effective_termination_date is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This case has no effective termination date set yet")

    effective_date: date = case.effective_termination_date
    line_items: list[RefundEntitlementLineItem] = []
    gross_refundable = 0.0

    for obligation in _rent_obligations_for_case(case):
        net_paid = _round2(sum(a.amount_allocated for a in obligation.allocations))
        if net_paid <= 0:
            continue  # never paid, or already fully refunded -- nothing to itemize
        if obligation.due_date <= effective_date:
            line_items.append(RefundEntitlementLineItem(
                type="EARNED_RENT", source_obligation_id=obligation.id, period_due_date=obligation.due_date,
                amount=net_paid,
                basis_note=f"Rent for the period due {obligation.due_date.isoformat()} was earned on or before the effective termination date.",
            ))
        else:
            gross_refundable = _round2(gross_refundable + net_paid)
            line_items.append(RefundEntitlementLineItem(
                type="REFUNDABLE_UNEARNED_RENT", source_obligation_id=obligation.id, period_due_date=obligation.due_date,
                amount=net_paid,
                basis_note=f"Rent for the period due {obligation.due_date.isoformat()} falls after the effective termination date -- unearned.",
            ))

    # ZR-ENG-CLR-006 Section 11.1/AC-12: the market pack's own resolved
    # liability model (case.policy_snapshot -- AC-02) is the estimate until a
    # Super Admin enters a real TRIBUNAL_OR_COURT_DETERMINED amount
    # (crud/termination.py:set_tribunal_liability); once entered, that
    # determination supersedes the modeled estimate rather than stacking on
    # top of it -- Section 11.1's own "Amount remains provisional until
    # competent determination" doctrine, applied as an override, not an add.
    tribunal_liability = _round2(float(case.tribunal_liability_amount))
    policy_liability, policy_note, mitigation_credit, mitigation_note = _compute_policy_liability(db, case)
    if tribunal_liability > 0:
        notice_liability = tribunal_liability
        notice_note = f"Tribunal/court-determined liability (Super Admin entry, supersedes the modeled estimate): {case.tribunal_liability_reason}"
    else:
        notice_liability = policy_liability
        notice_note = policy_note
    line_items.append(RefundEntitlementLineItem(type="NOTICE_LIABILITY", amount=notice_liability, basis_note=notice_note))
    line_items.append(RefundEntitlementLineItem(
        type="MITIGATION_CREDIT", amount=mitigation_credit,
        basis_note=mitigation_note or "No re-letting/mitigation credit applies to the resolved liability model for this case.",
    ))
    net_refund = _round2(max(0.0, gross_refundable - notice_liability))

    # Section 12.2's remaining named line items -- always zero in this build
    # (see module docstring for exactly why), shown honestly rather than
    # omitted so the calculation output matches the spec's own shape. No
    # renter-facing fee/tax obligation type exists anywhere in this codebase
    # (only RENT/DEPOSIT -- see app/models/finance.py:Obligation) for
    # RENTER_FEE/TAX to compute a nonzero value from; that's a Section 5
    # payment-obligation gap, not something this engine can fabricate around.
    for zero_type, note in (
        ("RENTER_FEE", "No renter-facing fee obligation type exists in this build to refund from (Section 5 dependency)."),
        ("TAX", "No tax/VAT/GST registration configured for renter-facing charges in this build."),
        ("OTHER_CREDIT", "No relocation/service-recovery credit policy configured."),
    ):
        line_items.append(RefundEntitlementLineItem(type=zero_type, amount=0.0, basis_note=note))

    previous_max_version = db.scalar(
        select(RefundEntitlement.version)
        .where(RefundEntitlement.termination_case_id == case.id)
        .order_by(RefundEntitlement.version.desc())
        .limit(1)
    )
    entitlement = RefundEntitlement(
        termination_case_id=case.id,
        version=(previous_max_version or 0) + 1,
        currency=case.occupancy.listing.currency,
        gross_refundable=gross_refundable,
        net_refund=net_refund,
        calculated_by_admin_id=admin.id,
    )
    entitlement.line_items = line_items
    db.add(entitlement)
    db.flush()
    emit_event(
        db, "refund.entitlement_calculated", "refund_entitlement", str(entitlement.id),
        {"terminationCaseId": case.id, "version": entitlement.version, "grossRefundable": gross_refundable},
    )
    db.commit()
    db.refresh(entitlement)
    return entitlement


def approve_refund_entitlement(db: Session, entitlement: RefundEntitlement, admin: AdminUser) -> RefundEntitlement:
    """ZR-ENG-CLR-006 Section 16.1/20.1 POST /refund-entitlements/{id}/approve
    -- see models/refund_entitlement.py:REFUND_ENTITLEMENT_STATUSES's own
    docstring for why this build requires an explicit approval on every
    entitlement rather than auto-approving deterministically: no dispute/
    threshold/fraud-signal scoring exists yet to tell a low-risk entitlement
    apart from one Section 16.2 would require manual review for."""
    case = entitlement.termination_case
    assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if entitlement.status != "CALCULATED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This entitlement is not awaiting approval (status: {entitlement.status})")

    entitlement.status = "APPROVED"
    entitlement.approved_by_admin_id = admin.id
    entitlement.approved_at = datetime.now(timezone.utc)
    emit_event(
        db, "refund.approved", "refund_entitlement", str(entitlement.id),
        {"terminationCaseId": case.id, "netRefund": float(entitlement.net_refund)},
    )
    db.commit()
    db.refresh(entitlement)
    return entitlement


def execute_refund_entitlement(db: Session, entitlement: RefundEntitlement, admin: AdminUser) -> RefundEntitlement:
    """ZR-ENG-CLR-006 Section 15: 'Zoiko orchestrates the refund through the
    ledger/PSP' -- creates a real Section 5 RefundRequest for each
    REFUNDABLE_UNEARNED_RENT line, against the same payment/obligation the
    rent was originally paid on, and auto-approves it (this build's only
    funding source is whatever's in HOST_PAYABLE/DEPOSIT_CUSTODY_LIABILITY;
    crud/finance.py:decide_refund already surfaces a resulting negative
    balance via FinancialHold rather than inventing a separate waterfall --
    see that function's own docstring). Idempotent per obligation: if a
    prior entitlement version already refunded the same obligation (same
    idempotency key), that existing RefundRequest is reused, never
    duplicated (AC-23). Requires approve_refund_entitlement to have run
    first -- entitlement.approve is the only thing that moves CALCULATED to
    APPROVED (Section 16.1's own state model)."""
    case = entitlement.termination_case
    assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if entitlement.status == "EXECUTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This entitlement has already been executed")
    if entitlement.status != "APPROVED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This entitlement must be approved before it can be executed")

    # ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED: gross_refundable
    # is the honest "rent unearned" total (Section 12.2's own "Refundable
    # unearned rent" row); net_refund is that total less any tribunal/court
    # liability offset (the "Net cash refund" row -- a distinct line by
    # design). The offset is applied against obligations in the same order
    # they were itemized, reducing (never inventing) what actually gets
    # refunded per obligation so the sum paid out equals net_refund exactly.
    remaining_offset = _round2(float(entitlement.gross_refundable) - float(entitlement.net_refund))

    for line in entitlement.line_items:
        if line.type != "REFUNDABLE_UNEARNED_RENT" or line.amount <= 0:
            continue
        refund_amount = float(line.amount)
        if remaining_offset > 0:
            applied = min(remaining_offset, refund_amount)
            refund_amount = _round2(refund_amount - applied)
            remaining_offset = _round2(remaining_offset - applied)
        if refund_amount <= 0:
            continue  # fully absorbed by the tribunal/court liability offset
        obligation = db.get(Obligation, line.source_obligation_id)
        paid_allocation = next((a for a in obligation.allocations if a.amount_allocated > 0), None)
        if paid_allocation is None:
            continue  # defensive -- calculate_refund_entitlement only creates this line when one exists

        refund = finance_crud.request_refund(
            db,
            RefundRequestCreate(
                payment_id=paid_allocation.payment_id, obligation_id=obligation.id, amount=refund_amount,
                reason=f"ZR-ENG-CLR-006 termination case #{case.id}: unearned rent refund",
                idempotency_key=f"termination-case-{case.id}-obligation-{obligation.id}",
            ),
            admin,
        )
        if refund.status == "REQUESTED":
            # ZR-ENG-CLR-006 Section 20.2: refund.submitted -- a genuinely new
            # RefundRequest was just created for this line (not a stale
            # version reusing an already-decided one, AC-23), so this is a
            # real, honest "submitted for processing" milestone even though
            # this build has no separate PSP-side submitted/processing lag
            # to report beyond it.
            emit_event(
                db, "refund.submitted", "refund_request", str(refund.id),
                {"terminationCaseId": case.id, "obligationId": obligation.id, "amount": refund_amount},
            )
            refund = finance_crud.decide_refund(db, refund, admin, RefundDecide(approve=True))
            # ZR-ENG-CLR-006 Section 13/AC-15: reverses the platform fee
            # already taken on this obligation if it had already gone
            # through a completed payout -- a no-op otherwise. Only run on
            # the branch that actually just completed the refund, not on a
            # stale version reusing an already-COMPLETED one (AC-23).
            finance_crud.reverse_platform_fee_for_refund(db, obligation, refund)
        line.refund_request_id = refund.id

    entitlement.status = "EXECUTED"
    entitlement.executed_by_admin_id = admin.id
    entitlement.executed_at = datetime.now(timezone.utc)
    # ZR-ENG-CLR-006 Section 20.2 also names refund.failed/refund.settled as
    # separate events -- this build's refund_entitlement.executed below is
    # the settled milestone; refund.failed has no genuine trigger to attach
    # to (finance_crud.decide_refund/request_refund either succeed or raise,
    # they never leave a RefundRequest in a distinct "failed" state), so it
    # is deliberately not fabricated (same "honest scope" discipline as
    # REFUND_ENTITLEMENT_STATUSES's own docstring).
    emit_event(
        db, "refund_entitlement.executed", "refund_entitlement", str(entitlement.id),
        {"terminationCaseId": case.id, "netRefund": float(entitlement.net_refund)},
    )
    db.commit()
    db.refresh(entitlement)

    # ZR-ENG-CLR-006 Section 15: the renter is told once the refund actually
    # moves, the same "committed event, not a state change notifying itself"
    # doctrine every other notification in this build already follows.
    notif_crud.notify_user_by_guest(
        db, case.occupancy.guest,
        title="Your refund has been processed",
        message=f"A refund of {entitlement.currency} {entitlement.net_refund:.2f} has been processed for your ended tenancy.",
        notification_type="refund_entitlement.executed",
        related_entity_type="refund_entitlement", related_entity_id=str(entitlement.id),
    )
    return entitlement


def list_refund_entitlements_for_case(db: Session, case: TerminationCase) -> list[RefundEntitlement]:
    return list(
        db.scalars(
            select(RefundEntitlement)
            .where(RefundEntitlement.termination_case_id == case.id)
            .order_by(RefundEntitlement.version.desc())
        )
    )


def list_refund_entitlements_for_admin(db: Session, admin: AdminUser) -> list[RefundEntitlement]:
    """ZR-ENG-CLR-006 Section 12/15: the Host's refund-entitlement inbox,
    across every termination case on rooms they provide -- same provider-
    ownership scoping as crud/termination.py:list_termination_cases_for_admin."""
    query = select(RefundEntitlement).order_by(RefundEntitlement.calculated_at.desc())
    if admin.role != "super_admin":
        query = (
            query.join(TerminationCase, TerminationCase.id == RefundEntitlement.termination_case_id)
            .join(Occupancy, Occupancy.id == TerminationCase.occupancy_id)
            .join(Listing, Listing.id == Occupancy.listing_id)
            .where(Listing.owner_id == admin.id)
        )
    return list(db.scalars(query))


def get_refund_entitlement_or_404(db: Session, entitlement_id: int) -> RefundEntitlement:
    entitlement = db.get(RefundEntitlement, entitlement_id)
    if not entitlement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund entitlement not found")
    return entitlement


def get_latest_refund_entitlement_or_404(db: Session, case: TerminationCase) -> RefundEntitlement:
    entitlement = db.scalar(
        select(RefundEntitlement)
        .where(RefundEntitlement.termination_case_id == case.id)
        .order_by(RefundEntitlement.version.desc())
        .limit(1)
    )
    if not entitlement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No refund entitlement has been calculated for this case yet")
    return entitlement
