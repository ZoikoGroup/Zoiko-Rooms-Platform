"""ZR-ENG-CLR-004 AC-25/Section 12 'Change legal template: ... Legal content
governance only': the minimal admin workflow for versioning the clause
registry -- create a new draft version, approve it (which retires whatever
was previously active for that clause_id), or roll back to an older version
(which retires whatever is currently active and reactivates the one named).
super_admin only, matching the route-level restriction on every other
governance-shaped action in this codebase (application decisions, market
release approval).

LEGAL CONTENT WARNING: this workflow makes the registry's versioning
machinery real and testable -- it does not make any clause's *content* real
legal wording. See models/agreement_clause.py's own warning.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.agreement_clause import CLAUSE_MANDATORY_LEVELS, ClauseDefinition


def list_clause_versions(db: Session, clause_id: str | None = None) -> list[ClauseDefinition]:
    query = select(ClauseDefinition).order_by(ClauseDefinition.clause_id, ClauseDefinition.version)
    if clause_id:
        query = query.where(ClauseDefinition.clause_id == clause_id)
    return list(db.scalars(query))


def get_clause_version_or_404(db: Session, clause_definition_id: int) -> ClauseDefinition:
    row = db.get(ClauseDefinition, clause_definition_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clause version not found")
    return row


def create_clause_draft(
    db: Session, admin: AdminUser, *, clause_id: str, jurisdiction_scope: str, agreement_class: str,
    mandatory_level: str, title: str, approval_note: str = "",
) -> ClauseDefinition:
    if mandatory_level not in CLAUSE_MANDATORY_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"mandatoryLevel must be one of {list(CLAUSE_MANDATORY_LEVELS)}")

    latest = db.scalar(
        select(ClauseDefinition)
        .where(ClauseDefinition.clause_id == clause_id)
        .order_by(ClauseDefinition.version.desc())
        .limit(1)
    )
    next_version = (latest.version + 1) if latest else 1

    row = ClauseDefinition(
        clause_id=clause_id,
        jurisdiction_scope=jurisdiction_scope,
        agreement_class=agreement_class,
        mandatory_level=mandatory_level,
        status="DRAFT",
        version=next_version,
        title=title,
        approval_note=approval_note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _retire(row: ClauseDefinition, *, today: date) -> None:
    row.status = "RETIRED"
    row.effective_to = today


def approve_clause_version(db: Session, admin: AdminUser, row: ClauseDefinition) -> ClauseDefinition:
    """DRAFT -> APPROVED, effective immediately. Whatever row was previously
    the currently-effective version of this same clause_id is retired in the
    same transaction -- there is never more than one APPROVED-and-in-window
    row per clause_id (see services/agreement_profile.py:_currently_effective_row,
    which would otherwise have to pick arbitrarily between two)."""
    if row.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a DRAFT clause version can be approved")

    today = date.today()
    previously_active = db.scalar(
        select(ClauseDefinition).where(
            ClauseDefinition.clause_id == row.clause_id,
            ClauseDefinition.status == "APPROVED",
            (ClauseDefinition.effective_to.is_(None)) | (ClauseDefinition.effective_to > today),
        )
    )
    if previously_active is not None and previously_active.id != row.id:
        _retire(previously_active, today=today)

    row.status = "APPROVED"
    row.effective_from = today
    row.effective_to = None
    db.commit()
    db.refresh(row)
    return row


def rollback_clause(db: Session, admin: AdminUser, row: ClauseDefinition) -> ClauseDefinition:
    """AC-25 'rollback-capable': reactivates an older (RETIRED) version --
    typically after a bad approve_clause_version -- by retiring whatever is
    currently active for this clause_id and reopening this row's effective
    window. Symmetric with approve_clause_version, not a separate code path,
    so the 'never two active rows for one clause_id' invariant holds either
    way."""
    if row.status == "APPROVED" and (row.effective_to is None or row.effective_to > date.today()):
        raise HTTPException(status.HTTP_409_CONFLICT, "This clause version is already the active one")

    today = date.today()
    currently_active = db.scalar(
        select(ClauseDefinition).where(
            ClauseDefinition.clause_id == row.clause_id,
            ClauseDefinition.status == "APPROVED",
            (ClauseDefinition.effective_to.is_(None)) | (ClauseDefinition.effective_to > today),
        )
    )
    if currently_active is not None and currently_active.id != row.id:
        _retire(currently_active, today=today)

    row.status = "APPROVED"
    row.effective_from = today
    row.effective_to = None
    db.commit()
    db.refresh(row)
    return row
