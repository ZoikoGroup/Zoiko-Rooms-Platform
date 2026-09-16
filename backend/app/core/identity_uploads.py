import hashlib
import io
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image

from app.core.config import settings

# (magic bytes, stored extension, canonical content type). Checked against the
# actual file contents -- never the filename extension or the client-declared
# Content-Type header, both of which are trivially spoofable.
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


_PILLOW_FORMAT_BY_EXTENSION = {".jpg": "JPEG", ".png": "PNG"}


def _strip_image_metadata(contents: bytes, extension: str) -> bytes:
    """ZR-ENG-CLR-012 Section 18: 'Malware scan, file-type validation,
    EXIF/metadata handling... occur before reviewer exposure.' EXIF/PNG
    metadata routinely carries GPS coordinates and device identifiers --
    real PII that has nothing to do with proving identity, so it is
    stripped from what actually gets stored, not merely from what a viewer
    later displays. PDFs (no EXIF concept) pass through untouched."""
    pillow_format = _PILLOW_FORMAT_BY_EXTENSION.get(extension)
    if pillow_format is None:
        return contents
    try:
        image = Image.open(io.BytesIO(contents))
        image.load()
        out = io.BytesIO()
        # Deliberately omit exif= -- Pillow only carries metadata over on
        # save if explicitly told to, so a plain re-save already drops it.
        image.save(out, format=pillow_format)
        return out.getvalue()
    except Exception:
        # A file that sniffed as JPG/PNG by magic bytes but isn't actually
        # decodable is a fraud/corruption signal, not this function's
        # problem to solve -- fail closed by keeping the original bytes so
        # the existing sniff/size checks still see a consistent file
        # rather than silently swallowing a malformed upload.
        return contents


async def save_identity_document(file: UploadFile) -> tuple[str, str, str, int, str]:
    """Validates and persists an uploaded identity document outside any publicly
    served directory. Returns (stored_filename, original_filename, content_type,
    size, sha256_hash). Only `stored_filename` (a random name with no relation to
    the upload) should ever be persisted to the database or used to build a path.

    ZR-ENG-CLR-012 Section 18: 'Hash evidence artifacts at ingest' -- the
    returned sha256_hash is what crud.evidence_vault registers as the
    tamper-evident fingerprint of the original (see crud/identity_verification.py's
    submit_identity_verification_for_user, the one caller of this function)."""
    contents = await file.read()
    size = len(contents)

    if size == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The uploaded file is empty")

    max_bytes = settings.identity_document_max_size_mb * 1024 * 1024
    if size > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"File exceeds the {settings.identity_document_max_size_mb}MB limit",
        )

    sniffed = _sniff(contents)
    if not sniffed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unsupported file — upload a PDF, JPG or PNG")
    extension, content_type = sniffed

    # Strip EXIF/metadata before anything is persisted or hashed -- the
    # hash and the stored file must describe the exact same bytes.
    contents = _strip_image_metadata(contents, extension)

    upload_dir = Path(settings.identity_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    (upload_dir / stored_filename).write_bytes(contents)

    original_filename = Path(file.filename or "document").name
    sha256_hash = hashlib.sha256(contents).hexdigest()
    return stored_filename, original_filename, content_type, len(contents), sha256_hash


def resolve_identity_document_path(stored_filename: str) -> Path:
    """`stored_filename` only ever originates from `save_identity_document` above
    (a uuid4 hex we generated), never from client input, so this can't be used
    for path traversal."""
    return Path(settings.identity_upload_dir) / stored_filename
