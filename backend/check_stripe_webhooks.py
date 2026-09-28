"""Checks, and optionally creates, the Stripe webhook endpoints Zoiko Rooms
depends on.

- Listing Fee (always): /api/finance/listing-fees/stripe/webhook, an
  ordinary *account* endpoint -- the Listing Fee is charged on Zoiko's own
  Stripe account. It must receive checkout completion/expiry, refunds
  (including failed ones) and disputes, or paid fees can sit pending,
  refunds can stick in "processing", and chargebacks go unnoticed.
- Rent (only while RENT_CARD_CHECKOUT_ENABLED is on): /api/finance/
  rental-payments/stripe/webhook, a *Connect* endpoint -- rent is charged on
  the host's own connected account, and Stripe only sends those events to an
  endpoint registered for connected accounts. With rent paid to hosts
  directly (the default) it isn't needed.

Usage (from backend/, with STRIPE_SECRET_KEY set):
    python check_stripe_webhooks.py https://api.zoikorooms.com
    python check_stripe_webhooks.py https://api.zoikorooms.com --create

--create registers any missing endpoint and prints its signing secret once
-- put it in STRIPE_LISTING_FEE_WEBHOOK_SECRET / STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET.
Exits non-zero when the setup is incomplete, so it can gate a deploy.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class EndpointSpec:
    label: str
    path: str
    connect: bool
    events: frozenset[str]
    secret_setting: str


LISTING_FEE = EndpointSpec(
    label="Listing Fee",
    path="/api/finance/listing-fees/stripe/webhook",
    connect=False,
    # Everything crud/listing_fee.py:STRIPE_EVENT_TYPE_MAP acts on.
    events=frozenset({
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
        "checkout.session.expired",
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "charge.refunded",
        "charge.refund.updated",
        "refund.updated",
        "refund.failed",
        "charge.dispute.created",
        "charge.dispute.closed",
    }),
    secret_setting="stripe_listing_fee_webhook_secret",
)

RENT = EndpointSpec(
    label="Rent (card checkout)",
    path="/api/finance/rental-payments/stripe/webhook",
    connect=True,
    # Everything crud/external_payment_session.py:STRIPE_EVENT_TYPE_MAP and the
    # route's own account.updated handling act on.
    events=frozenset({
        "checkout.session.completed",
        "checkout.session.async_payment_failed",
        "charge.refunded",
        "charge.refund.updated",
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.closed",
        "account.updated",
    }),
    secret_setting="stripe_rental_payment_webhook_secret",
)


def _check(stripe, base_url: str, spec: EndpointSpec, *, create: bool, all_endpoints: list) -> list[str]:
    url = base_url + spec.path
    at_url = [e for e in all_endpoints if e.url == url]
    # A Connect endpoint is tied to the platform's Connect application.
    matching = [e for e in at_url if bool(getattr(e, "application", None)) == spec.connect]
    problems: list[str] = []
    kind = "Connect" if spec.connect else "account"

    if not matching:
        if at_url:
            problems.append(
                f"{spec.label}: {url} is registered, but not as {'a Connect' if spec.connect else 'an account'} "
                f"endpoint -- it won't receive the right events."
            )
        else:
            problems.append(f"{spec.label}: no {kind} webhook endpoint is registered for {url}.")
        if create:
            endpoint = stripe.WebhookEndpoint.create(
                url=url, connect=spec.connect, enabled_events=sorted(spec.events),
                description=f"Zoiko Rooms -- {spec.label}",
            )
            print(f"Created {kind} endpoint {endpoint.id} for {url}.")
            print(f"Set {spec.secret_setting.upper()}={endpoint.secret}")
            return []
    for endpoint in matching:
        if endpoint.status != "enabled":
            problems.append(f"{spec.label}: endpoint {endpoint.id} is {endpoint.status}.")
        enabled = set(endpoint.enabled_events or [])
        if "*" not in enabled and not spec.events <= enabled:
            problems.append(f"{spec.label}: endpoint {endpoint.id} is missing events {sorted(spec.events - enabled)}")

    if not (getattr(settings, spec.secret_setting) or settings.stripe_webhook_secret):
        problems.append(f"{spec.label}: {spec.secret_setting.upper()} is not set -- every delivery will be rejected.")
    return problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    create = "--create" in argv
    if not args:
        print(__doc__)
        return 2
    if not settings.stripe_secret_key:
        print("STRIPE_SECRET_KEY is not set -- nothing to check.")
        return 2

    import stripe

    stripe.api_key = settings.stripe_secret_key
    base_url = args[0].rstrip("/")
    all_endpoints = list(stripe.WebhookEndpoint.list(limit=100).auto_paging_iter())

    specs = [LISTING_FEE] + ([RENT] if settings.rent_card_checkout_enabled else [])
    problems: list[str] = []
    for spec in specs:
        problems += _check(stripe, base_url, spec, create=create, all_endpoints=all_endpoints)

    if problems:
        print("Stripe webhook setup is incomplete:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: " + ", ".join(spec.label for spec in specs) + " webhook(s) registered with every required event.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
