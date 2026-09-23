"""ZR-PAY-002 Section 2/4/5/6/7: the rental payment record domain's crud
layer. Every function here only ever records a declaration, a confirmation,
a dispute or a correction -- none of them move money, and none of them may
resolve Zoiko Rooms as a payee (A1/A2). Kept independent of
crud/finance.py: no shared calls, no shared tables (models/rental_payment.py's
own module docstring)."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import save_dispute_evidence_file
from app.core.field_encryption import encrypt_json
from app.core.mailer import send_rental_payment_instruction_verification_code_email
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud import notification as notif_crud
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.domain_event import DomainEvent
from app.models.evidence_artifact import EvidenceArtifact
from app.models.guest import Guest
from app.models.party import Party
from app.models.payment_recipient_authority import PaymentRecipientAuthority
from app.models.rental_payment import (
    RENTAL_PAYMENT_DISCREPANCY_REASONS,
    RENTAL_PAYMENT_METHOD_CATEGORIES,
    RentalPaymentAllocation,
    RentalPaymentCorrection,
    RentalPaymentDispute,
    RentalPaymentEvidenceHold,
    RentalPaymentInstruction,
    RentalPaymentObligation,
    RentalPaymentRecord,
)
from app.services.bank_field_schemas import resolve_bank_field_schema, validate_bank_details
from app.schemas.rental_transaction_record import RentalTransactionTimelineEntryRead

# ZR-PAY-002 Section 11: 'Append corrective event -- Tenant: Controlled,
# Landlord/Agent: Controlled.' Deliberately narrower than
# CORRECTABLE_RECORD_FIELDS below (never declared_amount/declared_date --
# those stay admin-only, A10's heavier protection for the financially
# significant fields) and only while the record is still PAYER_RECORDED
# (own tenant_correct_own_record) or the dispute is still OPEN (own
# recipient_update_own_open_dispute) -- i.e. before the other side has acted
# on it, never after.
TENANT_CORRECTABLE_RECORD_FIELDS = ("payment_method_category", "external_reference")

# ZR-PAY-002 Section 7.2/A10: only these record fields may ever be corrected,
# and only by an admin with a reason -- never an arbitrary attribute set.
# Status corrections go through their own explicit transitions below
# (reverse_record), not through this generic field-correction path.
CORRECTABLE_RECORD_FIELDS = ("declared_amount", "declared_date", "external_reference", "payment_method_category")

# ZR-PAY-LINK-003 Section 16/G6: the status-transition matrix. Every mutating
# function below guards its precondition against one of these named sets
# instead of an inline literal, so the full set of legal transitions for a
# given action lives in exactly one place. Renaming/consolidating only --
# each set is exactly what its call sites already checked.
RECORD_CONFIRMABLE_STATUSES = ("PAYER_RECORDED", "DISPUTED")
RECORD_TENANT_CORRECTABLE_STATUSES = ("PAYER_RECORDED",)
RECORD_REVERSIBLE_STATUSES = ("CONFIRMED",)
OBLIGATION_BLOCKED_FOR_MARK_PAID_STATUSES = ("WAIVED", "CANCELLED")
OBLIGATION_TERMINAL_STATUSES = ("WAIVED", "CANCELLED")
DISPUTE_OPEN_STATUS = "OPEN"
DISPUTE_RESOLVED_STATUS = "RESOLVED"


def _round2(amount) -> float:
    return round(float(amount), 2)


def resolve_rent_recipient_party_id(db: Session, room) -> int | None:
    """ZR-PAY-LINK-003 Section 1.1/2: 'A provider's ability to list a
    property does not automatically mean that provider is authorized to
    receive money.' The one place either obligation-creation call site
    (crud/leasing.py:create_agreement, crud/occupancy.py:
    generate_next_rent_obligation) resolves who actually receives rent for
    a room -- never re-derived inline at either call site.

    Prefers a verified PAYMENT_RECEIPT authority claim
    (payment_recipient_authority.get_valid_payment_recipient_authority_for_room)
    over the property owner when one exists, so a host who has actually
    designated (and gotten admin-verified) an authorized agent/manager as
    recipient has that respected. Falls back to the property's own
    owner_party_id when no verified claim exists yet -- a still-PENDING
    claim (submitted but not yet admin-verified) does not change who's
    resolved either; this is a deliberate, non-breaking rollout choice:
    making a verified claim mandatory immediately would retroactively
    block rent collection for every existing room the moment this shipped,
    with nothing yet on file to satisfy it. recipient_party_id is only
    ever set once, at obligation-creation time (never reassigned after --
    ZR-PAY-LINK-003 Section 24's own 'historical obligations retain
    original payee lineage'), so a claim that gets verified later only
    ever governs obligations created from that point on, never
    retroactively changes who could act on ones already created."""
    from app.crud.payment_recipient_authority import get_valid_payment_recipient_authority_for_room

    if room is None or room.property is None:
        return None
    authority = get_valid_payment_recipient_authority_for_room(db, room.id)
    if authority is not None:
        return authority.party_id
    return room.property.owner_party_id


def create_obligation(
    db: Session, *, obligation_type: str, tenant_guest_id: str, recipient_party_id: int,
    amount: float, currency: str, due_date: date, agreement_id: int | None = None, occupancy_id: int | None = None,
) -> RentalPaymentObligation:
    """Called alongside models/finance.py:Obligation's own creation
    (crud/leasing.py:create_agreement, crud/occupancy.py:
    generate_next_rent_obligation) -- never in place of it. Callers wrap
    this in a best-effort try/except, same placement discipline as those
    call sites' existing get_or_create_rent_invoice hook: a failure here
    must never undo or fail the agreement/occupancy action that triggered it."""
    obligation = RentalPaymentObligation(
        obligation_type=obligation_type, agreement_id=agreement_id, occupancy_id=occupancy_id,
        tenant_guest_id=tenant_guest_id, recipient_party_id=recipient_party_id,
        amount=_round2(amount), currency=currency, due_date=due_date,
        status="UPCOMING" if due_date > date.today() else "DUE",
    )
    db.add(obligation)
    db.commit()
    db.refresh(obligation)
    return obligation


def create_payer_allocations(
    db: Session, obligation: RentalPaymentObligation, allocations: list[tuple[str, float]], *, correlation_id: str = "",
) -> RentalPaymentObligation:
    """ZR-PAY-LINK-003 Section 15: 'Agreement can create payer allocations
    ... never a user-entered split-routing form' (Section 15's own split-to-
    multiple-RECIPIENTS caution, applied here to splitting one obligation
    across multiple PAYERS instead) -- so this is settable exactly once, at
    the same point the obligation itself is set up, and only by the
    recipient side (never the tenant/payer). allocations is a list of
    (payer_guest_id, allocated_amount) pairs; they must sum to exactly the
    obligation's own amount -- a partial or over-allocated split would leave
    recompute_obligation_status's aggregate (crud/rental_payment.py) unable
    to ever reach CONFIRMED, or reach it on less than the full rent."""
    if obligation.payer_allocations:
        raise HTTPException(status.HTTP_409_CONFLICT, "This obligation already has payer allocations set")
    if obligation.records:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This obligation already has payment activity -- a reallocation must be a new or amended obligation",
        )
    if len(allocations) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "At least two payers are required for a joint allocation")

    guest_ids = [guest_id for guest_id, _amount in allocations]
    if len(set(guest_ids)) != len(guest_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Each payer may only appear once")
    for guest_id in guest_ids:
        if db.get(Guest, guest_id) is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown payer guest id: {guest_id}")

    total_allocated = _round2(sum(amount for _guest_id, amount in allocations))
    if total_allocated != _round2(float(obligation.amount)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Allocated amounts must sum to exactly {obligation.amount} {obligation.currency}, got {total_allocated}",
        )

    for guest_id, amount in allocations:
        db.add(RentalPaymentAllocation(obligation_id=obligation.id, payer_guest_id=guest_id, allocated_amount=_round2(amount)))
    db.commit()
    db.refresh(obligation)

    log_audit_event(
        db, None, "rental_payment.payer_allocations_created", "rental_payment_obligation", str(obligation.id), correlation_id,
    )
    emit_event(
        db, "rental_payment.payer_allocations_created", "rental_payment_obligation", str(obligation.id),
        {"payerGuestIds": guest_ids}, correlation_id=correlation_id,
    )
    db.commit()
    return obligation


def _recompute_joint_obligation_status(db: Session, obligation: RentalPaymentObligation) -> bool:
    """ZR-PAY-LINK-003 Section 15: 'Two tenants, one rent obligation ...
    aggregate obligation status is computed' / 'One tenant pays full amount
    -- one payer may satisfy the obligation if agreement/rules permit.'
    Only ever called when obligation.payer_allocations is non-empty (the
    ordinary single-payer path below is unchanged otherwise). Returns False
    (meaning 'fall through to the single-payer/no-record derivation') when
    no payer has ever declared anything yet -- there is nothing to aggregate."""
    latest_per_payer: dict[str, RentalPaymentRecord] = {}
    for allocation in obligation.payer_allocations:
        latest = db.scalar(
            select(RentalPaymentRecord)
            .where(
                RentalPaymentRecord.obligation_id == obligation.id,
                RentalPaymentRecord.declared_by_guest_id == allocation.payer_guest_id,
            )
            .order_by(RentalPaymentRecord.created_at.desc())
            .limit(1)
        )
        if latest is not None:
            latest_per_payer[allocation.payer_guest_id] = latest

    if not latest_per_payer:
        return False

    statuses = {record.status for record in latest_per_payer.values()}
    if "DISPUTED" in statuses:
        obligation.status = "DISPUTED"
    elif "REVERSED" in statuses:
        obligation.status = "REVERSED"
    else:
        total_confirmed = sum(
            float(record.confirmed_amount or 0)
            for record in latest_per_payer.values()
            if record.status in ("CONFIRMED", "PARTIALLY_PAID")
        )
        if total_confirmed >= float(obligation.amount):
            obligation.status = "CONFIRMED"
        elif total_confirmed > 0:
            obligation.status = "PARTIALLY_PAID"
        else:
            # Every payer who has declared anything is still awaiting the
            # recipient's own confirmation of their share.
            obligation.status = "RECIPIENT_CONFIRMATION_PENDING"
    return True


def recompute_obligation_status(db: Session, obligation: RentalPaymentObligation) -> None:
    """The only place RentalPaymentObligation.status is ever assigned from a
    record's own state -- same derived-never-direct discipline as
    models/finance.py:recompute_obligation_status. Waived/cancelled stay
    terminal regardless of any record."""
    if obligation.status in OBLIGATION_TERMINAL_STATUSES:
        return

    _recompute_single_or_joint_status(db, obligation)

    # ZR-PAY-LINK-003 <-> legacy finance.py bridge: an obligation with
    # agreement_id set is one of the two initial signing-time RENT/DEPOSIT
    # obligations crud/leasing.py:create_agreement creates alongside its own
    # legacy Obligation pair; one with occupancy_id set instead is a
    # recurring RENT obligation crud/occupancy.py:generate_next_rent_obligation
    # creates the same way. Either still has a legacy-domain counterpart
    # whose own PAID status something else depends on (move-in eligibility
    # for the former, next-period generation for the latter); see
    # _sync_legacy_obligation_from_confirmation's own docstring for why
    # this is a one-way status sync, never a real payment dispatch through
    # the old domain.
    if obligation.status == "CONFIRMED" and (obligation.agreement_id is not None or obligation.occupancy_id is not None):
        _sync_legacy_obligation_from_confirmation(db, obligation)


def _recompute_single_or_joint_status(db: Session, obligation: RentalPaymentObligation) -> None:
    if obligation.payer_allocations and _recompute_joint_obligation_status(db, obligation):
        return

    latest_record = db.scalar(
        select(RentalPaymentRecord)
        .where(RentalPaymentRecord.obligation_id == obligation.id)
        .order_by(RentalPaymentRecord.created_at.desc())
        .limit(1)
    )
    if latest_record is None:
        # ZR-PAY-LINK-003 Section 16: PAYMENT_SESSION_STARTED -- an online
        # payment session in flight takes priority over the plain due-date
        # derivation below, both so the tenant sees it's actually in
        # progress and so canMarkPaid-style UI checks (only ever true for
        # UPCOMING/DUE/OVERDUE) stop offering a second, conflicting payment
        # action while one is already underway. Deferred import, same
        # cross-module-boundary discipline as resolve_rent_recipient_party_id
        # below.
        from app.crud.external_payment_session import get_latest_session_for_obligation

        latest_session = get_latest_session_for_obligation(db, obligation.id)
        if latest_session is not None and latest_session.status == "STARTED":
            obligation.status = "PAYMENT_SESSION_STARTED"
            return

        obligation.status = "UPCOMING" if obligation.due_date > date.today() else (
            "OVERDUE" if obligation.due_date < date.today() else "DUE"
        )
        return

    if latest_record.status == "PAYER_RECORDED":
        obligation.status = "RECIPIENT_CONFIRMATION_PENDING"
    else:
        # CONFIRMED / PARTIALLY_PAID / DISPUTED / REVERSED all mirror the
        # record's own state directly -- each is already the exact display
        # status ZR-PAY-LINK-003 Section 16 names.
        obligation.status = latest_record.status


def _sync_legacy_obligation_from_confirmation(db: Session, obligation: RentalPaymentObligation) -> None:
    """The bridge between this non-custodial domain and the legacy
    custody-based one (models/finance.py:Obligation). Two downstream
    effects still depend on THAT domain's Obligation.status reaching PAID,
    and neither has been (or should be) rewired to read this domain
    directly -- rewriting either would touch its own blast radius for no
    benefit, when a narrow one-way status sync accomplishes the same
    outcome:
    - agreement_id case: move-in eligibility (crud/activation_gate.py) and
      Agreement.status reaching SIGNED (crud/leasing.py:
      confirm_agreement_payment).
    - occupancy_id case (recurring RENT): next period's rent obligation
      generation (services/booking_orchestrator.py:generate_downstream_rent
      -> crud/occupancy.py:generate_next_rent_obligation), or every
      subsequent month would simply stop being generated the moment a
      tenant starts paying rent through this rail instead of the legacy one.

    Deliberately never dispatches a real payment through the legacy domain
    -- no ledger entry, no SimulatedPayment/PaymentAllocation is created,
    only the one matching legacy Obligation row is marked PAID directly,
    the same 'terminal state set explicitly elsewhere, not derived from
    allocations' carve-out crud/finance.py:recompute_obligation_status
    already gives WAIVED/FAILED. Matches the EARLIEST legacy Obligation of
    that type for the same scope (agreement or occupancy) -- a later
    deposit-top-up amendment (crud/leasing.py:
    _generate_deposit_topup_obligation) can add a second, unrelated
    DEPOSIT Obligation to the same agreement with no RentalPaymentObligation
    counterpart at all; that one is never touched here and keeps using the
    legacy payment rail, matching the frontend's own earliest-of-type
    matching rule."""
    from app.crud.finance import ensure_deposit_record_for_paid_obligation
    from app.models.finance import Obligation as LegacyObligation
    from app.services import booking_orchestrator

    scope_column = LegacyObligation.agreement_id if obligation.agreement_id is not None else LegacyObligation.occupancy_id
    scope_value = obligation.agreement_id if obligation.agreement_id is not None else obligation.occupancy_id

    legacy_obligation = db.scalar(
        select(LegacyObligation)
        .where(scope_column == scope_value, LegacyObligation.obligation_type == obligation.obligation_type)
        .order_by(LegacyObligation.id)
        .limit(1)
    )
    if legacy_obligation is None or legacy_obligation.status in ("PAID", "WAIVED", "FAILED", "REFUNDED"):
        return

    legacy_obligation.status = "PAID"
    db.flush()
    ensure_deposit_record_for_paid_obligation(db, legacy_obligation)

    log_audit_event(
        db, None, "rental_payment.legacy_obligation_synced", "obligation", str(legacy_obligation.id),
        reason=f"Synced from rental_payment_obligation {obligation.id} reaching CONFIRMED",
    )
    emit_event(
        db, "rental_payment.legacy_obligation_synced", "rental_payment_obligation", str(obligation.id),
        {"legacyObligationId": legacy_obligation.id}, new_state="PAID",
    )
    db.commit()

    if obligation.agreement_id is not None:
        # Same Booking Orchestrator boundary crud/finance.py:confirm_payment
        # itself goes through (ZR-ENG-CLR-005 AC-16) -- reuses the exact same,
        # already-idempotent agreement-activation path (Agreement -> SIGNED,
        # pending-move-in Occupancy creation, version freeze, notifications)
        # rather than reimplementing any of it here.
        booking_orchestrator.confirm_downstream_agreements(db, [legacy_obligation])
    else:
        # Mirrors generate_downstream_rent's own best-effort placement
        # (services/booking_orchestrator.py) -- a failure to generate the
        # next period must never undo or fail this already-committed
        # confirmation. admin=None: this is the cross-domain bridge, not an
        # admin action -- see generate_next_rent_obligation's own docstring.
        from app.crud.occupancy import generate_next_rent_obligation

        try:
            generate_next_rent_obligation(db, legacy_obligation.occupancy, admin=None)
        except HTTPException:
            pass


def get_obligation_or_404(db: Session, obligation_id: int) -> RentalPaymentObligation:
    obligation = db.get(RentalPaymentObligation, obligation_id)
    if not obligation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rental payment obligation not found")
    return obligation


def get_record_or_404(db: Session, record_id: int) -> RentalPaymentRecord:
    record = db.get(RentalPaymentRecord, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rental payment record not found")
    return record


def list_payment_evidence_for_record(db: Session, record_id: int) -> list[EvidenceArtifact]:
    """ZR-PAY-002 Section 5.1 '[View document]' -- the list a UI needs before
    it can offer any individual evidence artifact for download."""
    return list(
        db.scalars(
            select(EvidenceArtifact)
            .where(
                EvidenceArtifact.related_entity_type == "rental_payment_record",
                EvidenceArtifact.related_entity_id == str(record_id),
                EvidenceArtifact.deleted_at.is_(None),
            )
            .order_by(EvidenceArtifact.created_at.desc())
        )
    )


def build_record_timeline(
    db: Session, record: RentalPaymentRecord, *, limit: int = 20, offset: int = 0,
) -> tuple[list[RentalTransactionTimelineEntryRead], int]:
    """ZR-PAY-LINK-003 Section 19 GET /rental-payment-records/{id}/timeline
    -- same merge-existing-DomainEvent-rows-into-one-chronological-view
    technique as crud/rental_transaction_record.py:_timeline_for, scoped to
    one record instead of a whole occupancy. A dispute's own resolve event
    is emitted against the dispute's resource id, not the record's (see
    resolve_dispute below), so each of record.disputes needs its own pair
    too -- corrections need no separate pair, theirs is already emitted
    against the record (see append_correction/tenant_correct_own_record).
    Paginated, same limit/offset/total shape as
    crud/listing.py:list_public_listings -- a long-lived record's timeline
    can otherwise grow without bound."""
    resource_pairs: list[tuple[str, str]] = [("rental_payment_record", str(record.id))]
    for dispute in record.disputes:
        resource_pairs.append(("rental_payment_dispute", str(dispute.id)))

    conditions = [
        (DomainEvent.resource_type == resource_type) & (DomainEvent.resource_id == resource_id)
        for resource_type, resource_id in resource_pairs
    ]
    query = select(DomainEvent).where(or_(*conditions))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = list(db.scalars(query.order_by(DomainEvent.occurred_at).limit(limit).offset(offset)))
    items = [
        RentalTransactionTimelineEntryRead(
            timestamp=event.occurred_at, source=event.resource_type.upper(), event_type=event.event_type, detail=event.payload,
        )
        for event in events
    ]
    return items, total


def _obligations_for_tenant_base_query(guest_id: str):
    """ZR-PAY-LINK-003 Section 15: a guest sees an obligation either as its
    ordinary sole tenant_guest_id, or as one of its allocated co-payers
    (joint tenancy) -- never only the former, or a co-payer would never see
    their own share at all."""
    return (
        select(RentalPaymentObligation)
        .outerjoin(RentalPaymentAllocation, RentalPaymentAllocation.obligation_id == RentalPaymentObligation.id)
        .where(
            or_(
                RentalPaymentObligation.tenant_guest_id == guest_id,
                RentalPaymentAllocation.payer_guest_id == guest_id,
            )
        )
        .distinct()
    )


def list_obligations_for_tenant(db: Session, guest_id: str, *, obligation_type: str | None = None) -> list[RentalPaymentObligation]:
    """ZR-PAY-002 Section 7's '[All] [Rent] [Deposit] [Other]' filter --
    server-side so a large payment history doesn't have to ship every row
    to the client to filter. Unbounded -- used internally (e.g. notification
    fan-out) wherever every matching obligation is genuinely needed;
    list_obligations_for_tenant_page below is the paginated variant the GET
    /obligations route itself uses."""
    query = _obligations_for_tenant_base_query(guest_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    return list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc())))


def list_obligations_for_recipient(db: Session, party_id: int, *, obligation_type: str | None = None) -> list[RentalPaymentObligation]:
    """Unbounded -- see list_obligations_for_tenant's own docstring above;
    list_obligations_for_recipient_page is the paginated variant."""
    query = select(RentalPaymentObligation).where(RentalPaymentObligation.recipient_party_id == party_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    return list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc())))


def list_obligations_for_tenant_page(
    db: Session, guest_id: str, *, obligation_type: str | None = None, agreement_id: int | None = None,
    occupancy_id: int | None = None, limit: int = 20, offset: int = 0,
) -> tuple[list[RentalPaymentObligation], int]:
    """Paginated counterpart of list_obligations_for_tenant, same
    limit/offset/total shape as crud/listing.py:list_public_listings --
    used by GET /obligations (tenant view) only. agreement_id is how a
    caller (e.g. the agreement-signing payment screen) scopes down to the
    one agreement's own RENT/DEPOSIT pair; occupancy_id is the same for the
    recurring-rent case (the 'My Rentals' ongoing dashboard) -- same
    '[All] [Rent] [Deposit] [Other]' server-side-filter discipline as
    obligation_type."""
    query = _obligations_for_tenant_base_query(guest_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    if agreement_id is not None:
        query = query.where(RentalPaymentObligation.agreement_id == agreement_id)
    if occupancy_id is not None:
        query = query.where(RentalPaymentObligation.occupancy_id == occupancy_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc()).limit(limit).offset(offset)))
    return items, total


def list_obligations_for_recipient_page(
    db: Session, party_id: int, *, obligation_type: str | None = None, agreement_id: int | None = None,
    limit: int = 20, offset: int = 0,
) -> tuple[list[RentalPaymentObligation], int]:
    """Paginated counterpart of list_obligations_for_recipient -- used by
    GET recipient/obligations only. agreement_id lets the host's own offer/
    agreement view (HostOfferAgreementPanel.tsx) scope down to just that
    one agreement's payment progress, same as the tenant-side filter."""
    query = select(RentalPaymentObligation).where(RentalPaymentObligation.recipient_party_id == party_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    if agreement_id is not None:
        query = query.where(RentalPaymentObligation.agreement_id == agreement_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc()).limit(limit).offset(offset)))
    return items, total


