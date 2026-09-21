from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

AGREEMENT_LEGAL_HOLD_STATUSES = ("ACTIVE", "RELEASED")


class AgreementLegalHold(Base):
    """ZR-ENG-CLR-004 Section 4 gap / spec API 'POST /retention/legal-hold |
    Apply scoped preservation hold with authority': agreements previously had
    no retention/legal-hold model at all -- nothing stopped a material change
    (crud/agreement_amendments.py:request_amendment) from being proposed
    against an agreement that's actually under litigation-hold preservation.

    Mirrors models/dispute_legal_hold.py's shape (a first-class, auditable
    hold record rather than a bare boolean that gates nothing) --
    authority_evidence_ref is required at creation (see
    crud/agreement_legal_hold.py:place_legal_hold), matching the spec's own
    'with authority' wording and the same pattern used for
    booking_change_requests/dispute_party's authority_evidence_ref."""

    __tablename__ = "agreement_legal_holds"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")
    reason: Mapped[str] = mapped_column(String(500), default="")
    authority_evidence_ref: Mapped[str] = mapped_column(String(255), default="")
    placed_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    released_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agreement: Mapped["Agreement"] = relationship()
    placed_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[placed_by_admin_id])
    released_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[released_by_admin_id])
