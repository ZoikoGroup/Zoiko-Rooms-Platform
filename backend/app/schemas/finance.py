from datetime import date, datetime

from app.schemas.common import CamelModel


class ObligationRead(CamelModel):
    id: int
    obligation_type: str
    money_plane: str
    amount: float
    currency: str
    due_date: date
    status: str
    guest_id: str
    agreement_id: int | None
    occupancy_id: int | None
    payout_id: int | None
    created_at: datetime


class PaymentAllocationInput(CamelModel):
    obligation_id: int
    amount: float


class ScheduledObligationPreview(CamelModel):
    """ZR-ENG-CLR-005 AC-11/Section 6.1 region C 'Future schedule': one
    projected-but-not-yet-created RENT obligation -- due date, period amount
    and cadence, no misleading 'subscription' framing since this is
    contractual rent, not a recurring charge object."""

    due_date: date
    amount: float
    currency: str
    cadence: str


class PaymentPreviewRead(CamelModel):
    """ZR-ENG-CLR-005 AC-11: 'A payer sees complete amount-due-now line items
    and future schedule before charge confirmation.' amount_due_now is every
    real, currently-unpaid Obligation on the agreement (rent + deposit --
    this build has no renter-side fee, tax or credit line items to add, see
    crud/finance.py:get_payment_preview); future_schedule is a pure
    projection (never persisted) of the RENT periods still to come."""

    amount_due_now: list[ObligationRead]
    future_schedule: list[ScheduledObligationPreview]
    cadence: str
    remaining_scheduled_count: int


class SimulatedPaymentCreate(CamelModel):
    guest_id: str
    amount: float
    currency: str = "INR"
    idempotency_key: str
    # ZR-ENG-CLR-005 AC-03: all optional, all default to "payer == occupant" when
    # omitted -- see crud.finance.create_payment_intent.
    payer_guest_id: str | None = None
    payer_name: str | None = None
    payer_email: str | None = None
    payer_phone: str | None = None
    # ZR-ENG-CLR-005 Section 12.1: "EXTERNAL" (the default) is a self-attested
    # off-platform record -- an admin recording a real cash/cheque payment
    # explicitly declares that here. Any other class is only ever set for
    # real by crud/payment_provider.py:dispatch_payment_to_provider, never
    # trusted from this create call alone -- see method_class's own model
    # docstring for why the confirm-side gate is the actual enforcement point.
    method_class: str = "EXTERNAL"


class PaymentConfirm(CamelModel):
    allocations: list[PaymentAllocationInput]


class PaymentAllocationRead(CamelModel):
    id: int
    payment_id: int
    obligation_id: int
    amount_allocated: float
    created_at: datetime
    # Populated by crud.finance._annotate_payment_context -- not a DB column on
    # PaymentAllocation, which only stores the raw obligation_id.
    obligation_type: str = ""


class SimulatedPaymentRead(CamelModel):
    id: int
    guest_id: str
    amount: float
    currency: str
    idempotency_key: str
    status: str
    method_class: str
    created_at: datetime
    confirmed_at: datetime | None
    allocations: list[PaymentAllocationRead] = []
    # Populated by crud.finance._annotate_payment_context. property/room/listing
    # come from the occupancy behind this payment's first allocation, if any --
    # a payment can in principle span more than one obligation, but in practice
    # always represents one tenant's charge for one room.
    guest_name: str = ""
    payer_guest_id: str | None = None
    payer_name: str | None = None
    payer_email: str | None = None
    payer_phone: str | None = None
    # Populated by crud.finance.annotate_payment_context -- the payer's registered
    # guest name when payer_guest_id is set, else payer_name, else guest_name (the
    # payer == occupant default).
    payer_display_name: str = ""
    listing_id: str | None = None
    listing_name: str = ""
    room_id: int | None = None
    property_address: str = ""


class DepositInstrumentRead(CamelModel):
    id: int
    instrument_type: str
    custody_model: str
    calculation_snapshot: dict
    created_at: datetime


class DepositRecordRead(CamelModel):
    id: int
    obligation_id: int
    status: str
    held_amount: float
    released_amount: float
    released_at: datetime | None
    notes: str
    instrument: DepositInstrumentRead | None = None
    claimed_amount: float = 0
    disputed_amount: float = 0


