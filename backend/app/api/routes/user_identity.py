from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.identity_uploads import read_identity_document
from app.crud import identity_verification as crud
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
        # Masked only -- the full number is never returned (ZR-IDENTITY-001 Section 5.4).
        "document_number": record.masked_document_number,
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


# Uploading a document here is no longer possible: identity is verified only
# through the identity provider's own capture (/api/users/identity, Veriff).
# These routes are read-only history.


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

    data = read_identity_document(db, record.document_file_path)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document could not be found")

    return Response(
        content=data,
        media_type=record.document_file_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{record.document_file_original_name or "document"}"'},
    )
