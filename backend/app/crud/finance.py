from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.mailer import (
    send_deposit_status_email,
    send_payment_confirmed_email,
    send_payout_paid_email,
    send_refund_completed_email,
)
from app.crud.authority import get_valid_authority_for_room
from app.crud.guest import get_user_for_guest
from app.crud import notification as notif_crud
from app.crud.occupancy import generate_next_rent_obligation
from app.crud.party import assert_provider_access, get_or_create_default_party
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.finance import (
    PLATFORM_FEE_RATE,
    DepositRecord,
    DisputeCase,
    Obligation,
    PaymentAllocation,
    PayoutRecord,
    ReconciliationRun,
    RefundRequest,
    SimulatedPayment,
)
from app.models.leasing import Agreement, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.room import Room
from app.schemas.finance import (
    DepositRelease,
    DisputeCreate,
    DisputeResolve,
    ObligationRead,
    PaymentConfirm,
    RefundDecide,
    RefundRequestCreate,
    SimulatedPaymentCreate,
)


def _round2(amount) -> float:
    return round(float(amount), 2)


def recompute_obligation_status(db: Session, obligation: Obligation) -> None:
    """The only place `Obligation.status` is ever assigned -- derived from the actual
    allocation sum so it can't drift from the ledger."""
    allocated = sum(_round2(a.amount_allocated) for a in obligation.allocations)
    outstanding = _round2(obligation.amount) - allocated

    if obligation.status in ("WAIVED", "FAILED"):
        return  # terminal states set explicitly elsewhere, not derived from allocations
    was_refunded = any(a.amount_allocated < 0 for a in obligation.allocations)
    if allocated <= 0:
        # A refund can zero out (or overshoot past zero) what was previously paid --
        # that's REFUNDED, not PENDING (which means "never paid at all").
        obligation.status = "REFUNDED" if was_refunded else "PENDING"
    elif outstanding <= 0:
        obligation.status = "PAID"
    else:
        obligation.status = "PARTIALLY_PAID"


def get_amount_outstanding(obligation: Obligation) -> float:
    allocated = sum(_round2(a.amount_allocated) for a in obligation.allocations)
    return _round2(obligation.amount) - allocated


def to_obligation_read(obligation: Obligation) -> ObligationRead:
    guest_id = obligation.agreement.offer.guest_id if obligation.agreement else obligation.occupancy.guest_id
    return ObligationRead(
        id=obligation.id,
        obligation_type=obligation.obligation_type,
        money_plane=obligation.money_plane,
        amount=obligation.amount,
        currency=obligation.currency,
        due_date=obligation.due_date,
        status=obligation.status,
        guest_id=guest_id,
        agreement_id=obligation.agreement_id,
        occupancy_id=obligation.occupancy_id,
        payout_id=obligation.payout_id,
        created_at=obligation.created_at,
    )


def _owned_listing_ids(db: Session, admin: AdminUser):
    return select(Listing.id).where(Listing.owner_id == admin.id)


def _owned_occupancy_ids(db: Session, admin: AdminUser):
    return select(Occupancy.id).where(Occupancy.listing_id.in_(_owned_listing_ids(db, admin)))


def _owned_obligation_ids(db: Session, admin: AdminUser) -> set[int]:
    """A regular admin only owns obligations reachable through one of their own
    listings -- either via the initial agreement-linked obligation, or via a
    recurring occupancy-linked one."""
    via_agreement = db.scalars(
        select(Obligation.id)
        .join(Agreement, Agreement.id == Obligation.agreement_id)
        .join(Offer, Offer.id == Agreement.offer_id)
        .where(Offer.listing_id.in_(_owned_listing_ids(db, admin)))
    )
    via_occupancy = db.scalars(select(Obligation.id).where(Obligation.occupancy_id.in_(_owned_occupancy_ids(db, admin))))
    return set(via_agreement) | set(via_occupancy)


def _owned_payment_ids(db: Session, admin: AdminUser) -> set[int]:
    obligation_ids = _owned_obligation_ids(db, admin)
    if not obligation_ids:
        return set()
    return set(db.scalars(select(PaymentAllocation.payment_id).where(PaymentAllocation.obligation_id.in_(obligation_ids))))


