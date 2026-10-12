"""Upload handling for property/lister evidence (models/property_verification.py).
Reuses identity_uploads.py's own file-type sniffing and EXIF-stripping logic
(same real validation, same security posture) rather than duplicating it --
only the storage directory and size limit differ.

ZR-PROPERTY-VERIFY-001 P0 #11: files are encrypted at rest with the field
encryption key (stored as "<uuid><ext>.enc"); read_property_verification_document
decrypts in memory. Files saved before encryption (no ".enc") are read as-is."""

import hashlib
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core import file_store
from app.core.config import settings
from app.core.field_encryption import decrypt_bytes, encrypt_bytes
from app.core.identity_uploads import _sniff, _strip_image_metadata

ENCRYPTED_SUFFIX = ".enc"
CATEGORY = "property_verification"


async def save_property_verification_document(db: Session, file: UploadFile) -> tuple[str, str, str, int, str]:
    """Validates and persists an uploaded property verification document outside
    any publicly served directory. Returns (stored_filename, original_filename,
    content_type, size, sha256_hash) -- same shape as save_identity_document."""
    contents = await file.read()
    size = len(contents)

    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")

    max_bytes = settings.property_verification_document_max_size_mb * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"File exceeds the {settings.property_verification_document_max_size_mb}MB limit",
        )

    sniffed = _sniff(contents)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file — upload a PDF, JPG or PNG")
    extension, content_type = sniffed

    contents = _strip_image_metadata(contents, extension)

    stored_filename = file_store.put(
        db, CATEGORY, encrypt_bytes(contents), extension=f"{extension}{ENCRYPTED_SUFFIX}",
        content_type=content_type, encrypted=True,
    )

    original_filename = Path(file.filename or "document").name
    sha256_hash = hashlib.sha256(contents).hexdigest()
    return stored_filename, original_filename, content_type, len(contents), sha256_hash


def read_property_verification_document(db: Session, stored_filename: str | None) -> bytes | None:
    """The original bytes (decrypted), or None when the file is missing."""
    data = file_store.read(db, CATEGORY, stored_filename)
    if data is None:
        return None
    return decrypt_bytes(data) if stored_filename.endswith(ENCRYPTED_SUFFIX) else data


def delete_property_verification_document(db: Session, stored_filename: str | None) -> None:
    file_store.delete(db, CATEGORY, stored_filename)


def document_response(db: Session, stored_filename: str, content_type: str, original_name: str):
    """Streams a decrypted evidence file (authorization is the caller's job)."""
    from urllib.parse import quote

    from fastapi.responses import Response

    data = read_property_verification_document(db, stored_filename)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document could not be found")
    name = original_name or "document"
    return Response(
        content=data, media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
                 "Cache-Control": "no-store"},
    )
