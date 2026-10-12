"""One-time copy of every file still on the server's disk into the
stored_files table (models/stored_file.py). Used by alembic revision
0038_stored_files and by copy_files_to_database.py for a re-run.

Idempotent: a file already in the table is skipped, so it can run any number
of times. Each copied row's hash is checked against the file on disk. Files
are left on disk -- delete the old directories by hand once a run reports
nothing missing."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from app.core.config import settings

logger = logging.getLogger(__name__)

# Kept here rather than imported from the model so the migration keeps working
# however the model changes later.
CATEGORY_DIR_SETTINGS = {
    "listing_image": "upload_dir",
    "identity_document": "identity_upload_dir",
    "property_verification": "property_verification_upload_dir",
    "property_location": "property_location_upload_dir",
    "authority_evidence": "authority_upload_dir",
    "dispute_evidence": "evidence_upload_dir",
    "agreement": "agreement_document_dir",
    "receipt": "receipt_document_dir",
    "listing_fee_document": "listing_fee_receipt_document_dir",
    "payout_statement": "payout_statement_document_dir",
    "service_fee_invoice": "service_fee_invoice_document_dir",
    "rent_invoice": "rent_invoice_document_dir",
}

stored_files = sa.table(
    "stored_files",
    sa.column("category", sa.String), sa.column("storage_ref", sa.String), sa.column("content", sa.LargeBinary),
    sa.column("content_type", sa.String), sa.column("size_bytes", sa.BigInteger), sa.column("sha256", sa.String),
    sa.column("encrypted", sa.Boolean), sa.column("created_at", sa.DateTime(timezone=True)),
)


def _content_type(name: str) -> str:
    plain = name[:-4] if name.endswith(".enc") else name
    return mimetypes.guess_type(plain)[0] or "application/octet-stream"


def copy_disk_files_to_database(connection: sa.engine.Connection) -> dict[str, dict[str, int]]:
    """Returns {category: {"copied", "skipped", "mismatched"}}."""
    report: dict[str, dict[str, int]] = {}
    for category, setting in CATEGORY_DIR_SETTINGS.items():
        counts = {"copied": 0, "skipped": 0, "mismatched": 0}
        report[category] = counts
        directory = Path(getattr(settings, setting))
        if not directory.is_dir():
            continue
        existing = set(connection.execute(
            sa.select(stored_files.c.storage_ref).where(stored_files.c.category == category)
        ).scalars())
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.name in existing:
                counts["skipped"] += 1
                continue
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            connection.execute(stored_files.insert().values(
                category=category, storage_ref=path.name, content=data, content_type=_content_type(path.name),
                size_bytes=len(data), sha256=digest, encrypted=path.name.endswith(".enc"),
                created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
            ))
            stored = connection.execute(
                sa.select(stored_files.c.content).where(
                    stored_files.c.category == category, stored_files.c.storage_ref == path.name,
                )
            ).scalar_one()
            if hashlib.sha256(bytes(stored)).hexdigest() != digest:
                counts["mismatched"] += 1
                logger.error("file copy: %s/%s does not match the disk copy after insert", category, path.name)
            else:
                counts["copied"] += 1
        logger.info("file copy: %s -> %s", category, counts)
    return report
