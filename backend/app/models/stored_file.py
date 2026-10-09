"""Every file the platform keeps -- generated PDFs (receipts, invoices,
statements, agreements, credit notes), uploaded evidence (identity, property,
authority, dispute) and listing photos -- stored in the database rather than
on the server's disk, so it is backed up, replicated and transactional with
the row that owns it.

A file is addressed by (category, storage_ref). storage_ref is the same
random name the owning row already records (PaymentReceipt.storage_ref,
DisputeEvidence.stored_filename, Listing.images "/uploads/<ref>", ...), so no
owning table changes shape. `content` holds the bytes exactly as they are
kept: files encrypted at rest (property, location and authority evidence)
stay encrypted here, flagged by `encrypted`."""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column

from app.db.base import Base

# category -> the settings attribute naming the directory these files used to
# live in (read by the one-time copy and the legacy fallback in core/file_store).
STORED_FILE_CATEGORIES = {
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


class StoredFile(Base):
    __tablename__ = "stored_files"
    __table_args__ = (UniqueConstraint("category", "storage_ref", name="uq_stored_files_category_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40))
    storage_ref: Mapped[str] = mapped_column(String(255))
    # Deferred: listing or checking files never pulls their bytes.
    content: Mapped[bytes] = deferred(mapped_column(LargeBinary))
    content_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Of `content` as stored (so of the ciphertext when encrypted).
    sha256: Mapped[str] = mapped_column(String(64))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
