"""ZR-PAY-002 Section 8/12: the Listing Fee is the only payment Zoiko Rooms
collects for itself -- a direct fee-collector/merchant relationship, quoted
and charged straight into Zoiko's own Stripe balance (never a Connect
destination). Mirrors the rent domain's real-Stripe-or-simulated-fallback
posture (app/services/stripe_client.py) and its dispatch/webhook-confirm
shape (crud/payment_provider.py), but is kept fully independent of it: no
shared tables, no shared crud calls into models/finance.py, per Section
12.1's architecture rule."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO

from fastapi import HTTPException, status
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.listing_fee_receipt_documents import save_listing_fee_receipt_document
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.crud.ids import new_id
from app.crud import notification as notif_crud
from app.models.admin_user import AdminUser
from app.models.listing import Listing
from app.models.listing_fee import (
    LISTING_FEE_QUOTE_TTL_SECONDS,
    LISTING_FEE_TAX_BEHAVIORS,
    ListingFeePayment,
    ListingFeePolicy,
    ListingFeeProviderEvent,
    ListingFeeQuote,
    ListingFeeReceipt,
    ListingFeeRefund,
)
from app.models.party import Party
from app.services import stripe_client

logger = logging.getLogger("uvicorn.error")

DEFAULT_JURISDICTION = "England"


def _round2(amount) -> float:
    return round(float(amount), 2)


def listing_jurisdiction_code(listing: Listing) -> str | None:
    """Same room -> property traversal crud/payment_provider.py:
    obligation_jurisdiction_code already uses for the rent domain, applied
    to a Listing instead of an Obligation."""
    if listing.room and listing.room.property:
        return listing.room.property.jurisdiction_code
    return None


# ZR-PAY-CFG-001 Section 2.2: the exact copy the listing flow shows when a
# market has no chargeable price.
LISTING_FEE_UNAVAILABLE_MESSAGE = "Listing fee currently unavailable in this market."


def _current_environment() -> str:
    return settings.environment.strip().lower()


def resolve_listing_fee_policy(db: Session, jurisdiction_code: str = DEFAULT_JURISDICTION, *, as_of: date | None = None) -> ListingFeePolicy:
    """The single entry point Listing Fee quoting must use -- never
    hard-code an amount or branch on jurisdiction_code directly.

    ZR-PAY-CFG-001: only an ACTIVE (approved) Price Book entry for this
    market, created in this environment (PAY-CFG-12), and effective now can
    resolve. With listing_fee_fail_closed on, it must also carry the billing
    entity and tax configuration a production charge needs (Decisions 2 and
    5). Anything missing fails closed with LISTING_FEE_UNAVAILABLE_MESSAGE --
    never an invented default price or a currency conversion (PAY-CFG-01/02)."""
    as_of = as_of or date.today()
    policy = db.scalar(
        select(ListingFeePolicy)
        .where(
            ListingFeePolicy.jurisdiction_code == jurisdiction_code,
            ListingFeePolicy.status == "ACTIVE",
            ListingFeePolicy.environment == _current_environment(),
            ListingFeePolicy.effective_from <= as_of,
            (ListingFeePolicy.effective_to.is_(None)) | (ListingFeePolicy.effective_to >= as_of),
        )
        .order_by(ListingFeePolicy.version.desc())
        .limit(1)
    )
    if not policy:
        raise HTTPException(status.HTTP_409_CONFLICT, LISTING_FEE_UNAVAILABLE_MESSAGE)
    if settings.listing_fee_fail_closed and _price_activation_problems(db, policy, as_of=as_of):
        raise HTTPException(status.HTTP_409_CONFLICT, LISTING_FEE_UNAVAILABLE_MESSAGE)
    return policy


def listing_fee_available(db: Session, jurisdiction_code: str) -> bool:
    try:
        resolve_listing_fee_policy(db, jurisdiction_code)
    except HTTPException:
        return False
    return True


def _price_activation_problems(db: Session, policy: ListingFeePolicy, *, as_of: date | None = None) -> list[str]:
    """Everything a Price Book entry needs before it may be charged in
    production (ZR-PAY-CFG-001 Sections 2, 3, 7 and 9.1)."""
    from app.models.market_release import MarketRelease

    as_of = as_of or date.today()
    problems: list[str] = []
    if not db.scalar(select(MarketRelease.id).where(MarketRelease.jurisdiction == policy.jurisdiction_code)):
        problems.append(f"'{policy.jurisdiction_code}' is not a configured market")
    if price_amount_minor(policy) < 0:
        problems.append("The amount cannot be negative")
    if policy.tax_behavior not in LISTING_FEE_TAX_BEHAVIORS:
        problems.append(f"Tax behavior must be one of {list(LISTING_FEE_TAX_BEHAVIORS)}")
    if not (policy.tax_rule_reference or "").strip():
        problems.append("A Finance/Tax-approved tax rule reference is required")
    entity = policy.billing_entity
    if entity is None:
        problems.append("A billing entity is required")
    else:
        if not entity.is_effective(as_of):
            problems.append(f"Billing entity {entity.code} is not active")
        if not entity.supports(policy.jurisdiction_code, policy.currency):
            problems.append(
                f"Billing entity {entity.code} is not approved to bill {policy.currency} in {policy.jurisdiction_code}"
            )
    return problems


def list_listing_fee_policies(db: Session, jurisdiction_code: str | None = None) -> list[ListingFeePolicy]:
    query = select(ListingFeePolicy).order_by(ListingFeePolicy.jurisdiction_code, ListingFeePolicy.version.desc())
    if jurisdiction_code:
        query = query.where(ListingFeePolicy.jurisdiction_code == jurisdiction_code)
    return list(db.scalars(query))


def get_listing_fee_policy_or_404(db: Session, policy_id: int) -> ListingFeePolicy:
    policy = db.get(ListingFeePolicy, policy_id)
    if not policy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing Fee policy not found")
    return policy


def _to_amount_fields(amount_minor, amount, currency: str) -> dict:
    if amount_minor is None:
        amount_minor = stripe_client.to_minor_units(float(amount), currency)
    return {"amount_minor": int(amount_minor), "amount": stripe_client.from_minor_units(int(amount_minor), currency)}


def create_listing_fee_policy(db: Session, admin: AdminUser, data: dict, *, correlation_id: str = "") -> ListingFeePolicy:
    """Creates the next Price Book version for this market as a DRAFT --
    never chargeable until approve_listing_fee_policy (ZR-PAY-CFG-001 2.1).
    Append-only by version: a price already quoted is never edited."""
    from app.models.market_release import MarketRelease

    data = dict(data)
    jurisdiction_code = data["jurisdiction_code"].strip()
    if not db.scalar(select(MarketRelease.id).where(MarketRelease.jurisdiction == jurisdiction_code)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{jurisdiction_code}' is not a configured market -- create its market release first",
        )
    data["jurisdiction_code"] = jurisdiction_code
    data["currency"] = data["currency"].strip().upper()
    data.update(_to_amount_fields(data.pop("amount_minor", None), data.get("amount"), data["currency"]))
    data.pop("quote_validity_minutes", None)
    if data.get("billing_entity_id") is not None:
        from app.crud.billing_entity import get_billing_entity_or_404

        get_billing_entity_or_404(db, data["billing_entity_id"])

    latest = db.scalar(
        select(ListingFeePolicy)
        .where(ListingFeePolicy.jurisdiction_code == jurisdiction_code)
        .order_by(ListingFeePolicy.version.desc())
        .limit(1)
    )
    next_version = (latest.version + 1) if latest else 1

    data.update(
        version=next_version, status="DRAFT", environment=_current_environment(), created_by_admin_id=admin.id,
    )
    policy = ListingFeePolicy(**data)
    db.add(policy)
    db.commit()
    db.refresh(policy)

    log_audit_event(
        db, admin, "listing_fee_policy.create", "listing_fee_policy", str(policy.id), correlation_id,
        reason=f"jurisdiction={jurisdiction_code}; version={next_version}; status=DRAFT",
    )
    db.commit()
    return policy


def update_listing_fee_policy(
    db: Session, admin: AdminUser, policy: ListingFeePolicy, updates: dict, *, correlation_id: str = "",
) -> ListingFeePolicy:
    """Only a DRAFT may be edited -- once approved, a price is part of the
    commercial record and changes need a new version."""
    if policy.status != "DRAFT":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A {policy.status.lower()} price can't be edited -- create a new version instead",
        )
    updates = {k: v for k, v in updates.items() if v is not None}
    updates.pop("quote_validity_minutes", None)
    if "currency" in updates:
        updates["currency"] = updates["currency"].strip().upper()
    currency = updates.get("currency", policy.currency)
    if "amount_minor" in updates or "amount" in updates:
        updates.update(_to_amount_fields(updates.pop("amount_minor", None), updates.get("amount"), currency))
    if updates.get("billing_entity_id") is not None:
        from app.crud.billing_entity import get_billing_entity_or_404

        get_billing_entity_or_404(db, updates["billing_entity_id"])
    for field, value in updates.items():
        setattr(policy, field, value)
    db.commit()
    db.refresh(policy)

    log_audit_event(
        db, admin, "listing_fee_policy.update", "listing_fee_policy", str(policy.id), correlation_id,
        reason=f"jurisdiction={policy.jurisdiction_code}; version={policy.version}",
    )
    db.commit()
    return policy


def approve_listing_fee_policy(
    db: Session, admin: AdminUser, policy: ListingFeePolicy, *, correlation_id: str = "",
) -> ListingFeePolicy:
    """DRAFT -> ACTIVE. Refuses unless everything a production charge needs
    is in place (market, tax rule, billing entity approved for this market
    and currency). The previously ACTIVE price for the same market and
    environment is RETIRED in the same transaction, so exactly one ACTIVE
    price can ever resolve."""
    if policy.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a draft price can be approved")
    problems = _price_activation_problems(db, policy)
    if problems:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": "This price can't be activated yet", "reasons": problems})

    previous = db.scalars(
        select(ListingFeePolicy).where(
            ListingFeePolicy.jurisdiction_code == policy.jurisdiction_code,
            ListingFeePolicy.environment == policy.environment,
            ListingFeePolicy.status == "ACTIVE",
            ListingFeePolicy.id != policy.id,
        )
    ).all()
    for old in previous:
        old.status = "RETIRED"
    policy.status = "ACTIVE"
    policy.approved_by_admin_id = admin.id
    policy.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(policy)

    retired_ids = ",".join(str(o.id) for o in previous) or "none"
    log_audit_event(
        db, admin, "listing_fee_policy.approve", "listing_fee_policy", str(policy.id), correlation_id,
        reason=f"jurisdiction={policy.jurisdiction_code}; version={policy.version}; retired={retired_ids}",
    )
    db.commit()
    return policy


def retire_listing_fee_policy(
    db: Session, admin: AdminUser, policy: ListingFeePolicy, *, correlation_id: str = "",
) -> ListingFeePolicy:
    if policy.status == "RETIRED":
        raise HTTPException(status.HTTP_409_CONFLICT, "This price is already retired")
    policy.status = "RETIRED"
    db.commit()
    db.refresh(policy)
    log_audit_event(
        db, admin, "listing_fee_policy.retire", "listing_fee_policy", str(policy.id), correlation_id,
        reason=f"jurisdiction={policy.jurisdiction_code}; version={policy.version}",
    )
    db.commit()
    return policy


def price_amount_minor(policy: ListingFeePolicy) -> int:
    if policy.amount_minor is not None:
        return int(policy.amount_minor)
    return stripe_client.to_minor_units(float(policy.amount), policy.currency)


def compute_tax_minor(amount_minor: int, tax_rate: float, tax_behavior: str) -> tuple[int, int, int]:
    """Returns (fee_minor, tax_minor, total_minor). EXCLUSIVE adds tax on
    top; INCLUSIVE identifies the tax already contained in the price
    without adding it again (ZR-PAY-CFG-001 Section 3.2)."""
    rate = Decimal(str(tax_rate))
    amount = Decimal(amount_minor)
    if tax_behavior == "INCLUSIVE":
        tax = (amount - amount / (Decimal(1) + rate)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(amount - tax), int(tax), int(amount)
    tax = (amount * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(amount), int(tax), int(amount + tax)


def to_policy_snapshot(policy: ListingFeePolicy) -> dict:
    """The dict frozen onto ListingFeeQuote.policy_snapshot -- an immutable
    record of which price priced this quote (ZR-PAY-CFG-001 8.1: market,
    currency, amount + Price Book version, tax mode/amount/rule, billing
    entity), never a live reference."""
    from app.crud.billing_entity import billing_entity_snapshot

    entity = policy.billing_entity
    return {
        "jurisdiction": policy.jurisdiction_code,
        "market": policy.jurisdiction_code,
        "policy_id": policy.id,
        "policy_version": policy.version,
        "price_book_version": policy.version,
        "environment": policy.environment,
        "currency": policy.currency,
        "amount_minor": price_amount_minor(policy),
        "legal_entity_name": entity.customer_facing_name() if entity else policy.legal_entity_name,
        "tax_registration_number": entity.tax_registration_number if entity else policy.tax_registration_number,
        "billing_entity": billing_entity_snapshot(entity) if entity else None,
        "tax_rate": float(policy.tax_rate),
        "tax_behavior": policy.tax_behavior,
        "tax_rule_reference": policy.tax_rule_reference,
        "refund_eligible": policy.refund_eligible,
        "refund_window_days": policy.refund_window_days,
        "disclosure_text": policy.disclosure_text,
    }


def get_quote_or_404(db: Session, quote_id: int) -> ListingFeeQuote:
    quote = db.get(ListingFeeQuote, quote_id)
    if not quote:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing Fee quote not found")
    return quote


def create_quote(db: Session, listing: Listing, party: Party, *, correlation_id: str = "") -> ListingFeeQuote:
    """ZR-PAY-002 Section 8.1/12.2 POST /listing-fees/quotes. Resolves amount/
    tax/currency from the listing's own jurisdiction (falling back to
    DEFAULT_JURISDICTION, same fallback crud/payment_provider.py:
    renter_pay_obligation uses) and freezes them -- a later checkout never
    re-resolves the policy, structurally preventing the 'currency or country
    changes between fee quote and checkout creation' scenario."""
    jurisdiction_code = listing_jurisdiction_code(listing) or DEFAULT_JURISDICTION
    policy = resolve_listing_fee_policy(db, jurisdiction_code)

    fee_minor, tax_minor, total_minor = compute_tax_minor(
        price_amount_minor(policy), float(policy.tax_rate), policy.tax_behavior,
    )
    amount = stripe_client.from_minor_units(fee_minor, policy.currency)
    quote = ListingFeeQuote(
        listing_id=listing.id,
        party_id=party.id,
        amount=amount,
        tax_amount=stripe_client.from_minor_units(tax_minor, policy.currency),
        total_amount=stripe_client.from_minor_units(total_minor, policy.currency),
        amount_minor=fee_minor,
        tax_amount_minor=tax_minor,
        total_amount_minor=total_minor,
        currency=policy.currency,
        policy_snapshot=to_policy_snapshot(policy),
        # ZR-PAY-CFG-001 Decision 6: 30 minutes, fixed.
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=LISTING_FEE_QUOTE_TTL_SECONDS),
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)

    emit_event(
        db, "listing_fee.quoted", "listing_fee_quote", str(quote.id),
        {"listingId": listing.id, "amount": amount, "currency": policy.currency},
        correlation_id=correlation_id, actor_kind="party", actor_id=str(party.id),
    )
    db.commit()
    return quote


