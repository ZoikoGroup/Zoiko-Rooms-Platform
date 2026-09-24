from datetime import date, datetime, timedelta, timezone
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import and_, or_, select
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
from app.core.rent_invoice_documents import save_rent_invoice_document
from app.core.service_fee_invoice_documents import save_service_fee_invoice_document
from app.crud.authority import get_valid_authority_for_room
from app.crud.events import emit_event
from app.crud.guest import get_user_for_guest
from app.crud.market_policy import DEFAULT_JURISDICTION, resolve_market_policy, to_policy_snapshot
from app.models.market_policy import SUPPORTED_FUNDS_FLOW_PROFILES
from app.crud import notification as notif_crud
from app.crud.party import assert_provider_access, get_or_create_default_party
from app.crud.habitability_incident import has_open_severe_incident_for_room
from app.crud.payout_beneficiary import get_verified_payout_beneficiary
from app.crud.user import get_user_by_party_id
from app.crud import host_stripe_account as host_stripe_account_crud
from app.services import booking_orchestrator
from app.services import ledger as ledger_service
from app.services import stripe_client
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.finance import (
    AutopayMandate,
    CADENCE_INTERVAL_DAYS,
    PAYMENT_METHOD_CLASSES,
    DEPOSIT_CLAIM_CATEGORIES,
    DepositClaim,
    DepositClaimItem,
    DepositInstrument,
    DepositRecord,
    DisputeCase,
    FinancialHold,
    HOST_RECOVERY_METHODS,
    HostRecovery,
    LedgerAccount,
    LedgerEntry,
    Obligation,
    PaymentAllocation,
    PaymentProviderEvent,
    PaymentReceipt,
    PaymentSchedule,
    PayoutRecord,
    PayoutStatement,
    ProcessorTransaction,
    ReconciliationRun,
    RefundRequest,
    RentInvoice,
    ServiceFeeInvoice,
    SimulatedPayment,
)
from app.models.audit import AuditEvent
from app.models.domain_event import DomainEvent
from app.models.guest import Guest
from app.models.leasing import Agreement, Offer
from app.models.listing import Listing
from app.models.membership import Membership
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
    FinancialHoldCreate,
    FinancialHoldResolve,
    PaymentAllocationInput,
    HostRecoveryRecordProgress,
    HostRecoveryWriteOff,
    ObligationRead,
    PaymentConfirm,
    PaymentPreviewRead,
    RefundDecide,
    RefundRequestCreate,
    ScheduledObligationPreview,
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


def ensure_deposit_record_for_paid_obligation(db: Session, obligation: Obligation) -> None:
    """ZR-ENG-CLR-002 Section 2.3/5.2: create the DepositRecord + its
    DepositInstrument the moment a DEPOSIT obligation reaches PAID -- exactly
    the side effect confirm_payment below already had inline, extracted so
    crud/rental_payment.py's cross-domain status-sync bridge (ZR-PAY-LINK-003
    <-> this legacy domain) can trigger the identical, already-correct
    jurisdiction-policy-resolved record/instrument pair without duplicating
    this logic. No-ops if not a PAID DEPOSIT, or one already exists."""
    if obligation.obligation_type != "DEPOSIT" or obligation.status != "PAID" or obligation.deposit_record:
        return

    record = DepositRecord(obligation_id=obligation.id, held_amount=obligation.amount)
    db.add(record)
    db.flush()
    # instrument type is SECURITY_DEPOSIT (the only one this platform issues
    # today); custody_model and the policy snapshot are resolved from the
    # market policy pack, not hard-coded, so a new jurisdiction is a data
    # row, not a code change.
    deposit_room = obligation.agreement.offer.listing.room if obligation.agreement else obligation.occupancy.room
    policy = resolve_market_policy(db, deposit_room.property.jurisdiction_code)
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


def get_payment_timeline(db: Session, payment_id: int, admin: AdminUser) -> list[dict]:
    """ZR-ENG-CLR-005 Section 6.4's admin console 'Timeline: Immutable
    normalized events + raw webhook references + actor/system timestamps'
    panel -- merges three already-existing, separately-queryable event
    trails (DomainEvent, AuditEvent, PaymentProviderEvent-via-
    ProcessorTransaction) into the one chronological view the doc
    describes, instead of an admin having to query three endpoints and
    merge them by hand. Same provider-ownership scoping as list_payments."""
    payment = get_payment_or_404(db, payment_id)
    if admin.role != "super_admin" and payment.id not in _owned_payment_ids(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this payment's timeline")

    entries: list[dict] = []
    for event in db.scalars(
        select(DomainEvent).where(DomainEvent.resource_type == "simulated_payment", DomainEvent.resource_id == str(payment_id))
    ):
        entries.append({
            "timestamp": event.occurred_at, "source": "DOMAIN_EVENT", "event_type": event.event_type,
            "actor": "system", "detail": event.payload,
        })
    for audit in db.scalars(
        select(AuditEvent).where(AuditEvent.resource_type == "simulated_payment", AuditEvent.resource_id == str(payment_id))
    ):
        entries.append({
            "timestamp": audit.created_at, "source": "AUDIT_EVENT", "event_type": audit.action,
            "actor": f"admin:{audit.actor_admin_id}" if audit.actor_admin_id else "system",
            "detail": {"reason": audit.reason, "beforeState": audit.before_state, "afterState": audit.after_state},
        })
    transaction_ids = list(db.scalars(select(ProcessorTransaction.id).where(ProcessorTransaction.payment_id == payment_id)))
    if transaction_ids:
        for webhook_event in db.scalars(
            select(PaymentProviderEvent).where(PaymentProviderEvent.processor_transaction_id.in_(transaction_ids))
        ):
            entries.append({
                "timestamp": webhook_event.received_at, "source": "PROVIDER_WEBHOOK", "event_type": webhook_event.event_type,
                "actor": "provider",
                "detail": {
                    "providerEventId": webhook_event.provider_event_id,
                    "processedAt": webhook_event.processed_at.isoformat() if webhook_event.processed_at else None,
                },
            })
    entries.sort(key=lambda e: e["timestamp"])
    return entries


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
    if data.method_class not in PAYMENT_METHOD_CLASSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized payment method class '{data.method_class}'")
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
        method_class=data.method_class,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def confirm_payment(db: Session, payment: SimulatedPayment, data: PaymentConfirm, admin: AdminUser) -> SimulatedPayment:
    """Idempotent: replaying a confirm call against an already-SUCCEEDED payment is a
    no-op that returns the existing state instead of allocating a second time.

    ZR-ENG-CLR-005 AC-27 'Host cannot manually mark a renter obligation paid
    without a controlled external-payment workflow': method_class ==
    EXTERNAL (the default -- an admin explicitly recording a real off-
    platform cash/cheque payment, Section 12.1's own carved-out allowance)
    is the only class a direct confirm call may complete. Any other class
    means crud/payment_provider.py:dispatch_payment_to_provider already
    asserted a real PSP rail is handling this payment -- only that
    function's own webhook-driven ingest_provider_callback (which always
    acts as the system/super_admin, see get_system_admin) may complete it
    from here on; a regular admin (including the obligation's own owning
    Host) calling this route directly for it is exactly the unaudited
    'mark paid myself' shortcut AC-27 prohibits."""
    if payment.status == "SUCCEEDED":
        return payment
    if payment.status == "FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment already failed")
    if payment.method_class != "EXTERNAL" and admin.role != "super_admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"This payment was dispatched via a real payment provider ({payment.method_class}) -- it can only be "
            "completed through that provider's own callback, not a direct confirmation",
        )
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
        ensure_deposit_record_for_paid_obligation(db, obligation)

    # ZR-ENG-CLR-005 AC-16: Payment Service reacts to a cleared payment only
    # through the Booking Orchestrator boundary -- it never imports
    # crud.leasing/crud.occupancy directly. See services/booking_orchestrator.py.
    booking_orchestrator.confirm_downstream_agreements(db, obligations)

    payment.status = "SUCCEEDED"
    payment.confirmed_at = datetime.now(timezone.utc)
    if payment.method_class == "EXTERNAL":
        payment.evidence_ref = data.evidence_ref.strip()
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


