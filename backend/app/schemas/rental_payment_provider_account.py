from datetime import datetime

from pydantic import computed_field

from app.schemas.common import CamelModel


class RentalPaymentProviderAccountRead(CamelModel):
    """Never the raw stripe_account_id -- same masking posture as
    RentalPaymentInstructionRead never exposing a full account identifier."""

    id: int
    status: str
    details_submitted: bool
    charges_enabled: bool
    payouts_enabled: bool
    is_high_risk: bool = False
    high_risk_reason: str = ""
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def can_simulate_onboarding(self) -> bool:
        """Whether the dev-only 'simulate onboarding complete' action will be
        accepted -- only while no real Stripe key is configured (see
        crud/rental_payment_provider_account.py:simulate_onboarding_complete).
        The frontend shows that button off this, never off its own build mode."""
        from app.services import stripe_client

        return not stripe_client.is_configured()


class RentalPaymentProviderAccountCreate(CamelModel):
    country: str
    email: str


class RentalPaymentProviderAccountConnectResult(CamelModel):
    account: RentalPaymentProviderAccountRead
    onboarding_url: str


class RentalPaymentProviderAccountChangeRequestResult(CamelModel):
    """ZR-PAY-LINK-003 Section 14.1 -- confirms a step-up code was sent,
    without exposing the code itself or the pending country/email (the
    caller already knows what they submitted)."""

    sent: bool = True


class RentalPaymentProviderAccountConfirmChange(CamelModel):
    code: str
    country: str
    email: str