def get_payment_or_404(db: Session, payment_id: int) -> ListingFeePayment:
    payment = db.get(ListingFeePayment, payment_id)
    if not payment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing Fee payment not found")
    return payment


def get_payment_by_checkout_session_id(db: Session, checkout_session_id: str) -> ListingFeePayment:
    """The return leg of create_checkout's Stripe-hosted redirect: resolves
    Stripe's own `{CHECKOUT_SESSION_ID}` (substituted into success_url/
    cancel_url) back to the ListingFeePayment it belongs to -- a plain
    lookup against our own provider_checkout_session_id column (set at
    checkout creation time, before Stripe's own PaymentIntent even exists;
    see create_checkout_session's own docstring for why that column, not
    provider_payment_intent_id, is what's known immediately). Fails closed
    (404) on anything that doesn't resolve to one of our own payments, same
    posture as every other *_or_404 lookup in this module.

    Self-heals a still-PENDING result by asking Stripe directly, right now,
    whether it already knows this session succeeded (retrieve_checkout_session)
    -- the customer's own return trip here must not be left hostage to
    webhook delivery having already happened by the time they land on this
    page (local dev without `stripe listen` running, or ordinary webhook
    latency in any environment). The webhook remains the authoritative
    completion path for every other caller (this reconciliation only ever
    runs when a customer is actually looking at this specific payment's
    result) -- _complete_payment_success's own already-SUCCEEDED guard
    makes a redundant webhook delivery afterward a no-op either way."""
    payment = db.scalar(
        select(ListingFeePayment).where(ListingFeePayment.provider_checkout_session_id == checkout_session_id)
    )
    if not payment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Checkout session not found")

    if payment.status == "PENDING":
        session_status = stripe_client.retrieve_checkout_session(checkout_session_id=checkout_session_id)
        if session_status and session_status["payment_status"] == "paid":
            if session_status["payment_intent_id"] and not payment.provider_payment_intent_id:
                payment.provider_payment_intent_id = session_status["payment_intent_id"]
                db.commit()
            _complete_payment_success(db, payment)
            db.refresh(payment)

    return payment


