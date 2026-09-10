"""ZR-ENG-CLR-004 Section 9.4/10.1/13.1/AC-18: the post-execution change
workflow. An amendment is a distinct, evidenced relationship between an
already-EXECUTED_IMMUTABLE AgreementVersion and the new version it produces
-- it never edits the source version in place (AC-07 still applies to it).

Section 9.4 lifecycle: REQUESTED -> CLASSIFIED -> TERMS_PROPOSED ->
APPROVALS_PENDING -> GENERATED -> EXECUTION_PENDING -> EXECUTED -> EFFECTIVE.
Every state is real and reachable via crud/agreement_amendments.py; two
adjacent pairs (TERMS_PROPOSED/APPROVALS_PENDING and GENERATED/
EXECUTION_PENDING, then EXECUTED/EFFECTIVE) are written inside the same
transaction as their own timestamp columns below, because the spec's own
diagram draws those particular arrows as direct/unconditional -- there is no
described intervening actor or fact between them, the same justification
already documented on AgreementVersion's own WORKING/FROZEN/HASHED/SIGNING
collapse. Nothing here is skipped -- every transition still gets its own
persisted timestamp, so the full history is always queryable even where two
statuses share one commit."""

from datetime import date, datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

AMENDMENT_STATUSES = (
    "REQUESTED", "CLASSIFIED", "TERMS_PROPOSED", "APPROVALS_PENDING", "GENERATED",
    "EXECUTION_PENDING", "EXECUTED", "EFFECTIVE",
)

# Section 10.1's own material change matrix, collapsed to the instrument
# names it actually distinguishes (assignment/novation and restatement are
# both "a different party/premises set" cases; renewal/correction are their
# own rows).
AMENDMENT_TYPES = (
    "MATERIAL_CHANGE", "ADDENDUM", "ASSIGNMENT_NOVATION", "RESTATED_AGREEMENT", "RENEWAL", "CORRECTION",
)


class AgreementAmendment(Base):
    __tablename__ = "agreement_amendments"

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id", ondelete="CASCADE"), nullable=False)
    source_version_id: Mapped[int] = mapped_column(ForeignKey("agreement_versions.id"), nullable=False)
    resulting_version_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_versions.id"), nullable=True)
    amendment_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="REQUESTED")
    reason: Mapped[str] = mapped_column(String(1000), default="")
    # {"monthlyRent": .., "depositAmount": .., "startDate": .., "termMonths": ..}
    # -- only the keys actually being changed; everything else in the
    # resulting version's snapshot is copied unchanged from source_version.
    proposed_terms: Mapped[dict] = mapped_column(JSON, default=dict)
    requested_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approvals_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_pending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agreement: Mapped["Agreement"] = relationship(back_populates="amendments")
