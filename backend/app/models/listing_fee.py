"""ZR-PAY-002 Section 2/12.1: the Listing Fee is the only money Zoiko Rooms
ever collects for itself -- a direct fee-collector/merchant relationship,
kept in its own tables with no foreign key into the rent/deposit/payout
domain in models/finance.py. Section 12.1's architecture rule ("do not build
a shared wallet/balance/settlement abstraction between platform_fee_payment
and rental_payment_record") is enforced structurally here: these tables
share nothing with Obligation/SimulatedPayment/PayoutRecord/LedgerEntry
beyond the generic, ownership-of-funds-free AuditEvent/DomainEvent/
Notification utilities every other domain in this codebase already uses.

Naming note: MarketPolicyPack.platform_fee_rate (models/market_policy.py) is
an unrelated concept -- the host-paid, percentage-of-rent commission on the
rent/payout domain. To avoid confusing the two, every object here is named
ListingFee*, never PlatformFee*, even though ZR-PAY-002 Section 12.1 itself
uses the platform_fee_* prefix."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

LISTING_FEE_PAYMENT_STATUSES = ("PENDING", "SUCCEEDED", "FAILED")
# ZR-PAY-002 Section 8.4's own state names, trimmed to what this build's
# single-refund-per-request flow actually produces -- REFUND_ELIGIBLE is a
# display-only derivation (a SUCCEEDED payment with remaining refundable
# amount), never a stored status.
LISTING_FEE_REFUND_STATUSES = ("REQUESTED", "PROCESSING", "PARTIALLY_REFUNDED", "REFUNDED", "FAILED")


class ListingFeePolicy(Base):
    """ZR-PAY-002 Section 8.1/10: the Listing Fee's own jurisdiction-resolved
    commercial configuration -- amount, currency, tax and disclosure, versioned
    and effective-dated the same way MarketPolicyPack is (crud/market_policy.py's
    resolve_market_policy). Deliberately its own table rather than new columns
    on MarketPolicyPack: the Listing Fee is a different money-flow domain
    (Zoiko's own merchant fee, not a rent/deposit/host-payout policy), and
    Section 12.1's architecture rule extends to not entangling their schemas.
    Listing-fee-quoting code must resolve values from here -- never hard-code
    an amount or branch on jurisdiction_code directly, same 'no hard-coded
    country branches' rule every other market-pack-shaped table in this
    codebase already follows."""

    __tablename__ = "listing_fee_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), default=0.0)
    # ZR-PAY-002 Section 1 refinement 4 / Section 8.1: "fee validity period" --
    # how long a resolved ListingFeeQuote may still be paid against before it
    # must be re-quoted. Also the structural fix for the "currency or country
    # changes between fee quote and checkout creation" negative test (Section
    # 16.1): checkout always reads amount/tax/currency off the frozen quote,
    # never re-resolves the policy, so nothing can drift between the two calls.
    quote_validity_minutes: Mapped[int] = mapped_column(default=30)
    legal_entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_registration_number: Mapped[str] = mapped_column(String(50), default="")
    # ZR-PAY-002 Section 8.1: "mandatory pre-payment disclosures" shown on the
    # publish/checkout screen before [Pay Listing Fee] -- resolved text, never
    # hard-coded in frontend copy.
    disclosure_text: Mapped[str] = mapped_column(String(2000), default="")
    # ZR-PAY-002 Section 8.4: 'REFUND_ELIGIBLE -- Display only when
    # commercial policy/jurisdiction configuration permits.' False by
    # default -- same fail-closed-to-'nothing extra permitted' posture as
    # every other boolean gate in this codebase's own market-pack-shaped
    # tables (e.g. MarketPolicyPack.occupancy_eligibility_required). A
    # market/jurisdiction must explicitly opt a Listing Fee into being
    # refundable at all; refund_window_days further bounds *how long* after
    # payment it stays eligible once opted in -- null means no time limit.
    refund_eligible: Mapped[bool] = mapped_column(default=False)
    refund_window_days: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ListingFeeQuote(Base):
    """ZR-PAY-002 Section 8.1/12.2 POST /listing-fees/quotes: an immutable,
    time-boxed price snapshot a checkout is created from. `policy_snapshot`
    freezes the resolving ListingFeePolicy's id/version/legal-entity/tax
    fields at quote time -- the same 'reproducible from the snapshot, not
    from whatever policy happens to be current' discipline
    crud/market_policy.py:to_policy_snapshot already applies to deposit/
    sublet/termination decisions."""

    __tablename__ = "listing_fee_quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing: Mapped["Listing"] = relationship()
    party: Mapped["Party"] = relationship()
    payments: Mapped[list["ListingFeePayment"]] = relationship(back_populates="quote")


class ListingFeePayment(Base):
    """ZR-PAY-002 Section 8.2/12.2 POST /listing-fees/checkout-sessions: one
    Listing Fee checkout attempt. `idempotency_key` is DB-unique, same
    retried-request guard as models/finance.py:SimulatedPayment.
    `provider_payment_intent_id` is a real Stripe PaymentIntent id when
    configured (created straight into Zoiko's own Stripe balance -- no
    Connect destination), otherwise a generated placeholder, same
    disclosed-simulation posture as the rent domain's own dispatch path.
    Amount/currency are copied from the quote at creation time and never
    re-resolved, so a later policy change can't retroactively alter an
    in-flight checkout."""

    __tablename__ = "listing_fee_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    quote_id: Mapped[int] = mapped_column(ForeignKey("listing_fee_quotes.id", ondelete="CASCADE"), nullable=False, index=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    billing_country: Mapped[str] = mapped_column(String(2), default="")
    # Known immediately at checkout creation (Stripe returns the Checkout
    # Session id synchronously) -- this is what the return-leg from Stripe's
    # hosted page resolves against (see crud/listing_fee.py:
    # get_payment_by_checkout_session_id), and what the webhook's
    # checkout.session.* handling looks this row up by.
    provider_checkout_session_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    # NOT known until the customer actually completes Stripe's hosted page --
    # Stripe only creates the underlying PaymentIntent at that point, not at
    # Checkout Session creation time (unlike a raw PaymentIntent, which is
    # confirmed to exist immediately). Backfilled by the webhook's
    # checkout.session.completed handling once it does.
    provider_payment_intent_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    failure_message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    quote: Mapped["ListingFeeQuote"] = relationship(back_populates="payments")
    listing: Mapped["Listing"] = relationship()
    party: Mapped["Party"] = relationship()
    receipt: Mapped["ListingFeeReceipt"] = relationship(back_populates="payment", uselist=False)
    refunds: Mapped[list["ListingFeeRefund"]] = relationship(back_populates="payment")


class ListingFeeReceipt(Base):
    """ZR-PAY-002 Section 8.5: the immutable receipt/invoice for one
    SUCCEEDED ListingFeePayment. Rendered and hashed exactly once -- same
    render-once-then-persist discipline as models/finance.py:PaymentReceipt.
    legal_entity_name/tax fields are frozen copies of the quote's own
    policy_snapshot, never re-read from a possibly-since-changed
    ListingFeePolicy row."""

    __tablename__ = "listing_fee_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("listing_fee_payments.id", ondelete="CASCADE"), unique=True, nullable=False)
    receipt_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    legal_entity_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_registration_number: Mapped[str] = mapped_column(String(50), default="")
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    tax_rate: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    payment: Mapped["ListingFeePayment"] = relationship(back_populates="receipt")


class ListingFeeRefund(Base):
    """ZR-PAY-002 Section 8.4/11: 'Issue Listing Fee refund' is a restricted
    admin action, gated at the route level (super_admin -- see
    api/routes/listing_fees.py). Single-attempt shape (no separate
    request/decide split like models/finance.py:RefundRequest): the Listing
    Fee is Zoiko's own direct charge, so a restricted admin's request IS the
    decision -- there is no separate 'business judgment about distributing
    someone else's custodied money' step the rent-refund domain has.
    `idempotency_key` is DB-unique, same retried-request guard as every
    other financial command in this codebase."""

    __tablename__ = "listing_fee_refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("listing_fee_payments.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reason: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(20), default="REQUESTED")
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    requested_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    failure_message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped["ListingFeePayment"] = relationship(back_populates="refunds")


class ListingFeeProviderEvent(Base):
    """Idempotent webhook event ledger for the Listing Fee's own Stripe
    events -- same provider_event_id-unique dedup idiom as
    models/finance.py:PaymentProviderEvent, kept as its own table rather than
    reused so a replayed rent-domain event id can never collide with (or be
    mistaken for) a Listing Fee one."""

    __tablename__ = "listing_fee_provider_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    listing_fee_payment_id: Mapped[int | None] = mapped_column(ForeignKey("listing_fee_payments.id"), nullable=True)
    listing_fee_refund_id: Mapped[int | None] = mapped_column(ForeignKey("listing_fee_refunds.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
