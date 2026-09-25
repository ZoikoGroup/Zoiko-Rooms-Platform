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

import logging
import time
from typing import Literal

from app.core.config import settings
from app.crud.ids import new_id

logger = logging.getLogger(__name__)

# Listing Fee hosted checkout lifetime -- see create_checkout_session.
CHECKOUT_SESSION_TTL_SECONDS = 31 * 60

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


def stripe_client_v2():
    """The Accounts v2 API (client.v2.core.accounts...) is only reachable
    through the newer stripe.StripeClient instance -- the legacy module-
    level stripe.api_key + stripe.Account.create pattern _client() returns
    is v1-only. A fresh client per call, same as _client() re-setting
    stripe.api_key every time -- this module never caches either across
    calls."""
    import stripe

    return stripe.StripeClient(settings.stripe_secret_key)


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


def create_checkout_session(
    *, amount: float, currency: str, metadata: dict, success_url: str, cancel_url: str,
    product_name: str = "Zoiko Rooms Listing Fee", idempotency_key: str | None = None,
) -> tuple[str, str]:
    """ZR-PAY-002 Section 8.2's PCI boundary: the Listing Fee checkout is a
    real, separate Stripe-hosted payment page -- the customer is redirected
    there directly by the browser (window.location, a real navigation, not
    an in-page component this app renders) and Stripe redirects back to
    success_url/cancel_url once done. This is the one call site in this
    codebase that hands a real payment page to a client -- unlike
    create_payment_intent above, whose callers never expose anything to a
    browser. No `on_behalf_of`/`transfer_data` -- this charges straight into
    Zoiko's own Stripe balance, never a Connected Account, matching the
    Listing Fee's 'direct fee collector / merchant relationship' role.
    Returns (checkout_session_id, checkout_url); checkout_url is empty in
    the simulated (unconfigured) fallback -- the payment already completed
    synchronously in that case, same disclosed-simulation posture as every
    other stub here.

    checkout_session_id, not a PaymentIntent id, is what's returned and
    stored (ListingFeePayment.provider_checkout_session_id) -- Stripe does
    NOT create the underlying PaymentIntent until the customer actually
    completes the hosted page (session.payment_intent is None right after
    this call returns, confirmed against the real API), unlike a raw
    PaymentIntent, which exists the instant it's created. The webhook's
    checkout.session.completed handling backfills
    ListingFeePayment.provider_payment_intent_id once Stripe actually
    creates one.

    idempotency_key, when supplied, is passed straight to Stripe's own
    idempotency mechanism (Stripe-Idempotency-Key) -- ZR-PAY-002 Section
    13: 'Idempotency: Required for fee-payment and refund commands.' This
    is what actually stops a network-level retry (client timeout, our own
    502-then-retry) from creating two separate real Checkout Sessions for
    one logical checkout attempt; this app's own idempotency_key unique-DB-
    constraint guard only ever catches a retry that reaches our database,
    not one that never got a response the first time."""
    if not is_configured():
        return new_id("PAYTXN"), ""
    stripe = _client()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "product_data": {"name": product_name},
                "unit_amount": to_minor_units(amount, currency),
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        # Match the 30-minute Listing Fee quote instead of Stripe's 24-hour
        # default; Stripe's own minimum is 30 minutes from creation, so one
        # extra minute keeps clock skew from rejecting the request.
        expires_at=int(time.time()) + CHECKOUT_SESSION_TTL_SECONDS,
        idempotency_key=idempotency_key,
    )
    return session.id, session.url or ""


def create_rent_payment_checkout_session(
    *, amount: float, currency: str, connected_account_id: str, metadata: dict, success_url: str, cancel_url: str,
    product_name: str = "Rent payment", idempotency_key: str | None = None,
) -> tuple[str, str]:
    """ZR-PAY-LINK-003 Section 1/G3: 'No rental fund settles to Zoiko Rooms.'
    Deliberately NOT create_checkout_session above -- that one charges
    straight into Zoiko's own Stripe balance by design (correct for the
    Listing Fee, Zoiko's own merchant charge; wrong here). This issues the
    Checkout Session as a Stripe Connect **direct charge**: the `stripe_account`
    request option makes the connected account itself the merchant of
    record, so the customer's payment settles directly there -- Zoiko's own
    balance is never touched, not even transiently, unlike a destination
    charge (`transfer_data`) or `on_behalf_of`, either of which would still
    route funds through the platform account first. Must never be used for
    the Listing Fee, and create_checkout_session must never be used for
    rent -- the two are not interchangeable despite the near-identical
    shape. Same simulated fallback posture as every other function here:
    without real credentials, returns a placeholder id and no URL."""
    if not is_configured():
        return new_id("PAYTXN"), ""
    stripe = _client()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "product_data": {"name": product_name},
                "unit_amount": to_minor_units(amount, currency),
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
        idempotency_key=idempotency_key,
        stripe_account=connected_account_id,
    )
    return session.id, session.url or ""


