"""ZR-ENG-CLR-005 Section 9.1: the one place this codebase actually talks to
Stripe. Every function here falls back to exactly the pre-existing simulated
behavior (a generated id, no network call) when settings.stripe_secret_key
is blank -- so every test and every dev environment without real Stripe
credentials keeps working unchanged, and setting real keys activates the
real integration without any other code needing to change. This mirrors how
SimulatedPayment/SignatureRequest already simulate their respective external
providers; it does not replace that simulation, it gives it a real backing
implementation that switches on by configuration alone.

Money model: Stripe amounts are integers in the currency's smallest unit
(pence for GBP, cents for USD) except for a small set of zero-decimal
currencies Stripe defines, where the integer *is* the major unit."""

from __future__ import annotations

from app.core.config import settings
from app.crud.ids import new_id

# https://docs.stripe.com/currencies#zero-decimal -- currencies Stripe expects
# as a whole-unit integer rather than its usual smallest-unit convention.
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}


def is_configured() -> bool:
    return bool(settings.stripe_secret_key)


def to_minor_units(amount: float, currency: str) -> int:
    if currency.upper() in ZERO_DECIMAL_CURRENCIES:
        return round(amount)
    return round(amount * 100)


def from_minor_units(amount: int, currency: str) -> float:
    if currency.upper() in ZERO_DECIMAL_CURRENCIES:
        return float(amount)
    return round(amount / 100, 2)


def _client():
    import stripe

    stripe.api_key = settings.stripe_secret_key
    return stripe


def create_payment_intent(*, amount: float, currency: str, metadata: dict) -> str:
    """Returns the provider transaction id -- a real Stripe PaymentIntent id
    (pi_...) when configured, otherwise the same PAYTXN-... id the simulated
    path always generated."""
    if not is_configured():
        return new_id("PAYTXN")
    stripe = _client()
    intent = stripe.PaymentIntent.create(
        amount=to_minor_units(amount, currency), currency=currency.lower(), metadata=metadata,
        # confirm=False (default): we only create the intent server-side here;
        # the client confirms it with a payment method, which is what
        # actually triggers the payment_intent.succeeded webhook this
        # module's caller (crud/payment_provider.py) is built to receive.
    )
    return intent.id


def create_connected_account(*, country: str, email: str, metadata: dict) -> str:
    """Returns the provider account id -- a real Stripe Connect Express
    account id (acct_...) when configured, otherwise a generated
    placeholder with the same shape."""
    if not is_configured():
        return new_id("ACCT")
    stripe = _client()
    account = stripe.Account.create(
        type="express", country=country, email=email, metadata=metadata,
        capabilities={"transfers": {"requested": True}},
    )
    return account.id


def create_account_onboarding_link(*, stripe_account_id: str) -> str:
    """Returns the hosted onboarding URL the host is redirected to. Without
    real credentials there is no real Stripe-hosted page to link to --
    returns a clearly-labeled placeholder rather than a URL that would 404,
    matching the disclosed-simulation posture of every other provider stub
    in this codebase."""
    if not is_configured():
        return f"https://stripe-onboarding.simulated.invalid/{stripe_account_id}"
    stripe = _client()
    link = stripe.AccountLink.create(
        account=stripe_account_id,
        refresh_url=settings.stripe_connect_refresh_url,
        return_url=settings.stripe_connect_return_url,
        type="account_onboarding",
    )
    return link.url


def retrieve_account_status(*, stripe_account_id: str) -> dict:
    """Returns {details_submitted, charges_enabled, payouts_enabled}.
    Without real credentials there is nothing to retrieve -- returns the
    account's already-stored status unchanged (the caller keeps whatever
    was last set, e.g. via the simulate-onboarding-complete test path)."""
    if not is_configured():
        return {}
    stripe = _client()
    account = stripe.Account.retrieve(stripe_account_id)
    return {
        "details_submitted": bool(account.details_submitted),
        "charges_enabled": bool(account.charges_enabled),
        "payouts_enabled": bool(account.payouts_enabled),
    }


def create_transfer(*, amount: float, currency: str, destination_account_id: str, metadata: dict) -> str:
    """Returns the provider transfer id -- a real Stripe Transfer id
    (tr_...) when configured, otherwise a generated placeholder. This is
    the actual money movement from Zoiko's own Stripe balance to the host's
    Connected Account -- the 'separate charges and transfers' pattern the
    spec's PSP_DEFERRED_PAYOUT profile needs (the transfer happens later,
    as its own call, not as part of the original PaymentIntent)."""
    if not is_configured():
        return new_id("TR")
    stripe = _client()
    transfer = stripe.Transfer.create(
        amount=to_minor_units(amount, currency), currency=currency.lower(),
        destination=destination_account_id, metadata=metadata,
    )
    return transfer.id


def create_setup_intent(*, customer_email: str, metadata: dict) -> str:
    """Returns the provider setup-intent id -- a real Stripe SetupIntent id
    (seti_...) when configured, otherwise a generated placeholder. This is
    the real primitive behind an autopay mandate (Section 10.5): saving a
    payment method for future off-session charges, distinct from a
    PaymentIntent's one-off collection -- see crud/finance.py:create_
    autopay_mandate."""
    if not is_configured():
        return new_id("SETI")
    stripe = _client()
    customer = stripe.Customer.create(email=customer_email, metadata=metadata)
    intent = stripe.SetupIntent.create(customer=customer.id, usage="off_session", metadata=metadata)
    return intent.id


def reverse_transfer(*, transfer_id: str, amount: float, currency: str, metadata: dict) -> str:
    """Returns the provider transfer-reversal id -- a real Stripe
    TransferReversal id (trr_...) when configured, otherwise a generated
    placeholder. ZR-ENG-CLR-006 Section 15 waterfall tier 3: pulls money the
    host's Connected Account already received back into Zoiko's own Stripe
    balance, up to what that specific transfer actually moved -- Stripe
    itself enforces that ceiling server-side when real credentials are
    configured; the simulated fallback (no credentials) trusts the caller's
    own amount the same way every other simulated path in this module does."""
    if not is_configured():
        return new_id("TRR")
    stripe = _client()
    reversal = stripe.Transfer.create_reversal(
        transfer_id, amount=to_minor_units(amount, currency), metadata=metadata,
    )
    return reversal.id


def construct_webhook_event(*, payload: bytes, signature_header: str):
    """Verifies a real Stripe webhook signature and returns the parsed
    Event. Raises stripe.error.SignatureVerificationError on a bad/forged
    signature -- the caller (the public webhook route) must let that surface
    as a 400, never as a 200 that would tell an attacker their forged
    payload was accepted."""
    stripe = _client()
    return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_webhook_secret)
