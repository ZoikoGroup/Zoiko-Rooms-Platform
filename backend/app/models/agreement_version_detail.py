"""ZR-ENG-CLR-004 Section 13.1: three entities that are each 1:1 with one
AgreementVersion, split out of the version's own generic `snapshot` JSON
blob into real queryable tables -- the JSON blob stays (nothing reads it
less), but 'premises' and 'commercial terms' now also exist as first-class
rows the way the spec's own data model names them, and an executed version
gets a distinct evidence-summary object separate from its rendered PDF.
"""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AgreementPremises(Base):
    """Section 13.1 'agreement_premises: Canonical property/room references;
    sourced from Listing Service.' Populated once, at version-creation time
    (crud/leasing.py:_build_agreement_snapshot's call sites), from the same
    listing/room facts already frozen into the snapshot -- this table exists
    so those facts are queryable/joinable without parsing JSON, not because
    the JSON copy is wrong."""

    __tablename__ = "agreement_premises"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_version_id: Mapped[int] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    listing_id: Mapped[str] = mapped_column(String(50), nullable=False)
    room_id: Mapped[int | None] = mapped_column(nullable=True)
    address: Mapped[str] = mapped_column(String(500), default="")
    city: Mapped[str] = mapped_column(String(100), default="")
    room_size: Mapped[int | None] = mapped_column(nullable=True)
    has_ensuite: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_areas: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement_version: Mapped["AgreementVersion"] = relationship(back_populates="premises")


class CommercialTermsSnapshot(Base):
    """Section 13.1 'commercial_terms_snapshot: Dates/rent/charges snapshot
    linked to Booking/Payments.' Same populate-once, queryable-table
    treatment as AgreementPremises above."""

    __tablename__ = "commercial_terms_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_version_id: Mapped[int] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    monthly_rent: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    deposit_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    term_months: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    agreement_version: Mapped["AgreementVersion"] = relationship(back_populates="commercial_terms")


class ExecutionCertificate(Base):
    """Section 7.1/13.1 'execution_certificate: Evidence summary for
    completed execution' -- distinct from DocumentArtifact (the rendered PDF
    itself): this is the structured evidence roll-up (document hash, every
    signer's role/method/consent timestamp, provider transaction ids),
    generated once inside freeze_agreement_version alongside the PDF, for
    the same EXECUTED_IMMUTABLE version."""

    __tablename__ = "execution_certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_version_id: Mapped[int] = mapped_column(
        ForeignKey("agreement_versions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # [{"role": "provider", "identifier": "...", "method": "SIMPLE_ESIGN", "consentedAt": "..."}]
    signer_summary: Mapped[list] = mapped_column(JSON, default=list)
    provider_transaction_ids: Mapped[list] = mapped_column(JSON, default=list)

    agreement_version: Mapped["AgreementVersion"] = relationship(back_populates="execution_certificate")
