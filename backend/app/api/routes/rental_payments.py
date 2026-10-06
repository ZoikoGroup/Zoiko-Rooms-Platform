"""ZR-PAY-002 Section 12.2's recommended API surface for rental payment
records -- obligations, declarations, confirmations, disputes and
corrections. Three routers matching Section 11's permission table: `router`
is the tenant's own view (mark-paid, evidence, disputes), `recipient_router`
is the landlord/agent's own view (confirm-receipt, disputes), `admin_router`
is the restricted correction/resolution surface."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, require_super_admin, require_super_admin_or_payment_staff
from app.core.correlation import get_correlation_id
from app.core.dispute_evidence_uploads import resolve_dispute_evidence_path
from app.core.field_encryption import decrypt_json
from app.crud import payment_connection as payment_connection_crud
from app.crud import rental_payment as rp_crud
from app.crud.audit import log_audit_event
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.services.rental_payment_due_soon import sweep_rental_payment_due_soon
from app.models.admin_user import AdminUser
from app.models.evidence_artifact import EvidenceArtifact
from app.models.guest import Guest
from app.models.party import Party
from app.models.rental_payment import RentalPaymentDispute, RentalPaymentEvidenceHold
from app.models.user_account import UserAccount
from app.schemas.payment_connection import PaymentConnectionRead
from app.schemas.rental_payment import (
    EvidenceArtifactRead,
    RentalPaymentAllocationsCreate,
    RentalPaymentConfirmReceiptRequest,
    RentalPaymentRecordReceiptRequest,
    RentalPaymentCorrectionCreate,
    RentalPaymentCorrectionRead,
    RentalPaymentDisputeCreate,
    RentalPaymentDisputeRead,
    RentalPaymentDisputeAdminRead,
    RentalPaymentDisputeUpdate,
    RentalPaymentEvidenceHoldCreate,
    RentalPaymentEvidenceHoldRead,
    RentalPaymentInstructionConfirm,
    RentalPaymentInstructionRead,
    RentalPaymentInstructionReviewRequest,
    RentalPaymentInstructionSubmit,
    RentalPaymentMarkPaidRequest,
    RentalPaymentObligationRead,
    RentalPaymentObligationsPage,
    RentalPaymentProviderConfirmRequest,
    RentalPaymentRecordRead,
    RentalPaymentReverseRequest,
    RentalPaymentTenantSelfCorrectionCreate,
    RentalPaymentTerminalActionRequest,
)
from app.schemas.rental_transaction_record import RentalPaymentTimelinePage

router = APIRouter(prefix="/api/users/rental-payments", tags=["user-rental-payments"], dependencies=[Depends(get_current_user)])
recipient_router = APIRouter(
    prefix="/api/users/rental-payments/recipient", tags=["recipient-rental-payments"], dependencies=[Depends(get_current_user)],
)
admin_router = APIRouter(prefix="/api/finance/rental-payments", tags=["finance-rental-payments"], dependencies=[Depends(get_current_admin)])

# Same pagination ceiling convention as api/routes/public.py:MAX_PUBLIC_LISTINGS_LIMIT.
MAX_RENTAL_PAYMENT_LIST_LIMIT = 100


def _get_own_guest_or_403(db: Session, user: UserAccount) -> Guest:
    guest = get_guest_for_user(db, user)
    if not guest:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No guest record for this account")
    return guest


def _get_own_party_or_400(db: Session, user: UserAccount) -> Party:
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    party = db.get(Party, user.party_id)
    if not party:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    return party


def _to_instruction_read(instruction, *, include_bank_details: bool = False) -> RentalPaymentInstructionRead:
    """include_bank_details=True decrypts encrypted_bank_details into the
    real field values -- only ever set at a call site that has already
    verified the caller is authorized to see THIS party's instructions in
    full (the tenant with a due obligation to this recipient, the
    recipient's own view, or restricted super-admin review). False
    (default) matches every other caller's existing masked-only behavior."""
    bank_details = decrypt_json(instruction.encrypted_bank_details) if include_bank_details else None
    return RentalPaymentInstructionRead(
        id=instruction.id, party_id=instruction.party_id, status=instruction.status, method=instruction.method,
        recipient_name=instruction.recipient_name, country_code=instruction.country_code,
        account_identifier_masked=(
            f"******{instruction.account_identifier_last4}" if instruction.account_identifier_last4 else ""
        ), bank_details=bank_details,
        reference_format=instruction.reference_format, additional_instructions=instruction.additional_instructions,
        verified_at=instruction.verified_at, created_at=instruction.created_at,
        is_high_risk=instruction.is_high_risk, high_risk_reason=instruction.high_risk_reason,
        reviewed_at=instruction.reviewed_at, review_reason=instruction.review_reason,
    )


@router.get("/obligations", response_model=RentalPaymentObligationsPage)
def list_my_rental_payment_obligations(
    obligation_type: str | None = None,
    agreement_id: int | None = None,
    occupancy_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_RENTAL_PAYMENT_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 3.1/7/12.2 GET /payments/obligations (tenant view).
    obligation_type is the '[Rent] [Deposit] [Other]' filter (Section 7);
    omitted means 'All'. agreement_id scopes to one agreement's own RENT/
    DEPOSIT pair -- used by the agreement-signing payment screen;
    occupancy_id is the same for the recurring-rent case -- used by the
    ongoing 'My Rentals' dashboard."""
    guest = _get_own_guest_or_403(db, user)
    items, total = rp_crud.list_obligations_for_tenant_page(
        db, guest.id, obligation_type=obligation_type, agreement_id=agreement_id, occupancy_id=occupancy_id,
        limit=limit, offset=offset,
    )
    return RentalPaymentObligationsPage(items=items, limit=limit, offset=offset, total=total, has_more=offset + len(items) < total)


@router.get("/obligations/{obligation_id}", response_model=RentalPaymentObligationRead)
def get_my_rental_payment_obligation(obligation_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    guest = _get_own_guest_or_403(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.assert_tenant_owns_obligation(obligation, guest.id)
    return obligation


@router.get("/obligations/{obligation_id}/connection", response_model=PaymentConnectionRead)
def get_my_rental_payment_obligation_connection(
    obligation_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 3.1 -- the tenant-facing counterpart to
    api/routes/user_hosting.py's own host-facing payment-connection route
    (that one is room-ownership-gated; this one is obligation-ownership-gated
    instead, same access-control shape difference
    api/routes/rental_payments.py's own evidence routes already have between
    the tenant and recipient sides)."""
    guest = _get_own_guest_or_403(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.assert_tenant_owns_obligation(obligation, guest.id)
    room = obligation.room
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This obligation has no linked room to check")
    return payment_connection_crud.get_payment_connection_for_room(db, room)


@router.post("/obligations/{obligation_id}/mark-paid", response_model=RentalPaymentObligationRead, status_code=status.HTTP_201_CREATED)
def post_mark_rental_payment_paid(
    obligation_id: int, payload: RentalPaymentMarkPaidRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 4.3/A3 POST /payments/obligations/{id}/mark-paid."""
    guest = _get_own_guest_or_403(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.mark_paid(
        db, guest, obligation, amount=payload.amount, currency=payload.currency, declared_date=payload.declared_date,
        payment_method_category=payload.payment_method_category, external_reference=payload.external_reference,
        correlation_id=get_correlation_id(request),
    )
    db.refresh(obligation)
    return obligation


def _get_own_record_or_404(db: Session, record_id: int, guest: Guest):
    record = rp_crud.get_record_or_404(db, record_id)
    if record.declared_by_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This record does not belong to you")
    return record


@router.post("/records/{record_id}/evidence", response_model=EvidenceArtifactRead, status_code=status.HTTP_201_CREATED)
async def post_upload_rental_payment_evidence(
    record_id: int, file: UploadFile, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 4.3 'Proof of payment [Upload] Optional.'"""
    guest = _get_own_guest_or_403(db, user)
    record = _get_own_record_or_404(db, record_id, guest)
    return await rp_crud.upload_payment_evidence(db, record, guest, file, correlation_id=get_correlation_id(request))


@router.post("/records/{record_id}/self-correct", response_model=RentalPaymentCorrectionRead, status_code=status.HTTP_201_CREATED)
def post_tenant_self_correct_record(
    record_id: int, payload: RentalPaymentTenantSelfCorrectionCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 11: 'Append corrective event -- Tenant:
    Controlled.' Narrow: own record only, method/reference only, only
    before the recipient has acted -- see
    crud/rental_payment.py:tenant_correct_own_record."""
    guest = _get_own_guest_or_403(db, user)
    record = _get_own_record_or_404(db, record_id, guest)
    return rp_crud.tenant_correct_own_record(
        db, guest, record, field_name=payload.field_name, new_value=payload.new_value, reason=payload.reason,
        correlation_id=get_correlation_id(request),
    )


@router.post("/records/{record_id}/disputes", response_model=RentalPaymentDisputeRead, status_code=status.HTTP_201_CREATED)
def post_tenant_report_discrepancy(
    record_id: int, payload: RentalPaymentDisputeCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 5.2/12.2 POST /payments/records/{id}/disputes (tenant side)."""
    guest = _get_own_guest_or_403(db, user)
    record = _get_own_record_or_404(db, record_id, guest)
    return rp_crud.report_discrepancy(
        db, record=record, reason_code=payload.reason_code, details=payload.details, reported_by_guest_id=guest.id,
        correlation_id=get_correlation_id(request),
    )


@recipient_router.get("/obligations", response_model=RentalPaymentObligationsPage)
def list_recipient_rental_payment_obligations(
    obligation_type: str | None = None,
    agreement_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=MAX_RENTAL_PAYMENT_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 3.2 (landlord/agent view) -- 'Amounts due' /
    'Awaiting confirmation'. Same '[Rent] [Deposit] [Other]' filter as the
    tenant view (Section 7). agreement_id scopes to one agreement's own
    RENT/DEPOSIT pair -- used by the host's offer/agreement payment-progress
    view."""
    party = _get_own_party_or_400(db, user)
    items, total = rp_crud.list_obligations_for_recipient_page(
        db, party.id, obligation_type=obligation_type, agreement_id=agreement_id, limit=limit, offset=offset,
    )
    return RentalPaymentObligationsPage(items=items, limit=limit, offset=offset, total=total, has_more=offset + len(items) < total)


@recipient_router.post("/obligations/{obligation_id}/payer-allocations", response_model=RentalPaymentObligationRead)
def post_rental_payment_obligation_payer_allocations(
    obligation_id: int, payload: RentalPaymentAllocationsCreate,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 15/Wireframe PAY-17: joint-tenancy payer
    allocation -- recipient-set only (never the tenant/payer side), and only
    once, before any payment activity exists against the obligation. See
    crud/rental_payment.py:create_payer_allocations for the full rule set."""
    party = _get_own_party_or_400(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.assert_party_is_recipient(obligation, party.id)
    return rp_crud.create_payer_allocations(
        db, obligation, [(entry.payer_guest_id, entry.allocated_amount) for entry in payload.allocations],
    )


def _get_recipient_record_or_403(db: Session, record_id: int, party: Party):
    record = rp_crud.get_record_or_404(db, record_id)
    rp_crud.assert_party_is_recipient(record.obligation, party.id)
    return record


@recipient_router.post(
    "/obligations/{obligation_id}/record-receipt", response_model=RentalPaymentObligationRead,
    status_code=status.HTTP_201_CREATED,
)
def post_record_receipt_as_recipient(
    obligation_id: int, payload: RentalPaymentRecordReceiptRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """The host marks rent/deposit as received -- paid to them directly,
    outside Zoiko -- without waiting for the renter to record it first.
    Returns the obligation with its new status."""
    party = _get_own_party_or_400(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.record_receipt_as_recipient(
        db, party, obligation, amount=payload.amount, received_date=payload.received_date,
        payment_method_category=payload.payment_method_category, external_reference=payload.external_reference,
        note=payload.note, correlation_id=get_correlation_id(request),
    )
    db.refresh(obligation)
    return obligation


@recipient_router.post("/records/{record_id}/confirm-receipt", response_model=RentalPaymentObligationRead)
def post_confirm_receipt(
    record_id: int, payload: RentalPaymentConfirmReceiptRequest, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 5.1/6/12.2 POST /payments/records/{id}/confirm-receipt.
    payload.amount, when supplied and less than the declared amount,
    produces PARTIALLY_PAID rather than CONFIRMED."""
    party = _get_own_party_or_400(db, user)
    record = _get_recipient_record_or_403(db, record_id, party)
    updated = rp_crud.confirm_receipt(
        db, party, record, amount=payload.amount, note=payload.note, correlation_id=get_correlation_id(request),
    )
    return updated.obligation


@recipient_router.post("/records/{record_id}/disputes", response_model=RentalPaymentDisputeRead, status_code=status.HTTP_201_CREATED)
def post_recipient_report_discrepancy(
    record_id: int, payload: RentalPaymentDisputeCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party = _get_own_party_or_400(db, user)
    record = _get_recipient_record_or_403(db, record_id, party)
    return rp_crud.report_discrepancy(
        db, record=record, reason_code=payload.reason_code, details=payload.details, reported_by_party_id=party.id,
        correlation_id=get_correlation_id(request),
    )


@recipient_router.patch("/disputes/{dispute_id}", response_model=RentalPaymentDisputeRead)
def patch_recipient_own_open_dispute(
    dispute_id: int, payload: RentalPaymentDisputeUpdate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 11: 'Append corrective event -- Landlord/Agent:
    Controlled.' Only the recipient's own still-OPEN dispute -- see
    crud/rental_payment.py:recipient_update_own_open_dispute."""
    party = _get_own_party_or_400(db, user)
    dispute = db.get(RentalPaymentDispute, dispute_id)
    if not dispute:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    return rp_crud.recipient_update_own_open_dispute(
        db, party, dispute, reason_code=payload.reason_code, details=payload.details,
        correlation_id=get_correlation_id(request),
    )


@router.get("/obligations/{obligation_id}/instructions", response_model=RentalPaymentInstructionRead)
def get_my_rental_payment_instructions(obligation_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 4.2/12.2 GET /payments/instructions/{rentalId}
    (tenant view) -- 'Authorized tenancy only.'"""
    guest = _get_own_guest_or_403(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    rp_crud.assert_tenant_owns_obligation(obligation, guest.id)
    # PAY-CFG-06: never show payment details for a recipient whose
    # PAYMENT_RECEIPT authority isn't verified.
    rp_crud.assert_recipient_holds_payment_receipt_authority(db, obligation, obligation.recipient_party_id)

    instruction = rp_crud.get_active_rental_payment_instruction(db, obligation.recipient_party_id)
    if not instruction:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The recipient has not set up payment instructions yet")
    # This tenant has a real due obligation to this recipient -- the whole
    # point of this route is letting them see the real details to pay.
    return _to_instruction_read(instruction, include_bank_details=True)


@recipient_router.get("/instructions", response_model=list[RentalPaymentInstructionRead])
def list_recipient_rental_payment_instructions(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    party = _get_own_party_or_400(db, user)
    return [
        _to_instruction_read(i, include_bank_details=True)
        for i in rp_crud.list_rental_payment_instructions_for_party(db, party.id)
    ]


@recipient_router.put("/instructions", response_model=RentalPaymentInstructionRead, status_code=status.HTTP_201_CREATED)
def put_submit_rental_payment_instruction(
    payload: RentalPaymentInstructionSubmit, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 9/12.2 PUT /payments/instructions/{rentalId}: 'Create
    versioned instruction change; requires high-risk auth controls.' Returns
    PENDING_VERIFICATION -- see the confirm endpoint for the step-up-auth
    step that activates it."""
    party = _get_own_party_or_400(db, user)
    instruction, _raw_code = rp_crud.submit_rental_payment_instruction(
        db, party, method=payload.method, recipient_name=payload.recipient_name,
        country_code=payload.country_code, bank_details=payload.bank_details,
        authorized_recipient_confirmed=payload.authorized_recipient_confirmed,
        reference_format=payload.reference_format,
        additional_instructions=payload.additional_instructions, correlation_id=get_correlation_id(request),
    )
    return _to_instruction_read(instruction, include_bank_details=True)


def _get_own_instruction_or_403(db: Session, instruction_id: int, party: Party):
    instruction = rp_crud.get_rental_payment_instruction_or_404(db, instruction_id)
    if instruction.party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment instruction does not belong to you")
    return instruction


@recipient_router.post("/instructions/{instruction_id}/resend-code")
def post_resend_rental_payment_instruction_code(
    instruction_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party = _get_own_party_or_400(db, user)
    instruction = _get_own_instruction_or_403(db, instruction_id, party)
    rp_crud.resend_rental_payment_instruction_code(db, instruction, party)
    return {"sent": True}


@recipient_router.post("/instructions/{instruction_id}/confirm", response_model=RentalPaymentInstructionRead)
def post_confirm_rental_payment_instruction(
    instruction_id: int, payload: RentalPaymentInstructionConfirm, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 9.1's step-up-auth confirmation -- only this call
    makes a submitted instruction ACTIVE."""
    party = _get_own_party_or_400(db, user)
    instruction = _get_own_instruction_or_403(db, instruction_id, party)
    updated = rp_crud.confirm_rental_payment_instruction(
        db, instruction, payload.code, correlation_id=get_correlation_id(request),
    )
    return _to_instruction_read(updated, include_bank_details=True)


def _record_access_or_403(db: Session, record, user: UserAccount) -> tuple[bool, bool]:
    """Returns (is_tenant, is_recipient) -- either side of the record, never
    an unrelated party (A8)."""
    guest = get_guest_for_user(db, user)
    is_tenant = guest is not None and record.declared_by_guest_id == guest.id
    is_recipient = bool(user.party_id) and record.obligation.recipient_party_id == user.party_id
    if not (is_tenant or is_recipient):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not authorized to view this record's evidence")
    return is_tenant, is_recipient


@router.get("/records/{record_id}/timeline", response_model=RentalPaymentTimelinePage)
def get_rental_payment_record_timeline(
    record_id: int,
    limit: int = Query(default=20, ge=1, le=MAX_RENTAL_PAYMENT_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 19 GET /rental-payment-records/{id}/timeline
    -- available to either side of the record, same access rule as the
    evidence routes below."""
    record = rp_crud.get_record_or_404(db, record_id)
    _record_access_or_403(db, record, user)
    items, total = rp_crud.build_record_timeline(db, record, limit=limit, offset=offset)
    return RentalPaymentTimelinePage(items=items, limit=limit, offset=offset, total=total, has_more=offset + len(items) < total)


@router.get("/records/{record_id}/evidence", response_model=list[EvidenceArtifactRead])
def list_rental_payment_evidence(record_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 5.1 [View document] -- the list a UI needs before
    offering any individual artifact for download."""
    record = rp_crud.get_record_or_404(db, record_id)
    _record_access_or_403(db, record, user)
    return rp_crud.list_payment_evidence_for_record(db, record_id)


@router.get("/records/{record_id}/evidence/{artifact_id}")
def download_rental_payment_evidence(
    record_id: int, artifact_id: int, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 5.1 [View document] -- available to either side of
    the record (tenant who declared it, or the authorized recipient
    reviewing it), never to an unrelated party (A8)."""
    record = rp_crud.get_record_or_404(db, record_id)
    is_tenant, _is_recipient = _record_access_or_403(db, record, user)
    guest = get_guest_for_user(db, user)

    artifact = db.get(EvidenceArtifact, artifact_id)
    if not artifact or artifact.related_entity_type != "rental_payment_record" or artifact.related_entity_id != str(record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    if artifact.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This evidence has been deleted")

    rp_crud.log_evidence_access(
        db, artifact, actor_kind="guest" if is_tenant else "party", actor_id=str(guest.id if is_tenant else user.party_id),
        correlation_id=get_correlation_id(request),
    )
    file_bytes = resolve_dispute_evidence_path(artifact.stored_filename).read_bytes()
    return Response(content=file_bytes, media_type=artifact.content_type or "application/octet-stream")


@recipient_router.post("/records/{record_id}/evidence", response_model=EvidenceArtifactRead, status_code=status.HTTP_201_CREATED)
async def post_upload_rental_payment_evidence_as_recipient(
    record_id: int, file: UploadFile, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 11: 'Upload payment evidence -- Landlord/Agent:
    Controlled.'"""
    party = _get_own_party_or_400(db, user)
    record = _get_recipient_record_or_403(db, record_id, party)
    return await rp_crud.upload_payment_evidence_as_recipient(
        db, record, party, file, correlation_id=get_correlation_id(request),
    )


@admin_router.get("/obligations/{obligation_id}", response_model=RentalPaymentObligationRead)
def get_rental_payment_obligation(obligation_id: int, db: Session = Depends(get_db)):
    return rp_crud.get_obligation_or_404(db, obligation_id)


@admin_router.post("/obligations/sweep-due-soon", dependencies=[Depends(require_super_admin)])
def post_sweep_rental_payment_due_soon(
    request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 14: manual substitute for a cron tick, same shape
    as verification.py's own follow-up sweeps -- no scheduler exists in
    this stack."""
    notified = sweep_rental_payment_due_soon(db)
    log_audit_event(
        db, admin, "rental_payment.due_soon_sweep", "rental_payment_obligation", "bulk", get_correlation_id(request),
        reason=f"notified {len(notified)} obligation(s)",
    )
    db.commit()
    return {"notifiedCount": len(notified), "obligationIds": [o.id for o in notified]}


@admin_router.post("/obligations/{obligation_id}/waive", response_model=RentalPaymentObligationRead, dependencies=[Depends(require_super_admin)])
def post_waive_rental_payment_obligation(
    obligation_id: int, payload: RentalPaymentTerminalActionRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    return rp_crud.waive_obligation(db, admin, obligation, reason=payload.reason, correlation_id=get_correlation_id(request))


@admin_router.post("/obligations/{obligation_id}/cancel", response_model=RentalPaymentObligationRead, dependencies=[Depends(require_super_admin)])
def post_cancel_rental_payment_obligation(
    obligation_id: int, payload: RentalPaymentTerminalActionRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    return rp_crud.cancel_obligation(db, admin, obligation, reason=payload.reason, correlation_id=get_correlation_id(request))


def _to_dispute_admin_read(dispute: RentalPaymentDispute) -> RentalPaymentDisputeAdminRead:
    record = dispute.record
    obligation = record.obligation
    return RentalPaymentDisputeAdminRead(
        **RentalPaymentDisputeRead.model_validate(dispute).model_dump(),
        record_status=record.status, declared_amount=float(record.declared_amount),
        declared_currency=record.declared_currency, declared_date=record.declared_date,
        payment_method_category=record.payment_method_category, external_reference=record.external_reference,
        obligation_id=obligation.id, obligation_label=obligation.display_label, obligation_amount=float(obligation.amount),
        obligation_status=obligation.status, tenant_guest_id=obligation.tenant_guest_id,
        recipient_party_id=obligation.recipient_party_id,
        reported_by="host" if dispute.reported_by_party_id is not None else "tenant",
    )


@admin_router.get(
    "/disputes", response_model=list[RentalPaymentDisputeAdminRead],
    dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def get_rental_payment_disputes(dispute_status: str | None = Query(default="OPEN", alias="status"), db: Session = Depends(get_db)):
    """Admin queue of rent payment disputes (status=OPEN by default; pass
    status= empty for all), with the payment each one is about."""
    return [_to_dispute_admin_read(d) for d in rp_crud.list_disputes(db, status_filter=dispute_status or None)]


# No admin resolve endpoint: a problem with a rent/deposit payment is
# resolved between the tenant and the host themselves
# (POST .../disputes/{case_id}/claims/{claim_id}/payment-resolution,
# crud/rental_payment.py:resolve_dispute_by_party).


@admin_router.post("/records/{record_id}/reverse", response_model=RentalPaymentRecordRead, dependencies=[Depends(require_super_admin)])
def post_reverse_rental_payment_record(
    record_id: int, payload: RentalPaymentReverseRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    record = rp_crud.get_record_or_404(db, record_id)
    return rp_crud.reverse_record(db, admin, record, reason=payload.reason, correlation_id=get_correlation_id(request))


@admin_router.post(
    "/records/{record_id}/confirm", response_model=RentalPaymentRecordRead,
    dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def post_admin_confirm_rental_payment_record(
    record_id: int, payload: RentalPaymentTerminalActionRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin_or_payment_staff), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 11: 'Confirm receipt -- Admin/Support: No, except
    explicit correction workflow.' ZR-PAY-LINK-003 Section 17: 'Controlled
    support only' -- super_admin or a payment_staff_role admin, reason
    required -- never a routine substitute for the recipient's own
    confirm-receipt action."""
    record = rp_crud.get_record_or_404(db, record_id)
    return rp_crud.admin_confirm_receipt(db, admin, record, reason=payload.reason, correlation_id=get_correlation_id(request))


@admin_router.post(
    "/records/{record_id}/provider-confirm", response_model=RentalPaymentRecordRead,
    dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def post_confirm_rental_payment_record_as_provider(
    record_id: int, payload: RentalPaymentProviderConfirmRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin_or_payment_staff), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 6: status CONFIRMED via PROVIDER_CONFIRMATION
    provenance -- 'Verified provider event.' ZR-PAY-LINK-003 Section 17:
    'Controlled support only' -- super_admin or a payment_staff_role admin,
    the manual-reconciliation fallback for a confirmation obtained some
    other way than the automated external-payment-session webhook (see
    crud/rental_payment.py:confirm_receipt_as_provider's own docstring)."""
    record = rp_crud.get_record_or_404(db, record_id)
    return rp_crud.confirm_receipt_as_provider(
        db, admin, record, provider_reference=payload.provider_reference, reason=payload.reason,
        correlation_id=get_correlation_id(request),
    )


@admin_router.post("/scheduled-jobs/run", dependencies=[Depends(require_super_admin)])
def post_run_scheduled_jobs(db: Session = Depends(get_db)):
    """Runs the hourly jobs now (monthly rent creation, due/overdue status
    and reminders, booking expiry) -- same work the in-process scheduler
    does, for a super admin who doesn't want to wait for the next tick."""
    from app.services.scheduled_jobs import run_scheduled_jobs

    return run_scheduled_jobs(db)


@admin_router.get(
    "/instructions/pending-review", response_model=list[RentalPaymentInstructionRead], dependencies=[Depends(require_super_admin)],
)
def get_rental_payment_instructions_pending_review(db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 9.1 step 7: the manual-review queue for
    high-risk payment-instruction changes. Registered ahead of
    /instructions/{party_id} so this literal path is matched first.
    include_bank_details=True -- reviewing a destination change without
    seeing the actual destination would make the review meaningless;
    super_admin-only, same restricted-not-never posture as Section 17's
    Permissions Matrix ('View payment destination... Staff: Restricted')."""
    return [
        _to_instruction_read(i, include_bank_details=True)
        for i in rp_crud.list_rental_payment_instructions_pending_review(db)
    ]


@admin_router.post(
    "/instructions/{instruction_id}/approve", response_model=RentalPaymentInstructionRead,
    dependencies=[Depends(require_super_admin)],
)
def post_approve_rental_payment_instruction(
    instruction_id: int, payload: RentalPaymentInstructionReviewRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    instruction = rp_crud.get_rental_payment_instruction_or_404(db, instruction_id)
    return _to_instruction_read(rp_crud.approve_pending_review_instruction(
        db, admin, instruction, reason=payload.reason, correlation_id=get_correlation_id(request),
    ), include_bank_details=True)


@admin_router.post(
    "/instructions/{instruction_id}/reject", response_model=RentalPaymentInstructionRead,
    dependencies=[Depends(require_super_admin)],
)
def post_reject_rental_payment_instruction(
    instruction_id: int, payload: RentalPaymentInstructionReviewRequest, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    instruction = rp_crud.get_rental_payment_instruction_or_404(db, instruction_id)
    return _to_instruction_read(rp_crud.reject_pending_review_instruction(
        db, admin, instruction, reason=payload.reason, correlation_id=get_correlation_id(request),
    ), include_bank_details=True)


@admin_router.get("/instructions/{party_id}", response_model=list[RentalPaymentInstructionRead], dependencies=[Depends(require_super_admin)])
def get_rental_payment_instructions_for_party(party_id: int, db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 11: 'View payment instructions -- Admin/Support:
    Restricted.'"""
    return [
        _to_instruction_read(i, include_bank_details=True)
        for i in rp_crud.list_rental_payment_instructions_for_party(db, party_id)
    ]


@admin_router.post(
    "/records/{record_id}/corrections", response_model=RentalPaymentCorrectionRead, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def post_append_rental_payment_correction(
    record_id: int, payload: RentalPaymentCorrectionCreate, request: Request,
    admin: AdminUser = Depends(require_super_admin_or_payment_staff), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 7.2/A10/11: 'Append corrective event: Restricted +
    reason.' ZR-PAY-LINK-003 Section 17: 'Controlled + audited' for Staff --
    super_admin or a payment_staff_role admin."""
    record = rp_crud.get_record_or_404(db, record_id)
    return rp_crud.append_correction(
        db, admin, record, field_name=payload.field_name, new_value=payload.new_value, reason=payload.reason,
        correlation_id=get_correlation_id(request),
    )


@admin_router.get(
    "/records/{record_id}/evidence", response_model=list[EvidenceArtifactRead],
    dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def get_admin_rental_payment_evidence(record_id: int, db: Session = Depends(get_db)):
    """Support's view of the proof attached to a payment -- what a dispute
    decision or a legal hold is made on."""
    rp_crud.get_record_or_404(db, record_id)
    return rp_crud.list_payment_evidence_for_record(db, record_id)


@admin_router.get(
    "/records/{record_id}/evidence/{artifact_id}", dependencies=[Depends(require_super_admin_or_payment_staff)],
)
def download_admin_rental_payment_evidence(
    record_id: int, artifact_id: int, request: Request,
    admin: AdminUser = Depends(require_super_admin_or_payment_staff), db: Session = Depends(get_db),
):
    """Same file the tenant/host can open -- every support access is logged."""
    rp_crud.get_record_or_404(db, record_id)
    artifact = db.get(EvidenceArtifact, artifact_id)
    if not artifact or artifact.related_entity_type != "rental_payment_record" or artifact.related_entity_id != str(record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    if artifact.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This evidence has been deleted")
    rp_crud.log_evidence_access(
        db, artifact, actor_kind="admin", actor_id=str(admin.id), correlation_id=get_correlation_id(request),
    )
    file_bytes = resolve_dispute_evidence_path(artifact.stored_filename).read_bytes()
    return Response(content=file_bytes, media_type=artifact.content_type or "application/octet-stream")


@admin_router.post(
    "/evidence/{artifact_id}/hold", response_model=RentalPaymentEvidenceHoldRead, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_super_admin)],
)
def post_place_rental_payment_evidence_hold(
    artifact_id: int, payload: RentalPaymentEvidenceHoldCreate, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 10/13: 'legal hold and deletion exceptions.'"""
    artifact = db.get(EvidenceArtifact, artifact_id)
    if not artifact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence not found")
    return rp_crud.place_evidence_legal_hold(
        db, admin, artifact, reason=payload.reason, correlation_id=get_correlation_id(request),
    )


@admin_router.post(
    "/evidence/holds/{hold_id}/release", response_model=RentalPaymentEvidenceHoldRead, dependencies=[Depends(require_super_admin)],
)
def post_release_rental_payment_evidence_hold(
    hold_id: int, request: Request, admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    hold = rp_crud.get_evidence_hold_or_404(db, hold_id)
    return rp_crud.release_evidence_legal_hold(db, admin, hold, correlation_id=get_correlation_id(request))


@admin_router.get(
    "/evidence/{artifact_id}/holds", response_model=list[RentalPaymentEvidenceHoldRead], dependencies=[Depends(require_super_admin)],
)
def get_rental_payment_evidence_holds(artifact_id: int, db: Session = Depends(get_db)):
    return rp_crud.list_evidence_holds_for_artifact(db, artifact_id)
