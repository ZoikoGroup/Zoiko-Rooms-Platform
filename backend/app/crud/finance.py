from datetime import date, datetime, timezone
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.mailer import (
    send_deposit_status_email,
    send_payment_confirmed_email,
    send_payout_paid_email,
    send_refund_completed_email,
)
from app.core.payout_statement_documents import save_payout_statement_document
from app.core.receipt_documents import save_receipt_document
from app.core.service_fee_invoice_documents import save_service_fee_invoice_document
from app.crud.authority import get_valid_authority_for_room
from app.crud.guest import get_user_for_guest
from app.crud.market_policy import resolve_market_policy, to_policy_snapshot
from app.models.market_policy import SUPPORTED_FUNDS_FLOW_PROFILES
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, get_or_create_default_party
from app.crud.user import get_user_by_party_id
from app.services import booking_orchestrator
from app.services import ledger as ledger_service
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.finance import (
    DEPOSIT_CLAIM_CATEGORIES,
    DepositClaim,
    DepositClaimItem,
    DepositInstrument,
    DepositRecord,
    DisputeCase,
    FinancialHold,
    LedgerAccount,
    LedgerEntry,
    Obligation,
    PaymentAllocation,
    PaymentReceipt,
    PayoutRecord,
    PayoutStatement,
    ReconciliationRun,
    RefundRequest,
    ServiceFeeInvoice,
    SimulatedPayment,
)
from app.models.guest import Guest
from app.models.leasing import Agreement, Offer
from app.models.listing import Listing
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.room import Room
from app.models.user_account import UserAccount
from app.schemas.finance import (
    DepositClaimCreate,
    DepositClaimItemRead,
    DepositClaimItemRespond,
    DepositClaimRead,
    DepositClaimResolve,
    DepositInstrumentRead,
    DepositRecordRead,
    DepositRelease,
    DisputeCreate,
    DisputeResolve,
    FinancialHoldResolve,
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
    payment.payer_display_name = (
        payment.payer_guest.name if payment.payer_guest else (payment.payer_name or payment.guest_name)
    )
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
            selectinload(SimulatedPayment.payer_guest),
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


def _resolve_payer(db: Session, data: SimulatedPaymentCreate) -> str | None:
    """ZR-ENG-CLR-005 AC-03: resolves the payer_guest_id to store. No payer info at
    all -> payer is the occupant (unchanged default). payer_guest_id given -> must
    be a real guest. Only name/email/phone given (no payer_guest_id) -> a payer
    with no platform account, identified by at least a name."""
    if data.payer_guest_id:
        if not db.get(Guest, data.payer_guest_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payer guest not found")
        return data.payer_guest_id
    if data.payer_name or data.payer_email or data.payer_phone:
        if not data.payer_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "payerName is required to identify a payer who is not a registered guest",
            )
        return None
    return data.guest_id


def create_payment_intent(db: Session, data: SimulatedPaymentCreate) -> SimulatedPayment:
    """Get-or-create by idempotency key -- a retried request never creates a second
    payment intent. A reused key must describe the *same* request (guest/amount/
    currency/payer) -- otherwise it's a key collision between two different
    requests, not a retry, and returning the old payment would silently discard
    the new one."""
    resolved_payer_guest_id = _resolve_payer(db, data)

    existing = db.scalar(select(SimulatedPayment).where(SimulatedPayment.idempotency_key == data.idempotency_key))
    if existing:
        if (
            existing.guest_id != data.guest_id
            or _round2(existing.amount) != _round2(data.amount)
            or existing.currency != data.currency
            or existing.payer_guest_id != resolved_payer_guest_id
            or (existing.payer_name or None) != (data.payer_name or None)
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
        payer_guest_id=resolved_payer_guest_id,
        payer_name=data.payer_name,
        payer_email=data.payer_email,
        payer_phone=data.payer_phone,
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

    # Every other finance mutation (release_deposit, forfeit_deposit, run_payout,
    # request_refund, decide_refund) scopes a non-super-admin to their own
    # provider's records -- this one must too, or a regular admin could confirm
    # a payment against any other provider's obligation.
    owned_ids = None if admin.role == "super_admin" else _owned_obligation_ids(db, admin)

    obligations = []
    for allocation in data.allocations:
        obligation = db.get(Obligation, allocation.obligation_id)
        if not obligation:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Obligation {allocation.obligation_id} not found")
        if owned_ids is not None and obligation.id not in owned_ids:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
        db.add(PaymentAllocation(payment_id=payment.id, obligation_id=obligation.id, amount_allocated=allocation.amount))
        obligations.append(obligation)

        # ZR-ENG-CLR-005 ledger foundation: book the cash actually received
        # against the provider's payable/custody-liability account. Skipped
        # for a non-positive allocation (not a normal case, but the sum-must-
        # equal-total check above doesn't itself forbid it) since post_entry
        # requires a positive amount and this must never turn an otherwise-
        # valid confirm into an error.
        if allocation.amount > 0:
            party_id = _obligation_party_id(obligation)
            if party_id is not None:
                platform_clearing = ledger_service.get_platform_account(db, "PLATFORM_CLEARING", payment.currency)
                if obligation.money_plane == "SAFEGUARDED":
                    credit_account = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, payment.currency)
                else:
                    credit_account = ledger_service.get_party_account(db, "HOST_PAYABLE", party_id, payment.currency)
                ledger_service.post_entry(
                    db,
                    debit_account=platform_clearing,
                    credit_account=credit_account,
                    amount=allocation.amount,
                    currency=payment.currency,
                    description="Payment allocated to obligation",
                    source_type="payment_allocation",
                    source_id=str(obligation.id),
                )

    db.flush()
    for obligation in obligations:
        db.refresh(obligation)
        recompute_obligation_status(db, obligation)

        if obligation.obligation_type == "DEPOSIT" and obligation.status == "PAID" and not obligation.deposit_record:
            record = DepositRecord(obligation_id=obligation.id, held_amount=obligation.amount)
            db.add(record)
            db.flush()
            # ZR-ENG-CLR-002 Section 2.3/5.2: instrument type is SECURITY_DEPOSIT
            # (the only one this platform issues today); custody_model and the
            # policy snapshot are resolved from the market policy pack, not
            # hard-coded, so a new jurisdiction is a data row, not a code change.
            policy = resolve_market_policy(db)
            calculation_snapshot = to_policy_snapshot(policy)
            calculation_snapshot.update({
                "amount": float(obligation.amount),
                "currency": obligation.currency,
                "formula": "FIXED",
            })
            db.add(
                DepositInstrument(
                    deposit_record_id=record.id,
                    instrument_type="SECURITY_DEPOSIT",
                    custody_model=policy.deposit_custody_model,
                    calculation_snapshot=calculation_snapshot,
                )
            )

    # ZR-ENG-CLR-005 AC-16: Payment Service reacts to a cleared payment only
    # through the Booking Orchestrator boundary -- it never imports
    # crud.leasing/crud.occupancy directly. See services/booking_orchestrator.py.
    booking_orchestrator.confirm_downstream_agreements(db, obligations)

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

    # ZR-ENG-CLR-005 AC-16: same Booking Orchestrator boundary as above, for
    # the post-commit best-effort recurring-rent-generation step.
    booking_orchestrator.generate_downstream_rent(db, obligations, admin)

    # ZR-ENG-CLR-005 Section 13.1/AC-25: same best-effort placement as the
    # rent-generation step above -- a receipt-rendering failure must never
    # undo or fail an already-committed successful payment. An admin/renter
    # can always regenerate it on demand via get_or_create_payment_receipt
    # (called again from the download routes) if this best-effort call
    # somehow didn't run.
    try:
        get_or_create_payment_receipt(db, payment)
    except Exception:
        pass

    db.refresh(payment)
    return payment


def _generate_payment_receipt_pdf(payment: SimulatedPayment, receipt_number: str) -> bytes:
    """ZR-ENG-CLR-005 Section 13.2 minimum receipt data. Plain summary
    document, not a branded/templated invoice -- same "simulated, no real
    processor" framing as leasing.py's _generate_native_agreement_pdf; there
    is no masked card/processor descriptor to show since nothing here is a
    real payment method."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def write(text: str, size: float = 10, bold: bool = False, gap: float = 7 * mm) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, text)
        y -= gap

    write("Zoiko Rooms -- Payment Receipt", size=16, bold=True, gap=10 * mm)
    write(f"Receipt {receipt_number}", size=10)
    write(f"Payment #{payment.id}  |  Status: {payment.status}", size=10)
    issued = payment.confirmed_at or datetime.now(timezone.utc)
    write(f"Issued {issued.strftime('%Y-%m-%d %H:%M UTC')}", size=9, gap=10 * mm)

    write("Payer", size=12, bold=True)
    guest = payment.guest
    write(guest.name if guest else "Unknown")
    write(guest.email if guest else "", gap=10 * mm)

    write("Amount", size=12, bold=True)
    write(f"{payment.currency} {payment.amount:.2f}", gap=10 * mm)

    write("Applied to", size=12, bold=True)
    for allocation in payment.allocations:
        obligation_type = allocation.obligation.obligation_type if allocation.obligation else "UNKNOWN"
        write(f"{obligation_type}: {payment.currency} {allocation.amount_allocated:.2f}", size=9, gap=6 * mm)

    y -= 4 * mm
    write("Simulated payment -- no real payment processor or card is involved.", size=8, gap=6 * mm)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def get_payment_receipt_for_admin(db: Session, payment_id: int, admin: AdminUser) -> PaymentReceipt:
    """404 + the same provider-ownership scoping every other finance mutation
    uses (_owned_payment_ids), then get-or-create the receipt -- the route
    layer never reaches into ownership-check internals directly."""
    payment = get_payment_or_404(db, payment_id)
    if admin.role != "super_admin" and payment.id not in _owned_payment_ids(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
    if payment.status != "SUCCEEDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "No receipt exists for a payment that hasn't succeeded")
    return get_or_create_payment_receipt(db, payment)


def get_or_create_payment_receipt(db: Session, payment: SimulatedPayment) -> PaymentReceipt:
    """Idempotent, same render-once-then-persist discipline as
    leasing.py:freeze_agreement_version -- the fast-path check is a
    convenience only, PaymentReceipt.payment_id being DB-unique is the real
    guarantee against a concurrent double-render (loser catches
    IntegrityError and returns the winner's row)."""
    if payment.receipt is not None:
        return payment.receipt

    receipt_number = f"RCPT-{payment.id:08d}"
    pdf_bytes = _generate_payment_receipt_pdf(payment, receipt_number)
    storage_ref, content_hash = save_receipt_document(pdf_bytes)

    try:
        with db.begin_nested():
            receipt = PaymentReceipt(
                payment_id=payment.id, receipt_number=receipt_number,
                content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(receipt)
            db.flush()
    except IntegrityError:
        return db.scalar(select(PaymentReceipt).where(PaymentReceipt.payment_id == payment.id))
    db.commit()
    db.refresh(receipt)
    return receipt


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


def to_deposit_instrument_read(instrument: DepositInstrument | None) -> DepositInstrumentRead | None:
    if not instrument:
        return None
    return DepositInstrumentRead(
        id=instrument.id,
        instrument_type=instrument.instrument_type,
        custody_model=instrument.custody_model,
        calculation_snapshot=instrument.calculation_snapshot,
        created_at=instrument.created_at,
    )


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


def to_deposit_record_read(record: DepositRecord) -> DepositRecordRead:
    return DepositRecordRead(
        id=record.id,
        obligation_id=record.obligation_id,
        status=record.status,
        held_amount=float(record.held_amount),
        released_amount=float(record.released_amount),
        released_at=record.released_at,
        notes=record.notes,
        instrument=to_deposit_instrument_read(record.instrument),
        claimed_amount=_deposit_committed_amount(record),
        disputed_amount=_deposit_disputed_amount(record),
    )


def to_deposit_claim_item_read(item: DepositClaimItem) -> DepositClaimItemRead:
    return DepositClaimItemRead(
        id=item.id,
        claim_id=item.claim_id,
        category_code=item.category_code,
        amount_requested=float(item.amount_requested),
        description=item.description,
        has_evidence=item.evidence_filename is not None,
        evidence_original_name=item.evidence_original_name,
        tenant_response=item.tenant_response,
        final_amount=float(item.final_amount) if item.final_amount is not None else None,
        created_at=item.created_at,
    )


def to_deposit_claim_read(claim: DepositClaim) -> DepositClaimRead:
    return DepositClaimRead(
        id=claim.id,
        deposit_record_id=claim.deposit_record_id,
        status=claim.status,
        submitted_by_admin_id=claim.submitted_by_admin_id,
        submitted_at=claim.submitted_at,
        renter_responded_at=claim.renter_responded_at,
        resolved_by_admin_id=claim.resolved_by_admin_id,
        resolved_at=claim.resolved_at,
        resolution_notes=claim.resolution_notes,
        items=[to_deposit_claim_item_read(i) for i in claim.items],
    )


def _deposit_record_party_id(record: DepositRecord) -> int:
    return record.obligation.agreement.offer.listing.room.property.owner_party_id if record.obligation.agreement \
        else record.obligation.occupancy.listing.room.property.owner_party_id


def _obligation_party_id(obligation: Obligation) -> int | None:
    """Same agreement/occupancy->room traversal run_payout's own _room_for
    already uses -- the provider party a ledger entry for this obligation
    should be posted against."""
    if obligation.agreement:
        room = obligation.agreement.offer.listing.room
    elif obligation.occupancy:
        room = obligation.occupancy.room
    else:
        return None
    return room.property.owner_party_id if room else None


def _assert_deposit_record_access(db: Session, admin: AdminUser, record: DepositRecord) -> None:
    assert_provider_access(db, admin, _deposit_record_party_id(record), roles=("provider_finance", "provider_owner_admin"))


def _deposit_record_guest_id(record: DepositRecord) -> str | None:
    obligation = record.obligation
    if obligation.agreement:
        return obligation.agreement.offer.guest_id
    if obligation.occupancy:
        return obligation.occupancy.guest_id
    return None


def _deposit_committed_amount(record: DepositRecord) -> float:
    """Money already claimed -- whether still awaiting renter response/dispute
    resolution, or already agreed/resolved -- is not freely releasable to the
    renter either way. A claim reserves its amount the moment it's submitted
    (ZR-ENG-CLR-002 Section 15.3), using amount_requested as the placeholder
    until final_amount is set."""
    total = 0.0
    for claim in record.claims:
        for item in claim.items:
            amount = item.final_amount if item.final_amount is not None else item.amount_requested
            total += _round2(amount)
    return _round2(total)


def _deposit_disputed_amount(record: DepositRecord) -> float:
    return _round2(sum(
        _round2(item.amount_requested)
        for claim in record.claims for item in claim.items
        if item.tenant_response == "DISPUTE" and item.final_amount is None
    ))


def _deposit_has_open_claim_items(record: DepositRecord) -> bool:
    return any(item.final_amount is None for claim in record.claims for item in claim.items)


def _remaining_releasable_deposit(record: DepositRecord) -> float:
    remaining = _round2(record.held_amount) - _round2(record.released_amount) - _deposit_committed_amount(record)
    return max(0.0, remaining)


def _recompute_deposit_record_status(record: DepositRecord) -> None:
    """Only ever moves the custody state between HELD/PARTIALLY_RELEASED and
    FROZEN, driven purely by whether any claim item is still awaiting a renter
    response or dispute resolution (ZR-ENG-CLR-002 Section 12.2). RELEASED and
    FORFEITED are terminal states only ever set by the explicit release_deposit
    / forfeit_deposit actions below, never by this recompute."""
    if record.status in ("RELEASED", "FORFEITED"):
        return
    if _deposit_has_open_claim_items(record):
        record.status = "FROZEN"
    elif record.status == "FROZEN":
        record.status = "PARTIALLY_RELEASED" if (_round2(record.released_amount) > 0 or _deposit_committed_amount(record) > 0) else "HELD"


def release_deposit(db: Session, record: DepositRecord, admin: AdminUser, data: DepositRelease) -> DepositRecord:
    _assert_deposit_record_access(db, admin, record)

    remaining = _remaining_releasable_deposit(record)
    if data.amount > remaining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot release more than the undisputed remaining balance of {remaining:.2f} "
            "(amount already released, plus any open or agreed claims, is excluded)",
        )

    record.released_amount = _round2(record.released_amount) + data.amount
    record.released_at = datetime.now(timezone.utc)
    record.notes = data.notes or record.notes
    if _deposit_has_open_claim_items(record):
        # An unresolved dispute can exist even once every releasable rupee has
        # been distributed -- the disputed portion is still open, so this can
        # never read as a fully closed RELEASED state (Section 12.4: "authorized"
        # and "completed" must never be conflated with a dispute still pending).
        record.status = "FROZEN"
    elif _round2(record.released_amount) + _deposit_committed_amount(record) >= _round2(record.held_amount):
        record.status = "RELEASED"
    else:
        record.status = "PARTIALLY_RELEASED"

    # ZR-ENG-CLR-005 ledger foundation: released deposit money leaves custody
    # and goes back out to the renter (as opposed to forfeit_deposit below,
    # where it converts to what's owed to the host instead).
    if data.amount > 0:
        currency = record.obligation.currency
        party_id = _deposit_record_party_id(record)
        deposit_custody_liability = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, currency)
        platform_clearing = ledger_service.get_platform_account(db, "PLATFORM_CLEARING", currency)
        ledger_service.post_entry(
            db,
            debit_account=deposit_custody_liability,
            credit_account=platform_clearing,
            amount=data.amount,
            currency=currency,
            description="Deposit released",
            source_type="deposit_record",
            source_id=str(record.id),
        )

    _notify_deposit_guest(db, record, released=True)

    db.commit()
    db.refresh(record)
    return record


def forfeit_deposit(db: Session, record: DepositRecord, admin: AdminUser) -> DepositRecord:
    """Per ZR-ENG-CLR-002 Rule 6/7: a deposit deduction is a claim against
    principal, not an automatic accounting adjustment. Forfeiture can only
    finalize an amount that a submitted claim has already been agreed or
    resolved for -- it can no longer seize the full deposit with zero
    itemization, evidence, or renter opportunity to respond."""
    _assert_deposit_record_access(db, admin, record)

    if _deposit_disputed_amount(record) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot forfeit while a claim item is still disputed and unresolved")

    remaining = _round2(record.held_amount) - _round2(record.released_amount)
    settled = _round2(sum(
        _round2(item.final_amount) for claim in record.claims for item in claim.items if item.final_amount is not None
    ))
    if settled < remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Cannot forfeit without an agreed or resolved claim covering the full remaining amount -- "
            f"submit a deposit claim for the outstanding {remaining - settled:.2f} first",
        )

    record.status = "FORFEITED"
    record.released_at = datetime.now(timezone.utc)

    # ZR-ENG-CLR-005 ledger foundation: forfeited money converts to what's
    # owed to the host (paid out later via run_payout), rather than leaving
    # custody back out to the renter -- the opposite destination from
    # release_deposit above. Reuses `remaining` as already computed for the
    # validation above, so the ledger amount can't drift from what this
    # function itself considers "the amount being forfeited".
    if remaining > 0:
        currency = record.obligation.currency
        party_id = _deposit_record_party_id(record)
        deposit_custody_liability = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, currency)
        host_payable = ledger_service.get_party_account(db, "HOST_PAYABLE", party_id, currency)
        ledger_service.post_entry(
            db,
            debit_account=deposit_custody_liability,
            credit_account=host_payable,
            amount=remaining,
            currency=currency,
            description="Deposit forfeited to host",
            source_type="deposit_record",
            source_id=str(record.id),
        )

    _notify_deposit_guest(db, record, released=False)

    db.commit()
    db.refresh(record)
    return record


