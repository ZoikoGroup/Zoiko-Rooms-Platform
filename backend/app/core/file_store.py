"""The one place files are written to and read from -- the stored_files table
(models/stored_file.py). Writes join the caller's transaction (added and
flushed, never committed here), so a document and the row that owns it are
saved together or not at all.

Reads fall back to the old on-disk location for a file the one-time copy
(alembic 0038 / copy_files_to_database.py) hasn't brought over yet, so
nothing becomes unreadable while a deploy is in progress."""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.stored_file import STORED_FILE_CATEGORIES, StoredFile

logger = logging.getLogger(__name__)


def new_ref(extension: str) -> str:
    """A random storage name, never derived from client input."""
    return f"{uuid.uuid4().hex}{extension}"


def _legacy_path(category: str, ref: str) -> Path | None:
    directory = getattr(settings, STORED_FILE_CATEGORIES[category])
    # A ref is always one of our own generated names; refuse anything that
    # could step outside its directory.
    if not ref or "/" in ref or "\\" in ref or ref.startswith("."):
        return None
    return Path(directory) / ref


def put(
    db: Session, category: str, data: bytes, *, ref: str | None = None, extension: str = "",
    content_type: str = "application/octet-stream", encrypted: bool = False,
) -> str:
    """Stores `data` and returns its storage_ref (a fresh random one unless
    `ref` is given). Joins the caller's transaction."""
    if category not in STORED_FILE_CATEGORIES:
        raise ValueError(f"Unknown file category {category!r}")
    storage_ref = ref or new_ref(extension)
    db.add(StoredFile(
        category=category, storage_ref=storage_ref, content=data, content_type=content_type,
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), encrypted=encrypted,
    ))
    db.flush()
    return storage_ref


def read(db: Session, category: str, ref: str | None) -> bytes | None:
    """The stored bytes (still encrypted, if they were stored encrypted), or
    None when there's no such file."""
    if not ref:
        return None
    data = db.scalar(
        select(StoredFile.content).where(StoredFile.category == category, StoredFile.storage_ref == ref)
    )
    if data is not None:
        return bytes(data)
    path = _legacy_path(category, ref)
    if path is not None and path.is_file():
        logger.warning("file_store: %s/%s read from disk -- not yet copied into the database", category, ref)
        return path.read_bytes()
    return None


def read_or_404(db: Session, category: str, ref: str | None) -> bytes:
    data = read(db, category, ref)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored document could not be found")
    return data


def exists(db: Session, category: str, ref: str | None) -> bool:
    if not ref:
        return False
    if db.scalar(
        select(StoredFile.id).where(StoredFile.category == category, StoredFile.storage_ref == ref)
    ) is not None:
        return True
    path = _legacy_path(category, ref)
    return path is not None and path.is_file()


def delete(db: Session, category: str, ref: str | None) -> None:
    """Erases a file -- the row (joins the caller's transaction) and any copy
    still on the old disk location. Missing files are not an error."""
    if not ref:
        return
    db.execute(sql_delete(StoredFile).where(StoredFile.category == category, StoredFile.storage_ref == ref))
    path = _legacy_path(category, ref)
    if path is not None:
        path.unlink(missing_ok=True)
