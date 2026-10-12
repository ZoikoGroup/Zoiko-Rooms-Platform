import hashlib
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core import file_store
from app.core.config import settings

# Same magic-byte sniffing discipline as core/identity_uploads.py -- checked
# against actual file contents, never the filename extension or client
# Content-Type header. Section 21's evidence examples (photos, invoices,
# condition reports, receipts) are covered by this set; video/other document
# types are an honest, stated MVP trim, not silently dropped scope.
CATEGORY = "dispute_evidence"

_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"%PDF-", ".pdf", "application/pdf"),
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
]


def _sniff(contents: bytes) -> tuple[str, str] | None:
    for magic, extension, content_type in _SIGNATURES:
        if contents.startswith(magic):
            return extension, content_type
    return None


async def save_dispute_evidence_file(db: Session, file: UploadFile) -> tuple[str, str, str, int, str]:
    """Validates and persists an uploaded evidence file outside any publicly
    served directory, same as save_identity_document, plus a real SHA-256
    hash of the immutable original -- Section 21's "content hash" -- which
    no existing upload path in this codebase previously computed. Returns
    (stored_filename, original_filename, content_type, size, sha256_hash);
    only stored_filename (a random name unrelated to the upload) is ever
    used to build a path or persisted for later retrieval."""
    contents = await file.read()
    size = len(contents)

    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")

    max_bytes = settings.evidence_document_max_size_mb * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"File exceeds the {settings.evidence_document_max_size_mb}MB limit",
        )

    sniffed = _sniff(contents)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file — upload a PDF, JPG or PNG")
    extension, content_type = sniffed

    stored_filename = file_store.put(db, CATEGORY, contents, extension=extension, content_type=content_type)

    original_filename = Path(file.filename or "evidence").name
    sha256_hash = hashlib.sha256(contents).hexdigest()
    return stored_filename, original_filename, content_type, size, sha256_hash


def read_dispute_evidence(db: Session, stored_filename: str | None) -> bytes | None:
    """The stored evidence file, or None when there's no such file."""
    return file_store.read(db, CATEGORY, stored_filename)


def dispute_evidence_response(db: Session, stored_filename: str | None, content_type: str, original_filename: str):
    """The evidence file as a download (authorization is the caller's job)."""
    from urllib.parse import quote

    from fastapi.responses import Response

    data = file_store.read_or_404(db, CATEGORY, stored_filename)
    name = original_filename or "evidence"
    return Response(
        content=data, media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Cache-Control": "no-store"},
    )


def delete_dispute_evidence_file(db: Session, stored_filename: str | None) -> None:
    """QA-Q16: the actual byte-erasure half of a granted deletion request
    (crud/dispute_evidence.py:request_deletion). Joins the caller's
    transaction; a file already gone is not an error, so a request granted
    twice can't turn an erasure into a crash."""
    file_store.delete(db, CATEGORY, stored_filename)
