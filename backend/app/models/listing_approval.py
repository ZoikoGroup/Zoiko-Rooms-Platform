from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-001 Section 5.2: a structured, append-only decision object --
# never a single overwritable status field. A Super Admin override appends a
# NEW row (is_override=True, supersedes_approval_id pointing at the prior
# decision) rather than editing history in place.
APPROVAL_DECISIONS = ("APPROVED", "REJECTED", "CHANGES_REQUESTED", "QUARANTINED", "SUSPENDED", "OVERRIDE")


class ListingApproval(Base):
    __tablename__ = "listing_approvals"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_version_id: Mapped[int] = mapped_column(
        ForeignKey("listing_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    market_profile: Mapped[str] = mapped_column(String(50), default="")
    decision: Mapped[str] = mapped_column(String(30), nullable=False)
    decision_reason_code: Mapped[str] = mapped_column(String(100), default="")
    reason_note: Mapped[str] = mapped_column(String(2000), default="")
    reviewer_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    reviewer_authority_scope: Mapped[str] = mapped_column(String(30), default="")
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(20), default="1.0")
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    supersedes_approval_id: Mapped[int | None] = mapped_column(ForeignKey("listing_approvals.id"), nullable=True)
    review_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing_version: Mapped["ListingVersion"] = relationship(back_populates="approvals")
    reviewer: Mapped["AdminUser"] = relationship()
    supersedes: Mapped["ListingApproval"] = relationship(remote_side=[id])
