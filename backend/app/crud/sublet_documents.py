"""ZR-SUB-003 Section 10/11: sublet_document -- reuses the existing generic
Evidence Vault (EvidenceArtifact, crud/evidence_vault.py) rather than a new
table, the same provenance/hash/scan_status index already used by identity
verification. scan_status stays NOT_SCANNED -- no live malware-scanning
provider exists anywhere in this codebase (see models/evidence_artifact.py's
own docstring); never claiming a file is CLEAN that was never actually
scanned."""

from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dispute_evidence_uploads import delete_dispute_evidence_file, resolve_dispute_evidence_path, save_dispute_evidence_file
from app.core.signed_urls import generate_signed_download_token
from app.crud import evidence_vault as evidence_vault_crud
from app.models.evidence_artifact import EvidenceArtifact
from app.models.sublet_request import SubletRequest
from app.schemas.sublet_document import SubletDocumentRead

RELATED_ENTITY_TYPE = "sublet_request"


async def upload_sublet_document(
    db: Session, sublet_request: SubletRequest, file: UploadFile, *,
    uploaded_by_admin_id: int | None = None, uploaded_by_user_id: int | None = None,
) -> EvidenceArtifact:
    stored_filename, original_filename, content_type, file_size, sha256_hash = await save_dispute_evidence_file(file)
    return evidence_vault_crud.register_evidence_artifact(
        db, related_entity_type=RELATED_ENTITY_TYPE, related_entity_id=str(sublet_request.id),
        stored_filename=stored_filename, sha256_hash=sha256_hash, original_filename=original_filename,
        content_type=content_type, file_size=file_size,
        uploaded_by_admin_id=uploaded_by_admin_id, uploaded_by_user_id=uploaded_by_user_id,
    )


def list_sublet_documents(db: Session, sublet_request: SubletRequest) -> list[EvidenceArtifact]:
    return [
        d for d in evidence_vault_crud.list_evidence_artifacts_for_entity(db, RELATED_ENTITY_TYPE, str(sublet_request.id))
        if d.deleted_at is None
    ]


def get_sublet_document_or_404(db: Session, sublet_request: SubletRequest, document_id: int) -> EvidenceArtifact:
    document = db.get(EvidenceArtifact, document_id)
    if (
        not document
        or document.related_entity_type != RELATED_ENTITY_TYPE
        or document.related_entity_id != str(sublet_request.id)
        or document.deleted_at is not None
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found on this sublet request")
    return document


def to_sublet_document_read(document: EvidenceArtifact, *, download_path: str) -> SubletDocumentRead:
    """download_path is the caller's own role-scoped route (renter/host/
    admin each have a distinct base path) with a freshly-minted signed
    token appended -- ZR-SUB-003 Section 10: 'signed, time-limited URLs...
    never expose storage bucket paths.'"""
    token = generate_signed_download_token("sublet_document", str(document.id))
    return SubletDocumentRead(
        id=document.id,
        original_filename=document.original_filename,
        content_type=document.content_type,
        file_size=document.file_size,
        sha256_hash=document.sha256_hash,
        scan_status=document.scan_status,
        uploaded_by_admin_id=document.uploaded_by_admin_id,
        uploaded_by_user_id=document.uploaded_by_user_id,
        created_at=document.created_at,
        download_url=f"{download_path}?token={token}",
    )


def sublet_document_file_response(document: EvidenceArtifact) -> FileResponse:
    return FileResponse(
        resolve_dispute_evidence_path(document.stored_filename), media_type=document.content_type, filename=document.original_filename,
    )


def delete_sublet_document(db: Session, document: EvidenceArtifact) -> EvidenceArtifact:
    """Removes the underlying file but keeps the row -- deleted_at IS the
    audit record of the deletion, same discipline as every other evidence
    deletion in this codebase (see models/evidence_artifact.py)."""
    from datetime import datetime, timezone

    delete_dispute_evidence_file(document.stored_filename)
    document.deleted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(document)
    return document
