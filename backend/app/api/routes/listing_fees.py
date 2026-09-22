"""ZR-PAY-002 Section 12.2's recommended API surface for the Listing Fee --
the only payment Zoiko Rooms collects for itself. Three routers, matching
the spec's own permission table (Section 11): `router` is the lister's own
self-service checkout (USER-authenticated, ownership-checked against
user.party_id, same pattern as api/routes/user_hosting.py); `admin_router`
is the restricted refund/policy-configuration surface (super_admin only --
'Issue Listing Fee refund: Restricted role/policy'); `webhook_router` is the
public, signature-verified Stripe endpoint for this domain's own events,
kept separate from api/routes/finance.py's rent-domain webhook per Section
12.1's architecture rule."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user, require_super_admin
from app.core.config import settings
from app.core.correlation import get_correlation_id
from app.core.listing_fee_receipt_documents import resolve_listing_fee_receipt_document_path
from app.crud import listing as listing_crud
from app.crud import listing_fee as listing_fee_crud
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.party import Party
from app.models.user_account import UserAccount
from app.schemas.listing_fee import (
    ListingFeeCheckoutSessionCreate,
    ListingFeeCheckoutSessionRead,
    ListingFeePaymentRead,
    ListingFeePolicyCreate,
    ListingFeePolicyRead,
    ListingFeePolicyUpdate,
    ListingFeeQuoteRead,
    ListingFeeReceiptRead,
    ListingFeeRefundCreate,
    ListingFeeRefundRead,
)
from app.services import stripe_client

router = APIRouter(prefix="/api/users/listing-fees", tags=["user-listing-fees"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/finance/listing-fees", tags=["finance-listing-fees"], dependencies=[Depends(get_current_admin)])
webhook_router = APIRouter(prefix="/api/finance", tags=["finance-listing-fee-webhooks"])


def _get_own_party_or_400(db: Session, user: UserAccount) -> Party:
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    party = db.get(Party, user.party_id)
    if not party:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    return party


def _resolve_frontend_origin(request: Request) -> str:
    """settings.frontend_url is a static per-deployment config value -- easy
    to drift out of sync with whatever port/host the browser actually made
    this request from (a local Next.js dev server auto-increments its port
    when the default is taken, exactly the mismatch that once sent a real
    Stripe redirect to a dead port). The browser's own Origin header is the
    actual, current truth for where it's serving the frontend from --
    trusted here only because it's independently validated against the same
    cors_origin_list already used to decide whether to answer the request
    at all (see app/main.py's CORS middleware), so this can't be used to
    redirect a paying customer somewhere attacker-controlled. Falls back to
    the static setting for a request with no Origin header at all (e.g. a
    same-origin/server-to-server call, or a test client)."""
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_origin_list:
        return origin
    return settings.frontend_url


def _get_own_listing_or_404(db: Session, listing_id: str, user: UserAccount):
    listing = listing_crud.get_listing(db, listing_id)
    if not listing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")
    listing_crud.assert_party_owns_listing(listing, user.party_id)
    return listing


@router.post("/listings/{listing_id}/quotes", response_model=ListingFeeQuoteRead, status_code=status.HTTP_201_CREATED)
def post_create_listing_fee_quote(
    listing_id: str, request: Request, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 8.1 POST /listing-fees/quotes."""
    listing = _get_own_listing_or_404(db, listing_id, user)
    party = _get_own_party_or_400(db, user)
    return listing_fee_crud.create_quote(db, listing, party, correlation_id=get_correlation_id(request))


@router.post("/checkout-sessions", response_model=ListingFeeCheckoutSessionRead, status_code=status.HTTP_201_CREATED)
def post_create_listing_fee_checkout_session(
    payload: ListingFeeCheckoutSessionCreate, request: Request,
    user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 8.2 POST /listing-fees/checkout-sessions. Returns
    Stripe's own hosted checkout_url -- the frontend does a real browser
    redirect there (Section 8.2's PCI boundary, met via Stripe's hosted page
    rather than an embedded component) -- empty when no real Stripe keys
    are configured, since the payment already completed synchronously in
    that case."""
    quote = listing_fee_crud.get_quote_or_404(db, payload.quote_id)
    party = _get_own_party_or_400(db, user)
    if quote.party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This quote does not belong to you")
    payment, checkout_url = listing_fee_crud.create_checkout(
        db, quote, party, idempotency_key=payload.idempotency_key, billing_country=payload.billing_country,
        frontend_origin=_resolve_frontend_origin(request), correlation_id=get_correlation_id(request),
    )
    return ListingFeeCheckoutSessionRead(
        id=payment.id, quote_id=payment.quote_id, listing_id=payment.listing_id,
        amount=float(payment.amount), currency=payment.currency, status=payment.status, checkout_url=checkout_url,
        created_at=payment.created_at,
    )


@router.get("/checkout-sessions/{checkout_session_id}/resolve", response_model=ListingFeePaymentRead)
def get_resolve_checkout_session(
    checkout_session_id: str, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db),
):
    """The return leg once Stripe redirects the customer back from its own
    hosted page: the frontend lands on ?checkoutSessionId={CHECKOUT_SESSION_ID}
    (Stripe's own placeholder, substituted server-side) and calls this to
    find out which of its own payments that was, and its current status."""
    payment = listing_fee_crud.get_payment_by_checkout_session_id(db, checkout_session_id)
    party = _get_own_party_or_400(db, user)
    if payment.party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment does not belong to you")
    return _to_payment_read(db, payment)


def _assert_own_payment(db: Session, payment_id: int, user: UserAccount):
    payment = listing_fee_crud.get_payment_or_404(db, payment_id)
    party = _get_own_party_or_400(db, user)
    if payment.party_id != party.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This payment does not belong to you")
    return payment


def _to_payment_read(db: Session, payment) -> ListingFeePaymentRead:
    """refund_eligible is computed per-request (Section 8.4) -- never an ORM
    attribute response_model's from_attributes could pick up on its own, so
    every route returning a payment builds it through here rather than
    handing the raw ORM object straight to response_model (which would
    silently serialize refund_eligible as its schema default, False, for
    every payment regardless of actual eligibility)."""
    return ListingFeePaymentRead(
        id=payment.id, quote_id=payment.quote_id, listing_id=payment.listing_id, amount=float(payment.amount),
        currency=payment.currency, status=payment.status, billing_country=payment.billing_country,
        failure_message=payment.failure_message, created_at=payment.created_at, paid_at=payment.paid_at,
        failed_at=payment.failed_at, refund_eligible=listing_fee_crud.is_listing_fee_payment_refund_eligible(db, payment),
    )


@router.get("/payments", response_model=list[ListingFeePaymentRead])
def list_my_listing_fee_payments(user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 3.2: 'Listing fee receipts | View.'"""
    party = _get_own_party_or_400(db, user)
    payments = listing_fee_crud.list_listing_fee_payments_for_party(db, party.id)
    return [_to_payment_read(db, p) for p in payments]


@router.get("/payments/{payment_id}", response_model=ListingFeePaymentRead)
def get_own_listing_fee_payment(payment_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    return _to_payment_read(db, _assert_own_payment(db, payment_id, user))


@router.get("/payments/{payment_id}/refunds", response_model=list[ListingFeeRefundRead])
def list_own_listing_fee_refunds(payment_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 8.4: the lister's own view of refund status
    against their payment -- issuing one stays admin-restricted."""
    payment = _assert_own_payment(db, payment_id, user)
    return listing_fee_crud.list_listing_fee_refunds_for_payment(db, payment.id)


@router.get("/payments/{payment_id}/receipt")
def download_own_listing_fee_receipt(payment_id: int, user: UserAccount = Depends(get_current_user), db: Session = Depends(get_db)):
    """ZR-PAY-002 Section 8.3/8.5 [View receipt]."""
    payment = _assert_own_payment(db, payment_id, user)
    if payment.status != "SUCCEEDED":
        raise HTTPException(status.HTTP_409_CONFLICT, "No receipt exists for a payment that hasn't succeeded")

    receipt = listing_fee_crud.get_or_create_listing_fee_receipt(db, payment)
    db.commit()

    pdf_bytes = resolve_listing_fee_receipt_document_path(receipt.storage_ref).read_bytes()
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt.receipt_number}.pdf"'},
    )


@admin_router.get("/policies", response_model=list[ListingFeePolicyRead])
def get_listing_fee_policies(jurisdiction_code: str | None = None, db: Session = Depends(get_db)):
    return listing_fee_crud.list_listing_fee_policies(db, jurisdiction_code)


@admin_router.post("/policies", response_model=ListingFeePolicyRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_super_admin)])
def post_create_listing_fee_policy(
    payload: ListingFeePolicyCreate, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    return listing_fee_crud.create_listing_fee_policy(
        db, admin, payload.model_dump(), correlation_id=get_correlation_id(request),
    )


@admin_router.put("/policies/{policy_id}", response_model=ListingFeePolicyRead, dependencies=[Depends(require_super_admin)])
def put_update_listing_fee_policy(
    policy_id: int, payload: ListingFeePolicyUpdate, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    policy = listing_fee_crud.get_listing_fee_policy_or_404(db, policy_id)
    return listing_fee_crud.update_listing_fee_policy(
        db, admin, policy, payload.model_dump(exclude_unset=True), correlation_id=get_correlation_id(request),
    )


@admin_router.get("/payments/{payment_id}", response_model=ListingFeePaymentRead)
def get_listing_fee_payment(payment_id: int, db: Session = Depends(get_db)):
    return _to_payment_read(db, listing_fee_crud.get_payment_or_404(db, payment_id))


@admin_router.post(
    "/payments/{payment_id}/refunds", response_model=ListingFeeRefundRead, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_super_admin)],
)
def post_request_listing_fee_refund(
    payment_id: int, payload: ListingFeeRefundCreate, request: Request,
    admin: AdminUser = Depends(require_super_admin), db: Session = Depends(get_db),
):
    """ZR-PAY-002 Section 8.4/11: 'Issue Listing Fee refund' is restricted --
    gated to super_admin, same restriction level as the rent domain's own
    most sensitive finance actions (e.g. reconciliation, financial holds).
    request_refund itself logs the definitive 'listing_fee.refund_requested'
    audit event (with this same correlation_id) -- no separate route-level
    audit call here, to avoid two differently-named audit rows for one
    action."""
    payment = listing_fee_crud.get_payment_or_404(db, payment_id)
    return listing_fee_crud.request_refund(db, admin, payment, payload, correlation_id=get_correlation_id(request))


@webhook_router.post("/listing-fees/stripe/webhook")
async def post_listing_fee_stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """The real endpoint Stripe calls for Listing Fee events. Verifies the
    Stripe-Signature header against settings.stripe_listing_fee_webhook_secret
    (falling back to the shared stripe_webhook_secret) before touching
    anything -- same fail-closed posture as
    api/routes/finance.py:post_stripe_webhook."""
    from app.core.config import settings

    payload = await request.body()
    signature_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe_client.construct_webhook_event(
            payload=payload, signature_header=signature_header,
            secret=settings.stripe_listing_fee_webhook_secret or settings.stripe_webhook_secret,
        )
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature")

    listing_fee_crud.ingest_stripe_webhook_event(db, event, correlation_id=get_correlation_id(request))
    return {"received": True}
