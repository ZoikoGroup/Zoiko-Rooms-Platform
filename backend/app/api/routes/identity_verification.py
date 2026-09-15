from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, require_super_admin
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path
from app.crud import break_glass_access as break_glass_crud
from app.crud import identity_verification as crud
from app.crud.audit import log_audit_event
from app.crud.eligibility import check_offer_eligibility
from app.db.session import get_db
from app.models.admin_user import AdminUser
from app.schemas.marketplace import (
    BreakGlassAccessRequest,
    IdentityVerificationCreate,
    IdentityVerificationReject,
    IdentityVerificationRead,
)

router = APIRouter(prefix="/api/identity-verifications", tags=["identity-verifications"], dependencies=[Depends(get_current_admin)])


@router.get("", response_model=list[IdentityVerificationRead])
def get_identity_verifications(
    party_id: int | None = None,
    status: str | None = None,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return crud.list_identity_verifications(db, admin, party_id, status)


@router.post("", response_model=IdentityVerificationRead, status_code=status.HTTP_201_CREATED)
def post_identity_verification(payload: IdentityVerificationCreate, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    return crud.submit_identity_verification(db, admin, payload)


@router.post("/{verification_id}/verify", response_model=IdentityVerificationRead, dependencies=[Depends(require_super_admin)])
def verify_identity_verification(
    verification_id: int,
    request: Request,
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    updated = crud.verify_identity_verification(db, record, admin)
    log_audit_event(db, admin, "identity_verification.verify", "identity_verification", str(verification_id), get_correlation_id(request))
    db.commit()
    return updated


@router.post("/{verification_id}/reject", response_model=IdentityVerificationRead, dependencies=[Depends(require_super_admin)])
def reject_identity_verification(
    verification_id: int,
    request: Request,
    payload: IdentityVerificationReject | None = Body(default=None),
    admin: AdminUser = Depends(require_super_admin),
    db: Session = Depends(get_db),
):
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    notes = payload.notes if payload else ""
    updated = crud.reject_identity_verification(db, record, admin, notes)
    log_audit_event(db, admin, "identity_verification.reject", "identity_verification", str(verification_id), get_correlation_id(request), reason=notes)
    db.commit()
    return updated


@router.get("/{verification_id}/document")
def download_identity_document(verification_id: int, admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Super admins can always view uploaded identity documents. A plain
    admin needs an active break-glass grant for this specific record --
    ZR-ENG-CLR-012 AC-27, AC-26 ('support agents cannot browse raw identity
    documents by default')."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if admin.role != "super_admin" and not break_glass_crud.has_valid_break_glass_access(
        db, admin.id, "identity_verification", str(verification_id)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin access or an active break-glass grant is required")
    if not record.document_file_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No document was uploaded for this verification")

    path = resolve_identity_document_path(record.document_file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document could not be found")

    return FileResponse(
        path,
        media_type=record.document_file_content_type or "application/octet-stream",
        filename=record.document_file_original_name or "document",
    )


@router.post("/{verification_id}/break-glass-access", status_code=status.HTTP_201_CREATED)
def post_grant_break_glass_access(
    verification_id: int, payload: BreakGlassAccessRequest, request: Request,
    admin: AdminUser = Depends(get_current_admin), db: Session = Depends(get_db),
):
    """ZR-ENG-CLR-012 AC-27: self-service, time-limited, reason-coded,
    audited emergency access for a plain admin to one specific document.
    Grants access to the calling admin only, for BREAK_GLASS_DEFAULT_
    DURATION_MINUTES -- not a permanent role change."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    grant = break_glass_crud.grant_break_glass_access(
        db, admin, related_entity_type="identity_verification", related_entity_id=str(verification_id),
        reason=payload.reason,
    )
    log_audit_event(
        db, admin, "identity_verification.break_glass_access", "identity_verification", str(verification_id),
        get_correlation_id(request), reason=payload.reason,
    )
    db.commit()
    return {"grantId": grant.id, "expiresAt": grant.expires_at.isoformat()}
