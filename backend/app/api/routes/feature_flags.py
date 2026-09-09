"""Admin feature-flag endpoints (super_admin) — Phase 7.

Server-authoritative capability gating. Only allow-listed non-secret flag names
can be read/updated; every change is audit-logged. Non-super-admins receive
403 on mutations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.services.feature_flags import (
    FLAG_INVARIANTS,
    FeatureFlagError,
    effective_flags,
    known_flags,
    set_flag,
)

router = APIRouter(
    prefix="/api/admin/feature-flags",
    tags=["feature-flags"],
    dependencies=[Depends(get_current_admin)],
)


@router.get("")
def list_flags(
    _admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """List every allowed flag with its safe default and effective value."""
    flags = known_flags()
    effective = effective_flags(db, role=_admin.role)
    return {
        "flags": [
            {
                "name": spec.name,
                "default": spec.default,
                "value": effective[spec.name],
                "description": spec.description,
                "markets": list(spec.markets),
                "roles": list(spec.roles),
            }
            for spec in flags
        ],
        "invariants": FLAG_INVARIANTS,
    }


class FlagUpdate(BaseModel):
    value: bool
    note: str = ""
    market: str | None = None
    role: str | None = None


@router.put("/{name}")
def update_flag(
    name: str,
    body: FlagUpdate,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    """Upsert an override for an allow-listed flag (super_admin only)."""
    try:
        row = set_flag(
            db,
            admin,
            name,
            body.value,
            note=body.note,
            market=body.market,
            role=body.role,
        )
    except FeatureFlagError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    db.commit()
    return {"name": name, "value": row.value, "note": row.note, "enabled_by": row.enabled_by}