def list_obligations(
    db: Session, admin: AdminUser, occupancy_id: int | None = None, agreement_id: int | None = None
) -> list[Obligation]:
    query = select(Obligation).order_by(Obligation.due_date)
    if occupancy_id is not None:
        query = query.where(Obligation.occupancy_id == occupancy_id)
    if agreement_id is not None:
        query = query.where(Obligation.agreement_id == agreement_id)
    if admin.role != "super_admin":
        query = query.where(Obligation.id.in_(_owned_obligation_ids(db, admin)))
    return list(db.scalars(query))


def annotate_payment_context(payment: SimulatedPayment) -> SimulatedPayment:
    """Sets transient (non-persisted) display attributes so SimulatedPaymentRead
    can show property/room/tenant context instead of a bare status flag --
    derived from the occupancy behind the payment's allocations, which is the
    only place that chain of relationships actually exists."""
    payment.guest_name = payment.guest.name if payment.guest else ""
    payment.listing_id = None
    payment.listing_name = ""
    payment.room_id = None
    payment.property_address = ""
    for allocation in payment.allocations:
        allocation.obligation_type = allocation.obligation.obligation_type if allocation.obligation else ""
        occupancy = allocation.obligation.occupancy if allocation.obligation else None
        if occupancy and payment.listing_id is None:
            payment.listing_id = occupancy.listing_id
            payment.listing_name = occupancy.listing.name if occupancy.listing else ""
            payment.room_id = occupancy.room_id
            if occupancy.room and occupancy.room.property:
                payment.property_address = occupancy.room.property.address
    return payment


def list_payments(db: Session, admin: AdminUser) -> list[SimulatedPayment]:
    query = (
        select(SimulatedPayment)
        .options(
            selectinload(SimulatedPayment.guest),
            selectinload(SimulatedPayment.allocations)
            .selectinload(PaymentAllocation.obligation)
            .selectinload(Obligation.occupancy)
            .selectinload(Occupancy.listing),
            selectinload(SimulatedPayment.allocations)
            .selectinload(PaymentAllocation.obligation)
            .selectinload(Obligation.occupancy)
            .selectinload(Occupancy.room)
            .selectinload(Room.property),
        )
        .order_by(SimulatedPayment.created_at.desc())
    )
    if admin.role != "super_admin":
        query = query.where(SimulatedPayment.id.in_(_owned_payment_ids(db, admin)))
    return [annotate_payment_context(p) for p in db.scalars(query)]


def get_payment_or_404(db: Session, payment_id: int) -> SimulatedPayment:
    payment = db.get(SimulatedPayment, payment_id)
    if not payment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
    return payment


