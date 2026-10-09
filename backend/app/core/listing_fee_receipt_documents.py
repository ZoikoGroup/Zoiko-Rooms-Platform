"""ZR-PAY-002 Section 8.5/13.1: storage for rendered Listing Fee receipt
PDFs. Same shape as core/receipt_documents.py -- a random filename outside
any publicly served directory, never derived from client input or reused
across artifacts. Its own module/directory (not a shared call into
receipt_documents.py) matches this codebase's one-module-per-document-series
convention (rent_invoice_documents.py, payout_statement_documents.py,
service_fee_invoice_documents.py all follow the same pattern).

Kept in the database (core/file_store.py, category "listing_fee_document")."""

import hashlib

from sqlalchemy.orm import Session

from app.core import file_store

CATEGORY = "listing_fee_document"


def save_listing_fee_receipt_document(db: Session, pdf_bytes: bytes) -> tuple[str, str]:
    """Stores a rendered PDF once, in the caller's transaction (so it is saved
    together with the row that records it). Returns (storage_ref, content_hash)."""
    storage_ref = file_store.put(db, CATEGORY, pdf_bytes, extension=".pdf", content_type="application/pdf")
    return storage_ref, hashlib.sha256(pdf_bytes).hexdigest()


def read_listing_fee_receipt_document(db: Session, storage_ref: str) -> bytes:
    """The stored PDF; 404 if it's missing."""
    return file_store.read_or_404(db, CATEGORY, storage_ref)