def list_listing_fee_payments_for_party(db: Session, party_id: int) -> list[ListingFeePayment]:
    """ZR-PAY-002 Section 3.2: the lister's own 'Listing fee receipts' view."""
    return list(
        db.scalars(
            select(ListingFeePayment)
            .where(ListingFeePayment.party_id == party_id)
            .order_by(ListingFeePayment.created_at.desc())
        )
    )


def list_listing_fee_refunds_for_payment(db: Session, payment_id: int) -> list[ListingFeeRefund]:
    """ZR-PAY-002 Section 8.4: 'Required customer behavior' for every refund
    state (REFUND_REQUESTED/PROCESSING/REFUNDED/FAILED) implies the lister
    can see it -- issuing a refund stays admin-restricted (Section 11), but
    viewing one's own payment's refund history does not."""
    return list(
        db.scalars(
            select(ListingFeeRefund)
            .where(ListingFeeRefund.payment_id == payment_id)
            .order_by(ListingFeeRefund.created_at.desc())
        )
    )


def listing_fee_is_paid(db: Session, listing_id: str) -> bool:
    """The one query used both by api/crud/listing.py:check_publish_eligibility
    (an informational signal on the admin review screen) and by
    crud/listing._require_listing_fee_paid_if_applicable (the actual hard
    publication gate -- ZR-PAY-002 A7/8.3). Paying the fee must never be
    treated as satisfying the other publication gates (identity/property/
    authority/compliance) -- this function only ever answers one question."""
    return (
        db.scalar(
            select(ListingFeePayment.id)
            .where(ListingFeePayment.listing_id == listing_id, ListingFeePayment.status == "SUCCEEDED")
            .limit(1)
        )
        is not None
    )


