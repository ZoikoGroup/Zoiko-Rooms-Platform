from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# ZR-ENG-CLR-010 Section 6/24: the real-world forums a claim this codebase
# cannot decide internally (authority_class != A0) actually gets referred
# to. A3 (payment rail/PSP) is deliberately absent -- that stays the
# existing finance.DisputeCase chargeback mechanism (see
# models/dispute.py's own docstring); this object never duplicates it.
EXTERNAL_PROCEEDING_AUTHORITY_TYPES = (
    "DEPOSIT_SCHEME", "TRIBUNAL_COURT", "REGULATOR_PUBLIC_AUTHORITY", "EMERGENCY_AUTHORITY", "MEDIATION_ADR", "OTHER",
)

DISPUTE_EXTERNAL_PROCEEDING_FINALITY_STATES = ("FINAL", "UNDER_REVIEW")


class DisputeExternalProceeding(Base):
    """ZR-ENG-CLR-010 Section 13 (P4/P5/P6)/24/Section 22: tracks one
    external filing -- a deposit-scheme adjudication, a tribunal/court case,
    a regulator complaint -- that an A2+ (external-only) claim was referred
    to. This is the ONLY thing allowed to move such a claim to a terminal
    outcome (decide_claim in crud/disputes.py refuses everything but A0,
    per AC-6); nothing here executes money movement or possession/access
    changes itself -- see crud/dispute_external_proceeding.py's own
    docstring for the explicit non-behaviors (AC-8/Section 14: a deposit
    claim's protected funds still only move through crud/finance.py's
    existing scheme/custody logic).

    outcome_evidence_id reuses Phase 2's evidence service (the uploaded
    order/decision document) rather than a second file store."""

    __tablename__ = "dispute_external_proceedings"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    authority_type: Mapped[str] = mapped_column(String(30), nullable=False)
    authority_name: Mapped[str] = mapped_column(String(255), default="")
    external_reference: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(15), default="FILED")
    finality_state: Mapped[str | None] = mapped_column(String(15), nullable=True)
    filed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    decision_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    outcome_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("dispute_evidence_items.id", ondelete="SET NULL"), nullable=True)
    outcome_summary: Mapped[str] = mapped_column(String(2000), default="")
    filed_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_by_admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # QA-Q21: "Claim filed after external deadline; system does not
    # invent extension; routes according to forum rules." Only ever
    # computed when the case's resolved MarketPolicyPack configures
    # `dispute_external_filing_deadline_days` (null on both fields
    # otherwise -- no statutory deadline is known, so none is recorded).
    # This never blocks or shortens a filing -- Zoiko has no authority to
    # decide a statutory limitation question, only to record what it
    # computed and let the external forum apply its own rule.
    external_deadline_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    filed_after_deadline: Mapped[bool] = mapped_column(default=False)
    # AC-39 -- see app/models/dispute.py's DisputeResolutionCase.version.
    version: Mapped[int] = mapped_column(default=1)

    __mapper_args__ = {"version_id_col": version}

    case: Mapped["DisputeResolutionCase"] = relationship()
    outcome_evidence: Mapped["DisputeEvidenceItem"] = relationship()
    filed_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[filed_by_admin_id])
    decided_by_admin: Mapped["AdminUser"] = relationship(foreign_keys=[decided_by_admin_id])
    claim_links: Mapped[list["DisputeExternalProceedingClaimLink"]] = relationship(back_populates="proceeding", cascade="all, delete-orphan")


class DisputeExternalProceedingClaimLink(Base):
    """One filing can cover more than one claim on the same case (e.g. a
    single tribunal case ruling on both a deposit deduction and an
    associated compensation claim) -- same many-to-many shape as Phase 2's
    DisputeEvidenceClaimLink."""

    __tablename__ = "dispute_external_proceeding_claim_links"
    __table_args__ = (UniqueConstraint("proceeding_id", "claim_id", name="uq_dispute_ext_proceeding_claim_links_proceeding_claim"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    proceeding_id: Mapped[int] = mapped_column(ForeignKey("dispute_external_proceedings.id", ondelete="CASCADE"), nullable=False, index=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("dispute_resolution_claims.id", ondelete="CASCADE"), nullable=False, index=True)

    proceeding: Mapped["DisputeExternalProceeding"] = relationship(back_populates="claim_links")
    claim: Mapped["DisputeResolutionClaim"] = relationship()