def assert_tenant_owns_obligation(obligation: RentalPaymentObligation, guest_id: str) -> None:
    """When obligation.payer_allocations is non-empty (joint tenancy --
    ZR-PAY-LINK-003 Section 15), any allocated co-payer owns it, not just
    tenant_guest_id; the single-payer case (payer_allocations empty, the
    default) keeps today's exact-match check unchanged."""
    if obligation.payer_allocations:
        if guest_id not in {allocation.payer_guest_id for allocation in obligation.payer_allocations}:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This obligation does not belong to you")
        return
    if obligation.tenant_guest_id != guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This obligation does not belong to you")


def assert_party_is_recipient(obligation: RentalPaymentObligation, party_id: int) -> None:
    if obligation.recipient_party_id != party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not the authorized recipient for this obligation")


def mark_paid(
    db: Session, guest: Guest, obligation: RentalPaymentObligation, *, amount: float, currency: str,
    declared_date: date, payment_method_category: str, external_reference: str = "", correlation_id: str = "",
) -> RentalPaymentRecord:
    """ZR-PAY-002 Section 4.3/6/A3/12.2 POST /payments/obligations/{id}/mark-paid.
    'Tenant action only' -- creates a declaration, never an authoritative
    settlement state. The canonical success message ('Payment recorded...
    the recipient may still need to confirm receipt') is the frontend's own
    copy; this call only ever returns the record with provenance=
    TENANT_DECLARATION so the UI can never accidentally render it as 'Payment
    successful' (Section 4.3's explicit prohibition)."""
    assert_tenant_owns_obligation(obligation, guest.id)
    if obligation.status in OBLIGATION_BLOCKED_FOR_MARK_PAID_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is {obligation.status.lower()} and cannot be marked paid")
    if payment_method_category not in RENTAL_PAYMENT_METHOD_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"paymentMethodCategory must be one of {RENTAL_PAYMENT_METHOD_CATEGORIES}")

    record = RentalPaymentRecord(
        obligation_id=obligation.id, status="PAYER_RECORDED", provenance="TENANT_DECLARATION",
        declared_amount=_round2(amount), declared_currency=currency, declared_date=declared_date,
        payment_method_category=payment_method_category, external_reference=external_reference,
        declared_by_guest_id=guest.id,
    )
    db.add(record)
    db.flush()
    recompute_obligation_status(db, obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(db, None, "rental_payment.marked_paid", "rental_payment_record", str(record.id), correlation_id)
    emit_event(
        db, "rental_payment.marked_paid", "rental_payment_record", str(record.id),
        {"obligationId": obligation.id, "amount": float(amount), "currency": currency},
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id, new_state="PAYER_RECORDED",
    )
    db.commit()

    notif_crud.notify_user_by_party(
        db, obligation.recipient_party_id,
        title="Payment marked as made",
        message="Payment marked as made -- review and confirm receipt.",
        notification_type="rental_payment.marked_paid",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    return record


async def _store_payment_evidence(
    db: Session, record: RentalPaymentRecord, file: UploadFile, *, uploaded_by_user_id: int | None,
    correlation_id: str = "",
) -> EvidenceArtifact:
    """Shared upload path -- reuses the dispute-evidence pipeline (malware-
    relevant content-type sniffing, size limit, private storage, sha256
    hash) and the shared EvidenceArtifact vault (Section 12.1 explicitly
    allows sharing generic evidence utilities across domains), same async
    shape as crud/dispute_evidence.py:upload_evidence."""
    stored_filename, original_filename, content_type, size_bytes, sha256_hash = await save_dispute_evidence_file(file)

    artifact = EvidenceArtifact(
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
        stored_filename=stored_filename, original_filename=original_filename, content_type=content_type,
        file_size=size_bytes, sha256_hash=sha256_hash, uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    log_audit_event(
        db, None, "rental_payment.evidence_uploaded", "rental_payment_record", str(record.id), correlation_id,
    )
    emit_event(
        db, "rental_payment.evidence_uploaded", "rental_payment_record", str(record.id),
        {"artifactId": artifact.id}, correlation_id=correlation_id,
    )
    db.commit()
    return artifact


async def upload_payment_evidence(
    db: Session, record: RentalPaymentRecord, guest: Guest, file: UploadFile, *, correlation_id: str = "",
) -> EvidenceArtifact:
    """ZR-PAY-002 Section 4.3/13/A8: 'Proof of payment [Upload] Optional' --
    the tenant's own upload, for their own declaration."""
    if record.declared_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This record does not belong to you")
    return await _store_payment_evidence(db, record, file, uploaded_by_user_id=guest.user_account_id, correlation_id=correlation_id)


async def upload_payment_evidence_as_recipient(
    db: Session, record: RentalPaymentRecord, party: Party, file: UploadFile, *, correlation_id: str = "",
) -> EvidenceArtifact:
    """ZR-PAY-002 Section 11: 'Upload payment evidence -- Landlord/Agent:
    Controlled.' The authorized recipient's own evidence (e.g. a bank
    statement supporting a discrepancy report), never a substitute for the
    tenant's own declaration evidence."""
    assert_party_is_recipient(record.obligation, party.id)
    host_user = get_user_by_party_id(db, party.id)
    return await _store_payment_evidence(
        db, record, file, uploaded_by_user_id=host_user.id if host_user else None, correlation_id=correlation_id,
    )


def log_evidence_access(
    db: Session, artifact: EvidenceArtifact, *, actor_kind: str, actor_id: str, correlation_id: str = "",
) -> None:
    """ZR-PAY-002 Section 7.1: record detail must retain 'evidence metadata
    and access history' -- this is that history, one append-only AuditEvent
    row per view/download, never a mutation of the artifact itself."""
    log_audit_event(
        db, None, "rental_payment.evidence_accessed", "evidence_artifact", str(artifact.id), correlation_id,
        reason=f"accessed_by={actor_kind}:{actor_id}",
    )
    db.commit()


def report_discrepancy(
    db: Session, *, record: RentalPaymentRecord, reason_code: str, details: str = "",
    reported_by_guest_id: str | None = None, reported_by_party_id: int | None = None, correlation_id: str = "",
) -> RentalPaymentDispute:
    """ZR-PAY-002 Section 5.2/6/11: either the tenant or the authorized
    recipient may report a discrepancy. 'Changes the record status but does
    not trigger a Zoiko Rooms refund' -- there is no refund concept in this
    domain at all to trigger."""
    if reason_code not in RENTAL_PAYMENT_DISCREPANCY_REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {RENTAL_PAYMENT_DISCREPANCY_REASONS}")
    if bool(reported_by_guest_id) == bool(reported_by_party_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Exactly one of guest or party must report the discrepancy")

    dispute = RentalPaymentDispute(
        record_id=record.id, reason_code=reason_code, details=details,
        reported_by_guest_id=reported_by_guest_id, reported_by_party_id=reported_by_party_id,
    )
    db.add(dispute)
    record.status = "DISPUTED"
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(dispute)

    log_audit_event(
        db, None, "rental_payment.disputed", "rental_payment_dispute", str(dispute.id), correlation_id, reason=reason_code,
    )
    emit_event(
        db, "rental_payment.disputed", "rental_payment_record", str(record.id),
        {"disputeId": dispute.id, "reasonCode": reason_code}, correlation_id=correlation_id, new_state="DISPUTED",
    )
    db.commit()

    # ZR-PAY-002 Section 14: notify whichever side did NOT report it --
    # never the reporter themselves.
    obligation = record.obligation
    if reported_by_party_id != obligation.recipient_party_id:
        notif_crud.notify_user_by_party(
            db, obligation.recipient_party_id, title="Payment discrepancy reported",
            message="A discrepancy has been reported on this payment record. Review the details and evidence.",
            notification_type="rental_payment.disputed",
            related_entity_type="rental_payment_dispute", related_entity_id=str(dispute.id),
        )
    if reported_by_guest_id != obligation.tenant_guest_id:
        notif_crud.notify_user_by_guest(
            db, obligation.tenant, title="Payment discrepancy reported",
            message="A discrepancy has been reported on this payment record. Review the details and evidence.",
            notification_type="rental_payment.disputed",
            related_entity_type="rental_payment_dispute", related_entity_id=str(dispute.id),
        )
    return dispute


def confirm_receipt(
    db: Session, party: Party, record: RentalPaymentRecord, *, amount: float | None = None, note: str = "",
    correlation_id: str = "",
) -> RentalPaymentRecord:
    """ZR-PAY-002 Section 5.1/6/11/A4: 'Authorized recipient confirms
    receipt' only -- never the tenant, never an admin substituting for the
    recipient outside an explicit correction. 'Does not certify legal
    sufficiency, does not satisfy a disputed obligation automatically, does
    not make Zoiko Rooms the payment processor.'

    Section 6: 'PARTIALLY_PAID -- Confirmed amount is less than obligation.'
    `amount` defaults to what the tenant declared (an ordinary full
    confirmation) -- passing a lesser amount is how the recipient records
    that they only actually received part of what was declared; it can
    never exceed the declared amount (that would not be a confirmation of
    this declaration at all)."""
    assert_party_is_recipient(record.obligation, party.id)
    if record.status not in RECORD_CONFIRMABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    declared = _round2(float(record.declared_amount))
    confirmed = _round2(amount) if amount is not None else declared
    if confirmed <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmed amount must be greater than zero")
    if confirmed > declared:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmed amount cannot exceed the declared amount")
    new_status = "CONFIRMED" if confirmed >= declared else "PARTIALLY_PAID"

    record.status = new_status
    record.provenance = "RECIPIENT_CONFIRMATION"
    record.confirmed_by_party_id = party.id
    record.confirmed_amount = confirmed
    record.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, None, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id), correlation_id, reason=note,
    )
    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": record.obligation_id, "confirmedAmount": confirmed},
        correlation_id=correlation_id, actor_kind="party", actor_id=str(party.id), new_state=new_status,
    )
    db.commit()

    message = (
        "Payment confirmed by the recipient." if new_status == "CONFIRMED"
        else f"The recipient confirmed receipt of {record.declared_currency} {confirmed:.2f} -- "
             f"less than the {record.declared_currency} {declared:.2f} declared."
    )
    notif_crud.notify_user_by_guest(
        db, record.obligation.tenant, title="Payment confirmed",
        message=message,
        notification_type="rental_payment.receipt_confirmed",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    return record


def admin_confirm_receipt(
    db: Session, admin: AdminUser, record: RentalPaymentRecord, *, reason: str, correlation_id: str = "",
) -> RentalPaymentRecord:
    """ZR-PAY-002 Section 11: 'Confirm receipt -- Admin/Support: No, except
    explicit correction workflow.' A narrow, reasoned override for when the
    authorized recipient genuinely cannot act (e.g. account access lost) --
    never a routine substitute for confirm_receipt above. provenance stays
    ADMIN_CORRECTION (never RECIPIENT_CONFIRMATION) so the record detail
    never overstates this as the recipient's own action (Section 6.1's
    display rule), and the override itself is captured as a
    RentalPaymentCorrection, same append-only discipline as
    append_correction."""
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required")
    if record.status not in RECORD_CONFIRMABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    previous_status = record.status
    db.add(RentalPaymentCorrection(
        record_id=record.id, field_name="status", previous_value=previous_status, new_value="CONFIRMED",
        reason=reason, actor_admin_id=admin.id,
    ))
    record.status = "CONFIRMED"
    record.provenance = "ADMIN_CORRECTION"
    record.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, admin, "rental_payment.receipt_confirmed_by_admin", "rental_payment_record", str(record.id), correlation_id,
        reason=reason, before_state=previous_status, after_state="CONFIRMED",
    )
    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": record.obligation_id, "overriddenByAdmin": True},
        correlation_id=correlation_id, new_state="CONFIRMED",
    )
    db.commit()

    notif_crud.notify_user_by_guest(
        db, record.obligation.tenant, title="Payment confirmed",
        message="Payment confirmed by the recipient.",
        notification_type="rental_payment.receipt_confirmed",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    return record


def confirm_receipt_as_provider(
    db: Session, admin: AdminUser, record: RentalPaymentRecord, *, provider_reference: str, reason: str,
    correlation_id: str = "",
) -> RentalPaymentRecord:
    """ZR-PAY-002 Section 6: status CONFIRMED via PROVIDER_CONFIRMATION
    provenance -- 'Integrated provider supplies authoritative confirmation.
    Allowed source/transition: Verified provider event.'
    crud/external_payment_session.py:record_provider_payment_success is now
    the real, automated path for an obligation actually paid through the
    external-provider rail (a webhook or self-heal read, not an admin
    action). This function stays as the admin-only manual-reconciliation
    fallback: a restricted admin recording an authoritative confirmation
    they obtained some other way (a bank reconciliation, the provider's own
    dashboard for a payment that fell outside the automated session flow),
    never a routine substitute for either the recipient's own confirmation
    or the automated one. provenance is PROVIDER_CONFIRMATION, never
    RECIPIENT_CONFIRMATION or ADMIN_CORRECTION -- Section 6.1's display rule
    requires the record detail to expose exactly which of the three this
    was."""
    if not provider_reference.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A provider reference is required")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required")
    if record.status not in RECORD_CONFIRMABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    previous_status = record.status
    record.status = "CONFIRMED"
    record.provenance = "PROVIDER_CONFIRMATION"
    record.provider_reference = provider_reference
    record.confirmed_amount = _round2(float(record.declared_amount))
    record.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, admin, "rental_payment.receipt_confirmed_by_provider", "rental_payment_record", str(record.id), correlation_id,
        reason=reason, before_state=previous_status, after_state="CONFIRMED",
    )
    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": record.obligation_id, "providerReference": provider_reference},
        correlation_id=correlation_id, new_state="CONFIRMED",
    )
    db.commit()

    notif_crud.notify_user_by_guest(
        db, record.obligation.tenant, title="Payment confirmed",
        message="Payment confirmed by an integrated payment provider.",
        notification_type="rental_payment.receipt_confirmed",
        related_entity_type="rental_payment_record", related_entity_id=str(record.id),
    )
    return record