def create_autopay_mandate(db: Session, guest: Guest, occupancy: Occupancy) -> AutopayMandate:
    """ZR-ENG-CLR-005 Section 6.1-E's 'Separate explicit consent' -- the
    calling guest is always the payer/consenting party; nobody can create a
    mandate on another payer's behalf through this path (a genuine
    authorized-third-party-payer mandate would need its own, separately
    authenticated consent flow this build doesn't have yet). consent_snapshot
    freezes the schedule terms the payer actually saw at consent time --
    Section 1.2's 'versioned and reproducible later' invariant, applied to
    autopay the same way calculation_snapshot already applies it to deposits."""
    if occupancy.guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only set up autopay for your own tenancy")
    if occupancy.status == "ENDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot set up autopay for an ended tenancy")
    existing = db.scalar(
        select(AutopayMandate).where(AutopayMandate.occupancy_id == occupancy.id, AutopayMandate.status == "ACTIVE")
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An active autopay mandate already exists for this tenancy")

    schedule = db.scalar(
        select(PaymentSchedule).where(PaymentSchedule.agreement_id == occupancy.offer.agreement.id, PaymentSchedule.status == "ACTIVE")
    ) if occupancy.offer.agreement else None
    snapshot = {
        "cadence": schedule.cadence if schedule else "MONTHLY",
        "amount": float(schedule.amount) if schedule else None,
        "currency": schedule.currency if schedule else "INR",
    }
    provider_ref = stripe_client.create_setup_intent(
        customer_email=guest.email, metadata={"occupancy_id": str(occupancy.id), "guest_id": guest.id},
    )
    mandate = AutopayMandate(
        occupancy_id=occupancy.id, payer_guest_id=guest.id, provider_ref=provider_ref, status="ACTIVE",
        consent_snapshot=snapshot,
    )
    db.add(mandate)
    db.commit()
    db.refresh(mandate)
    emit_event(db, "mandate.created", "autopay_mandate", str(mandate.id), {"occupancyId": occupancy.id, "guestId": guest.id})
    return mandate


def revoke_autopay_mandate(db: Session, mandate: AutopayMandate, guest: Guest) -> AutopayMandate:
    """AC-29: 'Revoking autopay does not cancel future rent obligations' --
    this only ever flips status; it must never touch Obligation/
    PaymentSchedule rows. AC-28: independently auditable -- revoked_at plus
    the domain event below stand on their own regardless of who resolves
    (or never resolves) the still-due obligations this stops auto-charging."""
    if mandate.payer_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This mandate does not belong to you")
    if mandate.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This mandate is not active (status: {mandate.status})")
    mandate.status = "REVOKED"
    mandate.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(mandate)
    emit_event(db, "mandate.revoked", "autopay_mandate", str(mandate.id), {"occupancyId": mandate.occupancy_id})
    return mandate


def list_autopay_mandates_for_guest(db: Session, guest: Guest) -> list[AutopayMandate]:
    return list(
        db.scalars(select(AutopayMandate).where(AutopayMandate.payer_guest_id == guest.id).order_by(AutopayMandate.created_at.desc()))
    )


def get_autopay_mandate_or_404(db: Session, mandate_id: int) -> AutopayMandate:
    mandate = db.get(AutopayMandate, mandate_id)
    if not mandate:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Autopay mandate not found")
    return mandate


def process_autopay_charge(db: Session, obligation: Obligation, admin: AdminUser) -> SimulatedPayment:
    """QA-27/Section 10.5's actual auto-pull -- there is no background
    scheduler anywhere in this stack (see crud/occupancy.py:generate_next_
    rent_obligation's own docstring for the same honesty about recurring
    rent generation itself), so this is the real charge-execution code path
    an admin triggers manually today and a real cron would call per due
    obligation once one exists. Requires an ACTIVE AutopayMandate for the
    obligation's occupancy -- revoking one only ever blocks this path, never
    the obligation (AC-29)."""
    if obligation.obligation_type != "RENT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Autopay only applies to RENT obligations")
    if obligation.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is not awaiting payment (status: {obligation.status})")
    occupancy = _occupancy_for_obligation(db, obligation)
    if occupancy is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Could not resolve the occupancy behind this obligation")
    mandate = db.scalar(
        select(AutopayMandate).where(AutopayMandate.occupancy_id == occupancy.id, AutopayMandate.status == "ACTIVE")
    )
    if mandate is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No active autopay mandate for this tenancy")

    payment = create_payment_intent(
        db,
        SimulatedPaymentCreate(
            guest_id=occupancy.guest_id, amount=float(obligation.amount), currency=obligation.currency,
            idempotency_key=f"autopay-{mandate.id}-{obligation.id}", payer_guest_id=mandate.payer_guest_id,
        ),
    )
    return confirm_payment(
        db, payment, PaymentConfirm(allocations=[PaymentAllocationInput(obligation_id=obligation.id, amount=float(obligation.amount))]), admin,
    )


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


def _listing_and_guest_for_obligation(obligation: Obligation) -> tuple[Listing | None, Guest | None]:
    """Same agreement-or-occupancy resolution as to_obligation_read's
    guest_id above, extended to also resolve the listing -- the initial RENT
    obligation is linked via agreement_id (no Occupancy exists yet), every
    recurring one via occupancy_id."""
    if obligation.agreement:
        offer = obligation.agreement.offer
        return offer.listing, offer.guest
    if obligation.occupancy:
        return obligation.occupancy.listing, obligation.occupancy.guest
    return None, None


