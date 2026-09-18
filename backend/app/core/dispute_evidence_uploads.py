import hashlib
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

# Same magic-byte sniffing discipline as core/identity_uploads.py -- checked
# against actual file contents, never the filename extension or client
# Content-Type header. Section 21's evidence examples (photos, invoices,
# condition reports, receipts) are covered by this set; video/other document
# types are an honest, stated MVP trim, not silently dropped scope.
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


async def save_dispute_evidence_file(file: UploadFile) -> tuple[str, str, str, int, str]:
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

    upload_dir = Path(settings.evidence_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    (upload_dir / stored_filename).write_bytes(contents)

    original_filename = Path(file.filename or "evidence").name
    sha256_hash = hashlib.sha256(contents).hexdigest()
    return stored_filename, original_filename, content_type, size, sha256_hash


def resolve_dispute_evidence_path(stored_filename: str) -> Path:
    """`stored_filename` only ever originates from save_dispute_evidence_file
    above (a uuid4 hex we generated), never from client input, so this can't
    be used for path traversal."""
    return Path(settings.evidence_upload_dir) / stored_filename


def delete_dispute_evidence_file(stored_filename: str) -> None:
    """QA-Q16: the actual byte-erasure half of a granted deletion request
    (crud/dispute_evidence.py:request_deletion) -- best-effort (missing_ok),
    since a request granted twice, or a row whose file already vanished for
    any other reason, must not turn an erasure into a crash."""
    resolve_dispute_evidence_path(stored_filename).unlink(missing_ok=True)
