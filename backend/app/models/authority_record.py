from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

AUTHORITY_STATUSES = (
    "not_started", "pending", "verified", "expiring", "expired", "failed", "conflict", "review_required", "revoked",
)

# Lister, Property & Authority Verification wireframe: the structured
# landlord/agent/manager relationship a party claims to this specific room --
# additive to (not a replacement for) the existing free-text authority_type
# (which describes the evidence basis, e.g. "lease_agreement"/"ownership_deed",
# not who the submitter is relative to the property). Nullable so every
# existing AuthorityRecord row/test predating this field is unaffected.
AUTHORITY_RELATIONSHIP_TYPES = ("OWNER", "AGENT", "MANAGER")


class AuthorityRecord(Base):
    __tablename__ = "authority_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id", ondelete="CASCADE"), nullable=False)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    authority_type: Mapped[str] = mapped_column(String(50), nullable=False)
    relationship_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    evidence_ref: Mapped[str] = mapped_column(String(1024), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verifier_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    party: Mapped["Party"] = relationship()
    room: Mapped["Room"] = relationship(back_populates="authority_records")
    verifier: Mapped["AdminUser"] = relationship()