def resolve_dispute(
    db: Session, admin: AdminUser, dispute: RentalPaymentDispute, *, resolution_notes: str, correlation_id: str = "",
) -> RentalPaymentDispute:
    if dispute.status == DISPUTE_RESOLVED_STATUS:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dispute is already resolved")
    dispute.status = DISPUTE_RESOLVED_STATUS
    dispute.resolved_by_admin_id = admin.id
    dispute.resolved_at = datetime.now(timezone.utc)
    dispute.resolution_notes = resolution_notes
    db.commit()
    db.refresh(dispute)

    log_audit_event(
        db, admin, "rental_payment.dispute_resolved", "rental_payment_dispute", str(dispute.id), correlation_id,
        reason=resolution_notes,
    )
    emit_event(
        db, "rental_payment.dispute_resolved", "rental_payment_dispute", str(dispute.id), {},
        correlation_id=correlation_id, new_state="RESOLVED",
    )
    db.commit()
    return dispute


def reverse_record(
    db: Session, admin: AdminUser, record: RentalPaymentRecord, *, reason: str, correlation_id: str = "",
) -> RentalPaymentRecord:
    """ZR-PAY-002 Section 6: REVERSED -- 'Previously confirmed payment was
    returned/reversed. Recipient/provider evidence.' A considered admin
    correction once evidence supports it, not an automatic transition from
    a discrepancy report -- see models/rental_payment.py's own status
    docstring."""
    if record.status not in RECORD_REVERSIBLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a confirmed record can be reversed")

    db.add(RentalPaymentCorrection(
        record_id=record.id, field_name="status", previous_value=record.status, new_value="REVERSED",
        reason=reason, actor_admin_id=admin.id,
    ))
    record.status = "REVERSED"
    record.provenance = "ADMIN_CORRECTION"
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, admin, "rental_payment.reversed", "rental_payment_record", str(record.id), correlation_id, reason=reason,
    )
    emit_event(
        db, "rental_payment.reversed", "rental_payment_record", str(record.id), {},
        correlation_id=correlation_id, new_state="REVERSED",
    )
    db.commit()
    return record


