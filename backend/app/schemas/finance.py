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


class SimulatedPaymentCreate(CamelModel):
    guest_id: str
    amount: float
    currency: str = "INR"
    idempotency_key: str


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
    created_at: datetime
    confirmed_at: datetime | None
    allocations: list[PaymentAllocationRead] = []
    # Populated by crud.finance._annotate_payment_context. property/room/listing
    # come from the occupancy behind this payment's first allocation, if any --
    # a payment can in principle span more than one obligation, but in practice
    # always represents one tenant's charge for one room.
    guest_name: str = ""
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
    created_at: datetime
    paid_at: datetime | None


class RefundRequestCreate(CamelModel):
    payment_id: int
    obligation_id: int
    amount: float
    reason: str = ""


class RefundDecide(CamelModel):
    approve: bool


class RefundRequestRead(CamelModel):
    id: int
    payment_id: int
    obligation_id: int
    amount: float
    reason: str
    status: str
    requested_by_admin_id: int
    decided_by_admin_id: int | None
    created_at: datetime
    decided_at: datetime | None


class DisputeCreate(CamelModel):
    payment_id: int | None = None
    occupancy_id: int | None = None
    category: str
    description: str = ""


class DisputeResolve(CamelModel):
    status: str  # "RESOLVED" | "REJECTED"
    resolution_notes: str = ""


class DisputeRead(CamelModel):
    id: int
    payment_id: int | None
    occupancy_id: int | None
    category: str
    description: str
    status: str
    opened_at: datetime
    resolved_at: datetime | None
    resolution_notes: str


class ReconciliationRunRead(CamelModel):
    id: int
    run_at: datetime
    totals: dict
    mismatches: list
    status: str
