from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.rental_payment import MAX_MONEY_AMOUNT


class RentalPaymentReturnRead(CamelModel):
    id: int
    occupancy_id: int
    obligation_id: int | None = None
    kind: str
    status: str
    tenant_guest_id: str
    recipient_party_id: int
    amount: float
    currency: str
    deductions_amount: float
    deductions_reason: str
    payment_method_category: str
    external_reference: str
    returned_date: date
    note: str
    tenant_responded_at: datetime | None
    tenant_dispute_details: str
    created_at: datetime


class RentalPaymentReturnCreate(CamelModel):
    kind: str
    amount: float = Field(ge=0, le=MAX_MONEY_AMOUNT)
    deductions_amount: float = Field(default=0, ge=0, le=MAX_MONEY_AMOUNT)
    deductions_reason: str = ""
    payment_method_category: str
    external_reference: str = ""
    returned_date: date
    note: str = ""


class RentalPaymentReturnDisputeRequest(CamelModel):
    details: str


class RentalPaymentReturnCandidateRead(CamelModel):
    """An ended/cancelled booking where the host received money and may owe
    some of it back."""

    occupancy_id: int
    obligation_id: int | None = None
    kind: str
    listing_name: str
    occupancy_status: str
    currency: str
    paid: float
    settled: float
    remaining: float
    ended_on: date | None
