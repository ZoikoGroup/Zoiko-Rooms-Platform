from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.crud import listing as crud
from app.crud.audit import log_audit_event
from app.crud.events import emit_event
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.listing import ListingCreate, ListingRead, ListingRejectRequest, ListingUpdate

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
    updated = crud.approve_listing(db, listing, admin)
    log_audit_event(db, admin, "listing.approve", "listing", listing_id, get_correlation_id(request))
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
    updated = crud.publish_listing(db, listing, admin)
    log_audit_event(db, admin, "listing.publish", "listing", listing_id, get_correlation_id(request))
    emit_event(db, "listing.published", "listing", listing_id, {"room_id": listing.room_id})
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
    updated = crud.reject_listing(db, listing, payload.reason, admin)
    log_audit_event(db, admin, "listing.reject", "listing", listing_id, get_correlation_id(request), reason=payload.reason)
    db.commit()
    return updated


@router.post("/{listing_id}/pause", response_model=ListingRead)
def pause_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Any admin/super admin may pause any listing -- same operational scope as
    approve/publish/reject, not restricted to "listings I personally own"
    (a USER-hosted listing has no owning admin at all, so that restriction would
    make it un-pausable by a plain admin)."""
    listing = _get_or_404(db, listing_id)
    updated = crud.set_listing_state(db, listing, "PAUSED")
    log_audit_event(db, admin, "listing.pause", "listing", listing_id, get_correlation_id(request))
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
    updated = crud.set_listing_state(db, listing, "WITHDRAWN")
    log_audit_event(db, admin, "listing.withdraw", "listing", listing_id, get_correlation_id(request))
    db.commit()
    return updated


@router.post("/{listing_id}/suspend", response_model=ListingRead, dependencies=[Depends(require_super_admin)])
def suspend_listing(
    listing_id: str,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    listing = _get_or_404(db, listing_id)
    updated = crud.set_listing_state(db, listing, "SUSPENDED")
    log_audit_event(db, admin, "listing.suspend", "listing", listing_id, get_correlation_id(request))
    db.commit()
    return updated