def _generate_rent_invoice_pdf(obligation: Obligation, invoice_number: str, *, listing: Listing | None, guest: Guest | None) -> bytes:
    """ZR-ENG-CLR-005 Section 13.1: the host's request for payment of one RENT
    obligation -- rendered as soon as the obligation exists, not after it's
    paid (PaymentReceipt is the after-the-fact counterpart for that). Same
    "simulated, no real processor" framing as _generate_payment_receipt_pdf;
    listing name/property address stand in for the host's own identity,
    since Party carries no display name in this build."""
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

    write("Zoiko Rooms -- Rent Invoice", size=16, bold=True, gap=10 * mm)
    write(f"Invoice {invoice_number}", size=10)
    write(f"Obligation #{obligation.id}  |  Status: {obligation.status}", size=10)
    write(f"Due {obligation.due_date.isoformat()}", size=9, gap=10 * mm)

    write("Property", size=12, bold=True)
    write(listing.name if listing else "Unknown listing")
    property_address = listing.room.property.address if listing and listing.room and listing.room.property else ""
    write(property_address, gap=10 * mm)

    write("Billed to", size=12, bold=True)
    write(guest.name if guest else "Unknown")
    write(guest.email if guest else "", gap=10 * mm)

    write("Amount due", size=12, bold=True)
    write(f"{obligation.currency} {obligation.amount:.2f}", gap=10 * mm)

    y -= 4 * mm
    write("Simulated invoice -- no real payment processor is involved.", size=8, gap=6 * mm)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def get_or_create_rent_invoice(db: Session, obligation: Obligation) -> RentInvoice:
    """Idempotent, same render-once-then-persist discipline as
    get_or_create_payment_receipt -- the fast-path check is a convenience
    only, RentInvoice.obligation_id being DB-unique is the real guarantee
    against a concurrent double-render (loser catches IntegrityError and
    returns the winner's row)."""
    if obligation.rent_invoice is not None:
        return obligation.rent_invoice

    listing, guest = _listing_and_guest_for_obligation(obligation)
    invoice_number = f"RINV-{obligation.id:08d}"
    pdf_bytes = _generate_rent_invoice_pdf(obligation, invoice_number, listing=listing, guest=guest)
    storage_ref, content_hash = save_rent_invoice_document(pdf_bytes)

    try:
        with db.begin_nested():
            invoice = RentInvoice(
                obligation_id=obligation.id, invoice_number=invoice_number,
                content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(invoice)
            db.flush()
    except IntegrityError:
        return db.scalar(select(RentInvoice).where(RentInvoice.obligation_id == obligation.id))
    db.commit()
    db.refresh(invoice)
    return invoice


def get_obligation_or_404(db: Session, obligation_id: int) -> Obligation:
    obligation = db.get(Obligation, obligation_id)
    if not obligation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Obligation not found")
    return obligation


def get_rent_invoice_for_admin(db: Session, obligation_id: int, admin: AdminUser) -> RentInvoice:
    """404 + the same provider-ownership scoping every other finance mutation
    uses (_owned_obligation_ids), then get-or-create the invoice -- the route
    layer never reaches into ownership-check internals directly. Unlike a
    receipt, a rent invoice never requires the obligation to be paid -- it's
    the request for payment, not proof one was made."""
    obligation = get_obligation_or_404(db, obligation_id)
    if admin.role != "super_admin" and obligation.id not in _owned_obligation_ids(db, admin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to manage this provider's records")
    if obligation.obligation_type != "RENT":
        raise HTTPException(status.HTTP_409_CONFLICT, "No rent invoice exists for a non-RENT obligation")
    return get_or_create_rent_invoice(db, obligation)


def _add_months(d: date, months: int) -> date:
    """Same calendar-month arithmetic as crud/occupancy.py's own _add_months
    (and services/overlap.py's) -- duplicated rather than imported, matching
    this codebase's existing precedent for this exact small helper, to avoid
    a cross-module import edge into crud.occupancy purely for one date
    calculation."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


MAX_PAYMENT_PREVIEW_ENTRIES = 24


def get_payment_preview(db: Session, agreement: Agreement) -> PaymentPreviewRead:
    """ZR-ENG-CLR-005 AC-11/Section 5.2/6.1 region B+C: 'amount due now' line
    items and the 'future schedule' preview, both computed from real rows --
    never a separately-maintained duplicate of the obligation/schedule
    engine. amount_due_now is every not-yet-fully-paid Obligation on this
    agreement (rent + deposit; this build has no renter-side fee, tax or
    credit object to add -- see models/market_policy.py's own
    'renter fees stay OFF by default with no toggle here yet'/AC-10).
    future_schedule is a pure projection -- it creates no Obligation rows --
    of the RENT periods still to come after the last one that exists,
    through the agreement's contractual end date, capped defensively at
    MAX_PAYMENT_PREVIEW_ENTRIES (remaining_scheduled_count stays the true,
    uncapped total)."""
    obligations = list(agreement.obligations)
    occupancy = db.scalar(select(Occupancy).where(Occupancy.offer_id == agreement.offer_id))
    if occupancy:
        obligations += [o for o in occupancy.obligations if o not in obligations]

    amount_due_now = [to_obligation_read(o) for o in obligations if o.status in ("PENDING", "PARTIALLY_PAID")]

    schedule = db.scalar(
        select(PaymentSchedule).where(PaymentSchedule.agreement_id == agreement.id, PaymentSchedule.status == "ACTIVE")
    )
    future_schedule: list[ScheduledObligationPreview] = []
    remaining_count = 0
    # UPFRONT's one obligation already covers the entire term -- nothing
    # further is ever projected (mirrors crud/occupancy.py:generate_next_rent_
    # obligation's own early return for it).
    if schedule is not None and schedule.cadence != "UPFRONT":
        rent_obligations = sorted([o for o in obligations if o.obligation_type == "RENT"], key=lambda o: o.due_date)
        latest_terms = agreement.offer.terms[-1]
        term_end = _add_months(latest_terms.start_date, latest_terms.term_months)
        next_due = (
            rent_obligations[-1].due_date if rent_obligations
            else latest_terms.start_date
        )
        cadence = schedule.cadence
        while True:
            if cadence == "CUSTOM":
                next_due = next_due + timedelta(days=schedule.custom_interval_days)
            elif cadence in CADENCE_INTERVAL_DAYS:
                next_due = next_due + timedelta(days=CADENCE_INTERVAL_DAYS[cadence])
            else:
                next_due = _add_months(next_due, 1)
            if next_due > term_end:
                break
            remaining_count += 1
            if len(future_schedule) < MAX_PAYMENT_PREVIEW_ENTRIES:
                future_schedule.append(
                    ScheduledObligationPreview(
                        due_date=next_due, amount=float(schedule.amount), currency=schedule.currency, cadence=cadence,
                    )
                )

    return PaymentPreviewRead(
        amount_due_now=amount_due_now,
        future_schedule=future_schedule,
        cadence=schedule.cadence if schedule else "MONTHLY",
        remaining_scheduled_count=remaining_count,
    )


def get_payment_preview_for_own_agreement(db: Session, agreement_id: int, guest_id: str) -> PaymentPreviewRead:
    """Renter-facing entry point -- same ownership shape as every other
    'download my own agreement X' route (guest.id == agreement.offer.guest_id)."""
    agreement = db.get(Agreement, agreement_id)
    if not agreement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agreement not found")
    if agreement.offer.guest_id != guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This agreement does not belong to you")
    return get_payment_preview(db, agreement)


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
        currency=record.obligation.currency,
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


def _jurisdiction_for_obligation(obligation: Obligation) -> str:
    """Same room-resolution path as run_payout's own _room_for -- shared here so
    every fee-policy lookup keyed off a payout/obligation (run_payout, service
    fee invoices, refund fee reversal) resolves the room's real jurisdiction
    instead of silently defaulting to DEFAULT_JURISDICTION ("IN")."""
    room = obligation.agreement.offer.listing.room if obligation.agreement else obligation.occupancy.room
    return room.property.jurisdiction_code


def _occupancy_for_obligation(db: Session, obligation: Obligation) -> Occupancy | None:
    """PSP_DEFERRED_PAYOUT gate: same agreement-or-occupancy resolution as
    _listing_and_guest_for_obligation, extended all the way to the Occupancy
    itself -- the initial RENT obligation only carries agreement_id (the
    Occupancy already exists by the time this obligation is PAID, just never
    linked back onto this row), every recurring one carries occupancy_id
    directly."""
    if obligation.occupancy_id:
        return obligation.occupancy
    if obligation.agreement:
        return db.scalar(select(Occupancy).where(Occupancy.offer_id == obligation.agreement.offer_id))
    return None


def _occupancy_active_for_obligation(db: Session, obligation: Obligation) -> bool:
    occupancy = _occupancy_for_obligation(db, obligation)
    return occupancy is not None and occupancy.status == "ACTIVE"


def run_payout(db: Session, party: Party, admin: AdminUser, period_key: str) -> PayoutRecord:
    return _resolve_payout(db, party, admin, period_key, existing_payout=None)


def retry_payout(db: Session, payout: PayoutRecord, admin: AdminUser) -> PayoutRecord:
    """ZR-ENG-CLR-005 Section 6.4/16.1 'Retry permitted payout' / QA-23 --
    re-evaluates a HELD or FAILED payout's own eligibility gates in place,
    against whatever's changed since the original run (a beneficiary now
    verified, a Stripe Connect account now onboarded, a habitability
    incident now resolved, ...). This never creates a second PayoutRecord
    for the same (party_id, period_key) -- the underlying obligations were
    never linked to this payout while it stayed HELD (see run_payout's own
    'if not held_reason' branch below), so they're still exactly as
    available to re-match as they were the first time."""
    party = db.get(Party, payout.party_id)
    if party is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    if payout.status not in ("HELD", "FAILED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Only a HELD or FAILED payout can be retried (current status: {payout.status})")
    return _resolve_payout(db, party, admin, payout.period_key, existing_payout=payout)


def _resolve_payout(db: Session, party: Party, admin: AdminUser, period_key: str, *, existing_payout: PayoutRecord | None) -> PayoutRecord:
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
    # Section 15.2 'Ledger balances are calculated/maintained ... with currency
    # segregation': a party renting in more than one jurisdiction could have
    # PAID rent obligations in different currencies pending payout for the same
    # party+period -- summing them as raw numbers below would silently add GBP
    # to INR. PayoutRecord/the (party_id, period_key) uniqueness only support one
    # currency per payout, so this run pays out whichever currency the first
    # matched obligation is in and leaves any other-currency obligations
    # unmatched (still PAID, payout_id null) for a future run.
    if matched:
        payout_currency = matched[0].currency
        matched = [o for o in matched if o.currency == payout_currency]
    # ZR-ENG-CLR-005 AC-09/AC-34: fee rate resolved from the effective-dated
    # market policy pack, not a hard-coded constant -- same resolver deposit
    # collection already uses (see confirm_payment above). Resolved as of the
    # period being paid out (period_key, "YYYY-MM"), not today -- a payout
    # run late (after a fee-policy change) must still apply the rate that was
    # actually in effect when this rent was earned, not retroactively apply a
    # rate change (AC-34). See _period_as_of below.
    policy = resolve_market_policy(
        db, _jurisdiction_for_obligation(matched[0]) if matched else DEFAULT_JURISDICTION, as_of=_period_as_of(period_key),
    )

    # ZR-ENG-CLR-005 AC-20/AC-35: fail closed rather than silently defaulting
    # to direct settlement or Zoiko custody -- a market pack resolving to a
    # funds-flow profile this build can't actually execute (no real trust
    # partner behind TRUST_ESCROW_CUSTODY; ZOIKO_REGULATED_CUSTODY is
    # off-by-default and needs separate licensing approval) must refuse the
    # payout outright, not create a HELD row for a configuration that was
    # never actually supported.
    if policy.funds_flow_profile not in SUPPORTED_FUNDS_FLOW_PROFILES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Funds-flow profile '{policy.funds_flow_profile}' is not supported by this build -- payout blocked",
        )

    # ZR-ENG-CLR-005 Section 9.1: PSP_DEFERRED_PAYOUT's whole point is that
    # money stays parked in Zoiko's own Stripe balance (never even offered
    # to the host) until a real release event fires -- move-in confirmed is
    # the only such event this build tracks. An obligation whose occupancy
    # is still PENDING_MOVE_IN is excluded from THIS run (payout_id stays
    # null, picked up automatically the first run after move-in), never
    # blocked outright the way an unsupported profile is above.
    deferred_pending_move_in = 0
    if policy.funds_flow_profile == "PSP_DEFERRED_PAYOUT":
        before = len(matched)
        matched = [o for o in matched if _occupancy_active_for_obligation(db, o)]
        deferred_pending_move_in = before - len(matched)

    gross = _round2(sum(o.amount for o in matched))
    # ZR-PAY-CFG-001 Decision 3: Zoiko Rooms takes no commission on rent --
    # no fee is calculated, accrued, invoiced or reported. Kept as an explicit
    # zero (not a policy lookup) so no configuration can reintroduce one.
    fee = 0.0
    net = _round2(gross - fee)

    # ZR-ENG-CLR-005 AC-19/AC-30/Section 9.2 + ZR-ENG-CLR-012 Section 7's
    # PAYMENT_ONBOARDING requirement type ("Payout account and PSP/KYC
    # requirements... owner: Host/payee"): which account model is the actual
    # gate depends on the resolved funds-flow profile, never a hard-coded
    # choice -- DIRECT_SETTLEMENT markets still gate on the legacy verified
    # bank-details PayoutBeneficiary; PSP_DEFERRED_PAYOUT markets gate on a
    # completed real Stripe Connect onboarding instead, since a bank detail
    # record was never the account that would actually receive this money.
    if policy.funds_flow_profile == "PSP_DEFERRED_PAYOUT":
        stripe_account = host_stripe_account_crud.get_for_party(db, party.id)
        held_reason = "" if (stripe_account is not None and stripe_account.payouts_enabled) else (
            "Stripe Connect payout onboarding (PAYMENT_ONBOARDING) is required by this jurisdiction's "
            "funds-flow profile and is not yet complete"
        )
    else:
        held_reason = "" if get_verified_payout_beneficiary(db, party.id) else "No verified payout beneficiary on file for this provider"

    if not held_reason and not matched and deferred_pending_move_in:
        held_reason = (
            f"{deferred_pending_move_in} obligation(s) are deferred under PSP_DEFERRED_PAYOUT until move-in "
            "is confirmed for the underlying occupancy"
        )

    # ZR-ENG-CLR-005 Section 6.4: an admin-placed operational hold
    # (crud/finance.py:create_financial_hold) blocks this party's payout the
    # same way every other gate here does -- the one gate a human chooses
    # to open rather than the system detecting it.
    if not held_reason:
        manual_hold = db.scalar(
            select(FinancialHold).where(
                FinancialHold.source_type == "party", FinancialHold.source_id == str(party.id),
                FinancialHold.status == "OPEN", FinancialHold.reason_code == "MANUAL_OPERATIONAL_HOLD",
            )
        )
        if manual_hold is not None:
            held_reason = f"An operational hold is open on this provider: {manual_hold.description}"

    if not held_reason:
        for obligation in matched:
            room = _room_for(obligation)
            if not get_valid_authority_for_room(db, room.id):
                held_reason = "One or more rooms no longer have a verified authority record"
                break

    # ZR-ENG-CLR-006 Section 9.2: "Do not release disputed Host payout
    # amounts while the related habitability liability is unresolved." An
    # open H2/H3 habitability incident on a room behind one of these
    # obligations blocks this payout the same way a missing authority record
    # already does.
    if not held_reason:
        for obligation in matched:
            room = _room_for(obligation)
            if has_open_severe_incident_for_room(db, room.id):
                held_reason = "One or more rooms have an open, unresolved habitability incident"
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

    # ZR-ENG-CLR-006 Section 15 waterfall tier 4/AC-22: if this party has an
    # open negative-balance hold from a prior refund clawback, this payout's
    # own net now automatically settles as much of it as this period's net
    # allows -- full offset when net covers the outstanding balance in one
    # shot, otherwise a partial offset (Section 2 doctrine: "Undisputed money
    # should not be trapped unnecessarily" -- this period's own earned rent
    # is undisputed and shouldn't be held hostage to a shortfall from an
    # unrelated earlier period). A partial offset leaves the hold open and
    # the recovery row(s) partially recovered, to be picked up by a later
    # payout or a manual record_host_recovery_progress call.
    open_recoveries: list[HostRecovery] = []
    auto_offset_amount = 0.0
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
                open_recoveries = list(db.scalars(
                    select(HostRecovery).where(
                        HostRecovery.party_id == party.id, HostRecovery.currency == payout_currency,
                        HostRecovery.status == "OPEN",
                    ).order_by(HostRecovery.created_at)
                ))
                outstanding = _round2(sum(float(r.amount) - float(r.recovered_amount) for r in open_recoveries))
                if open_recoveries and outstanding > 0:
                    auto_offset_amount = min(outstanding, net)

    if existing_payout is None:
        payout = PayoutRecord(
            party_id=party.id,
            period_key=period_key,
            amount=net,
            currency=matched[0].currency if matched else "INR",
            status="HELD" if held_reason else "PAID",
            hold_reason=held_reason,
            recovery_offset_amount=auto_offset_amount,
        )
        db.add(payout)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "A payout has already been run for this provider and period")
    else:
        # retry_payout's path -- update the same row in place rather than
        # creating a second one for this (party_id, period_key).
        payout = existing_payout
        payout.amount = net
        payout.currency = matched[0].currency if matched else "INR"
        payout.status = "HELD" if held_reason else "PAID"
        payout.hold_reason = held_reason
        payout.recovery_offset_amount = auto_offset_amount
        db.flush()

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

        disbursed = net
        if auto_offset_amount > 0:
            # ZR-ENG-CLR-006 Section 15 tier 4: the portion of this period's
            # net that settles a prior over-payment never actually reaches
            # the host -- reversing the same debit/credit pair the "Payout
            # paid to host" entry above just posted, for exactly the withheld
            # amount, so platform_clearing's real cash outflow nets to
            # (net - auto_offset_amount) while host_payable's balance moves
            # back toward zero by the recovered amount (get_balance's own
            # "positive means paid down/refunded more than owed" convention --
            # see app/services/ledger.py).
            ledger_service.post_entry(
                db,
                debit_account=platform_clearing,
                credit_account=host_payable,
                amount=auto_offset_amount,
                currency=payout.currency,
                description=f"Host recovery offset applied against payout #{payout.id}",
                source_type="payout_record",
                source_id=str(payout.id),
            )
            remaining_offset = auto_offset_amount
            for recovery in open_recoveries:
                if remaining_offset <= 0:
                    break
                owed = _round2(float(recovery.amount) - float(recovery.recovered_amount))
                applied = min(owed, remaining_offset)
                recovery.recovered_amount = _round2(float(recovery.recovered_amount) + applied)
                recovery.recovery_method = "FUTURE_PAYOUT_OFFSET"
                remaining_offset = _round2(remaining_offset - applied)
                if recovery.recovered_amount >= float(recovery.amount) - 0.01:
                    recovery.status = "RECOVERED"
                    recovery.resolved_at = datetime.now(timezone.utc)
                    emit_event(
                        db, "host_recovery.completed", "host_recovery", str(recovery.id),
                        {"status": recovery.status, "recoveredAmount": float(recovery.recovered_amount)},
                    )
                    hold = db.get(FinancialHold, recovery.financial_hold_id)
                    if hold is not None and hold.status == "OPEN":
                        hold.status = "RESOLVED"
                        hold.resolved_by_admin_id = admin.id
                        hold.resolved_at = datetime.now(timezone.utc)
                        hold.resolution_notes = f"Automatically resolved -- fully offset against payout #{payout.id}"
            disbursed = _round2(net - auto_offset_amount)

        # ZR-ENG-CLR-005 Section 9.1 PSP_DEFERRED_PAYOUT/'separate charges and
        # transfers': the actual money movement out of Zoiko's own Stripe
        # balance into the host's Connected Account, only for a party that has
        # completed Stripe Connect onboarding (see crud/host_stripe_account.py).
        # A party with no Stripe account, or one still ONBOARDING, keeps
        # exactly the pre-existing simulated behavior -- this never blocks or
        # changes the ledger/notification logic above, it only additionally
        # records a real transfer id when one was actually possible.
        stripe_account = host_stripe_account_crud.get_for_party(db, party.id)
        if stripe_account is not None and stripe_account.payouts_enabled and disbursed > 0:
            payout.stripe_transfer_id = stripe_client.create_transfer(
                amount=disbursed, currency=payout.currency, destination_account_id=stripe_account.stripe_account_id,
                metadata={"payout_id": str(payout.id), "period_key": period_key},
            )

        if auto_offset_amount > 0:
            message = (
                f"A payout of {payout.currency} {disbursed:.2f} for {period_key} has been paid out to you "
                f"({payout.currency} {auto_offset_amount:.2f} of {payout.currency} {net:.2f} earned this period was "
                "applied against a prior refund recovery)."
            )
        else:
            message = f"A payout of {payout.currency} {net:.2f} for {period_key} has been paid out to you."
        notif_crud.notify_user_by_party(
            db, party.id,
            title="Payout received",
            message=message,
            notification_type="payout.paid",
            related_entity_type="payout_record", related_entity_id=str(payout.id),
        )
        host_user = get_user_by_party_id(db, party.id)
        if host_user:
            send_payout_paid_email(host_user.email, host_user.full_name, disbursed, payout.currency, period_key)
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
    since run_payout already computed and ledgered them once.

    ZR-ENG-CLR-006 Section 15/16.2 communications honesty: `net` stays the
    full period's gross-minus-fee earning (matches the obligations listed
    below 1:1); `disbursed` is what actually reached the host once a
    recovery_offset_amount (Section 15 waterfall tier 4) is subtracted --
    shown as its own line rather than silently folded into "Net payout" so
    the host statement never claims they received money that in fact paid
    down a prior refund clawback."""
    gross = _round2(sum(o.amount for o in payout.obligations))
    net = _round2(payout.amount)
    fee = _round2(gross - net)
    recovery_offset = _round2(float(payout.recovery_offset_amount))
    disbursed = _round2(net - recovery_offset)

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
    write(f"Net payout: {payout.currency} {net:.2f}", size=9, gap=6 * mm)
    if recovery_offset > 0:
        write(f"Applied against prior refund recovery: -{payout.currency} {recovery_offset:.2f}", size=9, gap=6 * mm)
        write(f"Amount disbursed: {payout.currency} {disbursed:.2f}", size=9, bold=True, gap=10 * mm)
    else:
        y -= 4 * mm

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
    # ZR-PAY-CFG-001 Decision 3: no rental commission exists, so there is
    # nothing to invoice. Historical invoices (issued before the commission
    # was removed) are still returned above.
    raise HTTPException(
        status.HTTP_404_NOT_FOUND, "Zoiko Rooms takes no commission on rent, so there is no service-fee invoice",
    )

    jurisdiction = _jurisdiction_for_obligation(payout.obligations[0]) if payout.obligations else DEFAULT_JURISDICTION
    policy = resolve_market_policy(db, jurisdiction, as_of=_period_as_of(payout.period_key))
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

    # Section 6 gap: no-double-recovery guard -- a renter could otherwise be
    # refunded here AND separately win a bank chargeback on the exact same
    # payment (resolve_dispute's own LOST-outcome reversal). Mirrors the
    # chargeback-blocks-payout check already enforced at payout time
    # (get_payout_eligibility's own "AC-19/Section 9.2: No active dispute,
    # chargeback... hold blocks release" check) -- this is that same
    # invariant's missing other half, at the refund-approval choke point.
    conflicting_chargeback = db.scalar(
        select(DisputeCase).where(
            DisputeCase.category == "CHARGEBACK", DisputeCase.payment_id == refund.payment_id,
            or_(
                DisputeCase.status == "OPEN",
                and_(DisputeCase.status == "RESOLVED", DisputeCase.chargeback_outcome == "LOST"),
            ),
        )
    )
    if conflicting_chargeback is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This payment has a chargeback dispute (#{conflicting_chargeback.id}, "
            f"{conflicting_chargeback.status.lower()}) -- resolve it first to avoid refunding the renter twice",
        )

    # Section 10 gap: a MANUAL_OPERATIONAL_HOLD or NEGATIVE_ACCOUNT_BALANCE
    # FinancialHold already blocks this party's payout (get_payout_eligibility's
    # own AC-19 check) -- previously nothing checked either before a refund,
    # which pulls from the exact same party balances a payout does. Same
    # two reason codes, same "an open hold blocks money movement" invariant,
    # just checked at the refund choke point too.
    refund_party_id = _obligation_party_id(refund.obligation)
    if refund_party_id is not None:
        blocking_hold = db.scalar(
            select(FinancialHold).where(
                FinancialHold.source_type == "party", FinancialHold.source_id == str(refund_party_id),
                FinancialHold.status == "OPEN", FinancialHold.reason_code == "MANUAL_OPERATIONAL_HOLD",
            )
        )
        if blocking_hold is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"An operational hold is open on this provider: {blocking_hold.description}",
            )

    obligation = refund.obligation
    db.add(PaymentAllocation(payment_id=refund.payment_id, obligation_id=obligation.id, amount_allocated=-refund.amount))
    db.flush()
    db.refresh(obligation)
    recompute_obligation_status(db, obligation)

    # Section 5 gap: pull the money back out of Stripe itself, not just
    # Zoiko's own ledger -- only possible when the original payment actually
    # went through a real PSP transaction (SUCCEEDED ProcessorTransaction);
    # an EXTERNAL/cash payment has nothing to reverse here, same as before.
    if refund.amount > 0:
        processor_txn = db.scalar(
            select(ProcessorTransaction).where(
                ProcessorTransaction.payment_id == refund.payment_id, ProcessorTransaction.status == "SUCCEEDED",
            )
        )
        if processor_txn is not None:
            refund.psp_refund_id = stripe_client.create_refund(
                payment_intent_id=processor_txn.provider_transaction_id,
                amount=refund.amount, currency=refund.payment.currency,
                metadata={"refund_request_id": str(refund.id), "obligation_id": str(obligation.id)},
            )

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
            negative_balance = ledger_service.get_balance(db, debit_account)
            if negative_balance > 0.01:
                hold = FinancialHold(
                    source_type="ledger_account", source_id=str(debit_account.id),
                    reason_code="NEGATIVE_ACCOUNT_BALANCE", severity="HIGH",
                    description=(
                        f"Refund #{refund.id} drove {debit_account.account_type} account "
                        f"{debit_account.id} (party {party_id}) negative -- more was already paid out/"
                        "released than this refund leaves owed. Needs an approved reserve/offset/"
                        "collection policy before further payouts to this party."
                    ),
                )
                db.add(hold)
                db.flush()
                # ZR-ENG-CLR-006 AC-22/Section 18.4: the same event that flags
                # the FinancialHold above also opens a dedicated, queryable
                # HostRecovery record -- see that model's own docstring for
                # exactly how far this build automates recovery vs. leaves to
                # an admin to log manually.
                recovery = HostRecovery(
                    party_id=party_id, financial_hold_id=hold.id, refund_request_id=refund.id,
                    amount=_round2(negative_balance), currency=refund.payment.currency,
                )
                db.add(recovery)
                db.flush()
                # ZR-ENG-CLR-006 Section 20.2/18.4: the one Section-15/18.4
                # outcome that had no domain event at all -- a future
                # recovery/collections consumer needs to know a clawback was
                # opened just as much as it needs refund.completed above.
                emit_event(
                    db, "host_recovery.created", "host_recovery", str(recovery.id),
                    {"partyId": party_id, "amount": float(recovery.amount), "refundRequestId": refund.id},
                )
                # ZR-ENG-CLR-006 Section 15 waterfall tier 3: try to claw the
                # money straight back from the host's own PSP balance before
                # this recovery ever has to wait on tier 4 (a future payout
                # large enough to offset it, run_payout's own automation) or
                # an admin's manual record_host_recovery_progress. A no-op,
                # never an error, when tier 3 doesn't apply yet (no Stripe
                # Connect account, or no PAID payout with a transfer to
                # reverse) -- the recovery simply stays OPEN for the later
                # tiers, exactly as it did before this tier existed.
                _execute_psp_transfer_reversal(db, recovery, admin)

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


