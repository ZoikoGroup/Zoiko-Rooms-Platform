from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel

MAX_MONEY_AMOUNT = 9_999_999_999.99


# Defined ahead of RentalPaymentRecordRead below (rather than in their usual
# declaration order further down this file) so it can nest them directly --
# ZR-PAY-LINK-003 Section 19/Wireframe PAY-18: a record's disputes/corrections
# were previously only ever visible to admins; nesting them here is what
# actually surfaces that history to the tenant/recipient views that already
# return RentalPaymentRecordRead (RentalPaymentObligationRead.records).
class RentalPaymentDisputeRead(CamelModel):
    id: int
    record_id: int
    reason_code: str
    details: str
    status: str
    reported_by_guest_id: str | None
    reported_by_party_id: int | None
    reported_at: datetime
    resolved_by_admin_id: int | None
    resolved_by_guest_id: str | None = None
    resolved_by_party_id: int | None = None
    resolved_at: datetime | None
    resolution_notes: str
    outcome: str | None = None
    # The linked dispute case (services/payment_dispute_cases.py), if any.
    dispute_case_id: int | None = None


# What the tenant / host may do on an open payment-record dispute
# (crud/rental_payment.py:resolve_dispute_by_party).
PAYMENT_DISPUTE_PARTY_ACTIONS = ("CONFIRM_RECEIVED", "CONFIRM_NOT_PAID", "WITHDRAW")


class RentalPaymentDisputePartyResolve(CamelModel):
    action: str
    notes: str = ""


class RentalPaymentCorrectionRead(CamelModel):
    id: int
    record_id: int
    field_name: str
    previous_value: str
    new_value: str
    reason: str
    actor_admin_id: int | None
    actor_guest_id: str | None
    actor_party_id: int | None
    created_at: datetime


class RentalPaymentRecordRead(CamelModel):
    id: int
    obligation_id: int
    status: str
    provenance: str
    declared_amount: float
    declared_currency: str
    declared_date: date
    payment_method_category: str
    external_reference: str
    declared_by_guest_id: str
    confirmed_by_party_id: int | None
    confirmed_amount: float | None
    provider_reference: str
    confirmed_at: datetime | None
    created_at: datetime
    disputes: list[RentalPaymentDisputeRead] = []
    corrections: list[RentalPaymentCorrectionRead] = []


class RentalPaymentAllocationRead(CamelModel):
    """ZR-PAY-LINK-003 Section 15/Wireframe PAY-17: one co-tenant's share of
    a joint-tenancy obligation. Deliberately doesn't duplicate a computed
    'contributed so far' amount/status here -- that's already fully
    derivable by matching this payer_guest_id against
    RentalPaymentObligationRead.records[].declared_by_guest_id, the same
    'nest, don't duplicate' discipline the disputes/corrections nesting
    above already follows."""

    id: int
    obligation_id: int
    payer_guest_id: str
    allocated_amount: float
    created_at: datetime


class RentalPaymentObligationRead(CamelModel):
    id: int
    obligation_type: str
    agreement_id: int | None
    occupancy_id: int | None
    tenant_guest_id: str
    recipient_party_id: int
    amount: float
    # amount less what has already been received -- what "pay online" charges.
    outstanding_amount: float
    currency: str
    due_date: date
    status: str
    display_label: str
    waived_reason: str
    waived_at: datetime | None
    created_at: datetime
    # ZR-SUBLET-PAY-003 Section 11.1 ledger fields.
    payee_type: str = "LANDLORD_AGENT"
    payee_basis: str = ""
    payee_authority_ref: str = ""
    period_start: date | None = None
    period_end: date | None = None
    arrangement_id: int | None = None
    arrangement_version: int | None = None
    agreement_version_no: int | None = None
    payment_reference: str = ""
    platform_fee_amount: float = 0.0
    version: int = 1
    records: list[RentalPaymentRecordRead] = []
    # ZR-PAY-LINK-003 Section 15/Wireframe PAY-17 -- empty for the ordinary
    # single-payer obligation (the default).
    payer_allocations: list[RentalPaymentAllocationRead] = []


class RentalPaymentAllocationEntry(CamelModel):
    payer_guest_id: str
    allocated_amount: float = Field(gt=0, le=MAX_MONEY_AMOUNT)


class RentalPaymentAllocationsCreate(CamelModel):
    """ZR-PAY-LINK-003 Section 15 POST .../payer-allocations -- the
    recipient's one-time joint-tenancy split, never tenant-editable. See
    crud/rental_payment.py:create_payer_allocations for the sum-must-equal-
    the-obligation's-own-amount and settable-only-once rules."""

    allocations: list[RentalPaymentAllocationEntry]


class RentalPaymentObligationsPage(CamelModel):
    """Paginated envelope for GET /obligations (tenant + recipient views) --
    same shape as schemas/listing.py:PublicListingsPage."""

    items: list[RentalPaymentObligationRead]
    limit: int
    offset: int
    total: int
    has_more: bool


