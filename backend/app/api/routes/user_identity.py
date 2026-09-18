from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.correlation import get_correlation_id
from app.core.identity_uploads import resolve_identity_document_path, save_identity_document
from app.crud import evidence_vault as evidence_vault_crud
from app.crud import identity_verification as crud
from app.crud.audit import log_audit_event
from app.db.session import get_db
from app.models.identity_verification import IdentityVerification
from app.models.user_account import UserAccount
from app.schemas.marketplace import IdentityVerificationUserRead

router = APIRouter(
    prefix="/api/users/identity-verifications",
    tags=["user-identity-verifications"],
    dependencies=[Depends(get_current_user)],
)


def _to_user_read(record: IdentityVerification) -> dict:
    return {
        "id": record.id,
        "document_type": record.document_type,
        "document_category": record.document_category,
        "custom_document_name": record.custom_document_name,
        "document_number": record.encrypted_reference or "",
        "evidence_ref": record.evidence_ref,
        "status": record.status,
        "has_document": record.has_document,
        "document_original_name": record.document_file_original_name,
        "document_content_type": record.document_file_content_type,
        "verified_at": record.verified_at,
        "expires_at": record.expires_at,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "verifier_notes": record.verifier_notes,
    }


@router.post("", response_model=IdentityVerificationUserRead, status_code=status.HTTP_201_CREATED)
async def submit_identity_verification(
    request: Request,
    document_type: str = Form(...),
    document_number: str = Form(""),
    custom_document_name: str = Form(""),
    file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User submits their identity verification with an uploaded document. This is
    a multipart request (not JSON) because it always carries a real file -- see
    app/core/identity_uploads.py for the content validation and storage."""
    stored_filename, original_filename, content_type, file_size, sha256_hash = await save_identity_document(file)

    # ZR-ENG-CLR-012 Section 18: checked before the new record/artifact exist,
    # so a match here is necessarily a *prior* submission, never the one
    # being created right now.
    duplicate_artifact = evidence_vault_crud.find_duplicate_by_hash(db, sha256_hash)
    duplicate_of_verification_id = (
        int(duplicate_artifact.related_entity_id)
        if duplicate_artifact and duplicate_artifact.related_entity_type == "identity_verification"
        else None
    )

    record = crud.submit_identity_verification_for_user(
        db,
        user,
        document_type=document_type,
        document_number=document_number,
        custom_document_name=custom_document_name,
        stored_filename=stored_filename,
        original_filename=original_filename,
        content_type=content_type,
        file_size=file_size,
        duplicate_of_verification_id=duplicate_of_verification_id,
    )
    evidence_vault_crud.register_evidence_artifact(
        db, related_entity_type="identity_verification", related_entity_id=str(record.id),
        stored_filename=stored_filename, sha256_hash=sha256_hash, original_filename=original_filename,
        content_type=content_type, file_size=file_size, uploaded_by_user_id=user.id,
    )
    log_audit_event(
        db, None, "user_identity_verification.submit", "identity_verification", str(record.id),
        get_correlation_id(request), reason=f"user:{user.id}",
    )
    db.commit()
    return _to_user_read(record)


@router.get("", response_model=list[IdentityVerificationUserRead])
def list_identity_verifications(
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all identity verifications for current user."""
    records = crud.list_user_identity_verifications(db, user)
    return [_to_user_read(r) for r in records]


@router.get("/{verification_id}", response_model=IdentityVerificationUserRead)
def get_identity_verification(
    verification_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get details of a specific identity verification."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if record.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own identity verifications")

    return _to_user_read(record)


@router.get("/{verification_id}/document")
def download_own_identity_document(
    verification_id: int,
    user: UserAccount = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Streams the user's own uploaded document. A USER can never fetch another
    USER's document -- ownership is checked against party_id, exactly like the
    JSON detail endpoint above."""
    record = crud.get_identity_verification(db, verification_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity verification not found")
    if record.party_id != user.party_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only view your own identity verification documents")
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
