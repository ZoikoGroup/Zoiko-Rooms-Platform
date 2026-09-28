"""Checks, and optionally creates, the Stripe webhook endpoint the rent
payment flow depends on.

Rent is a Stripe Connect direct charge on the host's own connected account,
so every event about it (checkout completed, refunds, chargebacks, account
onboarding) is a *connected-account* event. Stripe only delivers those to an
endpoint registered as a Connect endpoint ("Listen to events on connected
accounts" in the Dashboard, connect=true in the API). An ordinary account
endpoint at the same URL silently never receives them -- rent paid after the
tenant closes the tab, host refunds and chargebacks would then never reach
Zoiko Rooms.

Usage (from backend/, with STRIPE_SECRET_KEY set):
    python check_stripe_webhooks.py https://api.zoikorooms.com
    python check_stripe_webhooks.py https://api.zoikorooms.com --create

--create registers the endpoint if it is missing and prints its signing
secret once -- put that in STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET.
Exits non-zero when the setup is incomplete, so it can gate a deploy.
"""

from __future__ import annotations

import sys

from app.core.config import settings

RENT_WEBHOOK_PATH = "/api/finance/rental-payments/stripe/webhook"
# Everything crud/external_payment_session.py:STRIPE_EVENT_TYPE_MAP and the
# route's own account.updated handling act on.
REQUIRED_EVENTS = {
    "checkout.session.completed",
    "checkout.session.async_payment_failed",
    "charge.refunded",
    "charge.refund.updated",
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
    "account.updated",
}


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
    url = args[0].rstrip("/") + RENT_WEBHOOK_PATH

    endpoints = [e for e in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter() if e.url == url]
    # A Connect endpoint is tied to the platform's Connect application.
    connect_endpoints = [e for e in endpoints if getattr(e, "application", None)]
    problems: list[str] = []

    if not connect_endpoints:
        if endpoints:
            problems.append(
                f"{url} is registered, but as an ordinary account endpoint -- it will never receive "
                "connected-account (rent) events. Register it as a Connect endpoint."
            )
        else:
            problems.append(f"No Connect webhook endpoint is registered for {url}.")
        if create:
            endpoint = stripe.WebhookEndpoint.create(
                url=url, connect=True, enabled_events=sorted(REQUIRED_EVENTS),
                description="Zoiko Rooms rent payments (connected accounts)",
            )
            print(f"Created Connect endpoint {endpoint.id} for {url}.")
            print(f"Set STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET={endpoint.secret}")
            return 0
    for endpoint in connect_endpoints:
        if endpoint.status != "enabled":
            problems.append(f"Connect endpoint {endpoint.id} is {endpoint.status}.")
        enabled = set(endpoint.enabled_events or [])
        if "*" not in enabled and not REQUIRED_EVENTS <= enabled:
            problems.append(f"Connect endpoint {endpoint.id} is missing events: {sorted(REQUIRED_EVENTS - enabled)}")

    if not (settings.stripe_rental_payment_webhook_secret or settings.stripe_webhook_secret):
        problems.append("STRIPE_RENTAL_PAYMENT_WEBHOOK_SECRET is not set -- every delivery will be rejected.")

    if problems:
        print("Rent webhook setup is incomplete:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"OK: {url} is a Connect endpoint receiving every rent payment event.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