def append_correction(
    db: Session, admin: AdminUser, record: RentalPaymentRecord, *, field_name: str, new_value: str, reason: str,
    correlation_id: str = "",
) -> RentalPaymentCorrection:
    """ZR-PAY-002 Section 7.2/A10/11: 'Append corrective event: Restricted +
    reason.' The correction row IS the append-only history; the live field
    is then updated to new_value so the record reflects the corrected fact
    going forward -- the original value is never lost, only ever superseded
    by an additive event."""
    if field_name not in CORRECTABLE_RECORD_FIELDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"fieldName must be one of {CORRECTABLE_RECORD_FIELDS}")

    previous_value = str(getattr(record, field_name))
    correction = RentalPaymentCorrection(
        record_id=record.id, field_name=field_name, previous_value=previous_value, new_value=new_value,
        reason=reason, actor_admin_id=admin.id,
    )
    db.add(correction)

    if field_name in ("declared_amount",):
        setattr(record, field_name, _round2(float(new_value)))
    elif field_name == "declared_date":
        setattr(record, field_name, date.fromisoformat(new_value))
    else:
        setattr(record, field_name, new_value)
    db.commit()
    db.refresh(correction)

    log_audit_event(
        db, admin, "rental_payment.corrected", "rental_payment_record", str(record.id), correlation_id,
        reason=reason, before_state=previous_value, after_state=new_value,
    )
    emit_event(
        db, "rental_payment.corrected", "rental_payment_record", str(record.id),
        {"fieldName": field_name, "previousValue": previous_value, "newValue": new_value}, correlation_id=correlation_id,
    )
    db.commit()
    return correction


