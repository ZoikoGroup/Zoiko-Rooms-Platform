from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ZR-ENG-CLR-012 Section 18/AC-24: "Hash evidence artifacts at ingest...
# Raw identity/biometric evidence is stored in a restricted Evidence Vault,
# not general profile/document storage." This is that vault's provenance
# index -- one row per uploaded original, pointing at the actual file
# already saved by core/identity_uploads.py (unchanged: random filename,
# outside any public directory). NOT_SCANNED is the honest default: this
# MVP has no live malware-scanning provider wired in, so it never claims a
# file is "CLEAN" it never actually scanned (the same honesty as every
# other "no live provider yet" corner of this codebase).
EVIDENCE_SCAN_STATUSES = ("NOT_SCANNED", "CLEAN", "FLAGGED")


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Generic (type, id) pointer -- same pattern as models/notification.py's
    # related_entity_type/id -- so this vault can index evidence for any
    # verification domain (identity, occupancy eligibility, screening,
    # property compliance) without a separate FK column per domain.
    related_entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    related_entity_id: Mapped[str] = mapped_column(String(50), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scan_status: Mapped[str] = mapped_column(String(20), default="NOT_SCANNED")
    uploaded_by_admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # ZR-ENG-CLR-012 Section 28/AC-29: "Scheduled deletion is auditable."
    # Set once services/evidence_retention.py's sweep actually deletes the
    # underlying file -- the row itself is kept (deleted_at IS the audit
    # record of the deletion event), only the stored bytes are removed.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