class DepositRelease(CamelModel):
    amount: float
    notes: str = ""


class DepositClaimItemCreate(CamelModel):
    category_code: str
    amount_requested: float
    description: str = ""


class DepositClaimCreate(CamelModel):
    items: list[DepositClaimItemCreate]


class DepositClaimItemRead(CamelModel):
    id: int
    claim_id: int
    category_code: str
    amount_requested: float
    description: str
    has_evidence: bool = False
    evidence_original_name: str = ""
    tenant_response: str
    final_amount: float | None
    created_at: datetime


class DepositClaimItemRespond(CamelModel):
    response: str  # "ACCEPT" | "PARTIAL_ACCEPT" | "DISPUTE"
    accepted_amount: float | None = None  # required when response == "PARTIAL_ACCEPT"


class DepositClaimItemFinalAmount(CamelModel):
    item_id: int
    final_amount: float


class DepositClaimResolve(CamelModel):
    item_final_amounts: list[DepositClaimItemFinalAmount]
    notes: str = ""


class DepositClaimRead(CamelModel):
    id: int
    deposit_record_id: int
    status: str
    submitted_by_admin_id: int
    submitted_at: datetime
    renter_responded_at: datetime | None
    resolved_by_admin_id: int | None
    resolved_at: datetime | None
    resolution_notes: str
    items: list[DepositClaimItemRead] = []


class PayoutRunRequest(CamelModel):
    party_id: int
    period_key: str


class PayoutRecordRead(CamelModel):
    id: int
    party_id: int
    period_key: str
    amount: float
    currency: str
    status: str
    hold_reason: str
    # ZR-ENG-CLR-006 Section 15 waterfall tier 4: how much of `amount` was
    # withheld to settle a prior HostRecovery -- 0.0 unless run_payout
    # actually applied an offset. Actual cash disbursed = amount - this.
    recovery_offset_amount: float
    # Set only when this payout actually moved money via a real Stripe
    # Transfer (crud/finance.py:run_payout) -- null otherwise (HELD, no
    # Stripe integration configured, or the party has no Connected Account).
    stripe_transfer_id: str | None
    created_at: datetime
    paid_at: datetime | None


class PayoutBeneficiarySubmit(CamelModel):
    party_id: int
    account_holder_name: str
    bank_name: str
    account_number: str
    # Generic secondary routing identifier -- IFSC code (IN), sort code
    # (England), etc; validated against the format resolved for the party's
    # jurisdiction, see app/services/bank_identifiers.py.
    bank_identifier_code: str


class PayoutBeneficiaryConfirm(CamelModel):
    code: str


class PayoutBeneficiaryRead(CamelModel):
    id: int
    party_id: int
    account_holder_name: str
    bank_name: str
    account_number_last4: str
    bank_identifier_code: str
    status: str
    verified_at: datetime | None
    created_at: datetime


class RefundRequestCreate(CamelModel):
    payment_id: int
    obligation_id: int
    amount: float
    reason: str = ""
    idempotency_key: str


class RefundDecide(CamelModel):
    approve: bool


class RefundRequestRead(CamelModel):
    id: int
    payment_id: int
    obligation_id: int
    amount: float
    reason: str
    idempotency_key: str
    status: str
    requested_by_admin_id: int
    decided_by_admin_id: int | None
    created_at: datetime
    decided_at: datetime | None


class DisputeCreate(CamelModel):
    payment_id: int | None = None
    occupancy_id: int | None = None
    # Required only when category == "CHARGEBACK" -- see crud/finance.py:open_dispute.
    obligation_id: int | None = None
    amount: float | None = None
    category: str
    description: str = ""


class DisputeResolve(CamelModel):
    status: str  # "RESOLVED" | "REJECTED"
    resolution_notes: str = ""
    # Required only when the dispute's category == "CHARGEBACK" -- "WON" | "LOST".
    chargeback_outcome: str | None = None


class DisputeRead(CamelModel):
    id: int
    payment_id: int | None
    occupancy_id: int | None
    obligation_id: int | None
    amount: float | None
    category: str
    description: str
    status: str
    chargeback_outcome: str | None
    opened_at: datetime
    resolved_at: datetime | None
    resolution_notes: str