def retrieve_checkout_session(*, checkout_session_id: str) -> dict | None:
    """Reconciliation read, not the primary success signal -- the webhook
    (checkout.session.completed/async_payment_succeeded, see
    crud/listing_fee.py:ingest_stripe_webhook_event) is still what actually
    completes a payment. This exists so the customer's own return trip to
    success_url isn't left hostage to webhook delivery having happened by
    the time they land there (local dev without `stripe listen` running,
    or ordinary webhook latency in any environment) -- the return page can
    ask Stripe directly, right now, whether it already knows this session
    succeeded, and self-heal immediately instead of only polling and
    waiting. Returns {payment_status, payment_intent_id, status, url} when
    configured, None otherwise -- nothing real to check without credentials,
    same disclosed-simulation posture as every other function in this
    module. `status` is the session's own open/complete/expired lifecycle
    and `url` its hosted page, which lets a repeat checkout attempt resume
    a still-open session instead of opening a second, separately payable one."""
    if not is_configured():
        return None
    stripe = _client()
    session = stripe.checkout.Session.retrieve(checkout_session_id)
    return {
        "payment_status": session.payment_status,
        "payment_intent_id": session.payment_intent,
        "status": session.status,
        "url": session.url or "",
    }


def retrieve_rent_payment_checkout_session(*, checkout_session_id: str, connected_account_id: str) -> dict | None:
    """Same reconciliation-read role as retrieve_checkout_session above, but
    for a create_rent_payment_checkout_session direct charge -- that session
    lives on the CONNECTED account, not the platform account, so it must be
    retrieved with the same `stripe_account` request option it was created
    with, or Stripe will simply not find it."""
    if not is_configured():
        return None
    stripe = _client()
    session = stripe.checkout.Session.retrieve(checkout_session_id, stripe_account=connected_account_id)
    return {"payment_status": session.payment_status, "payment_intent_id": session.payment_intent}


def create_refund(
    *, payment_intent_id: str, amount: float, currency: str, metadata: dict, idempotency_key: str | None = None,
) -> str:
    """Returns the provider refund id -- a real Stripe Refund id (re_...)
    when configured, otherwise a generated placeholder. Shared by both
    crud/listing_fee.py:request_refund (ZR-PAY-002 Section 8.4: refunds a
    Listing Fee PaymentIntent directly, Zoiko's own charge) and
    crud/finance.py's own decide_refund (Section 5 gap: pulling money back
    out of Stripe for the rent/payout domain's PSP_DIRECT-collected
    payments) -- both refund a PaymentIntent directly, never a
    Transfer/TransferReversal (see reverse_transfer above for that separate
    mechanism). idempotency_key is optional since only the Listing Fee
    caller currently supplies one -- see
    create_payment_intent_with_client_secret's own docstring for why it's
    passed to Stripe's own idempotency mechanism, not just guarded at the
    DB layer."""
    if not is_configured():
        return new_id("RE")
    stripe = _client()
    refund = stripe.Refund.create(
        payment_intent=payment_intent_id, amount=to_minor_units(amount, currency), metadata=metadata,
        idempotency_key=idempotency_key,
    )
    return refund.id