def submit_deposit_claim(db: Session, record: DepositRecord, admin: AdminUser, data: DepositClaimCreate) -> DepositClaim:
    _assert_deposit_record_access(db, admin, record)

    if record.status in ("RELEASED", "FORFEITED"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This deposit has already been closed out")
    if not data.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A claim needs at least one line item")

    remaining = _remaining_releasable_deposit(record)
    requested_total = _round2(sum(item.amount_requested for item in data.items))
    if requested_total > remaining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Claimed amount {requested_total:.2f} exceeds the remaining deposit balance of {remaining:.2f}",
        )

    for item in data.items:
        if item.category_code not in DEPOSIT_CLAIM_CATEGORIES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported deduction category: {item.category_code}")
        if item.amount_requested <= 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "amount_requested must be positive")

    claim = DepositClaim(deposit_record_id=record.id, submitted_by_admin_id=admin.id)
    db.add(claim)
    db.flush()
    for item in data.items:
        db.add(DepositClaimItem(
            claim_id=claim.id,
            category_code=item.category_code,
            amount_requested=item.amount_requested,
            description=item.description,
        ))

    _recompute_deposit_record_status(record)
    db.commit()
    db.refresh(claim)

    guest_id = _deposit_record_guest_id(record)
    guest = db.get(Guest, guest_id) if guest_id else None
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest,
            title="A deposit claim was submitted",
            message=f"Your host submitted a claim of {record.obligation.currency} {requested_total:.2f} against your deposit. Review and respond.",
            notification_type="deposit_claim.submitted",
            related_entity_type="deposit_claim", related_entity_id=str(claim.id),
        )
        db.commit()

    return claim


