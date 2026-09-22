"""ZR-PAY-LINK-003 Section 6/Wireframe C: a payment recipient's own connected
Stripe account for receiving rent/deposit payments directly -- the
self-service, rent-domain counterpart to models/finance.py:HostStripeAccount
(that one is AdminUser-gated and backs Zoiko paying a host LATER out of
Zoiko's own balance; this one backs a renter paying a recipient DIRECTLY,
Zoiko's balance never touched -- see services/stripe_client.py:
create_rent_payment_checkout_session's own docstring for how that's enforced
at the Stripe API level). Kept as its own table for the same reason every
other rental-payment table is independent of the finance/payout domain (see
models/rental_payment.py's own module docstring)."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-PAY-LINK-003 Section 14.1: SUPERSEDED extends the original ONBOARDING/
# COMPLETE pair for crud/rental_payment_provider_account.py:
# request_account_change/confirm_account_change's own "change this
# destination" flow -- same shape as RentalPaymentInstruction's own
# PENDING_VERIFICATION->ACTIVE->SUPERSEDED lifecycle (models/rental_payment.py),
# not PaymentRecipientAuthority's, since this is a destination object, not
# an authority claim. Requesting a change does NOT move the current row to
# some "pending" status of its own -- the account stays exactly as usable
# as it already was (ONBOARDING/COMPLETE) right up until confirm_account_change
# actually supersedes it; the step-up code only ever gates the *new* row's
# creation, never blocks continued use of the still-current destination.
RENTAL_PAYMENT_PROVIDER_ACCOUNT_STATUSES = ("ONBOARDING", "COMPLETE", "SUPERSEDED")


class RentalPaymentProviderAccount(Base):
    """At most one CURRENT (ONBOARDING/COMPLETE) row per party -- the
    partial unique index below, replacing a hard party-unique constraint,
    so a SUPERSEDED row from a confirmed account change can coexist with
    its replacement (same reasoning RentalPaymentInstruction's own partial
    unique index documents). `status` is derived, never assigned directly
    except by crud/rental_payment_provider_account.py's own _sync_status/
    confirm_account_change, same discipline as HostStripeAccount's own
    status field.

    verification_code_hash/_expires_at/_attempts and is_high_risk/
    high_risk_reason are only ever populated on the CURRENT row while a
    change is pending (PENDING_CHANGE_STEP_UP) -- there is no new row to
    hold them yet at that point, since creating the real Stripe Connect
    account (confirm_account_change) must happen only after step-up
    succeeds, never before (a failed step-up attempt must never leave a
    throwaway real Stripe account behind)."""

    __tablename__ = "rental_payment_provider_accounts"
    __table_args__ = (
        Index(
            "uq_rental_payment_provider_accounts_active_party", "party_id", unique=True,
            postgresql_where=text("status != 'SUPERSEDED'"), sqlite_where=text("status != 'SUPERSEDED'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False, index=True)
    stripe_account_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="ONBOARDING")
    details_submitted: Mapped[bool] = mapped_column(Boolean, default=False)
    charges_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_code_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_attempts: Mapped[int] = mapped_column(default=0)
    is_high_risk: Mapped[bool] = mapped_column(default=False)
    high_risk_reason: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