def is_listing_fee_payment_refund_eligible(db: Session, payment: ListingFeePayment) -> bool:
    """ZR-PAY-002 Section 8.4: 'REFUND_ELIGIBLE -- Display only when
    commercial policy/jurisdiction configuration permits.' Resolved from the
    frozen policy_snapshot on the payment's own quote -- the policy that
    actually priced it, never whatever policy happens to be current now
    (same discipline as every other snapshot-reproducible calculation in
    this codebase). Informational only: it does not itself gate whether a
    restricted admin may call request_refund -- 'role/policy' in Section
    11's permission table is the admin's own restricted judgment, informed
    by this signal, not mechanically blocked by it."""
    if payment.status != "SUCCEEDED":
        return False
    already_refunded = sum(
        _round2(r.amount) for r in payment.refunds if r.status in ("PROCESSING", "PARTIALLY_REFUNDED", "REFUNDED")
    )
    if already_refunded >= _round2(float(payment.amount)):
        return False

    snapshot = payment.quote.policy_snapshot or {}
    if not snapshot.get("refund_eligible", False):
        return False
    window_days = snapshot.get("refund_window_days")
    if window_days is None:
        return True
    if payment.paid_at is None:
        return False
    return datetime.now(timezone.utc) <= payment.paid_at + timedelta(days=window_days)


