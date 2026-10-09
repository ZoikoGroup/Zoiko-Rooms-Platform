from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core import file_store
from app.core.config import settings
from app.models.listing import MAX_LISTING_IMAGES

# Checked against the actual file bytes -- never the filename extension or the
# client-declared Content-Type header, both of which are trivially spoofable.
# Mirrors the approach in core/identity_uploads.py.
_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"
_GIF87A = b"GIF87a"
_GIF89A = b"GIF89a"


_CONTENT_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}


def _sniff_image_extension(contents: bytes) -> str | None:
    if contents.startswith(_JPEG):
        return ".jpg"
    if contents.startswith(_PNG):
        return ".png"
    if contents.startswith(_GIF87A) or contents.startswith(_GIF89A):
        return ".gif"
    if contents[:4] == b"RIFF" and contents[8:12] == b"WEBP":
        return ".webp"
    return None


async def save_listing_image(db: Session, file: UploadFile) -> str:
    """Validates and stores one listing/property photo (in the database, category
    listing_image), returning its public /uploads URL. Shared by the admin (`/api/uploads/images`)
    and USER hosting (`/api/users/hosting/uploads/images`) endpoints -- never for
    identity documents, which use their own private category
    (see identity_uploads.py); the two must never share a category or endpoint."""
    contents = await file.read()
    if not contents:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{file.filename}' is empty")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{file.filename}' is larger than {settings.max_upload_size_mb}MB",
        )

    extension = _sniff_image_extension(contents)
    if not extension:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{file.filename}' isn't a supported image type (jpg, png, webp, gif only)",
        )

    # Random, server-generated name -- the client's original filename is
    # never used or persisted anywhere.
    filename = file_store.put(db, "listing_image", contents, extension=extension, content_type=_CONTENT_TYPES[extension])

    # Deliberately relative, not settings.public_api_url + "/uploads/..." --
    # that setting is an independently-configured backend env var that can (and,
    # in this project's dev environment, did) drift from the frontend's own
    # NEXT_PUBLIC_API_URL, baking a wrong/stale origin permanently into stored
    # listing data. The frontend resolves this path against whichever origin
    # it's actually configured to talk to (see resolveImageUrl in lib/utils.ts).
    return f"/uploads/{filename}"


async def save_listing_images(db: Session, files: list[UploadFile]) -> list[str]:
    if len(files) > MAX_LISTING_IMAGES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"You can upload at most {MAX_LISTING_IMAGES} images at a time",
        )
    urls = [await save_listing_image(db, file) for file in files]
    db.commit()
    return urls
