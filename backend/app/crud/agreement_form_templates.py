"""ZR-ENG-CLR-004 AC-04/AC-25: governance for prescribed-form templates
(modes B/C/D) -- same versioned/effective-dated/approve-rollback shape as
crud/agreement_clauses.py, just for AgreementFormTemplate instead of
ClauseDefinition."""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.agreement_documents import save_agreement_document
from app.models.admin_user import AdminUser
from app.models.agreement_form_template import FORM_MODES_REQUIRING_TEMPLATE, AgreementFormTemplate


def list_form_templates(
    db: Session, *, jurisdiction_scope: str | None = None, agreement_class: str | None = None, form_mode: str | None = None,
) -> list[AgreementFormTemplate]:
    query = select(AgreementFormTemplate).order_by(
        AgreementFormTemplate.jurisdiction_scope, AgreementFormTemplate.agreement_class,
        AgreementFormTemplate.form_mode, AgreementFormTemplate.version,
    )
    if jurisdiction_scope:
        query = query.where(AgreementFormTemplate.jurisdiction_scope == jurisdiction_scope)
    if agreement_class:
        query = query.where(AgreementFormTemplate.agreement_class == agreement_class)
    if form_mode:
        query = query.where(AgreementFormTemplate.form_mode == form_mode)
    return list(db.scalars(query))


def get_form_template_or_404(db: Session, template_id: int) -> AgreementFormTemplate:
    row = db.get(AgreementFormTemplate, template_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form template not found")
    return row


def currently_effective_template(
    db: Session, jurisdiction_scope: str, agreement_class: str, form_mode: str, *, today: date | None = None,
) -> AgreementFormTemplate | None:
    today = today or date.today()
    return db.scalar(
        select(AgreementFormTemplate).where(
            AgreementFormTemplate.jurisdiction_scope == jurisdiction_scope,
            AgreementFormTemplate.agreement_class == agreement_class,
            AgreementFormTemplate.form_mode == form_mode,
            AgreementFormTemplate.status == "APPROVED",
            AgreementFormTemplate.effective_from.is_not(None),
            AgreementFormTemplate.effective_from <= today,
            (AgreementFormTemplate.effective_to.is_(None)) | (AgreementFormTemplate.effective_to > today),
        )
        .order_by(AgreementFormTemplate.version.desc())
        .limit(1)
    )


def any_currently_effective_template(
    db: Session, jurisdiction_scope: str, agreement_class: str, *, today: date | None = None,
) -> AgreementFormTemplate | None:
    """Returns whichever B/C/D template (if any) is currently effective for
    this jurisdiction/class -- resolve_agreement_profile uses this to pick
    form_mode; at most one mode is expected to be configured active per
    jurisdiction/class at a time, but this returns the first found if
    several somehow are."""
    for form_mode in FORM_MODES_REQUIRING_TEMPLATE:
        template = currently_effective_template(db, jurisdiction_scope, agreement_class, form_mode, today=today)
        if template is not None:
            return template
    return None


def create_form_template_draft(
    db: Session, admin: AdminUser, *, jurisdiction_scope: str, agreement_class: str, form_mode: str, title: str,
    field_anchor_map: dict | None = None, authoritative_content_text: str = "", approval_note: str = "",
    source_document_bytes: bytes | None = None,
) -> AgreementFormTemplate:
    if form_mode not in FORM_MODES_REQUIRING_TEMPLATE:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"formMode must be one of {list(FORM_MODES_REQUIRING_TEMPLATE)}")
    if form_mode in ("B", "D") and not source_document_bytes:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Mode {form_mode} requires an uploaded source document")

    latest = db.scalar(
        select(AgreementFormTemplate)
        .where(
            AgreementFormTemplate.jurisdiction_scope == jurisdiction_scope,
            AgreementFormTemplate.agreement_class == agreement_class,
            AgreementFormTemplate.form_mode == form_mode,
        )
        .order_by(AgreementFormTemplate.version.desc())
        .limit(1)
    )
    next_version = (latest.version + 1) if latest else 1

    storage_ref, content_hash = ("", "")
    if source_document_bytes:
        storage_ref, content_hash = save_agreement_document(source_document_bytes)

    row = AgreementFormTemplate(
        jurisdiction_scope=jurisdiction_scope, agreement_class=agreement_class, form_mode=form_mode,
        version=next_version, status="DRAFT", title=title,
        source_document_storage_ref=storage_ref, source_document_content_hash=content_hash,
        field_anchor_map=field_anchor_map or {}, authoritative_content_text=authoritative_content_text,
        approval_note=approval_note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _retire(row: AgreementFormTemplate, *, today: date) -> None:
    row.status = "RETIRED"
    row.effective_to = today


def approve_form_template(db: Session, admin: AdminUser, row: AgreementFormTemplate) -> AgreementFormTemplate:
    if row.status != "DRAFT":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only a DRAFT form template can be approved")

    today = date.today()
    previously_active = db.scalar(
        select(AgreementFormTemplate).where(
            AgreementFormTemplate.jurisdiction_scope == row.jurisdiction_scope,
            AgreementFormTemplate.agreement_class == row.agreement_class,
            AgreementFormTemplate.form_mode == row.form_mode,
            AgreementFormTemplate.status == "APPROVED",
            (AgreementFormTemplate.effective_to.is_(None)) | (AgreementFormTemplate.effective_to > today),
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


def rollback_form_template(db: Session, admin: AdminUser, row: AgreementFormTemplate) -> AgreementFormTemplate:
    if row.status == "APPROVED" and (row.effective_to is None or row.effective_to > date.today()):
        raise HTTPException(status.HTTP_409_CONFLICT, "This form template version is already the active one")

    today = date.today()
    currently_active = db.scalar(
        select(AgreementFormTemplate).where(
            AgreementFormTemplate.jurisdiction_scope == row.jurisdiction_scope,
            AgreementFormTemplate.agreement_class == row.agreement_class,
            AgreementFormTemplate.form_mode == row.form_mode,
            AgreementFormTemplate.status == "APPROVED",
            (AgreementFormTemplate.effective_to.is_(None)) | (AgreementFormTemplate.effective_to > today),
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