def reverse_platform_fee_for_refund(db: Session, obligation: Obligation, refund: RefundRequest) -> None:
    """Formerly credited back the platform fee run_payout took on refunded
    rent (ZR-ENG-CLR-006 Section 13). ZR-PAY-CFG-001 Decision 3 removed the
    rental commission entirely, so no fee is ever taken and there is never
    anything to reverse. Kept as a no-op so existing callers stay valid."""
    return


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
            # Section 6 gap: the symmetric no-double-recovery guard to the
            # one in decide_refund -- if this exact payment was already
            # refunded through Zoiko's own flow, reversing it AGAIN here
            # would be the same double payout from the other direction.
            conflicting_refund = db.scalar(
                select(RefundRequest).where(
                    RefundRequest.payment_id == dispute.payment_id, RefundRequest.status == "COMPLETED",
                )
            )
            if conflicting_refund is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"This payment already has a completed refund (#{conflicting_refund.id}) -- resolve that "
                    "first to avoid reversing the same money twice",
                )
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
    # A lost chargeback (crud/finance.py:resolve_dispute) reverses collected
    # money the same way a refund does, but posts a raw negative
    # PaymentAllocation directly rather than a RefundRequest row -- total_refunds
    # above never sees it. Without this, every lost chargeback permanently
    # false-positives the aggregate check below.
    total_chargeback_reversals = _round2(
        sum(
            d.amount or 0
            for d in db.scalars(
                select(DisputeCase).where(DisputeCase.category == "CHARGEBACK", DisputeCase.chargeback_outcome == "LOST")
            )
        )
    )

    # ZR-ENG-CLR-005 Section 19.2: each failed check becomes a (reason_code,
    # severity, message) triple -- the message still feeds the plain-string
    # `mismatches` list exactly as before, but each triple also becomes a real,
    # queryable, resolvable FinancialHold row below.
    failed_checks: list[tuple[str, str, str]] = []
    expected_allocated = _round2(total_payments - total_refunds - total_chargeback_reversals)
    if abs(total_allocated - expected_allocated) > 0.01:
        failed_checks.append((
            "AGGREGATE_MISMATCH", "MEDIUM",
            f"Allocated total ({total_allocated}) does not match payments minus refunds and chargeback reversals ({expected_allocated})",
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

    # Section 5 gap: every check above only ever compares this platform's
    # own internal tables against each other -- none of them would ever
    # catch a real Stripe-side discrepancy (e.g. a manually-issued Stripe
    # refund/dispute that never round-tripped back through this platform's
    # own webhook handling). A no-op when Stripe isn't configured -- there's
    # nothing real to check against without credentials, same disclosed-
    # simulation posture as every other stripe_client caller.
    psp_checked = 0
    if stripe_client.is_configured():
        succeeded_transactions = db.scalars(
            select(ProcessorTransaction).where(ProcessorTransaction.status == "SUCCEEDED")
        ).all()
        for txn in succeeded_transactions:
            remote = stripe_client.retrieve_payment_intent(payment_intent_id=txn.provider_transaction_id)
            if remote is None:
                continue
            psp_checked += 1
            payment = txn.payment
            expected_minor_units = stripe_client.to_minor_units(float(payment.amount), payment.currency)
            if remote["amount_received"] != expected_minor_units:
                failed_checks.append((
                    "PSP_AMOUNT_MISMATCH", "CRITICAL",
                    f"Stripe PaymentIntent {txn.provider_transaction_id} shows {remote['amount_received']} minor "
                    f"units received, but payment #{payment.id} records {expected_minor_units} -- Stripe's own "
                    "records disagree with this platform's",
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
            "totalChargebackReversals": total_chargeback_reversals,
            "ledgerTrialBalance": trial_balance,
            "totalLedgerCollected": total_ledger_collected,
            "pspTransactionsChecked": psp_checked,
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


def create_financial_hold(db: Session, admin: AdminUser, data: FinancialHoldCreate) -> FinancialHold:
    """ZR-ENG-CLR-005 Section 6.4's admin console 'place ... authorized
    operational hold' action -- the create half FinancialHold never had
    (only resolve_financial_hold existed, for the system-generated
    reconciliation/negative-balance rows). Actually gates run_payout (see
    that function's own beneficiary/Stripe-onboarding gate check) rather
    than being a purely informational record -- a Super Admin freezing a
    party's payouts here has real teeth, not just a note in a queue."""
    party = db.get(Party, data.party_id)
    if party is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Party not found")
    existing = db.scalar(
        select(FinancialHold).where(
            FinancialHold.source_type == "party", FinancialHold.source_id == str(data.party_id),
            FinancialHold.status == "OPEN",
        )
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This party already has an open operational hold")
    hold = FinancialHold(
        source_type="party", source_id=str(data.party_id), reason_code="MANUAL_OPERATIONAL_HOLD",
        severity=data.severity, description=data.description,
    )
    db.add(hold)
    db.commit()
    db.refresh(hold)
    return hold


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


def list_host_recoveries(db: Session, admin: AdminUser) -> list[HostRecovery]:
    """ZR-ENG-CLR-006 AC-22: the finance-ops queue of amounts owed back from
    a Host whose payout already went out before a later refund. Regular
    admins only see rows for parties they actually have an active Membership
    on -- the same Party/Membership ownership model run_payout/decide_refund
    already enforce for this exact party_id (assert_provider_access), rather
    than the separate Listing.owner_id scoping list_termination_cases_for_
    admin uses (this entity has no listing/occupancy of its own to join
    through -- it's a party-level financial record)."""
    query = select(HostRecovery).order_by(HostRecovery.created_at.desc())
    if admin.role != "super_admin":
        query = query.join(Membership, Membership.party_id == HostRecovery.party_id).where(
            Membership.admin_user_id == admin.id, Membership.status == "active",
        )
    return list(db.scalars(query))


def get_host_recovery_or_404(db: Session, recovery_id: int) -> HostRecovery:
    recovery = db.get(HostRecovery, recovery_id)
    if not recovery:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Host recovery not found")
    return recovery


def _execute_psp_transfer_reversal(db: Session, recovery: HostRecovery, admin: AdminUser) -> bool:
    """ZR-ENG-CLR-006 Section 15 waterfall tier 3's actual mechanics -- no
    permission check of its own (the caller, either decide_refund's own
    automatic cascade or attempt_psp_recovery's admin-facing wrapper below,
    has already established the admin's standing). Reverses against the
    party's single most recent PAID payout that actually moved money via a
    real Stripe Transfer, up to that payout's own amount -- real Stripe
    itself enforces the true per-transfer reversal ceiling server-side when
    credentials are configured; this stays a best-effort, non-blocking tier
    like every simulated provider path elsewhere in this codebase when they
    aren't. Returns whether a reversal actually happened."""
    outstanding = _round2(float(recovery.amount) - float(recovery.recovered_amount))
    if recovery.status != "OPEN" or outstanding <= 0:
        return False

    stripe_account = host_stripe_account_crud.get_for_party(db, recovery.party_id)
    if stripe_account is None or not stripe_account.payouts_enabled:
        return False

    payout = db.scalar(
        select(PayoutRecord)
        .where(
            PayoutRecord.party_id == recovery.party_id, PayoutRecord.currency == recovery.currency,
            PayoutRecord.status == "PAID", PayoutRecord.stripe_transfer_id.is_not(None),
        )
        .order_by(PayoutRecord.paid_at.desc())
    )
    if payout is None:
        return False

    reversed_amount = min(outstanding, float(payout.amount))
    recovery.psp_reversal_id = stripe_client.reverse_transfer(
        transfer_id=payout.stripe_transfer_id, amount=reversed_amount, currency=recovery.currency,
        metadata={"host_recovery_id": str(recovery.id), "payout_id": str(payout.id)},
    )
    recovery.recovered_amount = _round2(float(recovery.recovered_amount) + reversed_amount)
    recovery.recovery_method = "PSP_BALANCE_RECOVERY"
    if recovery.recovered_amount >= float(recovery.amount) - 0.01:
        recovery.status = "RECOVERED"
        recovery.resolved_at = datetime.now(timezone.utc)
        emit_event(
            db, "host_recovery.completed", "host_recovery", str(recovery.id),
            {"status": recovery.status, "recoveredAmount": float(recovery.recovered_amount)},
        )
        hold = db.get(FinancialHold, recovery.financial_hold_id)
        if hold is not None and hold.status == "OPEN":
            hold.status = "RESOLVED"
            hold.resolved_by_admin_id = admin.id
            hold.resolved_at = datetime.now(timezone.utc)
            hold.resolution_notes = f"Automatically resolved -- fully recovered via Stripe transfer reversal on recovery #{recovery.id}"
    db.flush()
    return True


def attempt_psp_recovery(db: Session, recovery: HostRecovery, admin: AdminUser) -> HostRecovery:
    """ZR-ENG-CLR-006 Section 15 waterfall tier 3's admin-facing retry --
    decide_refund already tries this automatically the moment a recovery
    opens; this exists for the realistic case where tier 3 didn't apply YET
    (the host had no Stripe Connect account, or no completed payout to
    reverse against at that moment) but does now, so an admin isn't stuck
    waiting on tier 4 or falling back to an out-of-band DIRECT_COLLECTION
    when a real PSP reversal has become possible in the meantime."""
    assert_provider_access(db, admin, recovery.party_id, roles=("provider_finance", "provider_owner_admin"))
    if recovery.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This recovery has already been {recovery.status.lower()}")
    reversed_any = _execute_psp_transfer_reversal(db, recovery, admin)
    if not reversed_any:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No PSP recovery is currently possible -- the party has no payouts-enabled Stripe Connect "
            "account, or no PAID payout with a real transfer to reverse against",
        )
    db.commit()
    db.refresh(recovery)
    return recovery


def record_host_recovery_progress(db: Session, recovery: HostRecovery, admin: AdminUser, data: HostRecoveryRecordProgress) -> HostRecovery:
    """ZR-ENG-CLR-006 Section 18.4: logs that some or all of an open
    recovery actually came back OUTSIDE this platform -- a direct wire/UPI
    collection an admin confirmed happened, method=DIRECT_COLLECTION being
    the realistic case now that run_payout (Section 15 waterfall tier 4)
    auto-applies FUTURE_PAYOUT_OFFSET for itself whenever a later payout is
    large enough to cover the outstanding balance in one shot -- see that
    function's own docstring. This function stays the fallback for
    everything run_payout can't reach on its own: a too-small later payout,
    a party with no further rent obligations coming, or an out-of-band
    collection -- the same honest 'surface, don't fabricate' discipline
    FinancialHold itself already applies to the underlying negative
    balance."""
    assert_provider_access(db, admin, recovery.party_id, roles=("provider_finance", "provider_owner_admin"))
    if recovery.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This recovery has already been {recovery.status.lower()}")
    if data.method not in HOST_RECOVERY_METHODS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized recovery method '{data.method}'")
    if data.amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Recorded amount must be positive")
    new_total = _round2(float(recovery.recovered_amount) + data.amount)
    if new_total > float(recovery.amount) + 0.01:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Recorded amount would exceed the outstanding recovery balance")

    recovery.recovered_amount = new_total
    recovery.recovery_method = data.method
    recovery.notes = data.notes
    if new_total >= float(recovery.amount) - 0.01:
        recovery.status = "RECOVERED"
        recovery.resolved_at = datetime.now(timezone.utc)
        emit_event(
            db, "host_recovery.completed", "host_recovery", str(recovery.id),
            {"status": recovery.status, "recoveredAmount": float(recovery.recovered_amount)},
        )
    db.commit()
    db.refresh(recovery)
    return recovery


def write_off_host_recovery(db: Session, recovery: HostRecovery, admin: AdminUser, data: HostRecoveryWriteOff) -> HostRecovery:
    """AC-29: a Super Admin's own logged override -- writing off money owed
    back to the platform is a significant, non-reversible finance decision,
    the same override tier AC-05's active-occupancy guard already reserves
    for a comparably consequential action."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a Super Admin can write off a host recovery")
    if recovery.status != "OPEN":
        raise HTTPException(status.HTTP_409_CONFLICT, f"This recovery has already been {recovery.status.lower()}")
    if not data.reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to write off a host recovery")

    recovery.status = "WRITTEN_OFF"
    recovery.recovery_method = "WRITTEN_OFF"
    recovery.notes = data.reason
    recovery.resolved_at = datetime.now(timezone.utc)
    emit_event(
        db, "host_recovery.completed", "host_recovery", str(recovery.id),
        {"status": recovery.status, "recoveredAmount": float(recovery.recovered_amount)},
    )
    db.commit()
    db.refresh(recovery)
    return recovery