def get_deposit_claim_or_404(db: Session, claim_id: int) -> DepositClaim:
    claim = db.get(DepositClaim, claim_id)
    if not claim:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deposit claim not found")
    return claim


def get_deposit_claim_item_or_404(db: Session, item_id: int) -> DepositClaimItem:
    item = db.get(DepositClaimItem, item_id)
    if not item:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deposit claim item not found")
    return item


def list_deposit_claims_for_record(db: Session, admin: AdminUser, record: DepositRecord) -> list[DepositClaim]:
    _assert_deposit_record_access(db, admin, record)
    return list(record.claims)


def assert_deposit_record_access(db: Session, admin: AdminUser, record: DepositRecord) -> None:
    _assert_deposit_record_access(db, admin, record)


def deposit_record_guest_id(record: DepositRecord) -> str | None:
    return _deposit_record_guest_id(record)


def attach_deposit_claim_item_evidence(
    db: Session, item: DepositClaimItem, admin: AdminUser,
    stored_filename: str, original_filename: str, content_type: str,
) -> DepositClaimItem:
    record = item.claim.deposit_record
    _assert_deposit_record_access(db, admin, record)
    item.evidence_filename = stored_filename
    item.evidence_original_name = original_filename
    item.evidence_content_type = content_type
    db.commit()
    db.refresh(item)
    return item


