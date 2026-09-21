"""ZR-PAY-002 Section 2/4/5/6/7: the rental payment record domain's crud
layer. Every function here only ever records a declaration, a confirmation,
a dispute or a correction -- none of them move money, and none of them may
resolve Zoiko Rooms as a payee (A1/A2). Kept independent of
crud/finance.py: no shared calls, no shared tables (models/rental_payment.py's
own module docstring)."""

from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import save_dispute_evidence_file
from app.core.mailer import send_rental_payment_instruction_verification_code_email
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud import notification as notif_crud
from app.crud.user import get_user_by_party_id
from app.models.admin_user import AdminUser
from app.models.evidence_artifact import EvidenceArtifact
from app.models.guest import Guest
from app.models.party import Party
from app.models.rental_payment import (
    RENTAL_PAYMENT_DISCREPANCY_REASONS,
    RENTAL_PAYMENT_METHOD_CATEGORIES,
    RentalPaymentCorrection,
    RentalPaymentDispute,
    RentalPaymentEvidenceHold,
    RentalPaymentInstruction,
    RentalPaymentObligation,
    RentalPaymentRecord,
)

# ZR-PAY-002 Section 11: 'Append corrective event -- Tenant: Controlled,
# Landlord/Agent: Controlled.' Deliberately narrower than
# CORRECTABLE_RECORD_FIELDS below (never declared_amount/declared_date --
# those stay admin-only, A10's heavier protection for the financially
# significant fields) and only while the record is still TENANT_MARKED_PAID
# (own tenant_correct_own_record) or the dispute is still OPEN (own
# recipient_update_own_open_dispute) -- i.e. before the other side has acted
# on it, never after.
TENANT_CORRECTABLE_RECORD_FIELDS = ("payment_method_category", "external_reference")

# ZR-PAY-002 Section 7.2/A10: only these record fields may ever be corrected,
# and only by an admin with a reason -- never an arbitrary attribute set.
# Status corrections go through their own explicit transitions below
# (reverse_record), not through this generic field-correction path.
CORRECTABLE_RECORD_FIELDS = ("declared_amount", "declared_date", "external_reference", "payment_method_category")


def _round2(amount) -> float:
    return round(float(amount), 2)


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


def recompute_obligation_status(db: Session, obligation: RentalPaymentObligation) -> None:
    """The only place RentalPaymentObligation.status is ever assigned from a
    record's own state -- same derived-never-direct discipline as
    models/finance.py:recompute_obligation_status. Waived/cancelled stay
    terminal regardless of any record."""
    if obligation.status in ("WAIVED", "CANCELLED"):
        return

    latest_record = db.scalar(
        select(RentalPaymentRecord)
        .where(RentalPaymentRecord.obligation_id == obligation.id)
        .order_by(RentalPaymentRecord.created_at.desc())
        .limit(1)
    )
    if latest_record is None:
        obligation.status = "UPCOMING" if obligation.due_date > date.today() else (
            "OVERDUE" if obligation.due_date < date.today() else "DUE"
        )
        return

    if latest_record.status == "TENANT_MARKED_PAID":
        obligation.status = "AWAITING_CONFIRMATION"
    else:
        # CONFIRMED_BY_RECIPIENT / CONFIRMED_BY_PROVIDER / PARTIALLY_PAID /
        # DISPUTED / REVERSED all mirror the record's own state directly --
        # each is already the exact display status ZR-PAY-002 Section 6 names.
        obligation.status = latest_record.status


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


def list_obligations_for_tenant(db: Session, guest_id: str, *, obligation_type: str | None = None) -> list[RentalPaymentObligation]:
    """ZR-PAY-002 Section 7's '[All] [Rent] [Deposit] [Other]' filter --
    server-side so a large payment history doesn't have to ship every row
    to the client to filter."""
    query = select(RentalPaymentObligation).where(RentalPaymentObligation.tenant_guest_id == guest_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    return list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc())))


def list_obligations_for_recipient(db: Session, party_id: int, *, obligation_type: str | None = None) -> list[RentalPaymentObligation]:
    query = select(RentalPaymentObligation).where(RentalPaymentObligation.recipient_party_id == party_id)
    if obligation_type is not None:
        query = query.where(RentalPaymentObligation.obligation_type == obligation_type)
    return list(db.scalars(query.order_by(RentalPaymentObligation.due_date.desc())))


