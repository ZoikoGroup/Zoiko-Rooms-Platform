from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 21: the full provenance taxonomy, modeled in full
# even though this phase's upload paths only ever write USER_UPLOAD (a
# renter/host upload) or ADMIN_NOTE (an admin's own upload or text-only
# note) -- no PSP/deposit-scheme/external-authority/third-party integration
# exists in this codebase yet to produce the rest. Same "model the full
# taxonomy, implement an honest subset" discipline as DISPUTE_CLAIM_FAMILIES
# (models/dispute.py).
DISPUTE_EVIDENCE_PROVENANCE = (
    "USER_UPLOAD", "SYSTEM_RECORD", "PSP_RECORD", "SCHEME_RECORD", "EXTERNAL_AUTHORITY", "ADMIN_NOTE", "THIRD_PARTY",
)

# Section 21 "Disclosure": full set modeled; only PARTY_VISIBLE (renter/host
# uploads) and INTERNAL_ONLY (admin notes, or an admin explicitly
# restricting a party upload) are ever set by this phase's write paths.
# PRIVILEGED_RESTRICTED/EXTERNAL_EXPORT_ELIGIBLE exist as valid values an
# admin's disclosure-class update can reach later, not inferred here.
DISPUTE_EVIDENCE_DISCLOSURE_CLASSES = (
    "PARTY_VISIBLE", "INTERNAL_ONLY", "PRIVILEGED_RESTRICTED", "EXTERNAL_EXPORT_ELIGIBLE",
)

# Section 22: an independent state machine from disclosure_class/legal_hold
# above -- "has an admin reviewed this for authenticity/relevance" is a
# separate question from "who may see it" or "is deletion suspended for
# it." See app/services/dispute_state_machine.py's own comment on why this
# doesn't literally reproduce Section 22's RECEIVED->...->DISCLOSED/
# RESTRICTED->ARCHIVED chain (DISCLOSED/RESTRICTED already duplicates
# disclosure_class, which models that dimension more precisely).
DISPUTE_EVIDENCE_VERIFICATION_STATUSES = ("RECEIVED", "VERIFIED", "UNVERIFIED", "ARCHIVED")


class DisputeEvidenceItem(Base):
    """ZR-ENG-CLR-010 Section 21/23: "Store original bytes/object reference,
    content hash, MIME/type, uploader/source, received_at, captured_at" --
    one row per uploaded file (private-disk-stored, same pattern as
    core/identity_uploads.py, now with a real SHA-256 hash computed at
    upload time -- see core/dispute_evidence_uploads.py) or per admin
    text-only note (stored_filename left null).

    Redaction (Section 21: "Preserve original; create separate redacted
    derivative. Never overwrite the evidentiary original.") is modeled as a
    NEW row with redacted_of_evidence_id pointing back at the original --
    the original row is never mutated by a redaction.

    Exactly one of uploaded_by_guest_id/uploaded_by_party_id/
    uploaded_by_admin_id is set, mirroring DisputeResolutionCase's own
    opened_by_guest_id/opened_by_party_id split."""

    __tablename__ = "dispute_evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    uploaded_by_guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_party_id: Mapped[int | None] = mapped_column(ForeignKey("parties.id", ondelete="SET NULL"), nullable=True)
    uploaded_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    # Null for a text-only ADMIN_NOTE; set for every real file upload.
    stored_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(50), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256_hash: Mapped[str] = mapped_column(String(64), default="")
    note_text: Mapped[str] = mapped_column(String(2000), default="")
    disclosure_class: Mapped[str] = mapped_column(String(30), default="PARTY_VISIBLE")
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_status: Mapped[str] = mapped_column(String(10), default="RECEIVED")
    redacted_of_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_evidence_items.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # Section 21/23 `evidence_item.captured_at`: when the underlying fact
    # actually happened (a photo taken, a message sent) as distinct from
    # `created_at` (when it was uploaded to this platform, i.e. "received
    # at"). This build has no EXIF/metadata extraction, so it's only ever
    # the uploader's own optional, self-reported value -- null (the
    # default) means "not reported", not "captured now".
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # QA-Q16: a data-subject erasure request against this item -- see
    # crud/dispute_evidence.py:request_deletion. deletion_requested_at is
    # set the moment a request is made, whatever the outcome; deleted_at is
    # set only once actually granted (legal_hold is the one thing that can
    # refuse it, recorded in deletion_refused_reason). The row itself is
    # never removed -- only its file bytes and other personal content --
    # so claim links, redaction lineage and the case chronology stay
    # intact, same "erase the content, not the audit trail" discipline
    # every other domain in this codebase already follows.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_refused_reason: Mapped[str] = mapped_column(String(500), default="")

    case: Mapped["DisputeResolutionCase"] = relationship()
    uploaded_by_guest: Mapped["Guest"] = relationship()
    uploaded_by_party: Mapped["Party"] = relationship()
    uploaded_by_admin: Mapped["AdminUser"] = relationship()
    claim_links: Mapped[list["DisputeEvidenceClaimLink"]] = relationship(back_populates="evidence", cascade="all, delete-orphan")


class DisputeEvidenceClaimLink(Base):
    """Section 23 evidence_item.claim_ids -- one evidence item can support
    more than one claim on the same case (a photo of the whole room can be
    evidence for both a DAMAGE claim and a HABITABILITY claim), so this is a
    real many-to-many join rather than a single nullable claim_id column."""

    __tablename__ = "dispute_evidence_claim_links"
    __table_args__ = (UniqueConstraint("evidence_id", "claim_id", name="uq_dispute_evidence_claim_links_evidence_claim"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("dispute_evidence_items.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False, index=True)

    evidence: Mapped["DisputeEvidenceItem"] = relationship(back_populates="claim_links")
    claim: Mapped["DisputeResolutionClaim"] = relationship()