def create_checkout(
    db: Session, quote: ListingFeeQuote, party: Party, *, idempotency_key: str, billing_country: str,
    frontend_origin: str = "", correlation_id: str = "",
) -> tuple[ListingFeePayment, str]:
    """ZR-PAY-002 Section 8.2/12.2 POST /listing-fees/checkout-sessions.
    Returns (payment, checkout_url) -- checkout_url is Stripe's own hosted
    payment page; the frontend does a real browser redirect there (never
    renders its own card form -- Section 8.2's PCI boundary, met here by
    Stripe's hosted page rather than an embedded component). Amount/currency
    come from the quote, never re-resolved. Get-or-create by idempotency
    key, same retried-request guard as crud/finance.py:request_refund.

    frontend_origin is the caller's own resolved, CORS-allowlist-validated
    request Origin (see api/routes/listing_fees.py:_resolve_frontend_origin)
    -- not settings.frontend_url directly. A static config value can drift
    out of sync with whatever port/host the browser is actually being
    served from (a local Next.js dev server auto-increments its port when
    the default is taken); redirecting back to wherever the browser
    genuinely came from is self-correcting and avoids that drift. Falls
    back to settings.frontend_url when the caller didn't resolve one (e.g.
    a test calling this directly).

    success_url/cancel_url embed the literal `{CHECKOUT_SESSION_ID}`
    placeholder Stripe substitutes on redirect -- never this payment's own
    id -- so the DB insert below can stay exactly where it was (after the
    Stripe call returns, not before): the frontend resolves that session id
    back to this payment via get_payment_by_checkout_session_id once the
    customer returns, rather than this function needing to know its own
    not-yet-created row's id up front."""
    existing = db.scalar(select(ListingFeePayment).where(ListingFeePayment.idempotency_key == idempotency_key))
    if existing:
        if existing.quote_id != quote.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This idempotency key was already used for a different checkout")
        return existing, ""

    if quote.party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This quote does not belong to you")
    if datetime.now(timezone.utc) >= quote.expires_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "This quote has expired -- request a new one")
    if listing_fee_is_paid(db, quote.listing_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "The Listing Fee for this listing has already been paid")

    origin = frontend_origin or settings.frontend_url
    success_url = f"{origin}/account/host/listings?stripeCheckout=success&checkoutSessionId={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin}/account/host/listings?stripeCheckout=cancel&checkoutSessionId={{CHECKOUT_SESSION_ID}}"

    try:
        checkout_session_id, checkout_url = stripe_client.create_checkout_session(
            amount=float(quote.total_amount), currency=quote.currency,
            metadata={"domain": "listing_fee", "quote_id": str(quote.id), "listing_id": quote.listing_id},
            success_url=success_url, cancel_url=cancel_url,
            idempotency_key=f"listing_fee_checkout:{idempotency_key}",
        )
    except Exception:
        # ZR-PAY-002 Section 8.4: 'Show a neutral failure message; do not
        # expose gateway diagnostics.' A provider-side failure creating the
        # Checkout Session itself (rare, but possible -- network/auth
        # errors) must never surface as a raw 500 with a leaked stack trace
        # to the caller -- but it must still be logged somewhere, or a real
        # misconfiguration (bad key, wrong API version, Stripe account
        # restriction) becomes undiagnosable in production.
        logger.exception("listing_fee: create_checkout_session failed (quote_id=%s)", quote.id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The payment provider could not be reached -- please try again")

    try:
        with db.begin_nested():
            payment = ListingFeePayment(
                quote_id=quote.id, listing_id=quote.listing_id, party_id=party.id,
                amount=quote.total_amount, currency=quote.currency, idempotency_key=idempotency_key,
                billing_country=billing_country.upper(), provider_checkout_session_id=checkout_session_id,
                # provider_payment_intent_id intentionally left unset here --
                # Stripe doesn't create one until the customer actually pays
                # (see create_checkout_session's docstring); the webhook's
                # checkout.session.completed handling backfills it.
            )
            db.add(payment)
            db.flush()
    except IntegrityError:
        # A concurrent request for this same idempotency_key won the race
        # while this one was talking to Stripe -- same SAVEPOINT idiom as
        # ingest_stripe_webhook_event's own ListingFeeProviderEvent insert.
        # Stripe's own idempotency_key above already prevented a second
        # real Checkout Session from being created there; this is just the
        # DB-level backstop, so we return the winner's row rather than
        # raising a raw 500.
        winner = db.scalar(select(ListingFeePayment).where(ListingFeePayment.idempotency_key == idempotency_key))
        if winner:
            return winner, ""
        raise
    db.commit()
    db.refresh(payment)

    emit_event(
        db, "listing_fee.checkout_started", "listing_fee_payment", str(payment.id),
        {"listingId": quote.listing_id, "amount": float(quote.total_amount), "currency": quote.currency},
        correlation_id=correlation_id, actor_kind="party", actor_id=str(party.id),
    )
    db.commit()

    # Same "no real webhook will ever arrive without real Stripe keys
    # configured" honesty as crud/payment_provider.py:renter_pay_obligation --
    # complete the payment synchronously instead of leaving it PENDING forever.
    if not stripe_client.is_configured():
        _complete_payment_success(db, payment, correlation_id=correlation_id)
        db.refresh(payment)

    return payment, checkout_url


def _complete_payment_success(db: Session, payment: ListingFeePayment, *, correlation_id: str = "") -> None:
    if payment.status == "SUCCEEDED":
        return
    payment.status = "SUCCEEDED"
    payment.paid_at = datetime.now(timezone.utc)
    db.commit()

    log_audit_event(db, None, "listing_fee.paid", "listing_fee_payment", str(payment.id), correlation_id)
    emit_event(
        db, "listing_fee.paid", "listing_fee_payment", str(payment.id),
        {"listingId": payment.listing_id, "amount": float(payment.amount), "currency": payment.currency},
        correlation_id=correlation_id, previous_state="PENDING", new_state="SUCCEEDED",
    )
    db.commit()

    get_or_create_listing_fee_receipt(db, payment)
    db.commit()

    notif_crud.notify_user_by_party(
        db, payment.party_id,
        title="Listing Fee paid",
        message=(
            f"We received your Listing Fee payment of {payment.currency} {payment.amount:.2f}. "
            "Your listing can proceed once every other publication requirement is complete."
        ),
        notification_type="listing_fee.paid",
        related_entity_type="listing_fee_payment", related_entity_id=str(payment.id),
    )

    # ZR-PAY-002 Section 2.1/8.3, Acceptance Gate A7: "Automatic publication
    # of a listing merely because its Listing Fee has been paid" is
    # explicitly out of scope -- paying the fee only ever satisfies the
    # "Listing fee" line of the publish-eligibility checklist
    # (crud/listing.py:check_publish_eligibility). An admin's own explicit
    # publish action (or the existing publication.requires_approval=False
    # auto-approve path, neither of which is triggered from here) remains
    # the only way a listing actually goes live.


def _complete_payment_failure(db: Session, payment: ListingFeePayment, message: str, *, correlation_id: str = "") -> None:
    if payment.status == "FAILED":
        return
    payment.status = "FAILED"
    payment.failed_at = datetime.now(timezone.utc)
    payment.failure_message = message
    db.commit()

    log_audit_event(
        db, None, "listing_fee.payment_failed", "listing_fee_payment", str(payment.id), correlation_id, reason=message,
    )
    emit_event(
        db, "listing_fee.payment_failed", "listing_fee_payment", str(payment.id),
        {"listingId": payment.listing_id}, correlation_id=correlation_id, previous_state="PENDING", new_state="FAILED",
    )
    db.commit()

    notif_crud.notify_user_by_party(
        db, payment.party_id,
        title="Listing Fee payment failed",
        message="Your Listing Fee payment did not go through. You can try again with the same or a different payment method.",
        notification_type="listing_fee.payment_failed",
        related_entity_type="listing_fee_payment", related_entity_id=str(payment.id),
    )


# Real Stripe event types this module understands -> our own vocabulary --
# same "ignore anything else, never error" posture as
# crud/payment_provider.py:STRIPE_EVENT_TYPE_MAP.
STRIPE_EVENT_TYPE_MAP = {
    "payment_intent.succeeded": "PAYMENT_SUCCEEDED",
    "payment_intent.payment_failed": "PAYMENT_FAILED",
    "charge.refunded": "REFUND_SUCCEEDED",
    # The Checkout Session events, not the PaymentIntent ones above, are the
    # authoritative success/failure signal for a Stripe-hosted checkout --
    # provider_payment_intent_id is null until one of these backfills it
    # (see create_checkout_session's docstring for why), so a
    # payment_intent.succeeded/payment_failed for this same payment simply
    # won't find a matching row if it happens to arrive first; it becomes a
    # harmless, idempotent no-op once _complete_payment_success's own
    # already-SUCCEEDED guard is reached on any later, redundant delivery.
    "checkout.session.completed": "CHECKOUT_SESSION_COMPLETED",
    "checkout.session.async_payment_succeeded": "CHECKOUT_SESSION_COMPLETED",
    "checkout.session.async_payment_failed": "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED",
}


def ingest_stripe_webhook_event(db: Session, event, *, correlation_id: str = "") -> None:
    """Maps a real, already signature-verified Stripe Event for the Listing
    Fee domain. Idempotent via ListingFeeProviderEvent's unique
    provider_event_id -- a replayed webhook loses the IntegrityError race and
    is treated as already-processed, same SAVEPOINT idiom as
    crud/payment_provider.py:ingest_provider_callback."""
    event_type = STRIPE_EVENT_TYPE_MAP.get(event["type"])
    if event_type is None:
        return

    provider_event_id = event["id"]
    stripe_object = event["data"]["object"]

    try:
        with db.begin_nested():
            db.add(ListingFeeProviderEvent(
                provider_event_id=provider_event_id, event_type=event_type,
                processed_at=datetime.now(timezone.utc),
            ))
            db.flush()
    except IntegrityError:
        return  # already-seen event id -- idempotent no-op

    if event_type in ("PAYMENT_SUCCEEDED", "PAYMENT_FAILED"):
        payment = db.scalar(
            select(ListingFeePayment).where(ListingFeePayment.provider_payment_intent_id == stripe_object["id"])
        )
        if not payment:
            return
        if event_type == "PAYMENT_SUCCEEDED":
            _complete_payment_success(db, payment, correlation_id=correlation_id)
        else:
            failure_message = (stripe_object.get("last_payment_error") or {}).get("message", "")
            _complete_payment_failure(db, payment, failure_message, correlation_id=correlation_id)
        return

    if event_type == "REFUND_SUCCEEDED":
        payment = db.scalar(
            select(ListingFeePayment).where(ListingFeePayment.provider_payment_intent_id == stripe_object["id"])
        )
        if not payment:
            return
        refunded_amount = stripe_client.from_minor_units(stripe_object.get("amount_refunded", 0), payment.currency)
        _apply_refund_confirmation(db, payment, refunded_amount, correlation_id=correlation_id)
        return

    if event_type in ("CHECKOUT_SESSION_COMPLETED", "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED"):
        # stripe_object here is the Checkout Session itself, not a
        # PaymentIntent -- looked up by the id create_checkout stored at
        # checkout-creation time (provider_payment_intent_id is still null
        # at this point for a brand-new session).
        payment = db.scalar(
            select(ListingFeePayment).where(ListingFeePayment.provider_checkout_session_id == stripe_object["id"])
        )
        if not payment:
            return
        if event_type == "CHECKOUT_SESSION_ASYNC_PAYMENT_FAILED":
            _complete_payment_failure(db, payment, "Your payment method could not be charged.", correlation_id=correlation_id)
            return
        # CHECKOUT_SESSION_COMPLETED fires for both a synchronous method
        # (card -- payment_status is already "paid") and the *start* of an
        # async one (payment_status "unpaid", resolved later by
        # checkout.session.async_payment_succeeded, mapped to this same
        # internal type above) -- only the "paid" case is an actual success.
        if stripe_object.get("payment_status") == "paid":
            payment_intent_id = stripe_object.get("payment_intent")
            if payment_intent_id and not payment.provider_payment_intent_id:
                payment.provider_payment_intent_id = payment_intent_id
                db.commit()
            _complete_payment_success(db, payment, correlation_id=correlation_id)


def get_or_create_listing_fee_receipt(db: Session, payment: ListingFeePayment) -> ListingFeeReceipt:
    """Idempotent, render-once-then-persist -- same discipline as
    crud/finance.py:get_or_create_payment_receipt. ListingFeeReceipt.payment_id
    being DB-unique is the real guarantee against a concurrent double-render."""
    if payment.receipt is not None:
        return payment.receipt

    quote = payment.quote
    snapshot = quote.policy_snapshot or {}
    tax_rate = float(snapshot.get("tax_rate", 0.0))
    receipt_number = f"LF-RCPT-{payment.id:08d}"
    pdf_bytes = _generate_listing_fee_receipt_pdf(
        payment, receipt_number, legal_entity_name=snapshot.get("legal_entity_name", "Zoiko Rooms"),
        tax_registration_number=snapshot.get("tax_registration_number", ""),
        billing_entity=snapshot.get("billing_entity"),
    )
    storage_ref, content_hash = save_listing_fee_receipt_document(pdf_bytes)

    try:
        with db.begin_nested():
            receipt = ListingFeeReceipt(
                payment_id=payment.id, receipt_number=receipt_number,
                legal_entity_name=snapshot.get("legal_entity_name", "Zoiko Rooms"),
                tax_registration_number=snapshot.get("tax_registration_number", ""),
                amount=quote.amount, tax_rate=tax_rate, tax_amount=quote.tax_amount,
                total_amount=quote.total_amount, currency=quote.currency,
                content_hash=content_hash, storage_ref=storage_ref,
            )
            db.add(receipt)
            db.flush()
    except IntegrityError:
        return db.scalar(select(ListingFeeReceipt).where(ListingFeeReceipt.payment_id == payment.id))
    db.commit()
    db.refresh(receipt)
    return receipt


def _generate_listing_fee_receipt_pdf(
    payment: ListingFeePayment, receipt_number: str, *, legal_entity_name: str, tax_registration_number: str,
    billing_entity: dict | None = None,
) -> bytes:
    """ZR-PAY-002 Section 8.5's minimum receipt/invoice data. Same plain
    summary-document framing as crud/finance.py:_generate_payment_receipt_pdf --
    no real payment-processor descriptor to show beyond what Stripe itself
    already discloses to the payer at checkout."""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    x = 20 * mm
    y = height - 25 * mm

    def write(text: str, size: float = 10, bold: bool = False, gap: float = 7 * mm) -> None:
        nonlocal y
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.drawString(x, y, text)
        y -= gap

    write(f"{legal_entity_name} -- Listing Fee Receipt", size=16, bold=True, gap=10 * mm)
    write(f"Receipt {receipt_number}", size=10)
    write(f"Listing {payment.listing_id}  |  Payment #{payment.id}", size=10)
    # ZR-PAY-CFG-001 Section 7: the legally required company details, from
    # the Billing Entity Registry snapshot frozen at quote time.
    if billing_entity:
        if billing_entity.get("registered_address"):
            write(f"Registered address: {billing_entity['registered_address']}", size=8, gap=5 * mm)
        if billing_entity.get("company_registration_number"):
            write(f"Company registration: {billing_entity['company_registration_number']}", size=8, gap=5 * mm)
    if tax_registration_number:
        write(f"Tax registration: {tax_registration_number}", size=9, gap=10 * mm)
    else:
        y -= 3 * mm
    issued = payment.paid_at or datetime.now(timezone.utc)
    write(f"Paid {issued.strftime('%Y-%m-%d %H:%M UTC')}", size=9, gap=10 * mm)

    quote = payment.quote
    snapshot = quote.policy_snapshot or {}
    write("Charge", size=12, bold=True)
    write(f"Listing Fee: {quote.currency} {float(quote.amount):.2f}", size=9, gap=6 * mm)
    tax_label = "Tax (included)" if snapshot.get("tax_behavior") == "INCLUSIVE" else "Tax"
    tax_rate = float(snapshot.get("tax_rate", 0.0))
    write(f"{tax_label} at {tax_rate * 100:.2f}%: {quote.currency} {float(quote.tax_amount):.2f}", size=9, gap=6 * mm)
    write(f"Total: {quote.currency} {float(quote.total_amount):.2f}", size=9, gap=10 * mm)

    write(
        "This fee is payable to Zoiko Rooms for publishing this listing. It is separate from "
        "rent, deposits and other rental payments.",
        size=8, gap=6 * mm,
    )
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def request_refund(
    db: Session, admin: AdminUser, payment: ListingFeePayment, data, *, correlation_id: str = "",
) -> ListingFeeRefund:
    """ZR-PAY-002 Section 8.4/11: a restricted admin action gated at the
    route level (see api/routes/listing_fees.py). Unlike
    crud/finance.py:request_refund's own request/decide split, a restricted
    admin's request here is the decision -- there is no separate custodied-
    funds business judgment to make about Zoiko's own direct charge. Get-or-
    create by idempotency key, same retried-request guard."""
    existing = db.scalar(select(ListingFeeRefund).where(ListingFeeRefund.idempotency_key == data.idempotency_key))
    if existing:
        if existing.payment_id != payment.id or _round2(existing.amount) != _round2(data.amount):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "This idempotency key was already used for a different refund request",
            )
        return existing

    if payment.status != "SUCCEEDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a succeeded payment can be refunded")
    already_refunded = sum(
        _round2(r.amount) for r in payment.refunds if r.status in ("PROCESSING", "PARTIALLY_REFUNDED", "REFUNDED")
    )
    if _round2(data.amount) > _round2(float(payment.amount)) - already_refunded:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Refund amount exceeds the amount still refundable")

    try:
        with db.begin_nested():
            refund = ListingFeeRefund(
                payment_id=payment.id, amount=data.amount, currency=payment.currency, reason=data.reason,
                idempotency_key=data.idempotency_key, requested_by_admin_id=admin.id, status="REQUESTED",
            )
            db.add(refund)
            db.flush()
    except IntegrityError:
        # Same concurrent-retry backstop as create_checkout's own SAVEPOINT
        # -- a second request with this idempotency_key won the DB race.
        winner = db.scalar(select(ListingFeeRefund).where(ListingFeeRefund.idempotency_key == data.idempotency_key))
        if winner:
            return winner
        raise
    db.commit()
    db.refresh(refund)

    log_audit_event(
        db, admin, "listing_fee.refund_requested", "listing_fee_refund", str(refund.id), correlation_id,
        reason=data.reason, after_state="REQUESTED",
    )
    emit_event(
        db, "listing_fee.refund_requested", "listing_fee_refund", str(refund.id),
        {"paymentId": payment.id, "amount": float(data.amount)}, correlation_id=correlation_id,
        actor_kind="admin", actor_id=str(admin.id),
    )
    db.commit()

    try:
        provider_refund_id = stripe_client.create_refund(
            payment_intent_id=payment.provider_payment_intent_id, amount=float(data.amount), currency=payment.currency,
            metadata={"domain": "listing_fee", "listing_fee_refund_id": str(refund.id)},
            idempotency_key=f"listing_fee_refund:{data.idempotency_key}",
        )
    except Exception as exc:
        # ZR-PAY-002 Section 8.4: REFUND_FAILED -- 'Route to controlled
        # retry/support process; retain provider error internally.' The
        # refund row itself must reach a terminal state, never stay stuck
        # at REQUESTED with an unhandled exception leaking gateway
        # diagnostics to the caller.
        refund.status = "FAILED"
        refund.failure_message = str(exc)
        db.commit()
        db.refresh(refund)

        log_audit_event(
            db, admin, "listing_fee.refund_failed", "listing_fee_refund", str(refund.id), correlation_id,
            reason=str(exc),
        )
        emit_event(
            db, "listing_fee.refund_failed", "listing_fee_refund", str(refund.id),
            {"paymentId": payment.id}, correlation_id=correlation_id, new_state="FAILED",
        )
        db.commit()
        return refund

    refund.provider_refund_id = provider_refund_id
    refund.status = "PROCESSING"
    db.commit()
    db.refresh(refund)

    if not stripe_client.is_configured():
        _apply_refund_confirmation(db, payment, float(data.amount), refund=refund, correlation_id=correlation_id)
        db.refresh(refund)

    return refund


