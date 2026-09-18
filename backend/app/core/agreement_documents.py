"""ZR-ENG-CLR-004 Section 13.1/AC-08: storage for executed agreement PDFs.
Same shape as core/identity_uploads.py -- a random filename outside any
publicly served directory, never derived from client input or reused across
artifacts."""

import hashlib
import uuid
from pathlib import Path

from app.core.config import settings


def save_agreement_document(pdf_bytes: bytes) -> tuple[str, str]:
    """Persists a rendered agreement PDF exactly once. Returns
    (storage_ref, content_hash). Callers must never call this twice for the
    same AgreementVersion -- see models.leasing.DocumentArtifact's unique
    agreement_version_id."""
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    upload_dir = Path(settings.agreement_document_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_ref = f"{uuid.uuid4().hex}.pdf"
    (upload_dir / storage_ref).write_bytes(pdf_bytes)

    return storage_ref, content_hash


def resolve_agreement_document_path(storage_ref: str) -> Path:
    """`storage_ref` only ever originates from save_agreement_document above
    (a uuid4 hex we generated), never from client input, so this can't be
    used for path traversal."""
    return Path(settings.agreement_document_dir) / storage_ref
