"""ZR-ENG-CLR-012 AC-27: break-glass sensitive-data access -- time-limited,
reason-coded, audited. Self-service (an admin grants it to themselves) by
design: emergency access that requires someone else's approval first isn't
actually emergency access. The audit trail (log_audit_event, called by the
route) plus the mandatory reason is what makes this reviewable after the
fact, which is the real control here -- not a gate before the fact."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.break_glass_access import BREAK_GLASS_DEFAULT_DURATION_MINUTES, BreakGlassAccessGrant


def grant_break_glass_access(
    db: Session, admin: AdminUser, *, related_entity_type: str, related_entity_id: str, reason: str,
    duration_minutes: int = BREAK_GLASS_DEFAULT_DURATION_MINUTES,
) -> BreakGlassAccessGrant:
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to grant break-glass access")

    now = datetime.now(timezone.utc)
    grant = BreakGlassAccessGrant(
        admin_id=admin.id, related_entity_type=related_entity_type, related_entity_id=related_entity_id,
        reason=reason.strip(), granted_at=now, expires_at=now + timedelta(minutes=duration_minutes),
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def has_valid_break_glass_access(db: Session, admin_id: int, related_entity_type: str, related_entity_id: str) -> bool:
    now = datetime.now(timezone.utc)
    grant = db.scalar(
        select(BreakGlassAccessGrant).where(
            BreakGlassAccessGrant.admin_id == admin_id,
            BreakGlassAccessGrant.related_entity_type == related_entity_type,
            BreakGlassAccessGrant.related_entity_id == related_entity_id,
            BreakGlassAccessGrant.expires_at > now,
            BreakGlassAccessGrant.revoked_at.is_(None),
        )
    )
    return grant is not None


def revoke_break_glass_access(db: Session, grant: BreakGlassAccessGrant) -> BreakGlassAccessGrant:
    grant.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(grant)
    return grant
