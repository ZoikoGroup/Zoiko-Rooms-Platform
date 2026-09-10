"""ZR-ENG-CLR-004 Section 6.8/13.1/AC-15/AC-16: required disclosures/
attachments for an agreement -- 'Execution blocked when a legally
pre-signature disclosure has not been delivered.'

LEGAL CONTENT WARNING: same status as models/agreement_clause.py's default
clause rows -- the disclosure types seeded by
services/agreement_profile.py:DEFAULT_DISCLOSURES are generic placeholder
categories (deposit protection information, house rules/safety), not real
jurisdiction-mandated disclosure content. A real market launch replaces them
with counsel-approved requirements before end users rely on this gate.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

DISCLOSURE_STATUSES = ("REQUIRED_MISSING", "READY", "DELIVERED", "ACKNOWLEDGED")


class DisclosureRequirement(Base):
    __tablename__ = "disclosure_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    disclosure_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="")
    # Section 3.2 required_disclosures: not every disclosure a market pack
    # lists is necessarily pre-signature-mandatory for every agreement class
    # -- only required=True rows gate execution (see
    # crud/leasing.py:_apply_signature).
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="REQUIRED_MISSING")
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AC-16 'records delivery to each party': these statutory disclosures are
    # delivered TO the renter/tenant (the party the law protects) by the
    # provider/platform, not exchanged both ways -- so "each party" here
    # tracks *which* party received it, defaulting to renter, rather than a
    # full N-party delivery matrix (Agreement has no broader party roster to
    # matrix against yet -- see crud/leasing.py:_apply_signature's own
    # required_signers docstring on that same limitation).
    delivered_to_party: Mapped[str] = mapped_column(String(20), default="renter")
    # AC-28 'accessible version... alternate execution/delivery paths': how
    # this disclosure was actually delivered -- IN_APP (the PDF document at
    # /document), EMAIL, POST (physical mail, for a party who withdrew
    # electronic-record consent or can't access electronic records), or
    # ACCESSIBLE_TEXT (the screen-reader-friendly plain-text rendering at
    # .../accessible-text, requested instead of/alongside the PDF).
    delivery_channel: Mapped[str] = mapped_column(String(20), default="IN_APP")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AC-16 'agreement package can include required attachments': a real
    # rendered document for this disclosure, hash-verified the same way as
    # DocumentArtifact -- generated once, at first delivery (see
    # crud/leasing.py:deliver_disclosure), never regenerated after.
    document_storage_ref: Mapped[str] = mapped_column(String(255), default="")
    document_content_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement: Mapped["Agreement"] = relationship(back_populates="disclosures")