def create_connected_account(
    *, country: str, email: str, metadata: dict, configuration: Literal["merchant", "recipient"],
) -> str:
    """Returns the provider account id -- a real Stripe Connect Express
    account id (acct_...) when configured, otherwise a generated
    placeholder with the same shape.

    Stripe Accounts v2 (POST /v2/core/accounts), not v1 -- Stripe now
    rejects v1 Account creation by default for new Connect integrations
    (InvalidRequestError account_controller_express_dash_without_
    application_losses_or_fees is what surfaces if this ever regresses to
    v1). This module has TWO callers with genuinely different money-flow
    shapes that the old v1 call conflated under one hardcoded
    capabilities={'transfers': ...} body:
    - crud/rental_payment_provider_account.py (ZR-PAY-LINK-003's
      non-custodial rent rail): the connected account IS the merchant of
      record for a Stripe Connect direct charge -- needs the 'merchant'
      configuration's card_payments capability.
    - crud/host_stripe_account.py (the legacy ZR-ENG-CLR-005 payout rail,
      separate-charges-and-transfers): the connected account only ever
      RECEIVES a Transfer from Zoiko's own balance -- needs the
      'recipient' configuration's stripe_balance.stripe_transfers
      capability instead.
    dashboard='express' preserves today's Express-dashboard host
    experience; per Stripe's own validation
    (account_controller_express_dash_without_application_losses_or_fees),
    that requires fees_collector/losses_collector both be 'application' --
    not a free choice, the only combination Express dashboards accept.
    stripe_version is left to the installed SDK's own default
    (stripe._api_version._ApiVersion.CURRENT) rather than hardcoded here,
    so a future stripe-python upgrade tracks Stripe's own version
    promotion automatically."""
    if not is_configured():
        return new_id("ACCT")
    client = stripe_client_v2()
    # Confirmed against a real Stripe test account: v2 rejects
    # recipient.capabilities.stripe_balance.stripe_transfers on its own --
    # "cannot be requested without the configuration.merchant.capabilities.
    # card_payments capability" -- so the 'recipient' case still has to
    # request merchant.card_payments alongside it, even though this
    # account never directly collects a card payment itself.
    configuration_params = (
        {"merchant": {"capabilities": {"card_payments": {"requested": True}}}}
        if configuration == "merchant"
        else {
            "merchant": {"capabilities": {"card_payments": {"requested": True}}},
            "recipient": {"capabilities": {"stripe_balance": {"stripe_transfers": {"requested": True}}}},
        }
    )
    account = client.v2.core.accounts.create(params={
        "contact_email": email,
        "dashboard": "express",
        "identity": {"country": country},
        "configuration": configuration_params,
        "defaults": {"responsibilities": {"fees_collector": "application", "losses_collector": "application"}},
        "metadata": metadata,
    })
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


def retrieve_payment_intent(*, payment_intent_id: str) -> dict | None:
    """Section 5 gap: reconciliation (crud/finance.py:run_reconciliation)
    previously only ever checked this platform's own internal tables
    against each other -- never against what Stripe itself actually
    recorded for a real-PSP-dispatched payment. Returns
    {amount_received, currency, status} when configured, None otherwise --
    there's nothing real to check without credentials, so the caller treats
    None as "skip this check", the same disclosed-simulation posture as
    every other function in this module."""
    if not is_configured():
        return None
    stripe = _client()
    intent = stripe.PaymentIntent.retrieve(payment_intent_id)
    return {"amount_received": int(intent.amount_received), "currency": intent.currency, "status": intent.status}


def construct_webhook_event(*, payload: bytes, signature_header: str, secret: str | None = None):
    """Verifies a real Stripe webhook signature and returns the parsed
    Event. Raises stripe.error.SignatureVerificationError on a bad/forged
    signature -- the caller (the public webhook route) must let that surface
    as a 400, never as a 200 that would tell an attacker their forged
    payload was accepted. `secret` lets a caller verify against a different
    endpoint's own signing secret (e.g. the Listing Fee webhook route) --
    defaults to the shared settings.stripe_webhook_secret, unchanged for
    every existing call site.

    Fails closed when no signing secret resolves at all -- an unconfigured
    endpoint rejects every delivery as unverifiable, and logs why, instead
    of leaving ops to guess from a stream of anonymous 400s in Stripe's
    dashboard."""
    stripe = _client()
    resolved_secret = secret or settings.stripe_webhook_secret
    if not resolved_secret:
        logger.error("stripe webhook: no signing secret configured for this endpoint -- rejecting delivery")
        raise stripe.error.SignatureVerificationError("Webhook signing secret is not configured", signature_header)
    return stripe.Webhook.construct_event(payload, signature_header, resolved_secret)