def tenant_correct_own_record(
    db: Session, guest: Guest, record: RentalPaymentRecord, *, field_name: str, new_value: str, reason: str = "",
    correlation_id: str = "",
) -> RentalPaymentCorrection:
    """ZR-PAY-002 Section 11: 'Append corrective event -- Tenant:
    Controlled.' Narrow on every axis: only the tenant's OWN record, only a
    clerical field (never amount/date -- see TENANT_CORRECTABLE_RECORD_FIELDS'
    own docstring), and only before the recipient has acted on it
    (PAYER_RECORDED) -- once RECIPIENT_CONFIRMATION_PENDING resolves to
    CONFIRMED/DISPUTED, only an admin correction may touch it."""
    if record.declared_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This record does not belong to you")
    if record.status not in RECORD_TENANT_CORRECTABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "This record can no longer be self-corrected -- ask an admin to append a correction")
    if field_name not in TENANT_CORRECTABLE_RECORD_FIELDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"fieldName must be one of {TENANT_CORRECTABLE_RECORD_FIELDS}")

    previous_value = str(getattr(record, field_name))
    correction = RentalPaymentCorrection(
        record_id=record.id, field_name=field_name, previous_value=previous_value, new_value=new_value,
        reason=reason, actor_guest_id=guest.id,
    )
    db.add(correction)
    setattr(record, field_name, new_value)
    db.commit()
    db.refresh(correction)

    log_audit_event(
        db, None, "rental_payment.corrected", "rental_payment_record", str(record.id), correlation_id,
        reason=reason or "tenant self-correction", before_state=previous_value, after_state=new_value,
    )
    emit_event(
        db, "rental_payment.corrected", "rental_payment_record", str(record.id),
        {"fieldName": field_name, "previousValue": previous_value, "newValue": new_value},
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id,
    )
    db.commit()
    return correction


