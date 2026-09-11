"""ZR-ENG-CLR-004 AC-23/AC-24: the signature-provider dispatch + callback
ingestion pipeline. See models/signature_provider.py's own module docstring
for why this is built the same way SimulatedPayment is (an authenticated
admin action standing in for a real provider's webhook) rather than an
unauthenticated public route -- this is the first genuinely working,
idempotent, outage-aware entrypoint into signing that isn't a synchronous
in-app click, not a stub."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crud.ids import new_id
from app.crud.party import assert_provider_access, party_id_for_listing
from app.models.admin_user import AdminUser
from app.models.leasing import Agreement
from app.models.signature_provider import SignatureProviderEvent, SignatureProviderStatus, SignatureRequest


def get_signature_request_or_404(db: Session, agreement: Agreement, signature_request_id: int) -> SignatureRequest:
    sr = db.get(SignatureRequest, signature_request_id)
    if not sr or sr.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Signature request not found")
    return sr


def get_or_create_provider_status(db: Session) -> SignatureProviderStatus:
    row = db.get(SignatureProviderStatus, 1)
    if row is None:
        row = SignatureProviderStatus(id=1, healthy=True)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def set_provider_health(db: Session, admin: AdminUser, healthy: bool) -> SignatureProviderStatus:
    """super_admin-only -- flips the simulated provider between healthy and
    an outage, so AC-24's 'recover from provider outage' behavior is
    actually exercisable, not just asserted in a docstring."""
    row = get_or_create_provider_status(db)
    row.healthy = healthy
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def dispatch_signature_request(db: Session, agreement: Agreement, signature_request: SignatureRequest, admin: AdminUser) -> SignatureRequest:
    """PENDING -> DISPATCHED: 'sends' the request to the (simulated)
    provider. AC-24: fails closed (503) while the provider is marked
    unhealthy -- never falls back to a weaker method to route around an
    outage."""
    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if signature_request.status != "PENDING":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a PENDING signature request can be dispatched")

    provider_status = get_or_create_provider_status(db)
    if not provider_status.healthy:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Signature provider is currently unavailable")

    signature_request.status = "DISPATCHED"
    signature_request.provider_transaction_id = new_id("SIGTXN")
    db.commit()
    db.refresh(signature_request)
    return signature_request


def ingest_provider_callback(
    db: Session, agreement: Agreement, admin: AdminUser, *, provider_event_id: str, provider_transaction_id: str, event_type: str,
) -> SignatureRequest:
    """AC-23 'authenticated, replay-protected and idempotent': provider_event_id
    is DB-unique -- a duplicate/replayed callback loses the IntegrityError
    race and is treated as already-processed, never reprocessed (same
    SAVEPOINT idiom as services/inventory.py:create_hold). event_type
    SIGNATURE_COMPLETED actually completes the signature via the same
    _apply_signature every other entrypoint uses; SIGNATURE_FAILED marks the
    request FAILED without touching the agreement."""
    from app.crud.leasing import _apply_signature

    assert_provider_access(db, admin, party_id_for_listing(agreement.offer.listing))
    if event_type not in ("SIGNATURE_COMPLETED", "SIGNATURE_FAILED"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "eventType must be SIGNATURE_COMPLETED or SIGNATURE_FAILED")

    signature_request = db.query(SignatureRequest).filter(
        SignatureRequest.agreement_id == agreement.id,
        SignatureRequest.provider_transaction_id == provider_transaction_id,
    ).first()
    if not signature_request:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No signature request matches that provider transaction id")

    try:
        with db.begin_nested():
            db.add(SignatureProviderEvent(
                provider_event_id=provider_event_id, signature_request_id=signature_request.id, event_type=event_type,
                processed_at=datetime.now(timezone.utc),
            ))
            db.flush()
    except IntegrityError:
        # Already-seen event id -- idempotent no-op, not an error.
        db.refresh(signature_request)
        return signature_request

    if event_type == "SIGNATURE_FAILED":
        signature_request.status = "FAILED"
        db.commit()
        db.refresh(signature_request)
        return signature_request

    # SIGNATURE_COMPLETED: route through the same signing completion every
    # other method uses -- this makes the webhook path a genuine alternate
    # entrypoint into signing, not a decorative status flip.
    _apply_signature(db, agreement, signature_request.party_role, method=signature_request.method)
    db.refresh(signature_request)
    return signature_request


def reconcile_stalled_signature_requests(db: Session) -> list[SignatureRequest]:
    """AC-24: manual sweep (same on-demand pattern as
    services/booking_expiry.py's sweep_* functions -- no scheduler exists in
    this stack). Anything still PENDING/DISPATCHED past its own deadline
    while the provider is unhealthy is marked FAILED for admin/legal review
    -- it is never auto-completed, and never silently retried under a
    weaker method."""
    provider_status = get_or_create_provider_status(db)
    if provider_status.healthy:
        return []

    now = datetime.now(timezone.utc)
    stalled = db.query(SignatureRequest).filter(
        SignatureRequest.status.in_(("PENDING", "DISPATCHED")),
        SignatureRequest.deadline.is_not(None),
        SignatureRequest.deadline <= now,
    ).all()
    for sr in stalled:
        sr.status = "FAILED"
    if stalled:
        db.commit()
    return stalled
