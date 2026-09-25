"""ZR-PAY-002 Section 12.2's recommended API surface for rental payment
records -- obligations, declarations, confirmations, disputes and
corrections. Three routers matching Section 11's permission table: `router`
is the tenant's own view (mark-paid, evidence, disputes), `recipient_router`
is the landlord/agent's own view (confirm-receipt, disputes), `admin_router`
is the restricted correction/resolution surface."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, require_super_admin, require_super_admin_or_payment_staff
from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.core.dispute_evidence_uploads import resolve_dispute_evidence_path
from app.core.field_encryption import decrypt_json
from app.crud import external_payment_session as eps_crud
from app.crud import host_stripe_account as hsa_crud
from app.crud import payment_connection as payment_connection_crud
from app.crud import rental_payment as rp_crud
from app.crud import rental_payment_provider_account as rpa_crud
from app.crud.audit import log_audit_event
from app.crud.guest import get_guest_for_user
from app.db.session import get_db
from app.services import stripe_client
from app.services.rental_payment_due_soon import sweep_rental_payment_due_soon
from app.models.admin_user import AdminUser
from app.models.evidence_artifact import EvidenceArtifact
from app.models.guest import Guest
from app.models.party import Party
from app.models.rental_payment import RentalPaymentDispute, RentalPaymentEvidenceHold
from app.models.user_account import UserAccount
from app.schemas.external_payment_session import ExternalPaymentSessionCreateResult, ExternalPaymentSessionRead
from app.schemas.payment_connection import PaymentConnectionRead
from app.schemas.rental_payment import (
    EvidenceArtifactRead,
    RentalPaymentAllocationsCreate,
    RentalPaymentConfirmReceiptRequest,
    RentalPaymentCorrectionCreate,
    RentalPaymentCorrectionRead,
    RentalPaymentDisputeCreate,
    RentalPaymentDisputeRead,
    RentalPaymentDisputeResolve,
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
from app.schemas.rental_payment_provider_account import (
    RentalPaymentProviderAccountChangeRequestResult,
    RentalPaymentProviderAccountConfirmChange,
    RentalPaymentProviderAccountConnectResult,
    RentalPaymentProviderAccountCreate,
    RentalPaymentProviderAccountRead,
)
from app.schemas.rental_transaction_record import RentalPaymentTimelinePage

router = APIRouter(prefix="/api/users/rental-payments", tags=["user-rental-payments"], dependencies=[Depends(get_current_user)])
recipient_router = APIRouter(
    prefix="/api/users/rental-payments/recipient", tags=["recipient-rental-payments"], dependencies=[Depends(get_current_user)],
)
admin_router = APIRouter(prefix="/api/finance/rental-payments", tags=["finance-rental-payments"], dependencies=[Depends(get_current_admin)])
webhook_router = APIRouter(prefix="/api/finance", tags=["finance-rental-payment-webhooks"])

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
        account_identifier_masked=f"******{instruction.account_identifier_last4}", bank_details=bank_details,
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


def _resolve_frontend_origin(request: Request) -> str:
    """Same Origin-header-validated-against-CORS-allowlist resolution as
    api/routes/listing_fees.py:_resolve_frontend_origin -- duplicated rather
    than imported to keep this domain's own routes independent, same
    small-duplication posture as this file's other helpers."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        return origin
    return settings.frontend_url