def create_payment_intent(db: Session, data: SimulatedPaymentCreate) -> SimulatedPayment:
    """Get-or-create by idempotency key -- a retried request never creates a second
    payment intent. A reused key must describe the *same* request (guest/amount/
    currency) -- otherwise it's a key collision between two different requests,
    not a retry, and returning the old payment would silently discard the new one."""
    existing = db.scalar(select(SimulatedPayment).where(SimulatedPayment.idempotency_key == data.idempotency_key))
    if existing:
        if (
            existing.guest_id != data.guest_id
            or _round2(existing.amount) != _round2(data.amount)
            or existing.currency != data.currency
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This idempotency key was already used for a different payment request",
            )
        return existing

    payment = SimulatedPayment(
        guest_id=data.guest_id,
        amount=data.amount,
        currency=data.currency,
        idempotency_key=data.idempotency_key,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def confirm_payment(db: Session, payment: SimulatedPayment, data: PaymentConfirm, admin: AdminUser) -> SimulatedPayment:
    """Idempotent: replaying a confirm call against an already-SUCCEEDED payment is a
    no-op that returns the existing state instead of allocating a second time."""
    if payment.status == "SUCCEEDED":
        return payment
    if payment.status == "FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment already failed")

    requested_total = _round2(sum(a.amount for a in data.allocations))
    if requested_total != _round2(payment.amount):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Allocations must sum to the full payment amount")

    obligations = []
    for allocation in data.allocations:
        obligation = db.get(Obligation, allocation.obligation_id)
        if not obligation:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Obligation {allocation.obligation_id} not found")
        db.add(PaymentAllocation(payment_id=payment.id, obligation_id=obligation.id, amount_allocated=allocation.amount))
        obligations.append(obligation)

    db.flush()
    for obligation in obligations:
        db.refresh(obligation)
        recompute_obligation_status(db, obligation)

        if obligation.obligation_type == "DEPOSIT" and obligation.status == "PAID" and not obligation.deposit_record:
            db.add(DepositRecord(obligation_id=obligation.id, held_amount=obligation.amount))

    payment.status = "SUCCEEDED"
    payment.confirmed_at = datetime.now(timezone.utc)
    notif_crud.notify_user_by_guest(
        db, payment.guest,
        title="Payment received",
        message=f"Your payment of {payment.currency} {payment.amount:.2f} has been confirmed.",
        notification_type="payment.confirmed",
        related_entity_type="simulated_payment", related_entity_id=str(payment.id),
    )
    payer = get_user_for_guest(db, payment.guest) if payment.guest else None
    if payer:
        send_payment_confirmed_email(payer.email, payer.full_name, payment.amount, payment.currency)
    db.commit()

    # Auto-generate the next recurring rent obligation once a period's rent clears --
    # the primary trigger for recurring billing in the absence of a scheduler. This is
    # a best-effort convenience step: an ownership mismatch (e.g. a super_admin's own
    # payment action touching another provider's occupancy) must not undo an already-
    # committed successful payment, so it never propagates a failure back to the caller.
    for obligation in obligations:
        if obligation.obligation_type == "RENT" and obligation.status == "PAID" and obligation.occupancy_id:
            db.refresh(obligation)
            try:
                generate_next_rent_obligation(db, obligation.occupancy, admin)
            except HTTPException:
                pass

    db.refresh(payment)
    return payment


def list_deposit_records(db: Session, admin: AdminUser) -> list[DepositRecord]:
    query = select(DepositRecord)
    if admin.role != "super_admin":
        query = query.where(DepositRecord.obligation_id.in_(_owned_obligation_ids(db, admin)))
    return list(db.scalars(query))