def respond_to_deposit_claim_item(
    db: Session, item: DepositClaimItem, user: UserAccount, data: DepositClaimItemRespond,
) -> DepositClaimItem:
    from app.crud.guest import get_guest_for_user

    record = item.claim.deposit_record
    guest_id = _deposit_record_guest_id(record)
    guest = db.get(Guest, guest_id) if guest_id else None
    my_guest = get_guest_for_user(db, user)
    if not guest or not my_guest or guest.id != my_guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This deposit claim does not belong to you")

    if item.tenant_response:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already responded to this claim item")
    if data.response not in ("ACCEPT", "PARTIAL_ACCEPT", "DISPUTE"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid response")

    item.tenant_response = data.response
    if data.response == "ACCEPT":
        item.final_amount = item.amount_requested
    elif data.response == "PARTIAL_ACCEPT":
        if data.accepted_amount is None or data.accepted_amount <= 0 or data.accepted_amount > float(item.amount_requested):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "accepted_amount must be between 0 and the requested amount")
        item.final_amount = data.accepted_amount
    # DISPUTE: final_amount stays None until an admin resolves the claim.

    claim = item.claim
    claim.renter_responded_at = datetime.now(timezone.utc)
    db.flush()

    if any(i.tenant_response == "" for i in claim.items):
        claim.status = "RENTER_RESPONSE_PENDING"
    elif any(i.tenant_response == "DISPUTE" and i.final_amount is None for i in claim.items):
        claim.status = "DISPUTED"
    else:
        claim.status = "AGREED"

    _recompute_deposit_record_status(record)
    db.commit()
    db.refresh(item)

    if data.response == "DISPUTE":
        party_id = _deposit_record_party_id(record)
        notif_crud.notify_user_by_party(
            db, party_id,
            title="A deposit claim was disputed",
            message=f"The renter disputed a {item.category_code} claim item. Zoiko will review before any further release.",
            notification_type="deposit_claim.disputed",
            related_entity_type="deposit_claim", related_entity_id=str(claim.id),
        )
        db.commit()

    return item


def resolve_deposit_claim(db: Session, claim: DepositClaim, admin: AdminUser, data: DepositClaimResolve) -> DepositClaim:
    """India-scope MVP simplification of ZR-ENG-CLR-002 Rule 7/Section 10: no
    external ADR/tribunal integration exists yet, so an Admin resolves disputed
    line items directly. This is not the doc's end-state -- Section 10.3 requires
    a market pack to explicitly authorize Zoiko as dispute resolver before this
    is legally appropriate in a real jurisdiction."""
    record = claim.deposit_record
    _assert_deposit_record_access(db, admin, record)

    if claim.status != "DISPUTED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a disputed claim can be resolved")

    items_by_id = {item.id: item for item in claim.items}
    for entry in data.item_final_amounts:
        item = items_by_id.get(entry.item_id)
        if not item:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Claim item {entry.item_id} not found on this claim")
        if item.final_amount is not None:
            continue  # already settled (e.g. renter accepted it) -- resolution can't override that
        if entry.final_amount < 0 or entry.final_amount > float(item.amount_requested):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "final_amount must be between 0 and the requested amount")
        item.final_amount = entry.final_amount

    if any(item.final_amount is None for item in claim.items):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Every disputed item needs a final_amount to resolve this claim")

    claim.status = "RESOLVED"
    claim.resolved_by_admin_id = admin.id
    claim.resolved_at = datetime.now(timezone.utc)
    claim.resolution_notes = data.notes

    _recompute_deposit_record_status(record)
    db.commit()
    db.refresh(claim)

    guest_id = _deposit_record_guest_id(record)
    guest = db.get(Guest, guest_id) if guest_id else None
    if guest:
        notif_crud.notify_user_by_guest(
            db, guest,
            title="Your deposit dispute was resolved",
            message=data.notes or "Zoiko has resolved your disputed deposit claim.",
            notification_type="deposit_claim.resolved",
            related_entity_type="deposit_claim", related_entity_id=str(claim.id),
        )
        db.commit()

    return claim