def assert_tenant_owns_obligation(obligation: RentalPaymentObligation, guest_id: str) -> None:
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
    if obligation.status in ("WAIVED", "CANCELLED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"This obligation is {obligation.status.lower()} and cannot be marked paid")
    if payment_method_category not in RENTAL_PAYMENT_METHOD_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"paymentMethodCategory must be one of {RENTAL_PAYMENT_METHOD_CATEGORIES}")

    record = RentalPaymentRecord(
        obligation_id=obligation.id, status="TENANT_MARKED_PAID", provenance="TENANT_DECLARATION",
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
        correlation_id=correlation_id, actor_kind="guest", actor_id=guest.id, new_state="TENANT_MARKED_PAID",
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
    if record.status not in ("TENANT_MARKED_PAID", "DISPUTED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    declared = _round2(float(record.declared_amount))
    confirmed = _round2(amount) if amount is not None else declared
    if confirmed <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmed amount must be greater than zero")
    if confirmed > declared:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Confirmed amount cannot exceed the declared amount")
    new_status = "CONFIRMED_BY_RECIPIENT" if confirmed >= declared else "PARTIALLY_PAID"

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
        "Payment confirmed by the recipient." if new_status == "CONFIRMED_BY_RECIPIENT"
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
    if record.status not in ("TENANT_MARKED_PAID", "DISPUTED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    previous_status = record.status
    db.add(RentalPaymentCorrection(
        record_id=record.id, field_name="status", previous_value=previous_status, new_value="CONFIRMED_BY_RECIPIENT",
        reason=reason, actor_admin_id=admin.id,
    ))
    record.status = "CONFIRMED_BY_RECIPIENT"
    record.provenance = "ADMIN_CORRECTION"
    record.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    recompute_obligation_status(db, record.obligation)
    db.commit()
    db.refresh(record)

    log_audit_event(
        db, admin, "rental_payment.receipt_confirmed_by_admin", "rental_payment_record", str(record.id), correlation_id,
        reason=reason, before_state=previous_status, after_state="CONFIRMED_BY_RECIPIENT",
    )
    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": record.obligation_id, "overriddenByAdmin": True},
        correlation_id=correlation_id, new_state="CONFIRMED_BY_RECIPIENT",
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
    """ZR-PAY-002 Section 6: CONFIRMED_BY_PROVIDER -- 'Integrated provider
    supplies authoritative confirmation. Allowed source/transition: Verified
    provider event.' This build has no live, real-time integration with an
    external rent-payment provider to receive that event automatically (the
    honest 'not yet built' limitation models/rental_payment.py's own
    RENTAL_PAYMENT_PROVENANCE docstring already states) -- this is the real
    path that exists today: a restricted admin recording an authoritative
    confirmation they actually obtained from a verified external source
    (the provider's own transaction record, a bank reconciliation), never a
    routine substitute for the recipient's own confirmation. provenance is
    PROVIDER_CONFIRMATION, never RECIPIENT_CONFIRMATION or ADMIN_CORRECTION
    -- Section 6.1's display rule requires the record detail to expose
    exactly which of the three this was."""
    if not provider_reference.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A provider reference is required")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required")
    if record.status not in ("TENANT_MARKED_PAID", "DISPUTED"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A record in status {record.status} cannot be confirmed")

    previous_status = record.status
    record.status = "CONFIRMED_BY_PROVIDER"
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
        reason=reason, before_state=previous_status, after_state="CONFIRMED_BY_PROVIDER",
    )
    emit_event(
        db, "rental_payment.receipt_confirmed", "rental_payment_record", str(record.id),
        {"obligationId": record.obligation_id, "providerReference": provider_reference},
        correlation_id=correlation_id, new_state="CONFIRMED_BY_PROVIDER",
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
    if dispute.status == "RESOLVED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This dispute is already resolved")
    dispute.status = "RESOLVED"
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
    if record.status not in ("CONFIRMED_BY_RECIPIENT", "CONFIRMED_BY_PROVIDER"):
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
    (TENANT_MARKED_PAID) -- once AWAITING_CONFIRMATION resolves to
    CONFIRMED/DISPUTED, only an admin correction may touch it."""
    if record.declared_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This record does not belong to you")
    if record.status != "TENANT_MARKED_PAID":
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
    if dispute.status != "OPEN":
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
    if obligation.status in ("WAIVED", "CANCELLED"):
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


def _assess_instruction_change_risk(db: Session, party: Party) -> tuple[bool, str]:
    """ZR-PAY-002 Section 9.1 step 3: 'Risk controls evaluate recent
    credential changes, device/session anomalies and high-risk account
    signals.' This build has no device/session-fingerprinting or fraud-
    scoring infrastructure to draw on (honest scope, same posture as
    CONFIRMED_BY_PROVIDER's un-backed taxonomy before it was closed) -- but
    UserAccount.password_changed_at already exists (api/deps.py uses it to
    invalidate pre-reset JWTs), so a recent password reset is a real signal
    this can check today."""
    host_user = get_user_by_party_id(db, party.id)
    if host_user and host_user.password_changed_at:
        age = datetime.now(timezone.utc) - host_user.password_changed_at
        if age <= timedelta(hours=INSTRUCTION_RECENT_CREDENTIAL_CHANGE_RISK_WINDOW_HOURS):
            return True, "Account password was changed within the last 24 hours"
    return False, ""


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
        o.tenant_guest_id for o in list_obligations_for_recipient(db, party_id) if o.status not in ("WAIVED", "CANCELLED")
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
    db: Session, party: Party, *, method: str, recipient_name: str, account_identifier: str,
    reference_format: str = "", additional_instructions: str = "", correlation_id: str = "",
) -> tuple[RentalPaymentInstruction, str]:
    """ZR-PAY-002 Section 9/12.2 PUT /payments/instructions/{rentalId}. Only
    `account_identifier`'s last 4 characters are ever persisted -- same
    never-store-the-full-value discipline as
    crud/payout_beneficiary.py:submit_payout_beneficiary, extended here to a
    generic identifier (bank account, IBAN, provider handle, ...) rather
    than assuming a digits-only account number, per Section 10's 'must not
    assume ... IBAN, UPI, BSB, routing numbers'."""
    if method not in RENTAL_PAYMENT_METHOD_CATEGORIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"method must be one of {RENTAL_PAYMENT_METHOD_CATEGORIES}")
    recipient_name = recipient_name.strip()
    account_identifier = account_identifier.strip()
    if not recipient_name or len(account_identifier) < 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Recipient name and account/payment ID (at least 4 characters) are required")

    is_high_risk, high_risk_reason = _assess_instruction_change_risk(db, party)
    instruction = RentalPaymentInstruction(
        party_id=party.id, method=method, recipient_name=recipient_name,
        account_identifier_last4=account_identifier[-4:], reference_format=reference_format,
        additional_instructions=additional_instructions,
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
