from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DISPUTE_LEGAL_HOLD_STATUSES = ("ACTIVE", "RELEASED")


class DisputeLegalHold(Base):
    """ZR-ENG-CLR-010 Section 21: legal hold as a first-class, auditable
    object rather than DisputeEvidenceItem.legal_hold's previous bare
    boolean -- that flag existed but gated nothing and carried no reason,
    placer, or timestamp, and toggling it off then on again silently lost
    any prior hold's history (same "declared but dead" shape as this
    codebase's own PROPOSED hold status/SYSTEM_RECORD provenance before
    they were given a real consumer). DisputeEvidenceItem.legal_hold is
    kept as a derived convenience flag -- "is there currently an ACTIVE row
    for this evidence item" -- so existing readers of that boolean are
    unaffected; this table is the actual record of who placed a hold, why,
    and when it was released. crud/dispute_evidence.py:archive_evidence now
    refuses (409) while an ACTIVE hold exists -- the first real enforcement
    this flag has ever had."""

    __tablename__ = "dispute_legal_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("dispute_evidence_items.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(500), default="")
    placed_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    evidence: Mapped["DisputeEvidenceItem"] = relationship()
    placed_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[placed_by_admin_id])
    released_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[released_by_admin_id])
