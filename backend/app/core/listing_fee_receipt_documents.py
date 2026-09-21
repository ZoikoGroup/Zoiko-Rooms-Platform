"""ZR-PAY-002 Section 8.5/13.1: storage for rendered Listing Fee receipt
PDFs. Same shape as core/receipt_documents.py -- a random filename outside
any publicly served directory, never derived from client input or reused
across artifacts. Its own module/directory (not a shared call into
receipt_documents.py) matches this codebase's one-module-per-document-series
convention (rent_invoice_documents.py, payout_statement_documents.py,
service_fee_invoice_documents.py all follow the same pattern)."""

import hashlib
import uuid
from pathlib import Path

from app.core.config import settings


def save_listing_fee_receipt_document(pdf_bytes: bytes) -> tuple[str, str]:
    """Persists a rendered Listing Fee receipt PDF exactly once. Returns
    (storage_ref, content_hash). Callers must never call this twice for the
    same ListingFeePayment -- see models.listing_fee.ListingFeeReceipt's
    unique payment_id."""
    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    upload_dir = Path(settings.listing_fee_receipt_document_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_ref = f"{uuid.uuid4().hex}.pdf"
    (upload_dir / storage_ref).write_bytes(pdf_bytes)

    return storage_ref, content_hash


def resolve_listing_fee_receipt_document_path(storage_ref: str) -> Path:
    """`storage_ref` only ever originates from save_listing_fee_receipt_document
    above (a uuid4 hex we generated), never from client input, so this can't
    be used for path traversal."""
    return Path(settings.listing_fee_receipt_document_dir) / storage_ref
