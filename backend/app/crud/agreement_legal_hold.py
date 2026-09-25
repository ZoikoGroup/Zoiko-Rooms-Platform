"""Section 4 gap / spec API 'POST /retention/legal-hold | Apply scoped
preservation hold with authority': agreements had no retention/legal-hold
model at all -- nothing stopped a material change being proposed against an
agreement under litigation-hold preservation. See
models/agreement_legal_hold.py for why this mirrors
models/dispute_legal_hold.py's shape rather than a bare boolean flag.

Real enforcement (not a decorative field): crud/agreement_amendments.py:
request_amendment refuses (409) while an active hold exists on the
agreement -- the same "hold blocks the one mutating operation that actually
exists" shape as dispute_evidence.py:archive_evidence refusing over an
active DisputeLegalHold."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.audit import log_audit_event
from app.models.admin_user import AdminUser
from app.models.agreement_legal_hold import AgreementLegalHold
from app.models.leasing import Agreement


def get_active_legal_hold(db: Session, agreement: Agreement) -> AgreementLegalHold | None:
    return db.scalar(
        select(AgreementLegalHold).where(
            AgreementLegalHold.agreement_id == agreement.id, AgreementLegalHold.status == "ACTIVE",
        )
    )


def list_legal_holds(db: Session, agreement: Agreement) -> list[AgreementLegalHold]:
    return list(
        db.scalars(
            select(AgreementLegalHold)
            .where(AgreementLegalHold.agreement_id == agreement.id)
            .order_by(AgreementLegalHold.placed_at)
        )
    )


def get_legal_hold_or_404(db: Session, agreement: Agreement, hold_id: int) -> AgreementLegalHold:
    hold = db.get(AgreementLegalHold, hold_id)
    if not hold or hold.agreement_id != agreement.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Legal hold not found")
    return hold


def place_legal_hold(
    db: Session, agreement: Agreement, admin: AdminUser, *, reason: str, authority_evidence_ref: str,
) -> AgreementLegalHold:
    if not authority_evidence_ref.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Authority evidence is required to place a legal hold")
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A reason is required to place a legal hold")
    if get_active_legal_hold(db, agreement) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This agreement is already under an active legal hold")

    hold = AgreementLegalHold(
        agreement_id=agreement.id, status="ACTIVE",
        reason=reason.strip(), authority_evidence_ref=authority_evidence_ref.strip(),
        placed_by_admin_id=admin.id,
    )
    db.add(hold)
    db.commit()
    db.refresh(hold)
    log_audit_event(
        db, admin, "agreement.legal_hold.place", "agreement_legal_hold", str(hold.id), reason=reason.strip(),
    )
    db.commit()
    return hold


def release_legal_hold(db: Session, agreement: Agreement, admin: AdminUser) -> AgreementLegalHold:
    hold = get_active_legal_hold(db, agreement)
    if hold is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This agreement has no active legal hold to release")

    hold.status = "RELEASED"
    hold.released_by_admin_id = admin.id
    hold.released_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(hold)
    log_audit_event(db, admin, "agreement.legal_hold.release", "agreement_legal_hold", str(hold.id))
    db.commit()
    return hold
