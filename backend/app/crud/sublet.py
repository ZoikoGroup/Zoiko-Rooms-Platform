from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.identity_verification import IdentityVerification
from app.models.occupancy import Occupancy
from app.models.party import Party
from app.models.sublet_request import SubletRequest
from app.models.user_account import UserAccount
from app.models.guest import Guest
from app.crud import guest as guest_crud
from app.crud import notification as notif_crud


def _assert_sublet_permitted(occupancy: Occupancy) -> None:
    """Only transfer an active signed tenancy with at least 30 nights remaining."""
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Can only sublet active occupancies")
    if not occupancy.offer.agreement or occupancy.offer.agreement.status != "SIGNED":
        raise HTTPException(status.HTTP_409_CONFLICT, "A signed agreement is required before subletting")
    if not occupancy.expected_end_date or (occupancy.expected_end_date - date.today()).days < 30:
        raise HTTPException(status.HTTP_409_CONFLICT, "A sublet transfer requires at least 30 nights remaining")


def _guest_for_proposed_party(db: Session, party_id: int) -> Guest:
    user = db.scalar(
        select(UserAccount)
        .where(UserAccount.party_id == party_id, UserAccount.is_active.is_(True))
        .order_by(UserAccount.id)
    )
    if not user:
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter party has no active user account")
    return guest_crud.get_or_create_guest_for_user(db, user)


