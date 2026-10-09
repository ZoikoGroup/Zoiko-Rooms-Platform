"""ZR-ENG-CLR-005 Section 6.3/13.1: storage for rendered payout-statement
PDFs. Same shape as core/receipt_documents.py / core/agreement_documents.py --
a random filename outside any publicly served directory, never derived from
client input or reused across artifacts.

Kept in the database (core/file_store.py, category "payout_statement")."""

import hashlib

from sqlalchemy.orm import Session

from app.core import file_store

CATEGORY = "payout_statement"


def save_payout_statement_document(db: Session, pdf_bytes: bytes) -> tuple[str, str]:
    """Stores a rendered PDF once, in the caller's transaction (so it is saved
    together with the row that records it). Returns (storage_ref, content_hash)."""
    storage_ref = file_store.put(db, CATEGORY, pdf_bytes, extension=".pdf", content_type="application/pdf")
    return storage_ref, hashlib.sha256(pdf_bytes).hexdigest()


def read_payout_statement_document(db: Session, storage_ref: str) -> bytes:
    """The stored PDF; 404 if it's missing."""
    return file_store.read_or_404(db, CATEGORY, storage_ref)
