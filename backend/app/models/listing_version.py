from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-001 Section 1, Rule 2/3: a listing's approved public content is an
# immutable snapshot, never the live mutable Listing row. Every edit (material
# or not) creates a new version; only an APPROVED version can ever become the
# listing's current_public_version_id. Editing after approval creates version
# N+1 -- it never mutates the row backing the still-published version N.
VERSION_APPROVAL_STATUSES = ("DRAFT", "UNDER_REVIEW", "APPROVED", "REJECTED", "CHANGES_REQUESTED")


class ListingVersion(Base):
    __tablename__ = "listing_versions"
    __table_args__ = (UniqueConstraint("listing_id", "version_no", name="uq_listing_version_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[str] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # Canonical JSON dump of every content field at the moment this version was
    # created (see services/listing_versioning.py:build_snapshot) -- the public
    # read path serves from this dict for a PUBLISHED listing, never from the
    # live Listing row, so a later draft edit can never silently change what's
    # already public.
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Which content fields differ from the previous version, and whether that
    # makes this version a material change (see services/listing_versioning.py).
    material_change_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    is_material: Mapped[bool] = mapped_column(default=False)
    approval_status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing: Mapped["Listing"] = relationship(foreign_keys=[listing_id])
    approvals: Mapped[list["ListingApproval"]] = relationship(
        back_populates="listing_version", order_by="ListingApproval.decided_at"
    )