def submit_sublet_request(
    db: Session,
    user: UserAccount,
    occupancy_id: int,
    proposed_renter_party_id: int,
    authority_evidence_ref: str = "",
) -> SubletRequest:
    """Current renter submits a sublet request."""
    if not user.party_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User has no associated party")

    # Verify the user owns the current occupancy
    occupancy = db.get(Occupancy, occupancy_id)
    if not occupancy:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Occupancy not found")

    _assert_sublet_permitted(occupancy)

    # Proposed renter party must exist
    proposed_party = db.get(Party, proposed_renter_party_id)
    if not proposed_party:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Proposed renter party not found")
    if not verify_sublet_identity(
        db, type("SubletIdentityCandidate", (), {"proposed_renter_party_id": proposed_renter_party_id})()
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Proposed renter must have an approved identity verification")

    # Check if sublet request already exists
    existing = db.scalar(
        select(SubletRequest).where(
            SubletRequest.current_occupancy_id == occupancy_id,
            SubletRequest.status.in_(["pending_verification", "pending_admin_review"]),
        )
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sublet request already exists for this occupancy")

    sublet_request = SubletRequest(
        current_occupancy_id=occupancy_id,
        proposed_renter_party_id=proposed_renter_party_id,
        status="pending_admin_review",
        authority_evidence_ref=authority_evidence_ref,
    )
    db.add(sublet_request)
    db.flush()

    notif_crud.notify_all_super_admins(
        db,
        title="New sublet request pending review",
        message=f"{user.full_name} requested to sublet occupancy #{occupancy_id}.",
        notification_type="sublet_request.submitted",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def list_sublet_requests_for_user(db: Session, user: UserAccount) -> list[SubletRequest]:
    """List all sublet requests initiated by current user."""
    if not user.party_id:
        return []

    return list(
        db.scalars(
            select(SubletRequest)
            .join(Occupancy, Occupancy.id == SubletRequest.current_occupancy_id)
            .where(Occupancy.guest_id.in_(select(guest_id_from_user(user))))
            .order_by(SubletRequest.created_at.desc())
        )
    )


def guest_id_from_user(user: UserAccount) -> str:
    """Get guest_id associated with user for queries."""
    # This is a simple mapping - in practice, you'd query the Guest table
    # For now, we'll handle this in the route layer
    pass


def get_sublet_request(db: Session, sublet_request_id: int) -> SubletRequest | None:
    return db.get(SubletRequest, sublet_request_id)


def verify_sublet_identity(
    db: Session,
    sublet_request: SubletRequest,
) -> bool:
    """Check if proposed renter has verified identity."""
    now = datetime.now(timezone.utc)
    verification = db.scalar(
        select(IdentityVerification)
        .where(
            IdentityVerification.party_id == sublet_request.proposed_renter_party_id,
            IdentityVerification.status == "verified",
            (IdentityVerification.expires_at.is_(None)) | (IdentityVerification.expires_at > now),
        )
        .order_by(IdentityVerification.id.desc())
    )
    return verification is not None


def _notify_sublet_requester(
    db: Session, requester_guest_id: str, sublet_request_id: int, *, approved: bool, notes: str
) -> None:
    guest = db.get(Guest, requester_guest_id)
    if not guest:
        return
    verb = "approved" if approved else "rejected"
    notif_crud.notify_user_by_guest(
        db, guest,
        title=f"Sublet request {verb}",
        message=notes or f"Your sublet request was {verb}.",
        notification_type=f"sublet_request.{verb}",
        related_entity_type="sublet_request",
        related_entity_id=str(sublet_request_id),
    )


def approve_sublet_request(db: Session, sublet_request: SubletRequest, admin: AdminUser, notes: str = "") -> SubletRequest:
    """Admin approves a sublet request."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can approve sublet requests")

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be approved")
    if not verify_sublet_identity(db, sublet_request):
        raise HTTPException(status.HTTP_409_CONFLICT, "Proposed renter no longer has an approved identity verification")
    _assert_sublet_permitted(sublet_request.current_occupancy)
    # Capture who to notify *before* reassigning the occupancy's guest below --
    # afterwards current_occupancy.guest_id points at the new tenant, not the
    # person who submitted this request.
    requester_guest_id = sublet_request.current_occupancy.guest_id

    listing = sublet_request.current_occupancy.listing
    proposed_guest = _guest_for_proposed_party(db, sublet_request.proposed_renter_party_id)
    sublet_request.current_occupancy.guest_id = proposed_guest.id
    sublet_request.status = "approved"
    sublet_request.admin_decision = "approved"
    sublet_request.admin_notes = notes
    sublet_request.decided_by_admin_id = admin.id
    sublet_request.decided_at = datetime.now(timezone.utc)

    _notify_sublet_requester(db, requester_guest_id, sublet_request.id, approved=True, notes=notes)

    # Only notified now that they're actually authorized -- never earlier in the
    # request/review flow, so a proposed occupant's involvement isn't exposed
    # prematurely.
    notif_crud.notify_user_by_guest(
        db, proposed_guest,
        title="You've been authorized as a new occupant",
        message=f'You have been approved to take over occupancy of "{listing.name}".' if listing else "You have been approved to take over an occupancy.",
        notification_type="sublet_request.authorized",
        related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
    )
    if listing and listing.party_id:
        notif_crud.notify_user_by_party(
            db, listing.party_id,
            title="Occupant changed via sublet",
            message=f'The occupant of "{listing.name}" has changed following an approved sublet request.',
            notification_type="sublet_request.tenant_changed",
            related_entity_type="sublet_request", related_entity_id=str(sublet_request.id),
        )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def reject_sublet_request(db: Session, sublet_request: SubletRequest, admin: AdminUser, notes: str = "") -> SubletRequest:
    """Admin rejects a sublet request."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can reject sublet requests")

    if sublet_request.status != "pending_admin_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only pending sublet requests can be rejected")
    sublet_request.status = "rejected"
    sublet_request.admin_decision = "rejected"
    sublet_request.admin_notes = notes
    sublet_request.decided_by_admin_id = admin.id
    sublet_request.decided_at = datetime.now(timezone.utc)

    _notify_sublet_requester(
        db, sublet_request.current_occupancy.guest_id, sublet_request.id, approved=False, notes=notes
    )

    db.commit()
    db.refresh(sublet_request)
    return sublet_request


def list_pending_sublet_requests(db: Session, admin: AdminUser) -> list[SubletRequest]:
    """List all pending sublet requests for admin review."""
    if admin.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only super admins can view all sublet requests")

    return list(
        db.scalars(
            select(SubletRequest)
            .where(SubletRequest.status == "pending_admin_review")
            .order_by(SubletRequest.created_at.desc())
        )
    )
