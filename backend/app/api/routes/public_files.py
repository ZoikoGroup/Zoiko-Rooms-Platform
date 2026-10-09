"""Public listing photos, served from the database at the same /uploads/<name>
URLs listings already store (Listing.images). Only the listing_image category
is reachable here -- every private document has its own authenticated route."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.stored_file import StoredFile

router = APIRouter(tags=["public-files"])

# A photo's name is random and never reused, so its bytes never change.
_CACHE = "public, max-age=31536000, immutable"


@router.api_route("/uploads/{filename}", methods=["GET", "HEAD"])
def get_listing_image(filename: str, request: Request, db: Session = Depends(get_db)):
    meta = db.execute(
        select(StoredFile.sha256, StoredFile.content_type)
        .where(StoredFile.category == "listing_image", StoredFile.storage_ref == filename)
    ).first()
    if meta is None:
        from app.core import file_store

        # Not copied into the database yet -- the legacy disk fallback.
        data = file_store.read(db, "listing_image", filename)
        if data is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        import mimetypes

        return Response(content=data, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                        headers={"Cache-Control": _CACHE})

    etag = f'"{meta.sha256}"'
    headers = {"Cache-Control": _CACHE, "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    if request.method == "HEAD":
        return Response(media_type=meta.content_type, headers=headers)
    content = db.scalar(
        select(StoredFile.content).where(StoredFile.category == "listing_image", StoredFile.storage_ref == filename)
    )
    return Response(content=bytes(content), media_type=meta.content_type, headers=headers)
