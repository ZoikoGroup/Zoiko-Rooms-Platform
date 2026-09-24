from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Lister, Property & Authority Verification wireframe: a claim distinct from
# both IdentityVerification (who the lister is) and PropertyComplianceCredential
# (per-jurisdiction regulatory documents, e.g. gas safety/EPC/HMO license) --
# this is "is the property/address itself real and evidenced." Mirrors
# AuthorityRecord's own status vocabulary/shape (see models/authority_record.py)
# rather than inventing a new one.
PROPERTY_VERIFICATION_STATUSES = (
    "pending", "verified", "rejected", "additional_evidence_required", "revoked",
)


class PropertyVerification(Base):
    __tablename__ = "property_verifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")

    # The actual uploaded evidence document (core/property_verification_uploads.py)
    # -- evidence_ref above was, until now, the only thing ever recorded: a
    # free-text description with no real file behind it. Same
    # never-publicly-mounted-directory convention as
    # IdentityVerification.document_file_path; never expose this path
    # directly, only through an authenticated download route.
    document_file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    document_file_original_name: Mapped[str] = mapped_column(String(255), default="")
    document_file_content_type: Mapped[str] = mapped_column(String(100), default="")
    document_file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="pending")
    verifier_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    verifier_notes: Mapped[str] = mapped_column(String(1000), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    room: Mapped["Room"] = relationship()
    verifier_admin: Mapped["AdminUser"] = relationship()

    @property
    def has_document(self) -> bool:
        return bool(self.document_file_path)
