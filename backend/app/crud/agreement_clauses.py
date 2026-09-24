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


def _same_clause(row: ClauseDefinition):
    """Versions are counted per (clause_id, jurisdiction, class): England's
    rent_and_charges and another region's rent_and_charges are separate
    clauses with independent version histories."""
    return (
        (ClauseDefinition.clause_id == row.clause_id)
        & (ClauseDefinition.jurisdiction_scope == row.jurisdiction_scope)
        & (ClauseDefinition.agreement_class == row.agreement_class)
    )


def list_clause_versions(
    db: Session, clause_id: str | None = None, jurisdiction_scope: str | None = None,
) -> list[ClauseDefinition]:
    query = select(ClauseDefinition).order_by(
        ClauseDefinition.jurisdiction_scope, ClauseDefinition.clause_id, ClauseDefinition.version,
    )
    if clause_id:
        query = query.where(ClauseDefinition.clause_id == clause_id)
    if jurisdiction_scope:
        query = query.where(ClauseDefinition.jurisdiction_scope == jurisdiction_scope)
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

    jurisdiction_scope = jurisdiction_scope.strip()
    if not jurisdiction_scope:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "jurisdictionScope is required")

    row = _add_clause_draft(
        db, clause_id=clause_id, jurisdiction_scope=jurisdiction_scope, agreement_class=agreement_class,
        mandatory_level=mandatory_level, title=title, approval_note=approval_note,
    )
    db.commit()
    db.refresh(row)
    return row


def _add_clause_draft(
    db: Session, *, clause_id: str, jurisdiction_scope: str, agreement_class: str,
    mandatory_level: str, title: str, approval_note: str,
) -> ClauseDefinition:
    latest = db.scalar(
        select(ClauseDefinition)
        .where(
            ClauseDefinition.clause_id == clause_id,
            ClauseDefinition.jurisdiction_scope == jurisdiction_scope,
            ClauseDefinition.agreement_class == agreement_class,
        )
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
    db.flush()
    return row


def copy_default_clauses_to_jurisdiction(db: Session, admin: AdminUser, jurisdiction_scope: str) -> list[ClauseDefinition]:
    """Gives a newly opened region a starting clause registry: one DRAFT row
    per default clause (England's placeholder catalog) that this region
    doesn't already have. Nothing becomes effective until an admin reviews
    and approves each draft -- code never makes another region's legal
    content live on its own. Returns the drafts created (empty if the
    region already has every default clause)."""
    from app.services.agreement_profile import AGREEMENT_CLASS, DEFAULT_CLAUSES, SUPPORTED_JURISDICTION

    jurisdiction_scope = jurisdiction_scope.strip()
    if not jurisdiction_scope:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "jurisdictionScope is required")
    if jurisdiction_scope == SUPPORTED_JURISDICTION:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{SUPPORTED_JURISDICTION} already has the default clauses",
        )

    existing = set(db.scalars(
        select(ClauseDefinition.clause_id).where(
            ClauseDefinition.jurisdiction_scope == jurisdiction_scope,
            ClauseDefinition.agreement_class == AGREEMENT_CLASS,
        )
    ))
    created = [
        _add_clause_draft(
            db, clause_id=clause_id, jurisdiction_scope=jurisdiction_scope, agreement_class=AGREEMENT_CLASS,
            mandatory_level=mandatory_level, title=title,
            approval_note=f"Copied from {SUPPORTED_JURISDICTION} defaults -- review for {jurisdiction_scope} before approving.",
        )
        for clause_id, mandatory_level, title in DEFAULT_CLAUSES
        if clause_id not in existing
    ]
    db.commit()
    for row in created:
        db.refresh(row)
    return created


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
            _same_clause(row),
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
            _same_clause(row),
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
