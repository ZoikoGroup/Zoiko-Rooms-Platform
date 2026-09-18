"""ZR-ENG-CLR-004 Section 7.2/AC-29: version-controlled clause translations.
See models/agreement_clause_translation.py's own docstring for the core
guarantee -- a translation is pinned to one exact (clause_id, version) row
and never silently carries forward when that clause gets a new version."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_user import AdminUser
from app.models.agreement_clause import ClauseDefinition
from app.models.agreement_clause_translation import ClauseTranslation


def get_translation_or_404(db: Session, translation_id: int) -> ClauseTranslation:
    row = db.get(ClauseTranslation, translation_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Translation not found")
    return row


def list_translations_for_clause(db: Session, clause_definition_id: int) -> list[ClauseTranslation]:
    return list(
        db.scalars(
            select(ClauseTranslation)
            .where(ClauseTranslation.clause_definition_id == clause_definition_id)
            .order_by(ClauseTranslation.language_code)
        )
    )


def create_translation(
    db: Session, admin: AdminUser, *, clause_definition_id: int, language_code: str, translated_title: str,
    translated_content: str,
) -> ClauseTranslation:
    clause = db.get(ClauseDefinition, clause_definition_id)
    if not clause:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Clause version not found")

    existing = db.scalar(
        select(ClauseTranslation).where(
            ClauseTranslation.clause_definition_id == clause_definition_id,
            ClauseTranslation.language_code == language_code,
        )
    )
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A translation for {language_code} already exists for this exact clause version -- create a new clause version to revise it",
        )

    row = ClauseTranslation(
        clause_definition_id=clause_definition_id, language_code=language_code,
        translated_title=translated_title, translated_content=translated_content, status="DRAFT",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def approve_translation(db: Session, admin: AdminUser, translation: ClauseTranslation) -> ClauseTranslation:
    if translation.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a DRAFT translation can be approved")
    translation.status = "APPROVED"
    db.commit()
    db.refresh(translation)
    return translation


def missing_translations_for_effective_clauses(db: Session, language_code: str) -> list[ClauseDefinition]:
    """AC-29 drift visibility: every currently-effective (APPROVED,
    in-window) clause version that has no APPROVED translation in
    language_code -- i.e. exactly the rows a market requiring that language
    cannot legally use yet."""
    today = date.today()
    effective_clauses = list(
        db.scalars(
            select(ClauseDefinition).where(
                ClauseDefinition.status == "APPROVED",
                ClauseDefinition.effective_from.is_not(None),
                ClauseDefinition.effective_from <= today,
                (ClauseDefinition.effective_to.is_(None)) | (ClauseDefinition.effective_to > today),
            )
        )
    )
    missing = []
    for clause in effective_clauses:
        approved_translation = db.scalar(
            select(ClauseTranslation).where(
                ClauseTranslation.clause_definition_id == clause.id,
                ClauseTranslation.language_code == language_code,
                ClauseTranslation.status == "APPROVED",
            )
        )
        if approved_translation is None:
            missing.append(clause)
    return missing