def recipient_update_own_open_dispute(
    db: Session, party: Party, dispute: RentalPaymentDispute, *, reason_code: str | None = None, details: str | None = None,
    correlation_id: str = "",
) -> RentalPaymentDispute:
    """ZR-PAY-002 Section 11: 'Append corrective event -- Landlord/Agent:
    Controlled.' Only the recipient's OWN still-OPEN dispute -- once an
    admin resolves it, the record is final and only an admin correction may
    revisit it. Direct field mutation, not a RentalPaymentCorrection row:
    an OPEN dispute is not yet the 'confirmed financial record' Section
    7.2's append-only rule protects (resolve_dispute already mutates this
    same row directly once RESOLVED, for the identical reason)."""
    if dispute.reported_by_party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This dispute does not belong to you")
    if dispute.status != DISPUTE_OPEN_STATUS:
        raise HTTPException(status.HTTP_409_CONFLICT, "This dispute is already resolved and can no longer be edited")
    if reason_code is not None and reason_code not in RENTAL_PAYMENT_DISCREPANCY_REASONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"reasonCode must be one of {RENTAL_PAYMENT_DISCREPANCY_REASONS}")

    if reason_code is not None:
        dispute.reason_code = reason_code
    if details is not None:
        dispute.details = details
    db.commit()
    db.refresh(dispute)

    log_audit_event(db, None, "rental_payment.dispute_updated", "rental_payment_dispute", str(dispute.id), correlation_id)
    db.commit()
    return dispute


def waive_obligation(
    db: Session, admin: AdminUser, obligation: RentalPaymentObligation, *, reason: str, correlation_id: str = "",
) -> RentalPaymentObligation:
    return _set_terminal_obligation_status(db, admin, obligation, "WAIVED", reason, correlation_id)


def cancel_obligation(
    db: Session, admin: AdminUser, obligation: RentalPaymentObligation, *, reason: str, correlation_id: str = "",
) -> RentalPaymentObligation:
    return _set_terminal_obligation_status(db, admin, obligation, "CANCELLED", reason, correlation_id)


def _set_terminal_obligation_status(
    db: Session, admin: AdminUser, obligation: RentalPaymentObligation, new_status: str, reason: str,
    correlation_id: str = "",
) -> RentalPaymentObligation:
    """ZR-PAY-002 Section 6: 'WAIVED/CANCELLED -- Authorized business action
    with reason and audit event.'"""
    if obligation.status in OBLIGATION_TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is already {obligation.status.lower()}")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required")

    obligation.status = new_status
    obligation.waived_reason = reason
    obligation.waived_by_admin_id = admin.id
    obligation.waived_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(obligation)

    log_audit_event(
        db, admin, f"rental_payment.{new_status.lower()}", "rental_payment_obligation", str(obligation.id),
        correlation_id, reason=reason,
    )
    emit_event(
        db, f"rental_payment.{new_status.lower()}", "rental_payment_obligation", str(obligation.id), {},
        correlation_id=correlation_id, new_state=new_status,
    )
    db.commit()
    return obligation


# ---------------------------------------------------------------------------
# ZR-PAY-002 Section 9: payment instruction management and anti-fraud controls
# ---------------------------------------------------------------------------

INSTRUCTION_VERIFICATION_CODE_EXPIRE_MINUTES = 15
INSTRUCTION_MAX_VERIFICATION_ATTEMPTS = 5
# ZR-PAY-002 Section 9.1 step 3's 'recent credential changes' signal, and the
# window for 16.1's own named negative scenario: 'Payment instruction is
# changed immediately after password or MFA reset.'
INSTRUCTION_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS = 24


# ZR-PAY-LINK-003 Section 14.1: 'destination novelty ... and timing' --
# a rolling window for 'multiple account-affecting changes in rapid
# succession', distinct from (and much shorter than) the 24h post-reset
# window above -- that one is specifically about a *credential* change;
# this one is about *this party's own destination-adjacent rows* clustering
# together regardless of what triggered them.
INSTRUCTION_RAPID_SUCCESSION_WINDOW_MINUTES = 60


def _fingerprint_destination(bank_details: dict[str, str]) -> str:
    """SHA-256 hex digest of the FULL structured bank details (stable-sorted
    JSON so key order never affects the hash) -- never the values
    themselves. One-way: lets novelty be checked by hash-equality without
    exposing anything reversible, same never-store-the-full-value
    discipline as account_identifier_last4. Fingerprints the whole dict
    (not just the primary field) so a change to any one field -- e.g. the
    sort code but not the account number -- still counts as a new
    destination."""
    canonical = json.dumps(bank_details, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assess_instruction_change_risk(db: Session, party: Party, *, bank_details: dict[str, str]) -> tuple[bool, str, str]:
    """ZR-PAY-002 Section 9.1 step 3 / ZR-PAY-LINK-003 Section 14.1: 'Risk
    controls evaluate recent credential changes, device/session anomalies
    and high-risk account signals' / 'destination novelty, jurisdiction,
    amount profile and timing.' This build has no device/session-
    fingerprinting or amount-profile infrastructure (honest scope, same
    posture as CONFIRMED_BY_PROVIDER's un-backed taxonomy before it was
    closed) -- but three real signals are checkable today: a recent
    password reset (UserAccount.password_changed_at, api/deps.py already
    uses it to invalidate pre-reset JWTs), a destination never used by this
    party before (destination_fingerprint hash-equality against their own
    past rows), and multiple account-affecting changes clustering together
    (this instruction plus any recent PaymentRecipientAuthority change for
    a room this party receives payment for). Returns
    (is_high_risk, combined_reason, destination_fingerprint) -- the caller
    persists all three."""
    now = datetime.now(timezone.utc)
    reasons: list[str] = []

    host_user = get_user_by_party_id(db, party.id)
    if host_user and host_user.password_changed_at:
        age = now - host_user.password_changed_at
        if age <= timedelta(hours=INSTRUCTION_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS):
            reasons.append("Account password was changed within the last 24 hours")

    fingerprint = _fingerprint_destination(bank_details)

    # Both signals below are about a CHANGE to an already-established
    # connection (Section 14.1 is titled 'High-risk change controls') --
    # neither applies to a party's very first-ever instruction, where an
    # authority-then-instructions sequence close together is the ordinary,
    # expected onboarding flow, not a risk signal, and there is by
    # definition no prior destination to compare novelty against.
    has_prior_instruction = db.scalar(
        select(RentalPaymentInstruction.id).where(RentalPaymentInstruction.party_id == party.id)
    )
    if has_prior_instruction is not None:
        seen_before = db.scalar(
            select(RentalPaymentInstruction.id).where(
                RentalPaymentInstruction.party_id == party.id,
                RentalPaymentInstruction.destination_fingerprint == fingerprint,
            )
        )
        if seen_before is None:
            reasons.append("This destination has not been used by this account before")

        rapid_window_start = now - timedelta(minutes=INSTRUCTION_RAPID_SUCCESSION_WINDOW_MINUTES)
        recent_authority_change = db.scalar(
            select(PaymentRecipientAuthority.id).where(
                PaymentRecipientAuthority.party_id == party.id,
                PaymentRecipientAuthority.created_at >= rapid_window_start,
            )
        )
        if recent_authority_change is not None:
            reasons.append("Payment recipient authority for this account changed within the last hour")

    return bool(reasons), "; ".join(reasons), fingerprint


def _hash_instruction_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode("utf-8")).hexdigest()


