"""Section 9 gap: Host right-of-entry / entry-notice rules. Previously
nothing in this codebase modeled a Host's right to enter an occupied unit
or the advance notice owed to the tenant -- see models/host_entry_visit.py.
Reuses the same jurisdiction-resolution and notification patterns as every
other market-policy-gated feature this pack has built."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud import notification as notif_crud
from app.crud.market_policy import jurisdiction_code_for_occupancy, resolve_market_policy
from app.crud.party import assert_provider_access, party_id_for_room
from app.models.admin_user import AdminUser
from app.models.guest import Guest
from app.models.host_entry_visit import ENTRY_VISIT_PURPOSES, HostEntryVisit
from app.models.occupancy import Occupancy


def schedule_entry_visit(
    db: Session,
    occupancy: Occupancy,
    admin: AdminUser,
    *,
    purpose: str,
    scheduled_at: datetime,
    is_emergency: bool = False,
    emergency_reason: str = "",
    notes: str = "",
) -> HostEntryVisit:
    if occupancy.status != "ACTIVE":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only an active occupancy can have an entry visit scheduled")
    if purpose not in ENTRY_VISIT_PURPOSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unrecognized purpose '{purpose}'")
    assert_provider_access(db, admin, party_id_for_room(occupancy.room))

    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    if is_emergency:
        if not emergency_reason.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "An emergency entry requires a reason")
    else:
        policy = resolve_market_policy(db, jurisdiction_code_for_occupancy(occupancy))
        earliest = datetime.now(timezone.utc) + timedelta(hours=policy.entry_notice_hours)
        if scheduled_at < earliest:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This jurisdiction requires at least {policy.entry_notice_hours} hours' notice before a Host entry visit",
            )

    visit = HostEntryVisit(
        occupancy_id=occupancy.id,
        room_id=occupancy.room_id,
        scheduled_by_admin_id=admin.id,
        purpose=purpose,
        notes=notes,
        scheduled_at=scheduled_at,
        is_emergency=is_emergency,
        emergency_reason=emergency_reason,
    )
    db.add(visit)
    db.commit()
    db.refresh(visit)

    notif_crud.notify_user_by_guest(
        db, occupancy.guest,
        title="A host entry visit has been scheduled" if not is_emergency else "An emergency host entry has been logged",
        message=(
            f"Your host has scheduled a {purpose.lower()} visit for {scheduled_at.isoformat()}."
            if not is_emergency
            else f"Your host logged an emergency entry ({emergency_reason})."
        ),
        notification_type="host_entry_visit.scheduled",
        related_entity_type="host_entry_visit", related_entity_id=str(visit.id),
    )
    return visit


def _get_visit_or_404(db: Session, visit_id: int) -> HostEntryVisit:
    visit = db.get(HostEntryVisit, visit_id)
    if visit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entry visit not found")
    return visit


def complete_entry_visit(db: Session, visit_id: int, admin: AdminUser, *, notes: str = "") -> HostEntryVisit:
    visit = _get_visit_or_404(db, visit_id)
    assert_provider_access(db, admin, party_id_for_room(visit.room))
    if visit.status != "SCHEDULED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot complete a visit with status {visit.status}")
    visit.status = "COMPLETED"
    visit.completed_at = datetime.now(timezone.utc)
    if notes:
        visit.notes = f"{visit.notes}\n{notes}".strip()
    db.commit()
    db.refresh(visit)
    return visit


def cancel_entry_visit(db: Session, visit_id: int, admin: AdminUser) -> HostEntryVisit:
    visit = _get_visit_or_404(db, visit_id)
    assert_provider_access(db, admin, party_id_for_room(visit.room))
    if visit.status != "SCHEDULED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel a visit with status {visit.status}")
    visit.status = "CANCELLED"
    visit.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(visit)

    notif_crud.notify_user_by_guest(
        db, visit.occupancy.guest,
        title="A host entry visit was cancelled",
        message=f"Your host cancelled the scheduled {visit.purpose.lower()} visit for {visit.scheduled_at.isoformat()}.",
        notification_type="host_entry_visit.cancelled",
        related_entity_type="host_entry_visit", related_entity_id=str(visit.id),
    )
    return visit


def list_entry_visits_for_occupancy(db: Session, occupancy: Occupancy) -> list[HostEntryVisit]:
    return list(
        db.scalars(
            select(HostEntryVisit)
            .where(HostEntryVisit.occupancy_id == occupancy.id)
            .order_by(HostEntryVisit.scheduled_at.desc())
        )
    )
