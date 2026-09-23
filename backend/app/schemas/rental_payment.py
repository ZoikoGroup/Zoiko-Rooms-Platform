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
    resolved_at: datetime | None
    resolution_notes: str


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


class RentalPaymentObligationRead(CamelModel):
    id: int
    obligation_type: str
    agreement_id: int | None
    occupancy_id: int | None
    tenant_guest_id: str
    recipient_party_id: int
    amount: float
    currency: str
    due_date: date
    status: str
    display_label: str
    waived_reason: str
    waived_at: datetime | None
    created_at: datetime
    records: list[RentalPaymentRecordRead] = []


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


class RentalPaymentProviderConfirmRequest(CamelModel):
    provider_reference: str
    reason: str


class RentalPaymentDisputeCreate(CamelModel):
    reason_code: str
    details: str = ""


class RentalPaymentDisputeResolve(CamelModel):
    resolution_notes: str


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
    account_identifier: str = Field(min_length=4, max_length=64)
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
    account_identifier_masked: str
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
