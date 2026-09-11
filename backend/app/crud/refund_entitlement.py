"""ZR-ENG-CLR-006 Section 12: the Refund Entitlement Calculation Engine.
See models/refund_entitlement.py's own module docstring for exactly what
this build can and cannot compute for real -- in short: which paid RENT
obligations fall after the case's effective_termination_date (refundable)
versus on/before it (earned), with every other named line item (fee/tax/
credit/mitigation/notice-liability-beyond-normal-rent) always zero, never
fabricated. This increment calculates and persists the entitlement only --
execution (an actual RefundRequest against Section 5's obligations) is a
deliberately separate, later step."""

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
from app.models.finance import Obligation
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.refund_entitlement import RefundEntitlement, RefundEntitlementLineItem
from app.models.termination_case import TerminationCase
from app.schemas.finance import RefundDecide, RefundRequestCreate


def _round2(amount) -> float:
    return round(float(amount), 2)


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

    # ZR-ENG-CLR-006 Section 11.1 TRIBUNAL_OR_COURT_DETERMINED: the one named
    # line item this build can carry a real, nonzero amount for -- a Super
    # Admin's own entry (crud/termination.py:set_tribunal_liability), never
    # a computed formula. Section 12's refundable_total formula treats it as
    # a lawful_non_deposit_offset, capped so net_refund never goes negative
    # (this build has no separate "renter owes Zoiko" collection flow).
    tribunal_liability = _round2(float(case.tribunal_liability_amount))
    if tribunal_liability > 0:
        line_items.append(RefundEntitlementLineItem(
            type="NOTICE_LIABILITY", amount=tribunal_liability,
            basis_note=f"Tribunal/court-determined liability (Super Admin entry): {case.tribunal_liability_reason}",
        ))
    else:
        line_items.append(RefundEntitlementLineItem(
            type="NOTICE_LIABILITY", amount=0.0,
            basis_note="No additional notice-period liability beyond ordinary rent already billed through the effective date.",
        ))
    net_refund = _round2(max(0.0, gross_refundable - tribunal_liability))

    # Section 12.2's remaining named line items -- always zero in this build
    # (see module docstring for exactly why), shown honestly rather than
    # omitted so the calculation output matches the spec's own shape.
    for zero_type, note in (
        ("MITIGATION_CREDIT", "No re-letting/mitigation tracking exists in this build yet."),
        ("RENTER_FEE", "Renter-facing service fees are disabled by default in this build."),
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
        currency="INR",
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
    duplicated (AC-23)."""
    case = entitlement.termination_case
    assert_provider_access(db, admin, party_id_for_listing(case.occupancy.listing))
    if entitlement.status != "CALCULATED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This entitlement has already been {entitlement.status.lower()}")

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
    # ZR-ENG-CLR-006 Section 20.2 names refund.submitted/refund.settled as
    # separate events -- this build has no separate PSP-settlement step to
    # distinguish them (REFUND_ENTITLEMENT_STATUSES's own docstring: "EXECUTED
    # is reached the moment every ... line's RefundRequest is approved"), so
    # one event honestly marks the single real milestone that occurs.
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