def list_deposit_claims_for_guest(db: Session, guest_id: str) -> list[DepositClaim]:
    """Every claim on a deposit belonging to this renter, across all their
    occupancies. Filters in Python over the (small, per-test-tenant) claim set
    rather than a joined query, since DepositRecord's guest link is derived
    (via agreement/occupancy), not a direct column."""
    all_claims = db.scalars(select(DepositClaim)).all()
    return [c for c in all_claims if _deposit_record_guest_id(c.deposit_record) == guest_id]


def _period_as_of(period_key: str) -> date:
    """AC-34: the effective date a "YYYY-MM" period_key resolves to for
    market-policy lookups -- shared by run_payout (fee rate) and
    get_or_create_service_fee_invoice (legal entity/tax rate), so both always
    resolve the same policy for the same period. Falls back to today only if
    period_key isn't in the expected shape, rather than ever erroring on it."""
    try:
        return date(int(period_key[:4]), int(period_key[5:7]), 1)
    except (ValueError, IndexError):
        return date.today()


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
    # ZR-ENG-CLR-005 AC-09/AC-34: fee rate resolved from the effective-dated
    # market policy pack, not a hard-coded constant -- same resolver deposit
    # collection already uses (see confirm_payment above). Resolved as of the
    # period being paid out (period_key, "YYYY-MM"), not today -- a payout
    # run late (after a fee-policy change) must still apply the rate that was
    # actually in effect when this rent was earned, not retroactively apply a
    # rate change (AC-34). See _period_as_of below.
    policy = resolve_market_policy(db, as_of=_period_as_of(period_key))

    # ZR-ENG-CLR-005 AC-20/AC-35: fail closed rather than silently defaulting
    # to direct settlement or Zoiko custody -- a market pack resolving to a
    # funds-flow profile this build can't actually execute (no real PSP/
    # trust partner behind PSP_DEFERRED_PAYOUT/TRUST_ESCROW_CUSTODY;
    # ZOIKO_REGULATED_CUSTODY is off-by-default and needs separate licensing
    # approval) must refuse the payout outright, not create a HELD row for a
    # configuration that was never actually supported.
    if policy.funds_flow_profile not in SUPPORTED_FUNDS_FLOW_PROFILES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Funds-flow profile '{policy.funds_flow_profile}' is not supported by this build -- payout blocked",
        )

    gross = _round2(sum(o.amount for o in matched))
    fee = _round2(gross * float(policy.platform_fee_rate))
    net = _round2(gross - fee)

    held_reason = ""
    for obligation in matched:
        room = _room_for(obligation)
        if not get_valid_authority_for_room(db, room.id):
            held_reason = "One or more rooms no longer have a verified authority record"
            break

    # ZR-ENG-CLR-005 AC-19/Section 9.2: "No active dispute, chargeback...
    # hold blocks release." FinancialHold/DisputeCase (increments 6/7/10)
    # were previously visibility-only -- a payout could go out even with an
    # open chargeback against one of these obligations, or an unresolved
    # negative balance already flagged against this party. Both checks are
    # scoped to what's concretely queryable today (a chargeback tied to one
    # of *these* obligations; a negative-balance hold tied to *this party's*
    # own HOST_PAYABLE account) rather than a generic party-wide hold scan,
    # which FinancialHold's source_type/source_id shape doesn't cleanly
    # support across every hold kind.
    if not held_reason and matched:
        matched_ids = [o.id for o in matched]
        open_chargeback = db.scalar(
            select(DisputeCase).where(
                DisputeCase.category == "CHARGEBACK", DisputeCase.status == "OPEN",
                DisputeCase.obligation_id.in_(matched_ids),
            )
        )
        if open_chargeback is not None:
            held_reason = f"Obligation #{open_chargeback.obligation_id} has an open chargeback dispute"

    if not held_reason:
        payout_currency = matched[0].currency if matched else "INR"
        existing_host_payable = db.scalar(
            select(LedgerAccount).where(
                LedgerAccount.account_type == "HOST_PAYABLE", LedgerAccount.party_id == party.id,
                LedgerAccount.currency == payout_currency,
            )
        )
        if existing_host_payable is not None:
            negative_balance_hold = db.scalar(
                select(FinancialHold).where(
                    FinancialHold.source_type == "ledger_account", FinancialHold.source_id == str(existing_host_payable.id),
                    FinancialHold.status == "OPEN", FinancialHold.reason_code == "NEGATIVE_ACCOUNT_BALANCE",
                )
            )
            if negative_balance_hold is not None:
                held_reason = "This provider has an unresolved negative account balance -- resolve before payout"

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

        # ZR-ENG-CLR-005 ledger foundation: extinguish what's owed to this host --
        # the fee portion is recognized as platform revenue, the net portion
        # actually leaves platform clearing. Two entries rather than a three-way
        # split, since LedgerEntry is a simple two-sided row.
        host_payable = ledger_service.get_party_account(db, "HOST_PAYABLE", party.id, payout.currency)
        if fee > 0:
            platform_fee_revenue = ledger_service.get_platform_account(db, "PLATFORM_FEE_REVENUE", payout.currency)
            ledger_service.post_entry(
                db,
                debit_account=host_payable,
                credit_account=platform_fee_revenue,
                amount=fee,
                currency=payout.currency,
                description="Platform fee on payout",
                source_type="payout_record",
                source_id=str(payout.id),
            )
        if net > 0:
            platform_clearing = ledger_service.get_platform_account(db, "PLATFORM_CLEARING", payout.currency)
            ledger_service.post_entry(
                db,
                debit_account=host_payable,
                credit_account=platform_clearing,
                amount=net,
                currency=payout.currency,
                description="Payout paid to host",
                source_type="payout_record",
                source_id=str(payout.id),
            )

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

    # ZR-ENG-CLR-005 Section 6.3/13.1: best-effort, same placement/discipline
    # as confirm_payment's receipt generation -- a rendering failure must
    # never undo or fail an already-committed payout. Never generated for a
    # HELD payout (nothing was actually paid out yet).
    if payout.status == "PAID":
        try:
            get_or_create_payout_statement(db, payout)
        except Exception:
            pass
        try:
            get_or_create_service_fee_invoice(db, payout)
        except Exception:
            pass

    return payout


def list_payouts_for(db: Session, admin: AdminUser) -> list[PayoutRecord]:
    query = select(PayoutRecord).order_by(PayoutRecord.created_at.desc())
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        query = query.where(PayoutRecord.party_id == party.id)
    return list(db.scalars(query))


