"""ZR-ENG-CLR-010 Section 23/26: dispute deadline tracking. No scheduler
exists anywhere in this codebase (see services/booking_expiry.py's own
docstring on the same limitation) -- "breached" is never a stored status a
missed background tick could get wrong; it's always computed against
datetime.now(UTC) at read time via is_overdue()."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.dispute import DisputeResolutionCase
from app.models.dispute_deadline import DISPUTE_DEADLINE_TYPES, DisputeDeadline


def compute_reminder_at(created_at: datetime, due_at: datetime) -> datetime | None:
    """AC-29: a reasonable MVP default -- the midpoint of the window
    between creation and the due date -- not a verified SLA figure. None
    for a deadline already due (or overdue) at creation time, since a
    reminder can't meaningfully precede "now"."""
    if due_at <= created_at:
        return None
    return created_at + (due_at - created_at) / 2


def create_deadline(
    db: Session,
    case: DisputeResolutionCase,
    *,
    deadline_type: str,
    due_at: datetime,
    claim_id: int | None = None,
    admin: AdminUser | None = None,
    source: str = "ADMIN_SET",
) -> DisputeDeadline:
    if deadline_type not in DISPUTE_DEADLINE_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown deadline type '{deadline_type}'")
    if claim_id is not None:
        claim = next((c for c in case.claims if c.id == claim_id), None)
        if claim is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Claim {claim_id} does not belong to this case")

    created_at = datetime.now(timezone.utc)
    deadline = DisputeDeadline(
        case_id=case.id,
        claim_id=claim_id,
        deadline_type=deadline_type,
        due_at=due_at,
        status="PENDING",
        source=source,
        created_by_admin_id=admin.id if admin is not None else None,
        created_at=created_at,
        reminder_at=compute_reminder_at(created_at, due_at),
    )
    db.add(deadline)
    db.commit()
    db.refresh(deadline)
    return deadline


def get_deadline_or_404(db: Session, deadline_id: int) -> DisputeDeadline:
    deadline = db.get(DisputeDeadline, deadline_id)
    if not deadline:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deadline not found")
    return deadline


def list_deadlines_for_case(db: Session, case: DisputeResolutionCase) -> list[DisputeDeadline]:
    query = select(DisputeDeadline).where(DisputeDeadline.case_id == case.id).order_by(DisputeDeadline.due_at.asc())
    return list(db.scalars(query))


def latest_deadline_for_claim(db: Session, claim_id: int, *, deadline_type: str) -> DisputeDeadline | None:
    """Most recently created still-tracked (not cancelled) deadline of this
    type for a claim -- used by crud/disputes.py:request_internal_review to
    find the authoritative INTERNAL_REVIEW window instead of recomputing a
    fixed offset inline."""
    query = (
        select(DisputeDeadline)
        .where(DisputeDeadline.claim_id == claim_id, DisputeDeadline.deadline_type == deadline_type, DisputeDeadline.status != "CANCELLED")
        .order_by(DisputeDeadline.created_at.desc())
    )
    return db.scalars(query).first()


def is_overdue(deadline: DisputeDeadline) -> bool:
    return deadline.status == "PENDING" and datetime.now(timezone.utc) > deadline.due_at


def is_reminder_due(deadline: DisputeDeadline) -> bool:
    """AC-29: true once the deadline's reminder_at has passed but it's
    still PENDING/EXTENDED and not yet overdue -- see reminder_at's own
    docstring for why nothing here actually sends anything."""
    if deadline.status not in ("PENDING", "EXTENDED"):
        return False
    if deadline.reminder_at is None:
        return False
    now = datetime.now(timezone.utc)
    return deadline.reminder_at <= now < deadline.due_at


def extend_deadline(db: Session, deadline: DisputeDeadline, admin: AdminUser, *, new_due_at: datetime, extension_basis: str) -> DisputeDeadline:
    if deadline.status == "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot extend a cancelled deadline")
    if not extension_basis.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "extension_basis is required to extend a deadline")

    # AC-33: original_due_at is set once, on the FIRST extension only --
    # never overwritten by a later extension.
    if deadline.original_due_at is None:
        deadline.original_due_at = deadline.due_at
    deadline.due_at = new_due_at
    deadline.extension_basis = extension_basis
    deadline.status = "EXTENDED"
    # AC-29: an extended deadline gets its own fresh reminder window,
    # computed from now (not the original creation time) to the new
    # due_at -- the old reminder point is meaningless once the deadline
    # itself has moved.
    deadline.reminder_at = compute_reminder_at(datetime.now(timezone.utc), new_due_at)
    db.commit()
    db.refresh(deadline)
    return deadline


def mark_met(db: Session, deadline: DisputeDeadline) -> DisputeDeadline:
    if deadline.status == "CANCELLED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot mark a cancelled deadline as met")
    deadline.status = "MET"
    db.commit()
    db.refresh(deadline)
    return deadline


def cancel_deadline(db: Session, deadline: DisputeDeadline) -> DisputeDeadline:
    deadline.status = "CANCELLED"
    db.commit()
    db.refresh(deadline)
    return deadline