def get_deposit_record_or_404(db: Session, deposit_id: int) -> DepositRecord:
    record = db.get(DepositRecord, deposit_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deposit record not found")
    return record


def _notify_deposit_guest(db: Session, record: DepositRecord, *, released: bool) -> None:
    """Tells the renter about the deposit's actual holding-status transition
    (HELD -> RELEASED/FORFEITED) -- distinct from the generic "payment received"
    notice sent when the deposit was originally collected."""
    obligation = record.obligation
    guest_id = obligation.agreement.offer.guest_id if obligation.agreement else obligation.occupancy.guest_id
    guest = db.get(Guest, guest_id)
    if not guest:
        return
    if released:
        title, message = "Your deposit was released", f"{_round2(record.released_amount)} of your security deposit has been released."
    else:
        title, message = "Your deposit was forfeited", "Your security deposit has been forfeited."
    notif_crud.notify_user_by_guest(
        db, guest,
        title=title, message=message,
        notification_type="deposit.released" if released else "deposit.forfeited",
        related_entity_type="deposit_record", related_entity_id=str(record.id),
    )
    renter_user = get_user_for_guest(db, guest)
    if renter_user:
        send_deposit_status_email(renter_user.email, renter_user.full_name, _round2(record.released_amount), released)


def release_deposit(db: Session, record: DepositRecord, admin: AdminUser, data: DepositRelease) -> DepositRecord:
    party_id = record.obligation.agreement.offer.listing.room.property.owner_party_id if record.obligation.agreement \
        else record.obligation.occupancy.listing.room.property.owner_party_id
    assert_provider_access(db, admin, party_id, roles=("provider_finance", "provider_owner_admin"))

    remaining = _round2(record.held_amount) - _round2(record.released_amount)
    if data.amount > remaining:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot release more than the remaining held amount")

    record.released_amount = _round2(record.released_amount) + data.amount
    record.released_at = datetime.now(timezone.utc)
    record.notes = data.notes or record.notes
    record.status = "RELEASED" if _round2(record.released_amount) >= _round2(record.held_amount) else "PARTIALLY_RELEASED"

    _notify_deposit_guest(db, record, released=True)

    db.commit()
    db.refresh(record)
    return record


def forfeit_deposit(db: Session, record: DepositRecord, admin: AdminUser) -> DepositRecord:
    party_id = record.obligation.agreement.offer.listing.room.property.owner_party_id if record.obligation.agreement \
        else record.obligation.occupancy.listing.room.property.owner_party_id
    assert_provider_access(db, admin, party_id, roles=("provider_finance", "provider_owner_admin"))

    record.status = "FORFEITED"
    record.released_at = datetime.now(timezone.utc)

    _notify_deposit_guest(db, record, released=False)

    db.commit()
    db.refresh(record)
    return record


def run_payout(db: Session, party: Party, admin: AdminUser, period_key: str) -> PayoutRecord:
    assert_provider_access(db, admin, party.id, roles=("provider_finance", "provider_owner_admin"))

    candidates = db.scalars(
        select(Obligation)
        .where(Obligation.obligation_type == "RENT", Obligation.status == "PAID", Obligation.payout_id.is_(None))
        .with_for_update()
    ).all()

    def _room_for(obligation: Obligation):
        if obligation.agreement:
            return obligation.agreement.offer.listing.room
        return obligation.occupancy.room

    matched = [o for o in candidates if _room_for(o).property.owner_party_id == party.id]
    gross = _round2(sum(o.amount for o in matched))
    fee = _round2(gross * PLATFORM_FEE_RATE)
    net = _round2(gross - fee)

    held_reason = ""
    for obligation in matched:
        room = _room_for(obligation)
        if not get_valid_authority_for_room(db, room.id):
            held_reason = "One or more rooms no longer have a verified authority record"
            break

    payout = PayoutRecord(
        party_id=party.id,
        period_key=period_key,
        amount=net,
        status="HELD" if held_reason else "PAID",
        hold_reason=held_reason,
    )
    db.add(payout)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A payout has already been run for this provider and period")

    if not held_reason:
        payout.paid_at = datetime.now(timezone.utc)
        for obligation in matched:
            obligation.payout_id = payout.id
        notif_crud.notify_user_by_party(
            db, party.id,
            title="Payout received",
            message=f"A payout of {payout.currency} {net:.2f} for {period_key} has been paid out to you.",
            notification_type="payout.paid",
            related_entity_type="payout_record", related_entity_id=str(payout.id),
        )
        host_user = get_user_by_party_id(db, party.id)
        if host_user:
            send_payout_paid_email(host_user.email, host_user.full_name, net, payout.currency, period_key)
    else:
        # Actionable, not just informational -- the host needs to resolve the
        # missing authority record before this payout can actually go out.
        notif_crud.notify_user_by_party(
            db, party.id,
            title="Payout on hold",
            message=f"Your payout for {period_key} is on hold: {held_reason}.",
            notification_type="payout.held",
            related_entity_type="payout_record", related_entity_id=str(payout.id),
        )

    db.commit()
    db.refresh(payout)
    return payout


def list_payouts_for(db: Session, admin: AdminUser) -> list[PayoutRecord]:
    query = select(PayoutRecord).order_by(PayoutRecord.created_at.desc())
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        query = query.where(PayoutRecord.party_id == party.id)
    return list(db.scalars(query))


def list_refund_requests(db: Session, admin: AdminUser) -> list[RefundRequest]:
    query = select(RefundRequest).order_by(RefundRequest.created_at.desc())
    if admin.role != "super_admin":
        query = query.where(RefundRequest.obligation_id.in_(_owned_obligation_ids(db, admin)))
    return list(db.scalars(query))


def get_refund_or_404(db: Session, refund_id: int) -> RefundRequest:
    refund = db.get(RefundRequest, refund_id)
    if not refund:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Refund request not found")
    return refund


def request_refund(db: Session, data: RefundRequestCreate, admin: AdminUser) -> RefundRequest:
    payment = db.get(SimulatedPayment, data.payment_id)
    obligation = db.get(Obligation, data.obligation_id)
    if not payment or not obligation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment or obligation not found")
    if admin.role != "super_admin" and obligation.id not in _owned_obligation_ids(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")

    refund = RefundRequest(
        payment_id=data.payment_id,
        obligation_id=data.obligation_id,
        amount=data.amount,
        reason=data.reason,
        requested_by_admin_id=admin.id,
    )
    db.add(refund)
    db.commit()
    db.refresh(refund)
    return refund


def decide_refund(db: Session, refund: RefundRequest, admin: AdminUser, data: RefundDecide) -> RefundRequest:
    """Approving is completing -- there's no separate money-movement step in a
    simulated system. Completing a refund creates a reversing PaymentAllocation and
    recomputes the obligation's status, so the refund actually affects the ledger."""
    if admin.role != "super_admin" and refund.obligation_id not in _owned_obligation_ids(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
    if refund.status != "REQUESTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Refund has already been decided")

    if not data.approve:
        refund.status = "REJECTED"
        refund.decided_by_admin_id = admin.id
        refund.decided_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(refund)
        return refund

    obligation = refund.obligation
    db.add(PaymentAllocation(payment_id=refund.payment_id, obligation_id=obligation.id, amount_allocated=-refund.amount))
    db.flush()
    db.refresh(obligation)
    recompute_obligation_status(db, obligation)

    refund.status = "COMPLETED"
    refund.decided_by_admin_id = admin.id
    refund.decided_at = datetime.now(timezone.utc)

    if refund.payment.guest:
        notif_crud.notify_user_by_guest(
            db, refund.payment.guest,
            title="Your refund was processed",
            message=f"A refund of {refund.payment.currency} {refund.amount:.2f} has been processed.",
            notification_type="refund.completed",
            related_entity_type="refund_request", related_entity_id=str(refund.id),
        )
        payer = get_user_for_guest(db, refund.payment.guest)
        if payer:
            send_refund_completed_email(payer.email, payer.full_name, refund.amount, refund.payment.currency)

    db.commit()
    db.refresh(refund)
    return refund


def _dispute_participants(dispute: DisputeCase) -> tuple[Guest | None, int | None]:
    """Resolves (guest, host_party_id) for a dispute from whichever of
    occupancy/payment it's linked to -- occupancy has both directly; a
    payment-only dispute is resolved via its first allocation's obligation,
    the same chain annotate_payment_context already walks for display."""
    if dispute.occupancy:
        listing = dispute.occupancy.listing
        return dispute.occupancy.guest, (listing.party_id if listing else None)
    if dispute.payment:
        for allocation in dispute.payment.allocations:
            obligation = allocation.obligation
            occupancy = obligation.occupancy if obligation else None
            if occupancy:
                listing = occupancy.listing
                return dispute.payment.guest, (listing.party_id if listing else None)
        return dispute.payment.guest, None
    return None, None


def _notify_dispute_participants(db: Session, dispute: DisputeCase, *, title: str, message: str, notification_type: str) -> None:
    guest, party_id = _dispute_participants(dispute)
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest, title=title, message=message, notification_type=notification_type,
            related_entity_type="dispute_case", related_entity_id=str(dispute.id),
        )
    if party_id:
        notif_crud.notify_user_by_party(
            db, party_id, title=title, message=message, notification_type=f"{notification_type}_for_host",
            related_entity_type="dispute_case", related_entity_id=str(dispute.id),
        )


def list_dispute_cases(db: Session, admin: AdminUser) -> list[DisputeCase]:
    query = select(DisputeCase).order_by(DisputeCase.opened_at.desc())
    if admin.role != "super_admin":
        query = query.where(
            or_(
                DisputeCase.occupancy_id.in_(_owned_occupancy_ids(db, admin)),
                DisputeCase.payment_id.in_(_owned_payment_ids(db, admin)),
            )
        )
    return list(db.scalars(query))


def get_dispute_or_404(db: Session, dispute_id: int) -> DisputeCase:
    dispute = db.get(DisputeCase, dispute_id)
    if not dispute:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute case not found")
    return dispute


def _assert_owns_dispute_target(db: Session, admin: AdminUser, *, occupancy_id: int | None, payment_id: int | None) -> None:
    if admin.role == "super_admin":
        return
    owns_occupancy = occupancy_id is not None and occupancy_id in set(db.scalars(_owned_occupancy_ids(db, admin)))
    owns_payment = payment_id is not None and payment_id in _owned_payment_ids(db, admin)
    if not (owns_occupancy or owns_payment):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")


def open_dispute(db: Session, data: DisputeCreate, admin: AdminUser) -> DisputeCase:
    _assert_owns_dispute_target(db, admin, occupancy_id=data.occupancy_id, payment_id=data.payment_id)
    dispute = DisputeCase(
        payment_id=data.payment_id,
        occupancy_id=data.occupancy_id,
        category=data.category,
        description=data.description,
    )
    db.add(dispute)
    db.flush()

    # No deadline exists on DisputeCase -- none is stated here, deliberately.
    _notify_dispute_participants(
        db, dispute,
        title="A dispute has been opened",
        message=f"A {dispute.category.lower()} dispute has been opened and is under review.",
        notification_type="dispute.opened",
    )
    notif_crud.notify_all_super_admins(
        db,
        title="New dispute opened",
        message=f"A {dispute.category.lower()} dispute (#{dispute.id}) requires review.",
        notification_type="dispute.opened_admin",
        related_entity_type="dispute_case", related_entity_id=str(dispute.id),
    )

    db.commit()
    db.refresh(dispute)
    return dispute


def resolve_dispute(db: Session, dispute: DisputeCase, admin: AdminUser, data: DisputeResolve) -> DisputeCase:
    _assert_owns_dispute_target(db, admin, occupancy_id=dispute.occupancy_id, payment_id=dispute.payment_id)
    if data.status not in ("RESOLVED", "REJECTED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be RESOLVED or REJECTED")

    dispute.status = data.status
    dispute.resolution_notes = data.resolution_notes
    dispute.resolved_at = datetime.now(timezone.utc)

    verb = "resolved" if data.status == "RESOLVED" else "closed"
    _notify_dispute_participants(
        db, dispute,
        title=f"Your dispute was {verb}",
        message=data.resolution_notes or f"Dispute #{dispute.id} was {verb}.",
        notification_type="dispute.resolved",
    )

    db.commit()
    db.refresh(dispute)
    return dispute


def run_reconciliation(db: Session, admin: AdminUser) -> ReconciliationRun:
    occupancy_paid = _round2(
        sum(o.amount for o in db.scalars(select(Obligation).where(Obligation.money_plane == "OCCUPANCY", Obligation.status == "PAID")))
    )
    safeguarded_paid = _round2(
        sum(o.amount for o in db.scalars(select(Obligation).where(Obligation.money_plane == "SAFEGUARDED", Obligation.status == "PAID")))
    )
    total_payments = _round2(sum(p.amount for p in db.scalars(select(SimulatedPayment).where(SimulatedPayment.status == "SUCCEEDED"))))
    total_allocated = _round2(sum(a.amount_allocated for a in db.scalars(select(PaymentAllocation))))
    total_payouts = _round2(sum(p.amount for p in db.scalars(select(PayoutRecord).where(PayoutRecord.status == "PAID"))))
    total_refunds = _round2(sum(r.amount for r in db.scalars(select(RefundRequest).where(RefundRequest.status == "COMPLETED"))))

    mismatches = []
    if abs(total_allocated - (total_payments - total_refunds)) > 0.01:
        mismatches.append(
            f"Allocated total ({total_allocated}) does not match payments minus refunds ({_round2(total_payments - total_refunds)})"
        )

    run = ReconciliationRun(
        totals={
            "occupancyPlanePaid": occupancy_paid,
            "safeguardedPlanePaid": safeguarded_paid,
            "totalPayments": total_payments,
            "totalAllocated": total_allocated,
            "totalPayouts": total_payouts,
            "totalRefunds": total_refunds,
        },
        mismatches=mismatches,
        status="DISCREPANCIES_FOUND" if mismatches else "CLEAN",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def list_reconciliation_runs(db: Session) -> list[ReconciliationRun]:
    return list(db.scalars(select(ReconciliationRun).order_by(ReconciliationRun.run_at.desc())))