def _generate_payout_statement_pdf(payout: PayoutRecord, statement_number: str) -> bytes:
    """ZR-ENG-CLR-005 Section 6.3 Earnings & Payouts columns: gross rent,
    Zoiko fee, net payout, shown separately -- "Net payout must never erase
    gross economics" (Section 8.2). gross/fee are reconstructed from the
    linked obligations and payout.amount (net) rather than stored again,
    since run_payout already computed and ledgered them once."""
    gross = _round2(sum(o.amount for o in payout.obligations))
    net = _round2(payout.amount)
    fee = _round2(gross - net)

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def write(text: str, size: float = 10, bold: bool = False, gap: float = 7 * mm) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, text)
        y -= gap

    write("Zoiko Rooms -- Payout Statement", size=16, bold=True, gap=10 * mm)
    write(f"Statement {statement_number}", size=10)
    write(f"Payout #{payout.id}  |  Period {payout.period_key}  |  Status: {payout.status}", size=10)
    paid = payout.paid_at or datetime.now(timezone.utc)
    write(f"Paid {paid.strftime('%Y-%m-%d %H:%M UTC')}", size=9, gap=10 * mm)

    write("Summary", size=12, bold=True)
    write(f"Gross rent: {payout.currency} {gross:.2f}", size=9, gap=6 * mm)
    write(f"Zoiko fee: {payout.currency} {fee:.2f}", size=9, gap=6 * mm)
    write(f"Net payout: {payout.currency} {net:.2f}", size=9, gap=10 * mm)

    write("Obligations included", size=12, bold=True)
    for obligation in payout.obligations:
        write(f"RENT due {obligation.due_date.isoformat()}: {payout.currency} {obligation.amount:.2f}", size=9, gap=6 * mm)

    y -= 4 * mm
    write("Simulated payout -- no real payment processor or bank transfer is involved.", size=8, gap=6 * mm)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def get_or_create_payout_statement(db: Session, payout: PayoutRecord) -> PayoutStatement:
    """Idempotent, same render-once-then-persist discipline as
    get_or_create_payment_receipt/freeze_agreement_version --
    PayoutStatement.payout_id being DB-unique is the real guarantee against a
    concurrent double-render."""
    if payout.statement is not None:
        return payout.statement

    statement_number = f"STMT-{payout.id:08d}"
    pdf_bytes = _generate_payout_statement_pdf(payout, statement_number)
    storage_ref, content_hash = save_payout_statement_document(pdf_bytes)

    try:
        with db.begin_nested():
            statement = PayoutStatement(
                payout_id=payout.id, statement_number=statement_number,
                content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(statement)
            db.flush()
    except IntegrityError:
        return db.scalar(select(PayoutStatement).where(PayoutStatement.payout_id == payout.id))
    db.commit()
    db.refresh(statement)
    return statement


def get_payout_statement_for_admin(db: Session, payout_id: int, admin: AdminUser) -> PayoutStatement:
    """404 + the same provider-ownership scoping list_payouts_for uses, then
    get-or-create the statement -- the route layer never reaches into
    ownership-check internals directly."""
    payout = db.get(PayoutRecord, payout_id)
    if not payout:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payout not found")
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        if payout.party_id != party.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
    if payout.status != "PAID":
        raise HTTPException(status.HTTP_409_CONFLICT, "No statement exists for a payout that hasn't been paid")
    return get_or_create_payout_statement(db, payout)


def _generate_service_fee_invoice_pdf(
    payout: PayoutRecord, invoice_number: str, *, legal_entity_name: str, tax_registration_number: str,
    fee_amount: float, tax_rate: float, tax_amount: float,
) -> bytes:
    """ZR-ENG-CLR-005 AC-26: issuer is a specific Zoiko legal entity (resolved
    from the market policy pack, never hard-coded), with tax treatment shown
    even when the configured rate is 0% -- an honest "no tax configured" is
    the correct answer for a market pack that has none, not an omission."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def write(text: str, size: float = 10, bold: bool = False, gap: float = 7 * mm) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, text)
        y -= gap

    write(f"{legal_entity_name} -- Service Fee Invoice", size=16, bold=True, gap=10 * mm)
    write(f"Invoice {invoice_number}", size=10)
    write(f"Payout #{payout.id}  |  Period {payout.period_key}", size=10)
    if tax_registration_number:
        write(f"Tax registration: {tax_registration_number}", size=9, gap=10 * mm)
    else:
        y -= 3 * mm

    write("Charge", size=12, bold=True)
    write(f"Platform service fee: {payout.currency} {fee_amount:.2f}", size=9, gap=6 * mm)
    write(f"Tax ({tax_rate * 100:.2f}%): {payout.currency} {tax_amount:.2f}", size=9, gap=6 * mm)
    write(f"Total: {payout.currency} {_round2(fee_amount + tax_amount):.2f}", size=9, gap=10 * mm)

    y -= 4 * mm
    write("Simulated fee invoice -- no real tax authority integration is involved.", size=8, gap=6 * mm)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def get_or_create_service_fee_invoice(db: Session, payout: PayoutRecord) -> ServiceFeeInvoice:
    """Idempotent, same render-once-then-persist discipline as
    get_or_create_payout_statement. Resolves the market policy as of the
    period being paid out (same _period_as_of helper run_payout itself uses
    for the fee rate), so the legal entity/tax rate shown are the ones
    actually in effect for this period, not whatever's current if this is
    regenerated later (AC-34)."""
    if payout.service_fee_invoice is not None:
        return payout.service_fee_invoice

    policy = resolve_market_policy(db, as_of=_period_as_of(payout.period_key))
    gross = _round2(sum(o.amount for o in payout.obligations))
    net = _round2(payout.amount)
    fee_amount = _round2(gross - net)
    tax_rate = float(policy.service_fee_tax_rate)
    tax_amount = _round2(fee_amount * tax_rate)

    invoice_number = f"INV-{payout.id:08d}"
    pdf_bytes = _generate_service_fee_invoice_pdf(
        payout, invoice_number,
        legal_entity_name=policy.zoiko_legal_entity_name, tax_registration_number=policy.zoiko_tax_registration_number,
        fee_amount=fee_amount, tax_rate=tax_rate, tax_amount=tax_amount,
    )
    storage_ref, content_hash = save_service_fee_invoice_document(pdf_bytes)

    try:
        with db.begin_nested():
            invoice = ServiceFeeInvoice(
                payout_id=payout.id, invoice_number=invoice_number,
                legal_entity_name=policy.zoiko_legal_entity_name, tax_registration_number=policy.zoiko_tax_registration_number,
                fee_amount=fee_amount, tax_rate=tax_rate, tax_amount=tax_amount,
                content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(invoice)
            db.flush()
    except IntegrityError:
        return db.scalar(select(ServiceFeeInvoice).where(ServiceFeeInvoice.payout_id == payout.id))
    db.commit()
    db.refresh(invoice)
    return invoice


def get_service_fee_invoice_for_admin(db: Session, payout_id: int, admin: AdminUser) -> ServiceFeeInvoice:
    """404 + the same provider-ownership scoping list_payouts_for/
    get_payout_statement_for_admin use, then get-or-create the invoice."""
    payout = db.get(PayoutRecord, payout_id)
    if not payout:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payout not found")
    if admin.role != "super_admin":
        party = get_or_create_default_party(db, admin)
        if payout.party_id != party.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
    if payout.status != "PAID":
        raise HTTPException(status.HTTP_409_CONFLICT, "No invoice exists for a payout that hasn't been paid")
    return get_or_create_service_fee_invoice(db, payout)


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
    """AC-14: get-or-create by idempotency key, same pattern as
    create_payment_intent -- a retried request never creates a second
    RefundRequest. A reused key must describe the *same* request
    (payment/obligation/amount), otherwise it's a key collision between two
    different requests, not a retry."""
    existing = db.scalar(select(RefundRequest).where(RefundRequest.idempotency_key == data.idempotency_key))
    if existing:
        if (
            existing.payment_id != data.payment_id
            or existing.obligation_id != data.obligation_id
            or _round2(existing.amount) != _round2(data.amount)
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This idempotency key was already used for a different refund request",
            )
        return existing

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
        idempotency_key=data.idempotency_key,
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

    # ZR-ENG-CLR-005 ledger foundation: reverse the original collection entry --
    # cash goes back out to the renter from whichever side originally received
    # it. This can drive HOST_PAYABLE/DEPOSIT_CUSTODY_LIABILITY negative if the
    # obligation was already paid out/released -- a real clawback (Section 21's
    # "Negative Host balance" edge case) -- flagged below via FinancialHold
    # rather than silently left invisible; the spec itself says any actual
    # reserve/offset/collection policy needs separate approval, so this only
    # surfaces the situation, it doesn't invent one.
    if refund.amount > 0:
        party_id = _obligation_party_id(obligation)
        if party_id is not None:
            platform_clearing = ledger_service.get_platform_account(db, "PLATFORM_CLEARING", refund.payment.currency)
            if obligation.money_plane == "SAFEGUARDED":
                debit_account = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, refund.payment.currency)
            else:
                debit_account = ledger_service.get_party_account(db, "HOST_PAYABLE", party_id, refund.payment.currency)
            ledger_service.post_entry(
                db,
                debit_account=debit_account,
                credit_account=platform_clearing,
                amount=refund.amount,
                currency=refund.payment.currency,
                description="Refund reversing prior collection",
                source_type="refund_request",
                source_id=str(refund.id),
            )
            db.flush()
            # get_balance's raw debit-minus-credit convention: for a liability
            # account like this one, a positive result means more was debited
            # (paid out/released/refunded) than was ever credited (owed) --
            # i.e. the account is genuinely negative in accounting terms.
            if ledger_service.get_balance(db, debit_account) > 0.01:
                db.add(FinancialHold(
                    source_type="ledger_account", source_id=str(debit_account.id),
                    reason_code="NEGATIVE_ACCOUNT_BALANCE", severity="HIGH",
                    description=(
                        f"Refund #{refund.id} drove {debit_account.account_type} account "
                        f"{debit_account.id} (party {party_id}) negative -- more was already paid out/"
                        "released than this refund leaves owed. Needs an approved reserve/offset/"
                        "collection policy before further payouts to this party."
                    ),
                ))

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

    # ZR-ENG-CLR-005 Section 20/AC-32: a chargeback needs a specific payment,
    # obligation and amount to eventually reverse (same triple RefundRequest
    # already requires) -- non-chargeback categories are unaffected (category
    # isn't otherwise validated at all).
    if data.category == "CHARGEBACK":
        if data.payment_id is None or data.obligation_id is None or data.amount is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A chargeback dispute requires payment_id, obligation_id and amount")
        if not db.get(SimulatedPayment, data.payment_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment not found")
        if not db.get(Obligation, data.obligation_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")

    dispute = DisputeCase(
        payment_id=data.payment_id,
        occupancy_id=data.occupancy_id,
        obligation_id=data.obligation_id,
        amount=data.amount,
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

    if data.category == "CHARGEBACK":
        # Section 20: "flag disputed amount; place configured payout/
        # negative-balance hold... do not assume renter wins." A real
        # payout-blocking mechanism isn't built here -- this is the
        # queryable exception record + visibility Section 19.2 asks for.
        db.add(FinancialHold(
            source_type="dispute_case", source_id=str(dispute.id),
            reason_code="CHARGEBACK_OPENED", severity="HIGH",
            description=f"Chargeback opened against obligation #{data.obligation_id} for {data.amount} -- do not assume renter wins.",
        ))

    db.commit()
    db.refresh(dispute)
    return dispute


def _resolve_chargeback_hold(db: Session, dispute: DisputeCase, admin: AdminUser, outcome: str) -> None:
    hold = db.scalar(
        select(FinancialHold).where(
            FinancialHold.source_type == "dispute_case", FinancialHold.source_id == str(dispute.id),
            FinancialHold.status == "OPEN",
        )
    )
    if hold is not None:
        hold.status = "RESOLVED"
        hold.resolved_by_admin_id = admin.id
        hold.resolved_at = datetime.now(timezone.utc)
        hold.resolution_notes = f"Chargeback {outcome.lower()}"


def resolve_dispute(db: Session, dispute: DisputeCase, admin: AdminUser, data: DisputeResolve) -> DisputeCase:
    _assert_owns_dispute_target(db, admin, occupancy_id=dispute.occupancy_id, payment_id=dispute.payment_id)
    if data.status not in ("RESOLVED", "REJECTED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be RESOLVED or REJECTED")
    if dispute.category == "CHARGEBACK" and data.chargeback_outcome not in ("WON", "LOST"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A chargeback dispute must be resolved with chargeback_outcome WON or LOST")

    dispute.status = data.status
    dispute.resolution_notes = data.resolution_notes
    dispute.resolved_at = datetime.now(timezone.utc)

    if dispute.category == "CHARGEBACK":
        dispute.chargeback_outcome = data.chargeback_outcome

        if data.chargeback_outcome == "LOST":
            # Section 20 "Chargeback lost": same reversal decide_refund posts
            # for an approved refund -- a reversing PaymentAllocation (so the
            # obligation's own status stops reading PAID), then the ledger
            # entry moving cash back out, then the same negative-balance
            # check/hold (Section 21) for an obligation that was already
            # paid out/released.
            obligation = dispute.obligation
            db.add(PaymentAllocation(payment_id=dispute.payment_id, obligation_id=obligation.id, amount_allocated=-dispute.amount))
            db.flush()
            db.refresh(obligation)
            recompute_obligation_status(db, obligation)

            if dispute.amount > 0:
                party_id = _obligation_party_id(obligation)
                if party_id is not None:
                    platform_clearing = ledger_service.get_platform_account(db, "PLATFORM_CLEARING", dispute.payment.currency)
                    if obligation.money_plane == "SAFEGUARDED":
                        debit_account = ledger_service.get_party_account(db, "DEPOSIT_CUSTODY_LIABILITY", party_id, dispute.payment.currency)
                    else:
                        debit_account = ledger_service.get_party_account(db, "HOST_PAYABLE", party_id, dispute.payment.currency)
                    ledger_service.post_entry(
                        db,
                        debit_account=debit_account,
                        credit_account=platform_clearing,
                        amount=dispute.amount,
                        currency=dispute.payment.currency,
                        description="Chargeback lost, reversing prior collection",
                        source_type="dispute_case",
                        source_id=str(dispute.id),
                    )
                    db.flush()
                    if ledger_service.get_balance(db, debit_account) > 0.01:
                        db.add(FinancialHold(
                            source_type="ledger_account", source_id=str(debit_account.id),
                            reason_code="NEGATIVE_ACCOUNT_BALANCE", severity="HIGH",
                            description=(
                                f"Chargeback #{dispute.id} drove {debit_account.account_type} account "
                                f"{debit_account.id} (party {party_id}) negative -- more was already paid out/"
                                "released than this chargeback leaves owed. Needs an approved reserve/offset/"
                                "collection policy before further payouts to this party."
                            ),
                        ))

        # Section 20 "Chargeback won": release held amount/close dispute --
        # same close-out for "lost" too, since either way the exception is
        # now decided, not still open.
        _resolve_chargeback_hold(db, dispute, admin, data.chargeback_outcome)

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

    # ZR-ENG-CLR-005 Section 19.2: each failed check becomes a (reason_code,
    # severity, message) triple -- the message still feeds the plain-string
    # `mismatches` list exactly as before, but each triple also becomes a real,
    # queryable, resolvable FinancialHold row below.
    failed_checks: list[tuple[str, str, str]] = []
    if abs(total_allocated - (total_payments - total_refunds)) > 0.01:
        failed_checks.append((
            "AGGREGATE_MISMATCH", "MEDIUM",
            f"Allocated total ({total_allocated}) does not match payments minus refunds ({_round2(total_payments - total_refunds)})",
        ))

    # ZR-ENG-CLR-005: the ledger foundation's own two checks, layered on top of
    # the pre-existing aggregate check above rather than replacing it.
    #
    # 1. Trial balance -- a LedgerEntry always debits one account and credits
    # another for the same amount, so summing get_balance() over every account
    # must always net to exactly zero; anything else means some code path
    # posted an entry outside ledger_service.post_entry's discipline (or the
    # data was hand-edited).
    all_accounts = db.scalars(select(LedgerAccount)).all()
    trial_balance = _round2(sum(ledger_service.get_balance(db, account) for account in all_accounts))
    if abs(trial_balance) > 0.01:
        failed_checks.append((
            "TRIAL_BALANCE_MISMATCH", "CRITICAL",
            f"Ledger trial balance is {trial_balance}, not zero -- some entry was posted unbalanced",
        ))

    # 2. Every positive (non-refund-reversal) PaymentAllocation created by
    # confirm_payment should have a matching ledger entry -- if this drifts,
    # the ledger wiring itself missed posting for some real collection (e.g.
    # an obligation with no resolvable provider party, which confirm_payment
    # silently skips rather than erroring on).
    total_allocated_positive = _round2(
        sum(a.amount_allocated for a in db.scalars(select(PaymentAllocation)) if a.amount_allocated > 0)
    )
    total_ledger_collected = _round2(
        sum(e.amount for e in db.scalars(select(LedgerEntry).where(LedgerEntry.source_type == "payment_allocation")))
    )
    if abs(total_allocated_positive - total_ledger_collected) > 0.01:
        failed_checks.append((
            "LEDGER_ALLOCATION_MISMATCH", "HIGH",
            f"Ledger-recorded collections ({total_ledger_collected}) do not match positive allocations ({total_allocated_positive})",
        ))

    mismatches = [message for _reason_code, _severity, message in failed_checks]

    run = ReconciliationRun(
        totals={
            "occupancyPlanePaid": occupancy_paid,
            "safeguardedPlanePaid": safeguarded_paid,
            "totalPayments": total_payments,
            "totalAllocated": total_allocated,
            "totalPayouts": total_payouts,
            "totalRefunds": total_refunds,
            "ledgerTrialBalance": trial_balance,
            "totalLedgerCollected": total_ledger_collected,
        },
        mismatches=mismatches,
        status="DISCREPANCIES_FOUND" if mismatches else "CLEAN",
    )
    db.add(run)
    db.flush()

    for reason_code, severity, message in failed_checks:
        db.add(FinancialHold(
            source_type="reconciliation_run", source_id=str(run.id),
            reason_code=reason_code, severity=severity, description=message,
        ))

    db.commit()
    db.refresh(run)
    return run


def list_reconciliation_runs(db: Session) -> list[ReconciliationRun]:
    return list(db.scalars(select(ReconciliationRun).order_by(ReconciliationRun.run_at.desc())))


def list_financial_holds(db: Session, status: str | None = None) -> list[FinancialHold]:
    """Platform-wide finance exceptions (today, only reconciliation produces
    them) -- no per-provider ownership scoping applies, same as reconciliation
    itself; both are super-admin-only at the route level."""
    query = select(FinancialHold).order_by(FinancialHold.opened_at.desc())
    if status is not None:
        query = query.where(FinancialHold.status == status)
    return list(db.scalars(query))


def get_financial_hold_or_404(db: Session, hold_id: int) -> FinancialHold:
    hold = db.get(FinancialHold, hold_id)
    if not hold:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Financial hold not found")
    return hold


def resolve_financial_hold(db: Session, hold: FinancialHold, admin: AdminUser, data: FinancialHoldResolve) -> FinancialHold:
    if hold.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, "This financial hold has already been resolved")

    hold.status = "RESOLVED"
    hold.resolved_by_admin_id = admin.id
    hold.resolved_at = datetime.now(timezone.utc)
    hold.resolution_notes = data.notes
    db.commit()
    db.refresh(hold)
    return hold