def _apply_refund_confirmation(
    db: Session, payment: ListingFeePayment, refunded_amount: float, refund: ListingFeeRefund | None = None,
    *, correlation_id: str = "",
) -> None:
    """Marks the most recent PROCESSING refund (or the one explicitly
    passed, for the synchronous simulated path) REFUNDED/PARTIALLY_REFUNDED
    once the provider confirms it, and notifies the lister -- ZR-PAY-002
    Section 8.4: 'Show amount and provider-confirmed status.'"""
    target = refund or db.scalar(
        select(ListingFeeRefund)
        .where(ListingFeeRefund.payment_id == payment.id, ListingFeeRefund.status == "PROCESSING")
        .order_by(ListingFeeRefund.created_at.desc())
    )
    if not target or target.status not in ("PROCESSING",):
        return

    total_refunded = sum(
        _round2(r.amount) for r in payment.refunds if r.status == "REFUNDED"
    ) + _round2(target.amount)
    target.status = "REFUNDED" if total_refunded >= _round2(float(payment.amount)) else "PARTIALLY_REFUNDED"
    target.completed_at = datetime.now(timezone.utc)
    db.commit()

    log_audit_event(
        db, None, f"listing_fee.{target.status.lower()}", "listing_fee_refund", str(target.id), correlation_id,
        after_state=target.status,
    )
    emit_event(
        db, "listing_fee.refunded", "listing_fee_refund", str(target.id),
        {"paymentId": payment.id, "amount": float(target.amount)}, correlation_id=correlation_id, new_state=target.status,
    )
    db.commit()

    notif_crud.notify_user_by_party(
        db, payment.party_id,
        title="Listing Fee refunded",
        message=f"{payment.currency} {float(target.amount):.2f} of your Listing Fee has been refunded.",
        notification_type="listing_fee.refunded",
        related_entity_type="listing_fee_refund", related_entity_id=str(target.id),
    )