class RentalPaymentMarkPaidRequest(CamelModel):
    amount: float = Field(gt=0, le=MAX_MONEY_AMOUNT)
    currency: str = Field(min_length=3, max_length=3)
    declared_date: date
    payment_method_category: str
    external_reference: str = ""


class RentalPaymentConfirmReceiptRequest(CamelModel):
    """amount is optional -- omitted means 'confirm the full declared
    amount' (the ordinary case). A lesser amount records a partial
    confirmation (ZR-PAY-002 Section 6 PARTIALLY_PAID); it can never exceed
    the record's own declared_amount."""

    amount: float | None = Field(default=None, gt=0, le=MAX_MONEY_AMOUNT)
    note: str = ""


class RentalPaymentRecordReceiptRequest(CamelModel):
    """The host marking money as received directly (no renter declaration
    first). amount omitted = everything still outstanding."""

    amount: float | None = Field(default=None, gt=0, le=MAX_MONEY_AMOUNT)
    received_date: date
    payment_method_category: str
    external_reference: str = ""
    note: str = ""


class RentalPaymentProviderConfirmRequest(CamelModel):
    provider_reference: str
    reason: str


class RentalPaymentDisputeCreate(CamelModel):
    reason_code: str
    details: str = ""


class RentalPaymentDisputeResolve(CamelModel):
    resolution_notes: str
    # CLOSE_ONLY (default) | PAYMENT_STANDS | PAYMENT_NOT_RECEIVED -- see
    # crud/rental_payment.py:_apply_dispute_outcome.
    outcome: str = "CLOSE_ONLY"


class RentalPaymentDisputeAdminRead(RentalPaymentDisputeRead):
    """A dispute with the payment context an admin needs to decide it."""

    record_status: str
    declared_amount: float
    declared_currency: str
    declared_date: date
    payment_method_category: str
    external_reference: str
    obligation_id: int
    obligation_label: str
    obligation_amount: float
    obligation_status: str
    tenant_guest_id: str
    recipient_party_id: int
    reported_by: str  # "tenant" | "host"


class RentalPaymentReverseRequest(CamelModel):
    reason: str


class RentalPaymentCorrectionCreate(CamelModel):
    field_name: str
    new_value: str
    reason: str


class RentalPaymentTerminalActionRequest(CamelModel):
    reason: str


class RentalPaymentTenantSelfCorrectionCreate(CamelModel):
    field_name: str
    new_value: str
    reason: str = ""


class RentalPaymentDisputeUpdate(CamelModel):
    reason_code: str | None = None
    details: str | None = None


class RentalPaymentEvidenceHoldCreate(CamelModel):
    reason: str


class RentalPaymentEvidenceHoldRead(CamelModel):
    id: int
    artifact_id: int
    status: str
    reason: str
    placed_by_admin_id: int
    placed_at: datetime
    released_by_admin_id: int | None
    released_at: datetime | None


class RentalPaymentInstructionSubmit(CamelModel):
    method: str
    recipient_name: str
    # ZR-PAY-LINK-003 Wireframe D: country_code resolves which structured
    # field set (services/bank_field_schemas.py) bank_details is validated
    # against -- e.g. {"sort_code": "12-34-56", "account_number": "12345678"}
    # for GB, {"iban": "..."} for an IBAN country, {"account_identifier":
    # "..."} for the generic fallback. Never a single free-text string
    # anymore -- see crud/rental_payment.py:submit_rental_payment_instruction.
    country_code: str = Field(min_length=2, max_length=2)
    bank_details: dict[str, str]
    # Wireframe D's mandatory checkbox -- must be true or submission is
    # rejected (400), not merely recorded as false.
    authorized_recipient_confirmed: bool
    reference_format: str = ""
    additional_instructions: str = ""


class RentalPaymentInstructionConfirm(CamelModel):
    code: str


class RentalPaymentInstructionRead(CamelModel):
    id: int
    party_id: int
    status: str
    method: str
    recipient_name: str
    country_code: str = ""
    account_identifier_masked: str
    # The real structured values, decrypted -- only ever populated for the
    # three call sites in api/routes/rental_payments.py that are actually
    # authorized to see this specific party's instructions in full (the
    # tenant with a due obligation to this recipient, the recipient
    # themselves, and payment-staff admin review). None everywhere else,
    # including any list-shaped response.
    bank_details: dict[str, str] | None = None
    reference_format: str
    additional_instructions: str
    verified_at: datetime | None
    created_at: datetime
    # ZR-PAY-002 Section 9.1 step 3/7: whether this change was flagged
    # high-risk at submission and, if so, why -- never the raw signal itself
    # (e.g. no password_changed_at timestamp), just the human-readable reason.
    is_high_risk: bool = False
    high_risk_reason: str = ""
    reviewed_at: datetime | None = None
    review_reason: str = ""


class RentalPaymentInstructionReviewRequest(CamelModel):
    reason: str = ""


class EvidenceArtifactRead(CamelModel):
    id: int
    related_entity_type: str
    related_entity_id: str
    original_filename: str
    content_type: str
    file_size: int
    scan_status: str
    created_at: datetime