def _generate_and_send_instruction_code(db: Session, instruction: RentalPaymentInstruction, party: Party) -> str:
    """Same mailed-one-time-code mechanic as
    crud/payout_beneficiary.py:_generate_and_send_code -- only the hash is
    ever persisted. Best-effort send: a delivery failure must never block
    submission/resend."""
    raw_code = f"{secrets.randbelow(1_000_000):06d}"
    instruction.verification_code_hash = _hash_instruction_code(raw_code)
    instruction.verification_code_expires_at = datetime.now(timezone.utc) + timedelta(minutes=INSTRUCTION_VERIFICATION_CODE_EXPIRE_MINUTES)
    instruction.verification_attempts = 0

    host_user = get_user_by_party_id(db, party.id)
    if host_user:
        send_rental_payment_instruction_verification_code_email(
            host_user.email, host_user.full_name, raw_code, INSTRUCTION_VERIFICATION_CODE_EXPIRE_MINUTES,
        )
    return raw_code


def _notify_affected_tenants_instructions_changed(db: Session, party_id: int) -> None:
    """ZR-PAY-002 Section 9.1 step 6: 'Affected tenants receive an in-product
    notice that payment instructions changed' -- fired at submission
    (PENDING_VERIFICATION), the same defensive-early-warning point the
    spec's own sequence places it at, not deferred until activation."""
    tenant_guest_ids = {
        o.tenant_guest_id for o in list_obligations_for_recipient(db, party_id) if o.status not in OBLIGATION_TERMINAL_STATUSES
    }
    for guest_id in tenant_guest_ids:
        guest = db.get(Guest, guest_id)
        if guest:
            notif_crud.notify_user_by_guest(
                db, guest, title="Payment instructions changed",
                message=(
                    "Your landlord or agent updated the payment details for this rental. Review the new "
                    "information carefully before making your next payment."
                ),
                notification_type="payment_instruction.changed",
                related_entity_type="rental_payment_instruction", related_entity_id=str(party_id),
            )


def submit_rental_payment_instruction(
    db: Session, party: Party, *, method: str, recipient_name: str, country_code: str,
    bank_details: dict[str, str], authorized_recipient_confirmed: bool,
    reference_format: str = "", additional_instructions: str = "", correlation_id: str = "",
) -> tuple[RentalPaymentInstruction, str]:
    """ZR-PAY-002 Section 9/12.2 PUT /payments/instructions/{rentalId} /
    ZR-PAY-LINK-003 Wireframe D. bank_details is validated against
    services/bank_field_schemas.py's country_code-resolved field set (e.g.
    UK sort code + account number, an IBAN, or the generic fallback), then
    Fernet-encrypted as a whole into RentalPaymentInstruction.
    encrypted_bank_details -- see that column's own docstring for why this
    is the one field in this domain that stores the real value rather than
    following the 'last 4 characters only' discipline
    crud/payout_beneficiary.py:submit_payout_beneficiary uses. Only
    account_identifier_last4 (derived from the schema's primary field) and
    a one-way fingerprint of the whole dict are ever exposed/compared in
    the clear."""
    if method not in RENTAL_PAYMENT_METHOD_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"method must be one of {RENTAL_PAYMENT_METHOD_CATEGORIES}")
    recipient_name = recipient_name.strip()
    if not recipient_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Recipient name is required")
    if not authorized_recipient_confirmed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You must confirm these instructions belong to the authorized recipient")

    country_code = country_code.strip().upper()
    bank_details = {k: v.strip() for k, v in bank_details.items()}
    try:
        validate_bank_details(country_code, bank_details, method)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    schema = resolve_bank_field_schema(country_code, method)
    primary_value = bank_details[schema.primary_field_key]
    # Country only means anything alongside real bank routing fields
    # (BANK_TRANSFER) -- persisting it for CASH/CARD/OTHER would just be a
    # confusing leftover from whatever the form happened to have selected.
    if method != "BANK_TRANSFER":
        country_code = ""

    is_high_risk, high_risk_reason, destination_fingerprint = _assess_instruction_change_risk(
        db, party, bank_details=bank_details,
    )
    instruction = RentalPaymentInstruction(
        party_id=party.id, method=method, recipient_name=recipient_name, country_code=country_code,
        account_identifier_last4=primary_value[-4:], destination_fingerprint=destination_fingerprint,
        encrypted_bank_details=encrypt_json(bank_details), authorized_recipient_confirmed=True,
        reference_format=reference_format, additional_instructions=additional_instructions,
        is_high_risk=is_high_risk, high_risk_reason=high_risk_reason,
    )
    db.add(instruction)
    db.flush()
    raw_code = _generate_and_send_instruction_code(db, instruction, party)
    db.commit()
    db.refresh(instruction)

    log_audit_event(
        db, None, "payment_instruction.changed", "rental_payment_instruction", str(instruction.id), correlation_id,
    )
    emit_event(
        db, "payment_instruction.changed", "rental_payment_instruction", str(instruction.id),
        {"partyId": party.id}, correlation_id=correlation_id, actor_kind="party", actor_id=str(party.id),
        new_state="PENDING_VERIFICATION",
    )
    db.commit()

    _notify_affected_tenants_instructions_changed(db, party.id)
    db.commit()
    return instruction, raw_code


def resend_rental_payment_instruction_code(db: Session, instruction: RentalPaymentInstruction, party: Party) -> str:
    if instruction.status != "PENDING_VERIFICATION":
        raise HTTPException(status.HTTP_409_CONFLICT, "This instruction is not awaiting verification")
    raw_code = _generate_and_send_instruction_code(db, instruction, party)
    db.commit()
    return raw_code


