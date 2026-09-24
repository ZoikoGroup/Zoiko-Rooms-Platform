"""Upload handling for property/lister evidence (models/property_verification.py).
Reuses identity_uploads.py's own file-type sniffing and EXIF-stripping logic
(same real validation, same security posture) rather than duplicating it --
only the storage directory and size limit differ."""

import hashlib
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings
from app.core.identity_uploads import _sniff, _strip_image_metadata


async def save_property_verification_document(file: UploadFile) -> tuple[str, str, str, int, str]:
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

    upload_dir = Path(settings.property_verification_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    (upload_dir / stored_filename).write_bytes(contents)

    original_filename = Path(file.filename or "document").name
    sha256_hash = hashlib.sha256(contents).hexdigest()
    return stored_filename, original_filename, content_type, len(contents), sha256_hash


def resolve_property_verification_document_path(stored_filename: str) -> Path:
    """`stored_filename` only ever originates from save_property_verification_document
    above (a uuid4 hex we generated), never from client input, so this can't be
    used for path traversal."""
    return Path(settings.property_verification_upload_dir) / stored_filename