class FinancialHoldResolve(CamelModel):
    notes: str = ""


class FinancialHoldCreate(CamelModel):
    """ZR-ENG-CLR-005 Section 6.4's 'place ... authorized operational hold'
    admin action -- deliberately scoped to freezing one provider's payouts
    (source_type is always forced to "party" server-side, see
    crud/finance.py:create_financial_hold) rather than an arbitrary
    source_type/source_id an admin could point at anything."""

    party_id: int
    severity: str = "HIGH"
    description: str


class FinancialHoldRead(CamelModel):
    id: int
    source_type: str
    source_id: str
    reason_code: str
    severity: str
    description: str
    status: str
    opened_at: datetime
    resolved_at: datetime | None
    resolved_by_admin_id: int | None
    resolution_notes: str


class HostRecoveryRead(CamelModel):
    id: int
    party_id: int
    financial_hold_id: int
    refund_request_id: int | None
    amount: float
    recovered_amount: float
    currency: str
    status: str
    recovery_method: str
    psp_reversal_id: str | None
    notes: str
    created_at: datetime
    resolved_at: datetime | None


class HostRecoveryRecordProgress(CamelModel):
    """ZR-ENG-CLR-006 Section 18.4: an admin logging that some or all of an
    open recovery amount actually came back -- how (recovery_method) and how
    much, not a mechanism this build executes automatically yet."""

    amount: float
    method: str
    notes: str = ""


class HostRecoveryWriteOff(CamelModel):
    reason: str


class ReconciliationRunRead(CamelModel):
    id: int
    run_at: datetime
    totals: dict
    mismatches: list
    status: str


class PaymentProviderStatusRead(CamelModel):
    healthy: bool
    updated_at: datetime


class SetPaymentProviderHealthRequest(CamelModel):
    healthy: bool


class PaymentDispatchRequest(CamelModel):
    """The allocations Zoiko is declaring this attempt is for, fixed at
    dispatch time -- see models/finance.py:ProcessorTransaction's own
    docstring for why a later provider callback can't decide this instead."""

    allocations: list[PaymentAllocationInput]


class ProcessorTransactionRead(CamelModel):
    id: int
    payment_id: int
    provider_transaction_id: str
    status: str
    declared_allocations: dict
    dispatch_deadline: datetime | None
    created_at: datetime
    completed_at: datetime | None


class PaymentProviderCallbackRequest(CamelModel):
    provider_event_id: str
    provider_transaction_id: str
    event_type: str


class HostStripeAccountCreate(CamelModel):
    party_id: int
    country: str = "GB"
    email: str


class HostStripeAccountRead(CamelModel):
    id: int
    party_id: int
    stripe_account_id: str
    status: str
    details_submitted: bool
    charges_enabled: bool
    payouts_enabled: bool
    created_at: datetime
    updated_at: datetime


class HostStripeOnboardingLinkRead(CamelModel):
    url: str


class AutopayMandateCreate(CamelModel):
    """ZR-ENG-CLR-005 Section 6.1-E: the payer's own explicit consent to set
    up autopay for their occupancy -- always self-consent (the caller's own
    guest record), see crud/finance.py:create_autopay_mandate for why there
    is no separate payer_guest_id override here."""

    occupancy_id: int


class AutopayMandateRead(CamelModel):
    id: int
    occupancy_id: int
    payer_guest_id: str
    provider_ref: str
    status: str
    consent_snapshot: dict
    created_at: datetime
    revoked_at: datetime | None


class PaymentTimelineEntryRead(CamelModel):
    """ZR-ENG-CLR-005 Section 6.4's admin console 'Timeline: Immutable
    normalized events + raw webhook references + actor/system timestamps'
    panel, one row per underlying record -- see
    crud/finance.py:get_payment_timeline for which three tables this merges."""

    timestamp: datetime
    source: str  # "DOMAIN_EVENT" | "AUDIT_EVENT" | "PROVIDER_WEBHOOK"
    event_type: str
    actor: str
    detail: dict


class PaymentTimelineRead(CamelModel):
    payment_id: int
    entries: list[PaymentTimelineEntryRead]