def confirm_rental_payment_instruction(
    db: Session, instruction: RentalPaymentInstruction, raw_code: str, *, correlation_id: str = "",
) -> RentalPaymentInstruction:
    """The strong-auth step itself (Section 9.1 steps 2-4 collapsed into one
    verified action, same shape as
    crud/payout_beneficiary.py:confirm_payout_beneficiary). Success
    supersedes whatever instruction was previously ACTIVE for this party --
    a payment-instruction change, never an edit in place (Section 9.1 step
    5: 'no silent overwrite')."""
    if instruction.status != "PENDING_VERIFICATION":
        raise HTTPException(status.HTTP_409_CONFLICT, "This instruction is not awaiting verification")
    if not instruction.verification_code_expires_at or instruction.verification_code_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Verification code has expired -- request a new one")
    if instruction.verification_attempts >= INSTRUCTION_MAX_VERIFICATION_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many incorrect attempts -- request a new code")

    if _hash_instruction_code(raw_code.strip()) != instruction.verification_code_hash:
        instruction.verification_attempts += 1
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect verification code")

    instruction.verified_at = datetime.now(timezone.utc)

    if instruction.is_high_risk:
        # Section 9.1 step 7: strong auth alone isn't enough for a high-risk
        # change -- it still needs a restricted admin's approval before it
        # can supersede the current instruction. The previous ACTIVE row is
        # left untouched here; approve_pending_review_instruction is the
        # only place that supersedes it.
        instruction.status = "PENDING_REVIEW"
        db.commit()
        db.refresh(instruction)

        log_audit_event(
            db, None, "payment_instruction.pending_review", "rental_payment_instruction", str(instruction.id),
            correlation_id,
        )
        emit_event(
            db, "payment_instruction.pending_review", "rental_payment_instruction", str(instruction.id),
            {"partyId": instruction.party_id, "highRiskReason": instruction.high_risk_reason},
            correlation_id=correlation_id, new_state="PENDING_REVIEW",
        )
        db.commit()
        return instruction

    previous = db.scalar(
        select(RentalPaymentInstruction).where(
            RentalPaymentInstruction.party_id == instruction.party_id, RentalPaymentInstruction.status == "ACTIVE",
        )
    )
    if previous:
        previous.status = "SUPERSEDED"

    instruction.status = "ACTIVE"
    db.commit()
    db.refresh(instruction)

    log_audit_event(
        db, None, "payment_instruction.activated", "rental_payment_instruction", str(instruction.id), correlation_id,
    )
    emit_event(
        db, "payment_instruction.activated", "rental_payment_instruction", str(instruction.id),
        {"partyId": instruction.party_id}, correlation_id=correlation_id, new_state="ACTIVE",
    )
    db.commit()
    return instruction


def list_rental_payment_instructions_pending_review(db: Session) -> list[RentalPaymentInstruction]:
    return list(
        db.scalars(
            select(RentalPaymentInstruction)
            .where(RentalPaymentInstruction.status == "PENDING_REVIEW")
            .order_by(RentalPaymentInstruction.created_at)
        )
    )


def approve_pending_review_instruction(
    db: Session, admin: AdminUser, instruction: RentalPaymentInstruction, reason: str = "", correlation_id: str = "",
) -> RentalPaymentInstruction:
    """Section 9.1 step 7's admin side: a restricted admin clears a high-risk
    instruction change to become active. Only now does it supersede whatever
    was previously ACTIVE for this party -- mirrors
    confirm_rental_payment_instruction's own non-high-risk path."""
    if instruction.status != "PENDING_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "This instruction is not awaiting review")

    previous = db.scalar(
        select(RentalPaymentInstruction).where(
            RentalPaymentInstruction.party_id == instruction.party_id, RentalPaymentInstruction.status == "ACTIVE",
        )
    )
    if previous:
        previous.status = "SUPERSEDED"

    instruction.status = "ACTIVE"
    instruction.reviewed_by_admin_id = admin.id
    instruction.reviewed_at = datetime.now(timezone.utc)
    instruction.review_reason = reason
    db.commit()
    db.refresh(instruction)

    log_audit_event(
        db, admin, "payment_instruction.activated", "rental_payment_instruction", str(instruction.id), correlation_id,
        reason=reason,
    )
    emit_event(
        db, "payment_instruction.activated", "rental_payment_instruction", str(instruction.id),
        {"partyId": instruction.party_id}, correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id),
        new_state="ACTIVE",
    )
    db.commit()

    _notify_affected_tenants_instructions_changed(db, instruction.party_id)
    db.commit()
    return instruction


def reject_pending_review_instruction(
    db: Session, admin: AdminUser, instruction: RentalPaymentInstruction, reason: str = "", correlation_id: str = "",
) -> RentalPaymentInstruction:
    """The previously ACTIVE instruction, if any, is left untouched -- a
    rejection means the change never takes effect."""
    if instruction.status != "PENDING_REVIEW":
        raise HTTPException(status.HTTP_409_CONFLICT, "This instruction is not awaiting review")

    instruction.status = "REJECTED"
    instruction.reviewed_by_admin_id = admin.id
    instruction.reviewed_at = datetime.now(timezone.utc)
    instruction.review_reason = reason
    db.commit()
    db.refresh(instruction)

    log_audit_event(
        db, admin, "payment_instruction.rejected", "rental_payment_instruction", str(instruction.id), correlation_id,
        reason=reason,
    )
    emit_event(
        db, "payment_instruction.rejected", "rental_payment_instruction", str(instruction.id),
        {"partyId": instruction.party_id}, correlation_id=correlation_id, actor_kind="admin", actor_id=str(admin.id),
        new_state="REJECTED",
    )
    db.commit()
    return instruction


def get_rental_payment_instruction_or_404(db: Session, instruction_id: int) -> RentalPaymentInstruction:
    instruction = db.get(RentalPaymentInstruction, instruction_id)
    if not instruction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment instruction not found")
    return instruction


def list_rental_payment_instructions_for_party(db: Session, party_id: int) -> list[RentalPaymentInstruction]:
    return list(
        db.scalars(
            select(RentalPaymentInstruction)
            .where(RentalPaymentInstruction.party_id == party_id)
            .order_by(RentalPaymentInstruction.created_at.desc())
        )
    )


def get_active_rental_payment_instruction(db: Session, party_id: int) -> RentalPaymentInstruction | None:
    """ZR-PAY-002 Section 4.2 GET /payments/instructions/{rentalId} (tenant
    view) -- the one row a renter is ever shown for a given obligation's
    recipient."""
    return db.scalar(
        select(RentalPaymentInstruction).where(
            RentalPaymentInstruction.party_id == party_id, RentalPaymentInstruction.status == "ACTIVE",
        )
    )


# ---------------------------------------------------------------------------
# ZR-PAY-002 Section 10/13: evidence legal holds
# ---------------------------------------------------------------------------


def get_evidence_hold_or_404(db: Session, hold_id: int) -> RentalPaymentEvidenceHold:
    hold = db.get(RentalPaymentEvidenceHold, hold_id)
    if not hold:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence hold not found")
    return hold


def list_evidence_holds_for_artifact(db: Session, artifact_id: int) -> list[RentalPaymentEvidenceHold]:
    return list(
        db.scalars(
            select(RentalPaymentEvidenceHold)
            .where(RentalPaymentEvidenceHold.artifact_id == artifact_id)
            .order_by(RentalPaymentEvidenceHold.placed_at.desc())
        )
    )


def place_evidence_legal_hold(
    db: Session, admin: AdminUser, artifact: EvidenceArtifact, *, reason: str, correlation_id: str = "",
) -> RentalPaymentEvidenceHold:
    """ZR-PAY-002 Section 10/13: 'legal hold and deletion exceptions.'
    Blocks services/evidence_retention.py:sweep_expired_evidence from ever
    deleting this artifact's file while ACTIVE, regardless of how far past
    its retention window it is."""
    if artifact.related_entity_type != "rental_payment_record":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This evidence hold applies only to rental payment evidence")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required")
    existing = db.scalar(
        select(RentalPaymentEvidenceHold).where(
            RentalPaymentEvidenceHold.artifact_id == artifact.id, RentalPaymentEvidenceHold.status == "ACTIVE",
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "An active legal hold already exists for this evidence")

    hold = RentalPaymentEvidenceHold(artifact_id=artifact.id, reason=reason, placed_by_admin_id=admin.id)
    db.add(hold)
    db.commit()
    db.refresh(hold)

    log_audit_event(
        db, admin, "rental_payment.evidence_hold_placed", "evidence_artifact", str(artifact.id), correlation_id,
        reason=reason,
    )
    db.commit()
    return hold


def release_evidence_legal_hold(
    db: Session, admin: AdminUser, hold: RentalPaymentEvidenceHold, *, correlation_id: str = "",
) -> RentalPaymentEvidenceHold:
    if hold.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "This hold is not active")
    hold.status = "RELEASED"
    hold.released_by_admin_id = admin.id
    hold.released_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(hold)

    log_audit_event(
        db, admin, "rental_payment.evidence_hold_released", "evidence_artifact", str(hold.artifact_id), correlation_id,
    )
    db.commit()
    return hold
