from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CamelModel

MAX_MONEY_AMOUNT = 9_999_999_999.99


class ListingFeePolicyCreate(CamelModel):
    jurisdiction_code: str = Field(max_length=10)
    effective_from: date
    effective_to: date | None = None
    amount: float = Field(ge=0, le=MAX_MONEY_AMOUNT)
    currency: str = Field(min_length=3, max_length=3)
    tax_rate: float = 0.0
    quote_validity_minutes: int = 30
    legal_entity_name: str
    tax_registration_number: str = ""
    disclosure_text: str = ""
    refund_eligible: bool = False
    refund_window_days: int | None = None


class ListingFeePolicyUpdate(CamelModel):
    """Every field optional -- only what's provided gets updated. Editing an
    already-relied-upon pack in place is for correcting a mistake, not for
    changing terms past decisions were made under (that gets a new version
    via create_listing_fee_policy instead, same rule
    crud/market_policy.py:update_market_policy_pack follows)."""

    effective_to: date | None = None
    amount: float | None = Field(default=None, ge=0, le=MAX_MONEY_AMOUNT)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_rate: float | None = None
    quote_validity_minutes: int | None = None
    legal_entity_name: str | None = None
    tax_registration_number: str | None = None
    disclosure_text: str | None = None
    refund_eligible: bool | None = None
    refund_window_days: int | None = None


class ListingFeePolicyRead(CamelModel):
    id: int
    jurisdiction_code: str
    version: int
    effective_from: date
    effective_to: date | None
    amount: float
    currency: str
    tax_rate: float
    quote_validity_minutes: int
    legal_entity_name: str
    tax_registration_number: str
    disclosure_text: str
    refund_eligible: bool
    refund_window_days: int | None
    created_at: datetime


class ListingFeeQuoteRead(CamelModel):
    id: int
    listing_id: str
    amount: float
    tax_amount: float
    total_amount: float
    currency: str
    expires_at: datetime
    created_at: datetime


class ListingFeeCheckoutSessionCreate(CamelModel):
    quote_id: int
    idempotency_key: str
    billing_country: str = Field(min_length=2, max_length=2)


class ListingFeeCheckoutSessionRead(CamelModel):
    id: int
    quote_id: int
    listing_id: str
    amount: float
    currency: str
    status: str
    client_secret: str
    created_at: datetime


class ListingFeePaymentRead(CamelModel):
    id: int
    quote_id: int
    listing_id: str
    amount: float
    currency: str
    status: str
    billing_country: str
    failure_message: str
    created_at: datetime
    paid_at: datetime | None
    failed_at: datetime | None
    # ZR-PAY-002 Section 8.4: REFUND_ELIGIBLE -- computed per-request (never
    # a stored column on the payment itself), so routes construct this
    # explicitly rather than relying on from_attributes auto-population.
    refund_eligible: bool = False


class ListingFeeReceiptRead(CamelModel):
    id: int
    payment_id: int
    receipt_number: str
    legal_entity_name: str
    tax_registration_number: str
    amount: float
    tax_rate: float
    tax_amount: float
    total_amount: float
    currency: str
    issued_at: datetime


class ListingFeeRefundCreate(CamelModel):
    amount: float = Field(gt=0, le=MAX_MONEY_AMOUNT)
    reason: str = ""
    idempotency_key: str


class ListingFeeRefundRead(CamelModel):
    id: int
    payment_id: int
    amount: float
    currency: str
    reason: str
    status: str
    requested_by_admin_id: int
    failure_message: str
    created_at: datetime
    completed_at: datetime | None