@router.post(
    "/obligations/{obligation_id}/payment-session", response_model=ExternalPaymentSessionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
def post_start_rental_payment_session(
    obligation_id: int, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 19 POST /rental-payment-obligations/{id}/payment-session
    -- Wireframe F: 'Continue to secure payment.' A real browser redirect to
    Stripe's own hosted page follows, never an in-app card form (same PCI
    boundary as the Listing Fee checkout)."""
    guest = _get_own_guest_or_403(db, user)
    obligation = rp_crud.get_obligation_or_404(db, obligation_id)
    origin = _resolve_frontend_origin(request)
    # /account/rent-payments is the tenant's own existing Payments page
    # (src/app/account/(shell)/rent-payments/page.tsx) -- same
    # land-back-on-the-existing-page pattern as the Listing Fee return
    # (HostingListingsManager.tsx reading its own checkoutSessionId param),
    # not a dedicated return route.
    success_url = f"{origin}/account/rent-payments?checkoutSessionId={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/account/rent-payments?checkoutSessionId={{CHECKOUT_SESSION_ID}}&cancelled=1"
    session, checkout_url = eps_crud.create_session(
        db, guest, obligation, success_url=success_url, cancel_url=cancel_url, correlation_id=get_correlation_id(request),
    )
    return ExternalPaymentSessionCreateResult(session=session, checkout_url=checkout_url)


@router.get("/payment-sessions/by-checkout-session/{checkout_session_id}", response_model=ExternalPaymentSessionRead)
def get_rental_payment_session_by_checkout_session_id(
    checkout_session_id: str, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """The return-page resolve: the frontend lands on
    ?checkoutSessionId={CHECKOUT_SESSION_ID} (Stripe's own placeholder,
    substituted server-side) and calls this to find out which of its own
    sessions that was, self-healing via resolve_session rather than only
    waiting on the webhook (Wireframe F: 'Browser return alone is not
    payment confirmation') -- same role as
    api/routes/listing_fees.py:get_resolve_checkout_session plays for the
    Listing Fee's own return leg."""
    guest = _get_own_guest_or_403(db, user)
    session = eps_crud.get_session_by_checkout_session_id_or_404(db, checkout_session_id)
    if session.tenant_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment session does not belong to you")
    return eps_crud.resolve_session(db, session)


@router.get("/payment-sessions/{session_id}", response_model=ExternalPaymentSessionRead)
def get_rental_payment_session(session_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """The return-page resolve -- self-heals via
    crud/external_payment_session.py:resolve_session rather than only
    waiting on the webhook (Wireframe F: 'Browser return alone is not
    payment confirmation')."""
    guest = _get_own_guest_or_403(db, user)
    session = eps_crud.get_session_or_404(db, session_id)
    if session.tenant_guest_id != guest.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment session does not belong to you")
    return eps_crud.resolve_session(db, session)


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


@recipient_router.get("/provider-account", response_model=RentalPaymentProviderAccountRead)
def get_recipient_rental_payment_provider_account(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    return account


@recipient_router.post(
    "/provider-account", response_model=RentalPaymentProviderAccountConnectResult, status_code=status.HTTP_201_CREATED,
)
def post_connect_rental_payment_provider_account(
    payload: RentalPaymentProviderAccountCreate, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Wireframe C: 'Connect payment account.' Returns the
    hosted onboarding URL the frontend does a real browser redirect to --
    same PCI/KYC boundary as the Listing Fee checkout, this app never
    collects the recipient's own bank/identity details itself."""
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.create_connected_account(db, party, country=payload.country, email=payload.email)
    onboarding_url = rpa_crud.create_onboarding_link(account)
    return RentalPaymentProviderAccountConnectResult(account=account, onboarding_url=onboarding_url)


@recipient_router.post("/provider-account/refresh", response_model=RentalPaymentProviderAccountRead)
def post_refresh_rental_payment_provider_account(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    return rpa_crud.refresh_account_status(db, account)


@recipient_router.post("/provider-account/resume-onboarding", response_model=RentalPaymentProviderAccountConnectResult)
def post_resume_rental_payment_provider_account_onboarding(
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """A fresh hosted onboarding link for the CURRENT (already-created)
    account -- for a host who closed the Stripe tab before finishing.
    Never creates a new account (unlike POST /provider-account itself) --
    the account this returns a link for is exactly the one already on
    file, same stripe_account_id, same status."""
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    if account.status == "COMPLETE":
        raise HTTPException(status.HTTP_409_CONFLICT, "This account has already completed onboarding")
    onboarding_url = rpa_crud.create_onboarding_link(account)
    return RentalPaymentProviderAccountConnectResult(account=account, onboarding_url=onboarding_url)


@recipient_router.post("/provider-account/simulate-onboarding-complete", response_model=RentalPaymentProviderAccountRead)
def post_simulate_rental_payment_provider_account_onboarding_complete(
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Dev/test-only -- refuses once real Stripe credentials are configured,
    see crud/rental_payment_provider_account.py:simulate_onboarding_complete's
    own guard."""
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    return rpa_crud.simulate_onboarding_complete(db, account)


@recipient_router.post("/provider-account/request-change", response_model=RentalPaymentProviderAccountChangeRequestResult)
def post_request_rental_payment_provider_account_change(
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-LINK-003 Section 14.1's step-up code for changing which
    account receives online rent payments -- the current account stays
    fully usable (see crud/rental_payment_provider_account.py:
    request_account_change's own docstring) until confirm-change succeeds."""
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    rpa_crud.request_account_change(db, account, user)
    return RentalPaymentProviderAccountChangeRequestResult()


@recipient_router.post("/provider-account/resend-change-code", response_model=RentalPaymentProviderAccountChangeRequestResult)
def post_resend_rental_payment_provider_account_change_code(
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    rpa_crud.resend_account_change_code(db, account, user)
    return RentalPaymentProviderAccountChangeRequestResult()


@recipient_router.post("/provider-account/confirm-change", response_model=RentalPaymentProviderAccountConnectResult)
def post_confirm_rental_payment_provider_account_change(
    payload: RentalPaymentProviderAccountConfirmChange, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """Section 14.1's strong-auth confirmation -- supersedes the current
    account and creates a brand-new one via a real Stripe onboarding flow,
    same 'never reuse a possibly-compromised destination' posture as the
    rest of this change flow."""
    party = _get_own_party_or_400(db, user)
    account = rpa_crud.get_for_party(db, party.id)
    if not account:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No connected payment account yet")
    new_account = rpa_crud.confirm_account_change(
        db, account, user, payload.code, country=payload.country, email=payload.email,
    )
    onboarding_url = rpa_crud.create_onboarding_link(new_account)
    return RentalPaymentProviderAccountConnectResult(account=new_account, onboarding_url=onboarding_url)


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


@admin_router.post("/disputes/{dispute_id}/resolve", response_model=RentalPaymentDisputeRead)
def post_resolve_rental_payment_dispute(
    dispute_id: int, payload: RentalPaymentDisputeResolve, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    dispute = db.get(RentalPaymentDispute, dispute_id)
    if not dispute:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispute not found")
    return rp_crud.resolve_dispute(
        db, admin, dispute, resolution_notes=payload.resolution_notes, correlation_id=get_correlation_id(request),
    )


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


@webhook_router.post("/rental-payments/stripe/webhook")
async def post_rental_payment_stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """The real endpoint Stripe calls for this domain's own Connect events.
    Verifies the Stripe-Signature header against
    settings.stripe_rental_payment_webhook_secret (falling back to the
    shared stripe_webhook_secret) before touching anything -- same
    fail-closed posture as api/routes/listing_fees.py:post_listing_fee_stripe_webhook.

    Also the one place account.updated is handled: Stripe's Connect
    webhooks aren't domain-scoped (an Express account is an Express
    account), so a single account.updated event here is tried against both
    rpa_crud's own table (the current rail) and hsa_crud's (the legacy
    rail) by stripe_account_id -- whichever one owns it applies, the other
    is a no-op. Without this, onboarding status only ever updates from a
    host manually clicking 'Refresh status' on a page they'd have to think
    to reopen -- see rpa_crud.apply_account_updated_event's own docstring."""
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe_client.construct_webhook_event(
            payload=payload, signature_header=signature_header,
            secret=settings.stripe_rental_payment_webhook_secret or settings.stripe_webhook_secret,
        )
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    if event["type"] == "account.updated":
        account_obj = event["data"]["object"]
        rpa_crud.apply_account_updated_event(
            db, stripe_account_id=account_obj["id"],
            details_submitted=bool(account_obj.get("details_submitted")),
            charges_enabled=bool(account_obj.get("charges_enabled")),
            payouts_enabled=bool(account_obj.get("payouts_enabled")),
        )
        hsa_crud.apply_account_updated_event(
            db, stripe_account_id=account_obj["id"],
            details_submitted=bool(account_obj.get("details_submitted")),
            charges_enabled=bool(account_obj.get("charges_enabled")),
            payouts_enabled=bool(account_obj.get("payouts_enabled")),
        )
        return {"received": True}

    eps_crud.ingest_stripe_webhook_event(db, event, correlation_id=get_correlation_id(request))
    return {"received": True}
