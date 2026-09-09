from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import listing as crud
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.models.listing_approval import CURRENT_POLICY_VERSION
from app.schemas.listing import (
    ListingChangesRequestedRequest,
    ListingCreate,
    ListingQuarantineRequest,
    ListingRead,
    ListingRejectRequest,
    ListingSuspendRequest,
    ListingUpdate,
)

router = APIRouter(prefix="/api/listings", tags=["listings"], dependencies=[Depends(get_current_admin)])


def _get_or_404(db: Session, listing_id: str):
    listing = crud.get_listing(db, listing_id)
    if not listing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found")
    return listing


def _assert_owner_or_super_admin(listing, admin: AdminUser) -> None:
    if admin.role != "super_admin" and listing.owner_id != admin.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only manage your own listings")


@router.get("", response_model=list[ListingRead])
def list_listings(admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.annotate_availability(db, crud.list_listings_for(db, admin))


@router.post("", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
def create_listing(payload: ListingCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.create_listing(db, payload, admin)


@router.put("/{listing_id}", response_model=ListingRead)
def update_listing(
    listing_id: str,
    payload: ListingUpdate,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    listing = _get_or_404(db, listing_id)
    _assert_owner_or_super_admin(listing, admin)
    return crud.update_listing(db, listing, payload)


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(listing_id: str, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    listing = _get_or_404(db, listing_id)
    _assert_owner_or_super_admin(listing, admin)
    crud.delete_listing(db, listing)


@router.post("/{listing_id}/duplicate", response_model=ListingRead, status_code=status.HTTP_201_CREATED)
def duplicate_listing(listing_id: str, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    listing = _get_or_404(db, listing_id)
    _assert_owner_or_super_admin(listing, admin)
    return crud.duplicate_listing(db, listing, admin)


@router.get("/{listing_id}/publish-eligibility")
def get_publish_eligibility(listing_id: str, db: Session = Depends(get_db)):
    listing = _get_or_404(db, listing_id)
    reasons = crud.check_publish_eligibility(db, listing)
    return {"eligible": not reasons, "reasons": reasons}


@router.post("/{listing_id}/approve", response_model=ListingRead)
def approve_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """REVIEW -> APPROVED. Any admin/super admin may do this for any listing --
    review is an operational task, not scoped to "listings I personally own"."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.approve_listing(db, listing, admin)
    log_audit_event(
        db, admin, "listing.approve", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state,
        object_version=str(updated.current_public_version_id), policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.approved", "listing", listing_id, {"listing_version_id": updated.current_public_version_id},
        correlation_id=correlation_id, idempotency_key=f"listing.approved:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/publish", response_model=ListingRead)
def publish_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Publish (from DRAFT, APPROVED, or PAUSED). Any admin/super admin may do
    this for any listing -- review is an operational task, not scoped to
    "listings I personally own"."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.publish_listing(db, listing, admin)

    if getattr(updated, "auto_approved_at_publish", False):
        # ZR-ENG-CLR-001 5.1: "Approval and publication must be distinct
        # events even if executed milliseconds apart" -- publish_listing can
        # implicitly approve an unapproved draft version; when it does, that
        # decision still gets its own audit + domain event, separate from the
        # publish event recorded below, even though one API call produced both.
        log_audit_event(
            db, admin, "listing.approve", "listing", listing_id, correlation_id,
            reason="auto-approved as part of publish",
            before_state=before_state, after_state="APPROVED",
            object_version=str(updated.current_public_version_id), policy_version=CURRENT_POLICY_VERSION,
        )
        emit_event(
            db, "listing.approved", "listing", listing_id, {"listing_version_id": updated.current_public_version_id},
            correlation_id=correlation_id, idempotency_key=f"listing.approved:{listing_id}:{correlation_id}",
        )

    log_audit_event(
        db, admin, "listing.publish", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state,
        object_version=str(updated.current_public_version_id), policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.published", "listing", listing_id, {"room_id": listing.room_id},
        correlation_id=correlation_id, idempotency_key=f"listing.published:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/reject", response_model=ListingRead)
def reject_listing(
    listing_id: str,
    payload: ListingRejectRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Reject a listing pending review, with a required reason. Any admin/super
    admin may do this, same as publish."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.reject_listing(db, listing, payload.reason, admin)
    log_audit_event(
        db, admin, "listing.reject", "listing", listing_id, correlation_id, reason=payload.reason,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.rejected", "listing", listing_id, {"reason": payload.reason},
        correlation_id=correlation_id, idempotency_key=f"listing.rejected:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/request-changes", response_model=ListingRead)
def request_changes_on_listing(
    listing_id: str,
    payload: ListingChangesRequestedRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """REVIEW -> CHANGES_REQUESTED, with a required reason. Any admin/super
    admin may do this, same as approve/reject (Rule 2, 5.1)."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.request_changes_on_listing(db, listing, payload.reason, admin)
    log_audit_event(
        db, admin, "listing.request_changes", "listing", listing_id, correlation_id, reason=payload.reason,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.changes_requested", "listing", listing_id, {"reason": payload.reason},
        correlation_id=correlation_id, idempotency_key=f"listing.changes_requested:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/quarantine", response_model=ListingRead, dependencies=[Depends(require_super_admin)])
def quarantine_listing(
    listing_id: str,
    payload: ListingQuarantineRequest,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Super admin only. Only legal from PUBLISHED or PAUSED (Section 6.2:
    'a new disclosure makes the currently published version unsafe or
    non-compliant'); a reason is mandatory."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.quarantine_listing(db, listing, payload.reason)
    log_audit_event(
        db, admin, "listing.quarantine", "listing", listing_id, correlation_id, reason=payload.reason,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.quarantined", "listing", listing_id, {"room_id": listing.room_id, "reason": payload.reason},
        correlation_id=correlation_id, idempotency_key=f"listing.quarantined:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/pause", response_model=ListingRead)
def pause_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """PUBLISHED -> PAUSED only (crud.pause_listing enforces this). Any admin/
    super admin may pause any listing -- same operational scope as
    approve/publish/reject, not restricted to "listings I personally own"
    (a USER-hosted listing has no owning admin at all, so that restriction would
    make it un-pausable by a plain admin)."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.pause_listing(db, listing)
    log_audit_event(
        db, admin, "listing.pause", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.paused", "listing", listing_id, {"room_id": listing.room_id},
        correlation_id=correlation_id, idempotency_key=f"listing.paused:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/resume", response_model=ListingRead)
def resume_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Section 12.2 ResumeListing: PAUSED -> PUBLISHED only. Same operational
    scope as pause -- see its docstring. (publish_listing also still accepts
    PAUSED for backward compatibility; this is the spec's own named,
    narrower command for the same transition.)"""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.resume_listing(db, listing)
    log_audit_event(
        db, admin, "listing.resume", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.resumed", "listing", listing_id, {"room_id": listing.room_id},
        correlation_id=correlation_id, idempotency_key=f"listing.resumed:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/withdraw", response_model=ListingRead)
def withdraw_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Same operational scope as pause -- see its docstring."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.withdraw_listing(db, listing)
    log_audit_event(
        db, admin, "listing.withdraw", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.withdrawn", "listing", listing_id, {"room_id": listing.room_id},
        correlation_id=correlation_id, idempotency_key=f"listing.withdrawn:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/suspend", response_model=ListingRead, dependencies=[Depends(require_super_admin)])
def suspend_listing(
    listing_id: str,
    payload: ListingSuspendRequest,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Super admin only. A reason is mandatory (Section 3: 'Suspend/quarantine
    -- Reason required')."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.suspend_listing(db, listing, payload.reason)
    log_audit_event(
        db, admin, "listing.suspend", "listing", listing_id, correlation_id, reason=payload.reason,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.suspended", "listing", listing_id, {"room_id": listing.room_id, "reason": payload.reason},
        correlation_id=correlation_id, idempotency_key=f"listing.suspended:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated


@router.post("/{listing_id}/archive", response_model=ListingRead, dependencies=[Depends(require_super_admin)])
def archive_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Super admin only. Only legal from WITHDRAWN or SUSPENDED, and only once
    no active offer/agreement still commits this listing's room (Section 8)."""
    listing = _get_or_404(db, listing_id)
    before_state = listing.state
    correlation_id = get_correlation_id(request)
    updated = crud.archive_listing(db, listing)
    log_audit_event(
        db, admin, "listing.archive", "listing", listing_id, correlation_id,
        before_state=before_state, after_state=updated.state, policy_version=CURRENT_POLICY_VERSION,
    )
    emit_event(
        db, "listing.archived", "listing", listing_id, {"room_id": listing.room_id},
        correlation_id=correlation_id, idempotency_key=f"listing.archived:{listing_id}:{correlation_id}",
    )
    db.commit()
    return updated
