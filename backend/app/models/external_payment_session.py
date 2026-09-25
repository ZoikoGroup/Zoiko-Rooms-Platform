"""ZR-PAY-LINK-003 Section 6/10.1/Wireframe F: a short-lived, provider-hosted
payment handoff for one rental_payment_obligation. Kept independent of
models/finance.py's own PaymentAllocation/SimulatedPayment (same "no shared
tables" rule as every other rental-payment table -- models/rental_payment.py's
own module docstring) and, importantly, is never itself a Zoiko-held
balance -- it only ever records that a session was started and, once a
webhook or self-heal read confirms it, hands off to
crud/rental_payment.py's own RentalPaymentRecord creation. The money itself
moves entirely within Stripe, directly to the recipient's own connected
account (services/stripe_client.py:create_rent_payment_checkout_session)."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

EXTERNAL_PAYMENT_SESSION_STATUSES = ("STARTED", "SUCCEEDED", "FAILED")


class ExternalPaymentSession(Base):
    """`recipient_stripe_account_id` is snapshotted at creation, never
    re-resolved later -- Section 10.1's own edge case ('Destination changes
    while payment session open -- Existing session obeys provider/session
    policy... Prevent ambiguous routing') means this session must keep
    routing to the account it was actually created against even if the
    recipient's connection changes underneath it before it resolves."""

    __tablename__ = "external_payment_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(ForeignKey("rental_payment_obligations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_guest_id: Mapped[str] = mapped_column(ForeignKey("guests.id", ondelete="CASCADE"), nullable=False)
    recipient_stripe_account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_checkout_session_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    provider_payment_intent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="STARTED")
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    failure_message: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    obligation: Mapped["RentalPaymentObligation"] = relationship()
    tenant: Mapped["Guest"] = relationship()


class RentalPaymentProviderEvent(Base):
    """Idempotent webhook event ledger for this domain's own Stripe events --
    same provider_event_id-unique dedup idiom as
    models/listing_fee.py:ListingFeeProviderEvent, kept as its own table so a
    replayed Listing Fee event id can never collide with (or be mistaken
    for) a rent-payment one, and vice versa."""

    __tablename__ = "rental_payment_provider_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    external_payment_session_id: Mapped[int | None] = mapped_column(ForeignKey("external_payment_sessions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
