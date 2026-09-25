from datetime import date, datetime

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.common import CamelModel

MAX_MONEY_AMOUNT = 9_999_999_999.99


class ListingFeePolicyCreate(CamelModel):
    """A new ZR-PAY-CFG-001 Price Book entry. Always created as a DRAFT; it
    only becomes chargeable once approved. `amount` is the decimal price as
    an admin types it -- stored as integer minor units (`amount_minor`),
    which may be sent directly instead."""

    jurisdiction_code: str = Field(min_length=1, max_length=10)
    effective_from: date
    effective_to: date | None = None
    amount: float | None = Field(default=None, ge=0, le=MAX_MONEY_AMOUNT)
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    tax_rate: float = Field(default=0.0, ge=0, le=1)
    tax_behavior: Literal["INCLUSIVE", "EXCLUSIVE"] = "EXCLUSIVE"
    tax_rule_reference: str = Field(default="", max_length=200)
    billing_entity_id: int | None = None
    # Legacy free-text issuer fields, superseded by billing_entity_id.
    legal_entity_name: str = ""
    tax_registration_number: str = ""
    disclosure_text: str = ""
    refund_eligible: bool = False
    refund_window_days: int | None = None

    @model_validator(mode="after")
    def _require_an_amount(self):
        if self.amount is None and self.amount_minor is None:
            raise ValueError("Provide amount or amountMinor")
        return self


class ListingFeePolicyUpdate(CamelModel):
    """Every field optional. Only a DRAFT price can be edited -- an approved
    one needs a new version (crud/listing_fee.py:update_listing_fee_policy)."""

    effective_from: date | None = None
    effective_to: date | None = None
    amount: float | None = Field(default=None, ge=0, le=MAX_MONEY_AMOUNT)
    amount_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    tax_rate: float | None = Field(default=None, ge=0, le=1)
    tax_behavior: Literal["INCLUSIVE", "EXCLUSIVE"] | None = None
    tax_rule_reference: str | None = Field(default=None, max_length=200)
    billing_entity_id: int | None = None
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
    amount_minor: int | None
    currency: str
    tax_rate: float
    tax_behavior: str
    tax_rule_reference: str
    billing_entity_id: int | None
    status: str
    environment: str
    legal_entity_name: str
    tax_registration_number: str
    disclosure_text: str
    refund_eligible: bool
    refund_window_days: int | None
    created_by_admin_id: int | None
    approved_by_admin_id: int | None
    approved_at: datetime | None
    created_at: datetime


class BillingEntityCreate(CamelModel):
    code: str = Field(min_length=2, max_length=40)
    legal_name: str = Field(min_length=1, max_length=200)
    trading_name: str = Field(default="Zoiko Rooms", max_length=200)
    registered_address: str = Field(default="", max_length=500)
    company_registration_number: str = Field(default="", max_length=80)
    tax_registration_type: str = Field(default="", max_length=40)
    tax_registration_number: str = Field(default="", max_length=80)
    supported_markets: list[str] = []
    supported_currencies: list[str] = []
    effective_from: date
    effective_to: date | None = None
    status: Literal["ACTIVE", "INACTIVE"] = "ACTIVE"


class BillingEntityUpdate(CamelModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    trading_name: str | None = Field(default=None, max_length=200)
    registered_address: str | None = Field(default=None, max_length=500)
    company_registration_number: str | None = Field(default=None, max_length=80)
    tax_registration_type: str | None = Field(default=None, max_length=40)
    tax_registration_number: str | None = Field(default=None, max_length=80)
    supported_markets: list[str] | None = None
    supported_currencies: list[str] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status: Literal["ACTIVE", "INACTIVE"] | None = None


class BillingEntityRead(CamelModel):
    id: int
    code: str
    legal_name: str
    trading_name: str
    registered_address: str
    company_registration_number: str
    tax_registration_type: str
    tax_registration_number: str
    supported_markets: list[str]
    supported_currencies: list[str]
    effective_from: date
    effective_to: date | None
    status: str
    created_at: datetime
    updated_at: datetime


class ListingFeeQuoteRead(CamelModel):
    """ZR-PAY-CFG-001 10.1 quote contract. The frontend renders these
    server-computed values and never calculates tax or totals itself."""

    id: int
    listing_id: str
    amount: float
    tax_amount: float
    total_amount: float
    currency: str
    fee_amount_minor: int | None = None
    tax_amount_minor: int | None = None
    total_amount_minor: int | None = None
    market: str | None = None
    tax_behavior: str | None = None
    tax_rate: float | None = None
    price_book_version: int | None = None
    billing_entity_id: str | None = None
    billing_entity_name: str | None = None
    disclosure_text: str | None = None
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
    checkout_url: str
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
